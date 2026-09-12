import unittest
from app.core.scheduling import is_overlapping

class TestScheduling(unittest.TestCase):
    def test_is_overlapping(self):
        # True intersections
        self.assertTrue(is_overlapping("10:00", "11:00", "10:30", "11:30"))
        self.assertTrue(is_overlapping("10:00", "11:00", "09:30", "10:30"))
        self.assertTrue(is_overlapping("10:00", "11:00", "10:15", "10:45"))
        self.assertTrue(is_overlapping("10:00", "11:00", "09:00", "12:00"))

        # Adjacent (exclusive)
        self.assertFalse(is_overlapping("10:00", "11:00", "11:00", "12:00"))
        self.assertFalse(is_overlapping("11:00", "12:00", "10:00", "11:00"))

        # Disjoint
        self.assertFalse(is_overlapping("10:00", "11:00", "12:00", "13:00"))
        self.assertFalse(is_overlapping("14:00", "15:00", "12:00", "13:00"))

if __name__ == '__main__':
    unittest.main()
