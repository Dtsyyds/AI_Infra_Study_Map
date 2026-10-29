import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(clamp(20, 0, 10), 10)
    def test_regression(self):
        self.assertEqual(clamp(-2, 0, 10), 0)
