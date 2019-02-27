from math import log2, log10
from collections import Counter

import numpy as np

class PMI(object):

    def __init__(self, unigram_freq: Counter, bigram_freq: Counter, n_docs: int):
        self.unigram_freq = unigram_freq
        self.bigram_freq = bigram_freq
        self.n_docs = n_docs

        self.num_unigrams = np.float32(sum(unigram_freq.values()))
        self.num_bigrams = np.float32(sum(bigram_freq.values()))

    def pmi(self, w1: int, w2: int):
        """
        PMI(w_i, w_j) = log_2(p(w_i, w_j) / (p(w_i) * p(w_j)))
        """
        pw1 = (self.unigram_freq[w1]) / self.n_docs
        pw2 = (self.unigram_freq[w2]) / self.n_docs
        # bigrams are stored unordered to save space. If you can't find bigram (w1, w2), try (w2, w1)
        pw1_w2 = (self.bigram_freq.get((w1, w2), self.bigram_freq[(w2, w1)])) / self.n_docs
        if pw1 == 0.0 or pw2 == 0.0 or pw1_w2 == 0.0:
            return 0.0
        else:
            return log10((pw1_w2) / pw1 / pw2)

    def npmi(self, w1: int, w2: int):
        """
        NPMI(w_i, w_j) = PMI(w_i, w_j) / -log_10(p(w_i, w_j))
        """
        pmi = self.pmi(w1, w2)
        # bigrams are stored unordered to save space. If you can't find bigram (w1, w2), try (w2, w1)
        pw1_w2 = (self.bigram_freq[(w1, w2)]) / self.n_docs
        if pmi == 0.0:
            return 0.0
        else:
            return pmi / -log10(pw1_w2)