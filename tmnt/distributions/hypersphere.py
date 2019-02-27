#coding: utf-8

import math
import mxnet as mx
import numpy as np
from scipy import special as sp
from mxnet import gluon
from tmnt.distributions.latent_distrib import LatentDistribution

__all__ = ['HyperSphericalLatentDistribution']

class HyperSphericalLatentDistribution(LatentDistribution):

    def __init__(self, n_latent, kappa=100.0, ctx=mx.cpu()):
        super(HyperSphericalLatentDistribution, self).__init__(n_latent, ctx)
        self.kappa = kappa
        self.kld_v = mx.nd.array(HyperSphericalLatentDistribution._vmf_kld(self.kappa, self.n_latent), ctx=ctx)
        #self.kld = mx.nd.broadcast_to(self.kld_v, shape=(batch_size,), ctx=ctx)
        with self.name_scope():
            self.mu_encoder = gluon.nn.Dense(units = n_latent, activation=None)


    def hybrid_forward(self, F, data, batch_size):
        mu = self.mu_encoder(data)
        #print("Shape mu = {}".format(mu.shape))        
        norm = F.norm(mu, axis=1, keepdims=True)
        #print("Shape norm = {}".format(norm.shape))                
        mu = F.broadcast_div(mu, norm)
        #kld = self.kld
        kld = F.broadcast_to(self.kld_v, shape=(batch_size,))
        vec = self._get_hypersphere_sample(F, mu, batch_size)
        return vec, kld
    
        
    def _get_hypersphere_sample(self, F, mu, batch_size):
        mu = mu # F.norm(...)  - already normalized
        sw = self._get_weight_batch(F, batch_size)
        sw = F.expand_dims(sw, axis=1)
        sw_v = sw * F.ones((batch_size, self.n_latent), ctx=self.model_ctx)
        vv = self._get_orthonormal_batch(F, mu)
        sc11 = F.ones((batch_size, self.n_latent), ctx=self.model_ctx)
        sc22 = sw_v ** 2.0
        sc_factor = F.sqrt(sc11 - sc22)
        orth_term = vv * sc_factor
        mu_scaled = mu * sw_v
        return orth_term + mu_scaled    
        #return F.expand_dims(orth_term + mu_scaled, axis=0)


    @staticmethod
    def _vmf_kld(k, d):
        tmp = (k * ((sp.iv(d / 2.0 + 1.0, k) + sp.iv(d / 2.0, k) * d / (2.0 * k)) / sp.iv(d / 2.0, k) - d / (2.0 * k)) \
               + d * np.log(k) / 2.0 - np.log(sp.iv(d / 2.0, k)) \
               - sp.loggamma(d / 2 + 1) - d * np.log(2) / 2).real
        if tmp != tmp:
            exit()
        return np.array([tmp])

    def _get_weight_batch(self, F, batch_size):
        batch_sample = F.zeros((batch_size,), ctx=self.model_ctx)
        for i in range(batch_size):
            batch_sample[i] = self._get_single_weight()
        return batch_sample

    def _get_single_weight(self):
        dim = self.n_latent
        kappa = self.kappa
        dim = dim - 1  
        b = dim / (np.sqrt(4. * kappa ** 2 + dim ** 2) + 2 * kappa)  # b= 1/(sqrt(4.* kdiv**2 + 1) + 2 * kdiv)
        x = (1. - b) / (1. + b)
        c = kappa * x + dim * np.log(1 - x ** 2)  # dim * (kdiv *x + np.log(1-x**2))

        while True:
            z = np.random.beta(dim / 2., dim / 2.)  # concentrates towards 0.5 as d-> inf
            w = (1. - (1. + b) * z) / (1. - (1. - b) * z)
            u = np.random.uniform(low=0, high=1)
            if kappa * w + dim * np.log(1. - x * w) - c >= np.log(u):  # thresh is dim *(kdiv * (w-x) + log(1-x*w) -log(1-x**2))
                return w

    def _get_orthonormal_batch(self, F, mu):
        batch_size = mu.shape[0]
        dim = self.n_latent
        mu_1 = F.expand_dims(mu, axis=1)
        rv = F.random_normal(loc=0, scale=1, shape=(batch_size, self.n_latent, 1), ctx=self.model_ctx)
        rescaled = F.squeeze(F.linalg.gemm2(mu_1, rv), axis=2)
        proj_mu_v = mu * rescaled
        o_vec = rv.squeeze() - proj_mu_v
        o_norm = F.norm(o_vec, axis=1, keepdims=True)
        return o_vec / o_norm


