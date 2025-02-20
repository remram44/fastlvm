import os

import kmeansc
import numpy as np
from d3m import container, utils
from d3m.metadata import hyperparams, base as metadata_base
from d3m.metadata import params
from d3m.primitive_interfaces import base
from d3m.primitive_interfaces.unsupervised_learning import UnsupervisedLearnerPrimitiveBase

Inputs = container.DataFrame  # type: DataFrame
Outputs = container.DataFrame  # type: DataFrame
OutputCenters = container.ndarray  # type: np.ndarray


class Params(params.Params):
    cluster_centers: bytes  # Byte stream represening coordinates of cluster centers.


class HyperParams(hyperparams.Hyperparams):
    k = hyperparams.UniformInt(lower=1, upper=10000, default=10,
                               semantic_types=['https://metadata.datadrivendiscovery.org/types/TuningParameter'],
                               description='The number of clusters to form as well as the number of centroids to '
                                           'generate.')
    iters = hyperparams.UniformInt(lower=1, upper=10000, default=100,
                                   semantic_types=['https://metadata.datadrivendiscovery.org/types/TuningParameter'],
                                   description='The number of iterations of the Lloyd’s algorithm for K-Means '
                                               'clustering.')
    initialization = hyperparams.Enumeration[str](values=['random', 'firstk', 'kmeanspp', 'covertree'],
                                                  default='kmeanspp', semantic_types=[
            'https://metadata.datadrivendiscovery.org/types/TuningParameter'],
                                                  description="'random': choose k observations (rows) at random from "
                                                              "data for the initial centroids. 'kmeanspp' : selects "
                                                              "initial cluster centers by finding well spread out "
                                                              "points using cover trees to speed up convergence. "
                                                              "'covertree' : selects initial cluster centers by "
                                                              "sampling to speed up convergence.")


def init_covertree(k: int, points):
    import covertreec
    trunc = 3
    ptr = covertreec.new(points, trunc)
    # covertreec.display(ptr)
    seeds = covertreec.spreadout(ptr, k)
    covertreec.delete(ptr)
    return seeds


def init_kmeanspp(k: int, points):
    import utilsc
    seed_idx = utilsc.kmeanspp(k, points)
    seeds = points[seed_idx]
    return seeds


