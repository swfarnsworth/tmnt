# coding: utf-8
"""
Copyright (c) 2019 The MITRE Corporation.
"""


import io
import os
import json
import gluonnlp as nlp
import glob
from gluonnlp.data import Counter
from multiprocessing import Pool, cpu_count
from mantichora import mantichora
from atpbar import atpbar
import threading
import logging

from tmnt.preprocess import BasicTokenizer

__all__ = ['JsonVectorizer', 'TextVectorizer']

lock = threading.RLock()

class Vectorizer(object):

    def __init__(self, custom_stop_word_file=None, encoding='utf-8'):
        self.encoding = encoding
        self.tokenizer = BasicTokenizer(use_stop_words=True, custom_stop_word_file=custom_stop_word_file, encoding=encoding)
        self.json_rewrite = False

    def get_counter_dir_parallel(self, data_dir, pat):
        raise NotImplementedError('Vectorizer must be instantiated as TextVectorizer or JsonVectorizer')

    def vectorize_fn(self, file_and_vocab):
        raise NotImplementedError('Vectorizer fn must be specified by concrete subclass')
    
    def get_vocab(self, counter, size):
        vocab = nlp.Vocab(counter, unknown_token=None, padding_token=None,
                          bos_token=None, eos_token=None, min_freq=5, max_size=size)
        return vocab


    def chunks(self, l, n):
        """Yield successive n-sized chunks from l."""
        for i in range(0, len(l), n):
            yield l[i:i + n]

    def task_vec_fn(self, name, files):
        sp_vecs = []
        for i in atpbar(range(len(files)), name=name):
            sp_vecs.append(self.vectorize_fn(files[i]))
        return sp_vecs

    
    def direct_out_vec_fn(self, name, files, sp_out_file):
        global lock
        for i in atpbar(range(len(files)), name=name):
            vs = self.vectorize_fn(files[i])
            lock.acquire()
            try:
                with io.open(sp_out_file, 'a+') as out_stream:
                    for (v,l) in vs:
                        out_stream.write(str(l))
                        for (i,c) in v:
                            out_stream.write(' ')
                            out_stream.write(str(i))
                            out_stream.write(':')
                            out_stream.write(str(c))
                        out_stream.write('\n')
            finally:
                lock.release()
        return None
        

    def get_sparse_vecs(self, sp_out_file, vocab_out_file, data_dir, vocab_size=2000, i_vocab=None, full_histogram_file=None,
                        pat='*.json'):
        files = glob.glob(data_dir + '/' + pat)
        if i_vocab is None:
            counter = self.get_counter_dir_parallel(data_dir, pat)
            vocab = self.get_vocab(counter, vocab_size)
        else:
            vocab = i_vocab
        files_and_vocab = [(f,vocab) for f in files]
        if self.json_rewrite:
            vec_fn = self.task_vec_fn
        else:
            vec_fn = self.direct_out_vec_fn
        if len(files_and_vocab) > 2:
            file_batches = list(self.chunks(files_and_vocab, max(1, len(files_and_vocab) // cpu_count())))
            with mantichora() as mcore:
                for i in range(len(file_batches)):
                    mcore.run(vec_fn,"Vectorizing Batch {}".format(i), file_batches[i], sp_out_file)
                sp_vecs = mcore.returns()
            if not self.json_rewrite:
                sp_vecs = [ item for sl in sp_vecs for item in sl ]
        else:
            sp_vecs = map(self.vectorize_fn, files_and_vocab)
        ## if we're not outputing json and we used non-concurrent processing, need to print out vecs here
        if not self.json_rewrite and len(files_and_vocab) <= 2:
            sp_list = list(sp_vecs)
            with io.open(sp_out_file, 'w', encoding=self.encoding) as fp:
                for block in sp_list:
                    for (v,l) in block:
                        fp.write(str(l))  
                        for (i,c) in v:
                            fp.write(' ')
                            fp.write(str(i))
                            fp.write(':')
                            fp.write(str(c))
                        fp.write('\n')
        if i_vocab is None: ## print out vocab if we had to create it
            with io.open(vocab_out_file, 'w', encoding=self.encoding) as fp:
                for i in range(len(vocab.idx_to_token)):
                    fp.write(vocab.idx_to_token[i])
                    fp.write('\n')
        if full_histogram_file:
            with io.open(full_histogram_file, 'w', encoding=self.encoding) as fp:
                items = list(counter.items())
                items.sort(key=lambda x: -x[1])
                for k,v in items:
                    fp.write(str(k))
                    fp.write(' ')
                    fp.write(str(v))
                    fp.write('\n')
        return vocab


class JsonVectorizer(Vectorizer):

    def __init__(self, custom_stop_word_file=None, text_key='body', label_key=None, min_doc_size=6, label_prefix=-1,
                 json_rewrite=False, encoding='utf-8'):
        super(JsonVectorizer, self).__init__(custom_stop_word_file, encoding=encoding)
        self.encoding = encoding
        self.text_key = text_key
        self.label_key = label_key
        self.label_prefix = label_prefix
        self.min_doc_size = min_doc_size
        self.json_rewrite = json_rewrite

    def get_counter_file(self, json_file):
        counter = None
        with io.open(json_file, 'r', encoding=self.encoding) as fp:
            for l in fp:
                js = json.loads(l)
                txt = js[self.text_key] ## text field
                counter = nlp.data.count_tokens(self.tokenizer.tokenize(txt), counter = counter)
        return counter

    def task(self, name, files):
        counters = []
        for i in atpbar(range(len(files)), name=name):
            counters.append(self.get_counter_file(files[i]))
        return counters

    def get_counter_dir_parallel(self, data_dir, pat):
        files = glob.glob(data_dir + '/' + pat)
        if len(files) > 2:
            file_batches = list(self.chunks(files, max(1, len(files) // cpu_count())))
            logging.info("Counting vocabulary over {} text files with {} batches".format(len(files), len(file_batches)))
            with mantichora() as mcore:
                for i in range(len(file_batches)):
                    mcore.run(self.task,"Counting Vocab Items - Batch {}".format(i), file_batches[i])
                counter_cs = mcore.returns()
            counters = [ item for sl in counter_cs for item in sl ]
        else:
            counters = map(self.get_counter_file, files)
        return sum(counters, Counter())

    def vectorize_fn_to_json(self, file_and_vocab):
        json_file, vocab = file_and_vocab
        json_path, file_name = os.path.split(json_file)
        n_json_file = os.path.join(json_path, "vec_"+file_name)
        with io.open(json_file, 'r', encoding=self.encoding) as fp:
            with io.open(n_json_file, 'w', encoding=self.encoding) as op:
                for l in fp:
                    js = json.loads(l)
                    toks = self.tokenizer.tokenize(js[self.text_key])
                    try:
                        lstr = js[self.label_key]
                        if self.label_prefix > 0:
                            label_str = lstr[:self.label_prefix]
                        else:
                            label_str = lstr
                    except KeyError:
                        label_str = "<unk>"
                    tok_ids = [vocab[token] for token in toks if token in vocab]
                    if (len(tok_ids) >= self.min_doc_size):
                        cnts_items = nlp.data.count_tokens(tok_ids).items()
                        js['sp_vec'] = [[k,v] for k,v in cnts_items]
                        op.write(json.dumps(js))
                        op.write('\n')
                        

    def vectorize_fn_std(self, file_and_vocab):
        json_file, vocab = file_and_vocab
        sp_vecs = []
        with io.open(json_file, 'r', encoding=self.encoding) as fp:
            for l in fp:
                js = json.loads(l)
                toks = self.tokenizer.tokenize(js[self.text_key])
                try:
                    lstr = js[self.label_key]
                    if self.label_prefix > 0:
                        label_str = lstr[:self.label_prefix]
                    else:
                        label_str = lstr
                except KeyError:
                    label_str = "<unk>"
                tok_ids = [vocab[token] for token in toks if token in vocab]
                if (len(tok_ids) >= self.min_doc_size):
                    cnts = nlp.data.count_tokens(tok_ids)
                    sp_vecs.append((sorted(cnts.items()), label_str))
        return sp_vecs

    def vectorize_fn(self, file_and_vocab):
        if self.json_rewrite:
            return self.vectorize_fn_to_json(file_and_vocab)
        else:
            return self.vectorize_fn_std(file_and_vocab)


class TextVectorizer(Vectorizer):

    def __init__(self, custom_stop_word_file=None, min_doc_size=6, encoding='utf-8'):
        super(TextVectorizer, self).__init__(custom_stop_word_file, encoding=encoding)
        self.min_doc_size = min_doc_size

        
    def task(self, name, files):
        counters = []
        for i in atpbar(range(len(files)), name=name):
            counters.append(self.get_counter_file_batch(files[i]))
        return counters


    def get_counter_dir_parallel(self, txt_dir, pat='*.txt'):
        def batches(l, n):
            for i in range(0, len(l), n):
                yield l[i:i+n]
        files = glob.glob(txt_dir + '/' + pat)
        batch_size = max(1, int(len(files) / 20))
        file_batches = list(batches(files, batch_size))
        if len(file_batches) > 2:
            file_batch_batches = list(self.chunks(file_batches, max(1, len(files) // cpu_count())))
            with mantichora() as mcore:
                for i in range(len(file_batch_batches)):
                    mcore.run(self.task,"Counting Vocab Items - Batch {}".format(i), file_batch_batches[i])
                counter_cs = mcore.returns()
            counters = [ item for sl in counter_cs for item in sl ]
        else:
            counters = map(self.get_counter_file_batch, file_batches)    
        return sum(counters, Counter())


    def get_counter_file_batch(self, txt_file_batch):
        counter = Counter()
        for txt_file in txt_file_batch:
            with io.open(txt_file, 'r', encoding=self.encoding) as fp:
                for txt in fp:
                    counter = nlp.data.count_tokens(self.tokenizer.tokenize(txt), counter = counter)
        return counter


    def vectorize_fn(self, txt_file_and_vocab):
        txt_file, vocab = txt_file_and_vocab
        sp_vecs = []
        with io.open(txt_file, 'r', encoding=self.encoding) as fp:
            doc_tok_ids = []
            for txt in fp:
                toks = self.tokenizer.tokenize(txt)
                tok_ids = [vocab[token] for token in toks if token in vocab]
                doc_tok_ids.extend(tok_ids)
            if (len(doc_tok_ids) >= self.min_doc_size):
                cnts = nlp.data.count_tokens(doc_tok_ids)
                sp_vecs.append((sorted(cnts.items()), "<unk>"))
        return sp_vecs

    
