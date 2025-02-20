import copy
import os
import typing

import ldac
import numpy as np
import pandas as pd
from d3m import container, utils
from d3m.metadata import hyperparams, base as metadata_base
from d3m.metadata import params
from d3m.primitive_interfaces import base
from d3m.primitive_interfaces.unsupervised_learning import UnsupervisedLearnerPrimitiveBase
from sklearn.feature_extraction.text import CountVectorizer

from fastlvm.utils import get_documents, mk_text_features, tokenize, split_inputs

Inputs = container.DataFrame
Outputs = container.DataFrame
Predicts = container.ndarray  # type: np.ndarray


class Params(params.Params):
    topic_matrix: bytes  # Byte stream represening topics
    vectorizer: typing.Any
    analyze: typing.Any


class HyperParams(hyperparams.Hyperparams):
    k = hyperparams.UniformInt(lower=1, upper=10000, default=10,
                               semantic_types=['https://metadata.datadrivendiscovery.org/types/TuningParameter'],
                               description='The number of clusters to form as well as the number of centroids to '
                                           'generate.')
    iters = hyperparams.UniformInt(lower=1, upper=10000, default=100,
                                   semantic_types=['https://metadata.datadrivendiscovery.org/types/TuningParameter'],
                                   description='The number of iterations of inference.')
    num_top = hyperparams.UniformInt(lower=1, upper=10000, default=1,
                                     semantic_types=['https://metadata.datadrivendiscovery.org/types/TuningParameter'],
                                     description='The number of top words requested')
    frac = hyperparams.Uniform(lower=0, upper=1, default=0.01, upper_inclusive=False,
                               semantic_types=['https://metadata.datadrivendiscovery.org/types/TuningParameter'],
                               description='The fraction of training data set aside as the validation. 0 = use all '
                                           'training as validation')


