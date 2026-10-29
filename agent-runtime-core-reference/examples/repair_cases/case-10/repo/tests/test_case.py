import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(offset(1, 20), 0)
    def test_regression(self):
        self.assertEqual(offset(2, 0), 0)
