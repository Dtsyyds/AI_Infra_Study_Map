import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(total([{}]), 0)
    def test_regression(self):
        self.assertEqual(total([{"amount": 2}]), 2)
