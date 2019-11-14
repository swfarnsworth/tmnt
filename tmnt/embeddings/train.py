# coding: utf-8

import argparse
import logging
import os
import random
import sys
import time
import io
import datetime
import multiprocessing

from tmnt.embeddings.data import transform_data_fasttext, transform_data_word2vec, preprocess_dataset_stream, preprocess_dataset, CustomDataSet
from tmnt.utils.log_utils import logging_config
from tmnt.embeddings.model import SG, CBOW

import mxnet as mx
import numpy as np
import gluonnlp as nlp


def get_context(args):
    if args.gpu is None or args.gpu == '':
        context = [mx.cpu()]
    elif isinstance(args.gpu, int):
        context = [mx.gpu(args.gpu)]
    else:
        context = [mx.gpu(int(i)) for i in args.gpu]
    return context



def train_embeddings(args, exp_folder):
    """Training helper."""
    if not args.model.lower() in ['cbow', 'skipgram']:
        logging.error('Unsupported model %s.', args.model)
        sys.exit(1)

    if args.pre_embedding_name and args.ngram_buckets > 0:
        logging.error("Pre-trained embeddings not yet usable with fasttext-style embeddings")
        sys.exit(1)

    if args.pre_embedding_name:
        e_type, e_name = tuple(args.pre_embedding_name.split(':'))
        pt_embedding = nlp.embedding.create(e_type, source=e_name)
        logging.info("Fine-tuning pre-trained (downloaded) embeddings {}".format(e_name))
        em_size = len(pt_embedding.idx_to_vec[0])
    else:
        pt_embedding = None
        em_size = args.emsize
    logging.info("Embedding size: {}".format(em_size))


    data = CustomDataSet(args.data_root,args.file_pattern, '<bos>', '<eos>', skip_empty=True)
    data, vocab, idx_to_counts = preprocess_dataset_stream(data, logging, max_vocab_size = args.max_vocab_size)

    if pt_embedding:
        pt_embedding = nlp.embedding.TokenEmbedding(allow_extend=True, idx_to_token=pt_embedding.idx_to_token, idx_to_vec=pt_embedding.idx_to_vec)
        for t in vocab.token_to_idx:
            if pt_embedding.token_to_idx[t] == 0:  ## means it's not in the pre-embedding vocab
                pt_embedding[t] = mx.random.normal(loc=0.0, scale=0.1, shape=em_size)
        n_idx_to_counts = [0 for i in pt_embedding.idx_to_token]
        n_counter = nlp.data.Counter(pt_embedding.idx_to_token)
        for i,cnt in idx_to_counts:
            t = vocab.idx_to_token[i]
            ni = pt_embedding.token_to_idx[t]
            n_idx_to_counts[ni] += cnt
        vocab = nlp.Vocab(n_counter)
        idx_to_counts = n_idx_to_counts

    logging.info('Data pre-processing complete.  Data transform beginning...')

    if args.ngram_buckets > 0:
        data, batchify_fn, subword_function = transform_data_fasttext(
            data, vocab, idx_to_counts, cbow=args.model.lower() == 'cbow',
            ngram_buckets=args.ngram_buckets, ngrams=args.ngrams,
            batch_size=args.batch_size, window_size=args.window,
            frequent_token_subsampling=args.frequent_token_subsampling)
    else:
        subword_function = None
        data, batchify_fn = transform_data_word2vec(
            data, vocab, idx_to_counts, cbow=args.model.lower() == 'cbow',
            batch_size=args.batch_size, window_size=args.window,
            frequent_token_subsampling=args.frequent_token_subsampling)

    num_tokens = float(sum(idx_to_counts))
    
    model = CBOW if args.model.lower() == 'cbow' else SG
    embedding = model(token_to_idx=vocab.token_to_idx, output_dim=em_size,
                      batch_size=args.batch_size, num_negatives=args.negative,
                      negatives_weights=mx.nd.array(idx_to_counts),
                      subword_function=subword_function)
    context = get_context(args)
    embedding.initialize(ctx=context)

    if pt_embedding:
        vocab.set_embedding(pt_embedding)
        embedding.embedding.weight.set_data(vocab.embedding.idx_to_vec)
        
    if not args.no_hybridize:
        embedding.hybridize(static_alloc=True, static_shape=True)

    optimizer_kwargs = dict(learning_rate=args.lr)
    try:
        trainer = mx.gluon.Trainer(embedding.collect_params(), args.optimizer,
                                   optimizer_kwargs)
    except ValueError as e:
        if args.optimizer == 'groupadagrad':
            logging.warning('MXNet <= v1.3 does not contain '
                            'GroupAdaGrad support. Falling back to AdaGrad')
            trainer = mx.gluon.Trainer(embedding.collect_params(), 'adagrad',
                                       optimizer_kwargs)
        else:
            raise e

    try:
        if args.no_prefetch_batch:
            data = data.transform(batchify_fn)
        else:
            from tmnt.embeddings.executors import LazyThreadPoolExecutor
            num_cpu = multiprocessing.cpu_count()
            ex = LazyThreadPoolExecutor(num_cpu)
    except (ImportError, SyntaxError, AttributeError):
        # Py2 - no async prefetching is supported
        logging.warning(
            'Asynchronous batch prefetching is not supported on Python 2. '
            'Consider upgrading to Python 3 for improved performance.')
        data = data.transform(batchify_fn)

    num_update = 0
    prefetched_iters = []
    for _ in range(min(args.num_prefetch_epoch, args.epochs)):
        prefetched_iters.append(iter(data))
    for epoch in range(args.epochs):
        if epoch + len(prefetched_iters) < args.epochs:
            prefetched_iters.append(iter(data))
        data_iter = prefetched_iters.pop(0)

        try:
            batches = ex.map(batchify_fn, data_iter)
        except NameError:  # Py 2 or batch prefetching disabled
            batches = data_iter

        # Logging variables
        log_wc = 0
        log_start_time = time.time()
        log_avg_loss = 0

        for i, batch in enumerate(batches):
            ctx = context[i % len(context)]
            batch = [array.as_in_context(ctx) for array in batch]
            with mx.autograd.record():
                loss = embedding(*batch)
            loss.backward()

            num_update += loss.shape[0]
            if len(context) == 1 or (i + 1) % len(context) == 0:
                trainer.step(batch_size=1)

            # Logging
            log_wc += loss.shape[0]
            log_avg_loss += loss.mean().as_in_context(context[0])
            if (i + 1) % args.log_interval == 0:
                # Forces waiting for computation by computing loss value
                log_avg_loss = log_avg_loss.asscalar() / args.log_interval
                wps = log_wc / (time.time() - log_start_time)
                # Due to subsampling, the overall number of batches is an upper
                # bound
                num_batches = num_tokens // args.batch_size
                if args.model.lower() == 'skipgram':
                    num_batches = (num_tokens * args.window * 2) // args.batch_size
                else:
                    num_batches = num_tokens // args.batch_size
                logging.info('[Epoch {} Batch {}/{}] loss={:.4f}, '
                             'throughput={:.2f}K wps, wc={:.2f}K'.format(
                                 epoch, i + 1, num_batches, log_avg_loss,
                                 wps / 1000, log_wc / 1000))
                log_start_time = time.time()
                log_avg_loss = 0
                log_wc = 0
    with io.open(os.path.join(exp_folder, 'vocab.json'), 'w') as f:
        js_vocab = vocab.to_json()
        f.write(js_vocab)
    embedding.embedding.save_parameters(os.path.join(exp_folder, 'embedding.params'))
    if args.model_export:
        idx_to_vec = embedding.embedding.weight.data()
        with io.open(os.path.join(exp_folder, args.model_export), 'w') as f:
            for i in range(len(vocab.idx_to_token)):
                f.write(vocab.idx_to_token[i])
                for j in range(len(idx_to_vec[i])):
                    f.write(' ')
                    f.write(str(idx_to_vec[i][j].asscalar()))
                f.write('\n')

    
def norm_vecs_by_row(x):
    return x / (mx.nd.sum(x * x, axis=1) + 1e-10).sqrt().reshape((-1, 1))


def get_k_closest_tokens(vocab, embedding, k, word):
    word_vec = norm_vecs_by_row(embedding[[word]])    
    vocab_vecs = norm_vecs_by_row(embedding[vocab._idx_to_token])
    dot_prod = mx.nd.dot(vocab_vecs, word_vec.T)
    indices = mx.nd.topk(
        dot_prod.reshape((len(vocab._idx_to_token), )),
        k=k + 1,
        ret_typ='indices')
    indices = [int(i.asscalar()) for i in indices]
    result = [vocab.idx_to_token[i] for i in indices[1:]]
    return result



def train(args):
    i_dt = datetime.datetime.now()
    exp_folder = '{}/exp_{}_{}_{}_{}_{}_{}'.format(args.logdir,i_dt.year,i_dt.month,i_dt.day,i_dt.hour,i_dt.minute,i_dt.second)
    logging_config(exp_folder, name="Embeddings", level=logging.INFO)
    logging.info(args)
    random.seed(args.seed)
    mx.random.seed(args.seed)
    np.random.seed(args.seed)
    train_embeddings(args, exp_folder)