class LDA(UnsupervisedLearnerPrimitiveBase[Inputs, Outputs, Params, HyperParams]):
    """
    This class provides functionality for unsupervised inference on latent Dirichlet allocation, which is a
    probabilistic topic model of corpora of documents which seeks to represent the underlying thematic structure of
    the document collection. They have emerged as a powerful new technique of finding useful structure in an
    unstructured collection as it learns distributions over words. The high probability words in each distribution
    gives us a way of understanding the contents of the corpus at a very high level. In LDA, each document of the
    corpus is assumed to have a distribution over K topics, where the discrete topic distributions are drawn from a
    symmetric dirichlet distribution. Standard packages, like those in scikit learn are inefficient in addition to
    being limited to a single machine. Whereas our underlying C++ implementation can be distributed to run on
    multiple machines. To enable the distribution through python interface is work in progress. The API is similar to
    sklearn.decomposition.LatentDirichletAllocation.
    """

    metadata = metadata_base.PrimitiveMetadata({
        "id": "f410b951-1cb6-481c-8d95-2d97b31d411d",
        "version": "3.1.1",
        "name": "Latent Dirichlet Allocation Topic Modelling",
        "description": "This class provides functionality for unsupervised inference on latent Dirichlet allocation, "
                       "which is a probabilistic topic model of corpora of documents which seeks to represent the "
                       "underlying thematic structure of the document collection. They have emerged as a powerful new "
                       "technique of finding useful structure in an unstructured collection as it learns "
                       "distributions over words. The high probability words in each distribution gives us a way of "
                       "understanding the contents of the corpus at a very high level. In LDA, each document of the "
                       "corpus is assumed to have a distribution over K topics, where the discrete topic "
                       "distributions are drawn from a symmetric dirichlet distribution. Standard packages, "
                       "like those in scikit learn are inefficient in addition to being limited to a single machine. "
                       "Whereas our underlying C++ implementation can be distributed to run on multiple machines. To "
                       "enable the distribution through python interface is work in progress. The API is similar to "
                       "sklearn.decomposition.LatentDirichletAllocation.",
        "python_path": "d3m.primitives.natural_language_processing.lda.Fastlvm",
        "primitive_family": metadata_base.PrimitiveFamily.NATURAL_LANGUAGE_PROCESSING,
        "algorithm_types": ["LATENT_DIRICHLET_ALLOCATION"],
        "keywords": ["large scale LDA", "topic modeling", "clustering"],
        "source": {
            "name": "CMU",
            "contact": "mailto:donghanw@cs.cmu.edu",
            "uris": ["https://gitlab.datadrivendiscovery.org/cmu/fastlvm", "https://github.com/autonlab/fastlvm"]
        },
        "installation": [
            {
                "type": "PIP",
                "package_uri": 'git+https://github.com/autonlab/fastlvm.git@{git_commit}#egg=fastlvm'.format(
                    git_commit=utils.current_git_commit(os.path.dirname(__file__)))
            }
        ]
    })

    def __init__(self, *, hyperparams: HyperParams, random_seed: int = 0) -> None:
        # super(LDA, self).__init__()
        super().__init__(hyperparams=hyperparams, random_seed=random_seed)
        self._this = None
        self._k = hyperparams['k']
        self._iters = hyperparams['iters']
        self._num_top = hyperparams['num_top']
        self._frac = hyperparams['frac']  # the fraction of training data set aside as the validation

        self._training_inputs = None  # type: Inputs
        self._fitted = False
        self._ext = None
        self._vectorizer = None  # for tokenization
        self._analyze = None  # to tokenize raw documents

        self.hyperparams = hyperparams

    def __del__(self):
        if self._this is not None:
            ldac.delete(self._this, self._ext)

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            if k == "_this":
                new_v = ldac.deserialize(ldac.serialize(v))
                setattr(result, k, new_v)
            elif k == "_ext":
                setattr(result, k, v)
            else:
                setattr(result, k, copy.deepcopy(v, memo))
        return result

    def set_training_data(self, *, inputs: Inputs) -> None:
        """
        Sets training data for LDA.

        Parameters
        ----------
        inputs : Inputs
            A list of 1d numpy array of dtype uint32. Each numpy array contains a document with each token mapped to its word id.
        """

        self._training_inputs = inputs

        self._fitted = False

    def fit(self, *, timeout: float = None, iterations: int = None) -> base.CallResult[None]:
        """
        Inference on the latent Dirichley allocation model
        """
        if self._fitted:
            return

        if self._training_inputs is None:
            raise ValueError("Missing training data.")

        # Create documents from the data-frame
        raw_documents = get_documents(self._training_inputs)

        if raw_documents is None:  # training data contains no text fields
            self._fitted = True
            if self._this is not None:
                ldac.delete(self._this, self._ext)
            self._this = None
            return base.CallResult(None)

        # Extract the vocabulary from the inputs data-frame
        self._vectorizer = CountVectorizer()
        self._vectorizer.fit(raw_documents)
        vocab_size = len(self._vectorizer.vocabulary_)

        # Build analyzer that handles tokenization
        self._analyze = self._vectorizer.build_analyzer()

        vocab = ['w' + str(i) for i in range(vocab_size)]
        self._this = ldac.new(self._k, self._iters, vocab)

        # Tokenize documents
        tokenized = tokenize(raw_documents, self._vectorizer.vocabulary_, self._analyze)

        # Uniformly split the data to training and validation
        training, validation = split_inputs(tokenized, self._frac, self.random_seed)

        ldac.fit(self._this, training.tolist(), validation.tolist())

        self._fitted = True

        return base.CallResult(None)

    def get_call_metadata(self) -> bool:
        """
        Returns metadata about the last ``fit`` call if it succeeded

        Returns
        -------
        Status : bool
            True/false status of fitting.

        """
        return self._fitted

    def produce(self, *, inputs: Inputs, timeout: float = None, iterations: int = None) -> base.CallResult[Outputs]:
        """
        Finds the token topic assignment (and consequently topic-per-document distribution) for the given set of docs using the learned model.

        Parameters
        ----------
        inputs : Inputs
            A list of 1d numpy array of dtype uint32. Each numpy array contains a document with each token mapped to its word id.

        Returns
        -------
        Outputs
            A list of 1d numpy array which represents probability of the topic each document belongs to.

        """
        if self._this is None:
            return base.CallResult(inputs)

        raw_documents, non_text_features = get_documents(inputs, non_text=True)
        tokenized = tokenize(raw_documents, self._vectorizer.vocabulary_, self._analyze)
        predicted = ldac.predict(self._this, tokenized.tolist())  # per word topic assignment
        text_features = mk_text_features(predicted, self._k)

        # concatenate the features row-wise
        features = pd.concat([non_text_features, text_features], axis=1)

        # append columns in the metadata
        features.metadata = features.metadata.append_columns(text_features.metadata)

        return base.CallResult(features)

    def evaluate(self, *, inputs: Inputs) -> float:
        """
        Finds the per-token log likelihood (-ve log perplexity) of learned model on a set of test docs.

        Parameters
        ----------
        inputs : Inputs
            A list of 1d numpy array of dtype uint32. Each numpy array contains a document with each token mapped to
            its word id. This represents test docs to test the learned model.

        Returns
        -------
        score : float
            Final per-token log likelihood (-ve log perplexity).
        """
        return ldac.evaluate(self._this, inputs)

    def produce_top_words(self) -> Outputs:
        """
        Get the top words of each topic for this model.

        Returns
        ----------
        topic_matrix : list
            A list of size k containing list of size num_top words.
        """

        return ldac.top_words(self._this, self._num_top)

    def produce_topic_matrix(self) -> Predicts:
        """
        Get current word|topic distribution matrix for this model.

        Returns
        ----------
        topic_matrix : numpy.ndarray
            A numpy array of shape (vocab_size,k) with each column containing the word|topic distribution.
        """

        if self._ext is None:
            self._ext = ldac.topic_matrix(self._this)
        return self._ext

    def multi_produce(self, *, produce_methods: typing.Sequence[str], inputs: Inputs, timeout: float = None,
                      iterations: int = None) -> base.MultiCallResult:
        return self._multi_produce(produce_methods=produce_methods, timeout=timeout, iterations=iterations,
                                   inputs=inputs)

    def get_params(self) -> Params:
        """
        Get parameters of LDA.

        Parameters are basically the topic matrix in byte stream.

        Returns
        ----------
        params : Params
            A named tuple of parameters.
        """

        return Params(topic_matrix=ldac.serialize(self._this),
                      vectorizer=self._vectorizer,
                      analyze=self._analyze)

    def set_params(self, *, params: Params) -> None:
        """
        Set parameters of LDA.

        Parameters are basically the topic matrix in byte stream.

        Parameters
        ----------
        params : Params
            A named tuple of parameters.
        """
        self._this = ldac.deserialize(params['topic_matrix'])
        self._vectorizer = params['vectorizer']
        self._analyze = params['analyze']

    def set_random_seed(self) -> None:
        """
        NOT SUPPORTED YET
        Sets a random seed for all operations from now on inside the primitive.

        By default it sets numpy's and Python's random seed.

        Parameters
        ----------
        seed : int
            A random seed to use.
        """

        raise NotImplementedError("Not supported yet")
