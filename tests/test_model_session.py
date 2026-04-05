import unittest
from unittest.mock import patch

from generator.core.lm_runtime import LmRuntime
from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL


class LmRuntimeTests(unittest.TestCase):
    @patch("generator.core.lm_runtime.get_loaded_model_instances")
    @patch("generator.core.lm_runtime.load_model")
    def test_activate_primary_when_already_active_skips_reload(self, mock_load, mock_instances):
        mock_instances.return_value = {PRIMARY_MODEL.model_id: "inst-primary"}

        runtime = LmRuntime(multi_model=True)
        current = runtime.activate_primary()

        self.assertEqual(PRIMARY_MODEL, current)
        self.assertEqual(PRIMARY_MODEL, runtime.current_model)
        self.assertEqual(0, runtime.switch_count)
        self.assertEqual(0, runtime.activation_count)
        mock_load.assert_not_called()

    @patch("generator.core.lm_runtime.time.sleep")
    @patch("generator.core.lm_runtime.unload_instance")
    @patch("generator.core.lm_runtime.get_loaded_model_instances")
    @patch("generator.core.lm_runtime.load_model")
    def test_activate_secondary_reconciles_stale_primary_cache(
        self,
        mock_load,
        mock_instances,
        mock_unload,
        _mock_sleep,
    ):
        mock_instances.side_effect = [
            {SECONDARY_MODEL.model_id: "inst-secondary"},
            {SECONDARY_MODEL.model_id: "inst-secondary"},
        ]

        runtime = LmRuntime(multi_model=True)
        runtime.current_model = PRIMARY_MODEL
        current = runtime.activate_secondary()

        self.assertEqual(SECONDARY_MODEL, current)
        self.assertEqual(SECONDARY_MODEL, runtime.current_model)
        mock_unload.assert_not_called()
        mock_load.assert_not_called()

    @patch("generator.core.lm_runtime.time.sleep")
    @patch("generator.core.lm_runtime.unload_instance")
    @patch("generator.core.lm_runtime.get_loaded_model_instances")
    @patch("generator.core.lm_runtime.load_model")
    def test_activate_primary_unloads_other_live_model_then_loads(
        self,
        mock_load,
        mock_instances,
        mock_unload,
        _mock_sleep,
    ):
        mock_instances.side_effect = [
            {SECONDARY_MODEL.model_id: "inst-secondary"},
            {SECONDARY_MODEL.model_id: "inst-secondary"},
            {},
            {PRIMARY_MODEL.model_id: "inst-primary"},
        ]

        runtime = LmRuntime(multi_model=True)
        current = runtime.activate_primary()

        self.assertEqual(PRIMARY_MODEL, current)
        self.assertEqual(1, runtime.activation_count)
        mock_unload.assert_called_once_with("inst-secondary", model_id=SECONDARY_MODEL.model_id)
        mock_load.assert_called_once_with(PRIMARY_MODEL)

    @patch("generator.core.lm_runtime.time.sleep")
    @patch("generator.core.lm_runtime.unload_instance")
    @patch("generator.core.lm_runtime.get_loaded_model_instances")
    @patch("generator.core.lm_runtime.load_model")
    def test_activate_retries_after_failed_load_with_refresh(
        self,
        mock_load,
        mock_instances,
        mock_unload,
        _mock_sleep,
    ):
        mock_instances.side_effect = [
            {},
            {SECONDARY_MODEL.model_id: "inst-secondary"},
            {SECONDARY_MODEL.model_id: "inst-secondary"},
            {},
            {PRIMARY_MODEL.model_id: "inst-primary"},
        ]
        mock_load.side_effect = [RuntimeError("500"), None]

        runtime = LmRuntime(multi_model=True)
        current = runtime.activate_primary()

        self.assertEqual(PRIMARY_MODEL, current)
        self.assertEqual(2, mock_load.call_count)
        mock_unload.assert_called_once_with("inst-secondary", model_id=SECONDARY_MODEL.model_id)

    @patch("generator.core.lm_runtime.time.sleep")
    @patch("generator.core.lm_runtime.unload_instance")
    @patch("generator.core.lm_runtime.get_loaded_model_instances")
    @patch("generator.core.lm_runtime.load_model")
    def test_activate_raises_after_failed_retry(
        self,
        mock_load,
        mock_instances,
        mock_unload,
        _mock_sleep,
    ):
        mock_instances.side_effect = [{}, {}, {}]
        mock_load.side_effect = RuntimeError("500")

        runtime = LmRuntime(multi_model=True)

        with self.assertRaises(RuntimeError):
            runtime.activate_primary()

        self.assertEqual(2, mock_load.call_count)
        mock_unload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
