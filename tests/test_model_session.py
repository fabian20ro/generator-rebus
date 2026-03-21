import unittest
from unittest.mock import patch

from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from generator.core.model_session import ModelSession


class ModelSessionTests(unittest.TestCase):
    @patch("generator.core.model_session.ensure_model_loaded")
    def test_start_primary_sets_current_without_switch(self, mock_ensure):
        session = ModelSession(multi_model=True)

        current = session.start_primary()

        self.assertEqual(PRIMARY_MODEL, current)
        self.assertEqual(PRIMARY_MODEL, session.current_model)
        self.assertEqual(0, session.switch_count)
        mock_ensure.assert_called_once_with(PRIMARY_MODEL)

    @patch("generator.core.model_session.switch_model")
    @patch("generator.core.model_session.ensure_model_loaded")
    def test_switch_counts_only_on_real_switch(self, mock_ensure, mock_switch):
        session = ModelSession(multi_model=True)
        session.start_primary()

        current = session.start_secondary()

        self.assertEqual(SECONDARY_MODEL, current)
        self.assertEqual(1, session.switch_count)
        mock_switch.assert_called_once_with(PRIMARY_MODEL, SECONDARY_MODEL)

    @patch("generator.core.model_session.ensure_model_loaded")
    def test_alternate_in_single_model_mode_keeps_primary(self, mock_ensure):
        session = ModelSession(multi_model=False)

        session.activate_initial_evaluator()
        current = session.alternate()

        self.assertEqual(PRIMARY_MODEL, current)
        self.assertEqual(0, session.switch_count)


if __name__ == "__main__":
    unittest.main()
