import unittest

from rebus_generator.domain.plateau import has_plateaued


class PlateauTests(unittest.TestCase):
    def test_empty_history_not_plateaued(self):
        self.assertFalse(has_plateaued([], 7))

    def test_short_history_not_plateaued(self):
        self.assertFalse(has_plateaued([3, 3, 3], 7))

    def test_exactly_lookback_length_flat_is_plateaued(self):
        self.assertTrue(has_plateaued([5, 5, 5, 5, 5, 5, 5], 7))

    def test_improving_not_plateaued(self):
        self.assertFalse(has_plateaued([1, 2, 3, 4, 5, 6, 7], 7))

    def test_declining_is_plateaued(self):
        self.assertTrue(has_plateaued([5, 4, 3, 3, 3, 3, 3], 7))

    def test_improved_then_flat_not_plateaued_if_above_lookback_ago(self):
        self.assertFalse(has_plateaued([1, 2, 3, 4, 5, 5, 5], 7))

    def test_single_element_lookback_one(self):
        self.assertTrue(has_plateaued([3], 1))

    def test_lookback_two_improving(self):
        self.assertFalse(has_plateaued([3, 5], 2))

    def test_lookback_two_flat(self):
        self.assertTrue(has_plateaued([5, 5], 2))


if __name__ == "__main__":
    unittest.main()