class KMeans(UnsupervisedLearnerPrimitiveBase[Inputs, Outputs, Params, HyperParams]):
    """
    This class provides functionality for unsupervised clustering, which according to Wikipedia is 'the task of
    grouping a set of objects in such a way that objects in the same group (called a cluster) are more similar to
    each other than to those in other groups'. It is a main task of exploratory data mining, and a common technique
    for statistical data analysis. The similarity measure can be, in general, any metric measure: standard Euclidean
    distance is the most common choice and the one currently implemented. In future, adding other metrics should not
    be too difficult. Standard packages, like those in scikit learn run on a single machine and often only on one
    thread. Whereas our underlying C++ implementation can be distributed to run on multiple machines. To enable the
    distribution through python interface is work in progress. In this class, we implement a K-Means clustering using
    Llyod's algorithm and speed-up using Cover Trees. The API is similar to sklearn.cluster.KMeans. The class is
    pickle-able.
    """

    metadata = metadata_base.PrimitiveMetadata({
        "id": "66c3bb07-63f7-409e-9f0f-5b07fbf7cd8e",
        "version": "3.1.1",
        "name": "K-means Clustering",
        "description": "This class provides functionality for unsupervised clustering, which according to Wikipedia "
                       "is 'the task of grouping a set of objects in such a way that objects in the same group ("
                       "called a cluster) are more similar to each other than to those in other groups'. It is a main "
                       "task of exploratory data mining, and a common technique for statistical data analysis. The "
                       "similarity measure can be, in general, any metric measure: standard Euclidean distance is the "
                       "most common choice and the one currently implemented. In future, adding other metrics should "
                       "not be too difficult. Standard packages, like those in scikit learn run on a single machine "
                       "and often only on one thread. Whereas our underlying C++ implementation can be distributed to "
                       "run on multiple machines. To enable the distribution through python interface is work in "
                       "progress. In this class, we implement a K-Means clustering using Llyod's algorithm and "
                       "speed-up using Cover Trees. The API is similar to sklearn.cluster.KMeans. The class is "
                       "pickle-able.",
        "python_path": "d3m.primitives.clustering.k_means.Fastlvm",
        "primitive_family": metadata_base.PrimitiveFamily.CLUSTERING,
        "algorithm_types": ["K_MEANS_CLUSTERING"],
        "keywords": ["large scale K-Means", "clustering"],
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

    def __init__(self, *, hyperparams: HyperParams) -> None:
        # super(KMeans, self).__init__()
        super().__init__(hyperparams=hyperparams)
        self._this = None
        self._k = hyperparams['k']
        self._iters = hyperparams['iters']
        self._initialization = hyperparams['initialization']

        self._training_inputs = None  # type: Inputs
        self._validation_inputs = None  # type: Inputs
        self._fitted = False

        self.hyperparams = hyperparams

    def __del__(self) -> None:
        if self._this is not None:
            kmeansc.delete(self._this)

    def set_training_data(self, *, inputs: Inputs) -> None:
        """
        Sets training data for KMeans.

        Parameters
        ----------
        training_inputs : Inputs
            A NxD DataFrame of data points for training.
        """
        training_inputs = inputs.values
        self._training_inputs = training_inputs
        self._validation_inputs = training_inputs

        initial_centres = None
        if self._initialization == 'random':
            idx = np.random.choice(training_inputs.shape[0], self._k, replace=False)
            initial_centres = training_inputs[idx]
        elif self._initialization == 'firstk':
            initial_centres = training_inputs[:self._k]
        elif self._initialization == 'kmeanspp':
            initial_centres = init_kmeanspp(self._k, training_inputs)
        elif self._initialization == 'covertree':
            initial_centres = init_covertree(self._k, training_inputs)
        else:
            raise NotImplementedError('This type of initial centres is not implemented')
        self._this = kmeansc.new(self._k, self._iters, initial_centres)

        self._fitted = False

    def fit(self, *, timeout: float = None, iterations: int = None) -> base.CallResult[None]:
        """
        Compute k-means clustering
        """
        if self._fitted:
            return base.CallResult(None)

        if self._training_inputs is None:
            raise ValueError("Missing training data.")

        kmeansc.fit(self._this, self._training_inputs, self._validation_inputs)
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
        return self.fitted

    def produce(self, *, inputs: Inputs, timeout: float = None, iterations: int = None) -> base.CallResult[Outputs]:
        """
        Finds the closest cluster for the given set of test points using the learned model.

        Parameters
        ----------
        inputs : Inputs
            A NxD matrix of data points.

        Returns
        -------
        Outputs
            The index of the cluster each sample belongs to.

        """
        results = kmeansc.predict(self._this, inputs.values)
        output = container.DataFrame(results, generate_metadata=True)
        # output.metadata = inputs.metadata.clear(source=self, for_value=output, generate_metadata=True)

        return base.CallResult(output)

    def evaluate(self, *, inputs: Inputs) -> float:
        """
        Finds the score of learned model on a set of test points
        
        Parameters
        ----------
        inputs : Inputs
            A NxD matrix of data points.

        Returns
        -------
        score : float
            The score (-ve of K-Means objective value) on the supplied points.
        """
        return kmeansc.evaluate(self._this, inputs.values)

    def produce_centers(self) -> OutputCenters:
        """
        Get current cluster centers for this model.

        Returns
        ----------
        centers : numpy.ndarray
            A KxD matrix of cluster centres.
        """

        return kmeansc.centers(self._this)

    def get_params(self) -> Params:
        """
        Get parameters of KMeans.

        Parameters are basically the cluster centres in byte stream.

        Returns
        ----------
        params : Params
            A named tuple of parameters.
        """

        return Params(cluster_centers=kmeansc.serialize(self._this))

    def set_params(self, *, params: Params) -> None:
        """
        Set parameters of KMeans.

        Parameters are basically the cluster centres in byte stream.

        Parameters
        ----------
        params : Params
            A named tuple of parameters.
        """
        self._this = kmeansc.deserialize(params['cluster_centers'])
