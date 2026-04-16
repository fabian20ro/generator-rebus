import unittest
from rebus_generator.platform.llm.ai_clues import compute_rebus_score

class TestClueRatingLengthAware(unittest.TestCase):
    def test_compute_rebus_score_weights(self):
        # Length 2-3: 50/50 balance
        # guess=10, creative=2 -> (5+1) = 6
        self.assertEqual(compute_rebus_score(10, 2, answer_length=2), 6)
        # guess=6, creative=10 -> (3+5) = 8
        self.assertEqual(compute_rebus_score(6, 10, answer_length=3), 8)

        # Length 4-6: 75/25 balance (standard)
        # guess=10, creative=2 -> (7.5+0.5) = 8
        self.assertEqual(compute_rebus_score(10, 2, answer_length=5), 8)
        # guess=8, creative=4 -> (6+1) = 7
        self.assertEqual(compute_rebus_score(8, 4, answer_length=4), 7)

        # Length 7+: 90/10 balance (precision priority)
        # guess=10, creative=2 -> (9+0.2) = 9.2 -> 9
        self.assertEqual(compute_rebus_score(10, 2, answer_length=7), 9)
        # guess=2, creative=10 -> (1.8+1) = 2.8 -> 3
        self.assertEqual(compute_rebus_score(2, 10, answer_length=10), 3)

if __name__ == "__main__":
    unittest.main()
