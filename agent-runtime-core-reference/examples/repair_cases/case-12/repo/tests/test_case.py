import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(seconds(1500), 1.5)
    def test_regression(self):
        self.assertEqual(seconds(2000), 2)
