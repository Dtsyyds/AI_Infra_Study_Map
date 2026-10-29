import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertFalse(parse_flag("false"))
    def test_regression(self):
        self.assertTrue(parse_flag("true"))
