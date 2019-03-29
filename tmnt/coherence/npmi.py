from math import log10
from collections import Counter

import numpy as np

from tmnt.utils.ngram_helpers import BigramReader
from itertools import combinations

__all__ = ['NPMI', 'EvaluateNPMI']

class NPMI(object):

    def __init__(self, unigram_cnts: Counter, bigram_cnts: Counter, n_docs: int):
        self.unigram_cnts = unigram_cnts
        self.bigram_cnts = bigram_cnts
        self.n_docs = n_docs
        

    def wd_id_pair_npmi(self, w1: int, w2: int):
        cw1 = self.unigram_cnts.get(w1, 0.0)
        cw2 = self.unigram_cnts.get(w2, 0.0)
        c12 = self.bigram_cnts.get((w1, w2), 0.0)
        if cw1 == 0.0 or cw2 == 0.0 or c12 == 0.0:
            return 0.0
        else:
            return (log10(self.n_docs) + log10(c12) - log10(cw1) - log10(cw2)) / (log10(self.n_docs) - log10(c12))


class EvaluateNPMI(object):

    def __init__(self, top_k_words_per_topic):
        self.top_k_words_per_topic = top_k_words_per_topic

    def evaluate_sp_vec(self, test_sparse_vec):
        reader = BigramReader(test_sparse_vec)
        npmi = NPMI(reader.unigrams, reader.bigrams, reader.n_docs)
        total_npmi = 0
        for i, words_per_topic in enumerate(self.top_k_words_per_topic):
            total_topic_npmi = 0
            N = len(words_per_topic)
            for (w1, w2) in combinations(sorted(words_per_topic), 2):
                wp_npmi = npmi.wd_id_pair_npmi(w1, w2)
                total_topic_npmi += wp_npmi
            total_topic_npmi *= (2 / (N * (N-1)))
            total_npmi += total_topic_npmi
        return total_npmi / len(self.top_k_words_per_topic)

    def evaluate_csr_mat(self, csr_mat):
        mat = csr_mat.asnumpy() ## converts to dense matrix - could take up a lot of memory for large test sets
        n_docs = mat.shape[0]
        total_npmi = 0
        for i, words_per_topic in enumerate(self.top_k_words_per_topic):
            total_topic_npmi = 0
            n_topics = len(words_per_topic)
            for (w1, w2) in combinations(sorted(words_per_topic), 2):
                occur_1 = np.array(mat[:, w1] > 0, dtype='int')
                occur_2 = np.array(mat[:, w2] > 0, dtype='int')
                unigram_1 = occur_1.sum()
                unigram_2 = occur_2.sum()
                bigram_cnt = np.sum(occur_1 * occur_2)
                if bigram_cnt < 1:
                    npmi = 0.0
                else:
                    npmi = (log10(n_docs) + log10(bigram_cnt) - log10(unigram_1) - log10(unigram_2)) / (log10(n_docs) - log10(bigram_cnt))
                total_topic_npmi += npmi
            total_topic_npmi *= (2 / (n_topics * (n_topics-1)))
            total_npmi += total_topic_npmi
        return total_npmi / len(self.top_k_words_per_topic)
        

