import unittest
from solution import *
import asyncio
async def bad():
    raise ValueError("bad")
async def good():
    return 3

class Case(unittest.TestCase):
    def test_bug(self):
        self.assertIsNone(asyncio.run(read_or_default(bad)))
    def test_regression(self):
        self.assertEqual(asyncio.run(read_or_default(good)), 3)
