import math
import unittest
from recoll_eg2.encoder import checked_vector


class VectorValidationTests(unittest.TestCase):
    def test_valid_finite_vector(self):
        self.assertEqual(checked_vector([1, 0, -1], 3), [1.0, 0.0, -1.0])

    def test_reject_invalid_vectors(self):
        for values in ([], [0, 0], [math.nan], [math.inf], [-math.inf]):
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                checked_vector(values)
        with self.assertRaises(RuntimeError):
            checked_vector([1, 2], 3)
