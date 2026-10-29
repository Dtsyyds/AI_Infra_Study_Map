import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(merge_defaults({"x":1},{"x":2})["x"], 2)
    def test_regression(self):
        self.assertEqual(merge_defaults({"x":1},{}), {"x":1})
