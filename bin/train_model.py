# coding: utf-8

import os, sys
import argparse

from tmnt.bow_vae.train import train_bow_vae
from tmnt.common_params import get_base_argparser

parser = get_base_argparser()
parser.description = 'Train a bag-of-words representation topic model as Variational AutoEncoder'

parser.add_argument('--config', type=str, help='Configuration file (generated by select_model.py or set by hand)')
parser.add_argument('--eval_freq', type=int, help='Frequency of evaluation against validation data during training', default=1)
parser.add_argument('--trace_file', type=str, default=None, help='Trace: (epoch, perplexity, NPMI) on validation data into a separate file')

args = parser.parse_args()

if __name__ == '__main__':
    os.environ["MXNET_STORAGE_FALLBACK_LOG_VERBOSE"] = "0"
    train_bow_vae(args)

