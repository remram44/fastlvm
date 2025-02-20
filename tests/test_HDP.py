from d3m import container
from d3m.metadata import base as metadata_base
from unittest import TestCase
from fastlvm import read_corpus, LDA, HDP
from fastlvm.hdp import HyperParams
from fastlvm.lda import HyperParams as LDAHyperParams


class TestHDP(TestCase):
    def setUp(self) -> None:
        self.num_topics = 10
        # Load NIPS data
        self.trngdata, self.vocab = read_corpus('../data/nips/corpus.train')
        self.testdata, self.vocab = read_corpus('../data/nips/corpus.test', self.vocab)

        self.canlda = None  # LDA model

    def hdp(self, trngdata, testdata):
        # Init HDP model
        hp = HyperParams(k=self.num_topics, iters=100, num_top=15, frac=0.01)
        hdp = HDP(hyperparams=hp, random_seed=7654321)
        hdp.set_training_data(inputs=self.transform(trngdata))

        hdp.fit()
        # Test on held out data using learned model
        a = hdp.evaluate(inputs=testdata)

        self.canlda = hdp  # HDP model
        return a

    def test_produce(self):
        a = self.hdp(trngdata=self.trngdata, testdata=self.testdata)
        self.assertTrue(a is not None)

    def test_compare_to_baseline(self):
        a = self.hdp(trngdata=self.trngdata, testdata=self.testdata)

        # TODO is it a good idea to use LDA as the baseline?
        # Use LDA model as baseline
        hp = LDAHyperParams(k=self.num_topics, iters=100, num_top=1, seed=1, frac=0.01)
        canlda = LDA(hyperparams=hp)
        canlda.set_training_data(inputs=self.transform(self.trngdata))
        canlda.fit()
        # Test on held out data using learned model
        b = canlda.evaluate(inputs=self.testdata)

        self.assertAlmostEqual(a, b, places=1)

    @staticmethod
    def transform(corpus):
        """
        Convert corpus to D3M dataframe of shape Nx1
        N is the number of sentences.
        The column is of vary length with metadata text.

        :param corpus: list of ndarray, each element of the ndarray is a number representing a word
        :return:
        """
        text = []
        for sentence in corpus:
            text.append(" ".join(sentence.astype(str)))
        df = container.DataFrame(text, generate_metadata=True)

        # create metadata for the text feature columns
        for column_index in range(df.shape[1]):
            col_dict = dict(df.metadata.query((metadata_base.ALL_ELEMENTS, column_index)))
            col_dict['structural_type'] = type(1.0)
            col_dict['name'] = 'fastlvm_' + str(column_index)
            col_dict['semantic_types'] = ('http://schema.org/Text',
                                          'https://metadata.datadrivendiscovery.org/types/Attribute')
            df.metadata = df.metadata.update((metadata_base.ALL_ELEMENTS, column_index), col_dict)
        return df
