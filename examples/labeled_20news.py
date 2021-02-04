"""
Joint topic + labeling model
============================

Another example with 20 news dataset. This involves
building a model using the labels as prediction targets.
"""

from tmnt.estimator import BowEstimator, LabeledBowEstimator
import numpy as np
import gluonnlp as nlp
import os
import umap
from sklearn.datasets import fetch_20newsgroups
from tmnt.preprocess.vectorizer import TMNTVectorizer
from tmnt.inference import BowVAEInferencer


data, y = fetch_20newsgroups(shuffle=True, random_state=1,
                             remove=('headers', 'footers', 'quotes'),
                             return_X_y=True)
tf_vectorizer = TMNTVectorizer(vocab_size=1000)
X, _ = tf_vectorizer.fit_transform(data)

num_label_values = int(np.max(y)) + 1 # get the number of possible labels

def estimate_with_gamma(gamma, f=None):
    ## gamma determines the degree to which classification loss is weighted
    ## total loss = topic_loss + gamma * classification_loss
    l_estimator = LabeledBowEstimator(tf_vectorizer.get_vocab(), n_labels=num_label_values, gamma=gamma)
    _ = l_estimator.fit(X, y) # fit a covariate model using y
    ## get results from validation
    v_results = l_estimator.validate(X, y)
    l_inferencer = BowVAEInferencer(l_estimator.model)
    embeddings = l_inferencer.get_umap_embeddings(X)
    l_inferencer.plot_to(embeddings, y, f)
    return embeddings, v_results

embeddings, v_res = estimate_with_gamma(1.0)


