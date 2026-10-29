import unittest
from solution import *

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(get_header({"Content-Type":"json"}, "content-type"), "json")
    def test_regression(self):
        self.assertIsNone(get_header({}, "x"))
