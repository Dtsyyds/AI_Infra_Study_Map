import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(chunks([1,2,3], 2), [[1,2],[3]])
    def test_regression(self):
        self.assertEqual(chunks([1,2], 2), [[1,2]])
