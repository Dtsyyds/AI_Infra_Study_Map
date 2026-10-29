import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(encode({"x": True}), '{"x": true}')
    def test_regression(self):
        self.assertEqual(encode(1), "1")
