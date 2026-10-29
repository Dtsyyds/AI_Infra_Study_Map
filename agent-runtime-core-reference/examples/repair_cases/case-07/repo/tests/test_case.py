import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(median([1,3]), 2)
    def test_regression(self):
        self.assertEqual(median([1,2,3]), 2)
