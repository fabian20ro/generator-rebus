import unittest
from unittest.mock import patch

from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL


class LmRuntimeTests(unittest.TestCase):
    @patch("rebus_generator.platform.llm.lm_runtime.get_loaded_model_instances")
    @patch("rebus_generator.platform.llm.lm_runtime.load_model")
    def test_activate_primary_when_already_active_skips_reload(self, mock_load, mock_instances):
        mock_instances.return_value = {PRIMARY_MODEL.model_id: "inst-primary"}

        runtime = LmRuntime(multi_model=True)
        current = runtime.activate_primary()

        self.assertEqual(PRIMARY_MODEL, current)
        self.assertEqual(PRIMARY_MODEL, runtime.current_model)
        self.assertEqual(0, runtime.switch_count)
        self.assertEqual(0, runtime.activation_count)
        mock_load.assert_not_called()

    @patch("rebus_generator.platform.llm.lm_runtime.time.sleep")
    @patch("rebus_generator.platform.llm.lm_runtime.unload_instance")
    @patch("rebus_generator.platform.llm.lm_runtime.get_loaded_model_instances")
    @patch("rebus_generator.platform.llm.lm_runtime.load_model")
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

    @patch("rebus_generator.platform.llm.lm_runtime._wait_for_unload_model")
    @patch("rebus_generator.platform.llm.lm_runtime.time.sleep")
    @patch("rebus_generator.platform.llm.lm_runtime.unload_instance")
    @patch("rebus_generator.platform.llm.lm_runtime.get_available_model_ids")
    @patch("rebus_generator.platform.llm.lm_runtime.get_loaded_model_instances")
    @patch("rebus_generator.platform.llm.lm_runtime.load_model")
    def test_activate_primary_unloads_other_live_model_then_loads(
        self,
        mock_load,
        mock_instances,
        mock_available,
        mock_unload,
        mock_sleep,
        _mock_wait_unload,
    ):
        mock_available.return_value = {PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id}
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
        mock_sleep.assert_called_once_with(5.0)
        mock_load.assert_called_once_with(PRIMARY_MODEL)

    @patch("rebus_generator.platform.llm.lm_runtime._wait_for_unload_model")
    @patch("rebus_generator.platform.llm.lm_runtime.time.sleep")
    @patch("rebus_generator.platform.llm.lm_runtime.unload_instance")
    @patch("rebus_generator.platform.llm.lm_runtime.get_available_model_ids")
    @patch("rebus_generator.platform.llm.lm_runtime.get_loaded_model_instances")
    @patch("rebus_generator.platform.llm.lm_runtime.load_model")
    def test_activate_retries_after_failed_load_with_refresh(
        self,
        mock_load,
        mock_instances,
        mock_available,
        mock_unload,
        mock_sleep,
        _mock_wait_unload,
    ):
        mock_available.return_value = {PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id}
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
        mock_sleep.assert_called_once_with(5.0)

    @patch("rebus_generator.platform.llm.lm_runtime.time.sleep")
    @patch("rebus_generator.platform.llm.lm_runtime.unload_instance")
    @patch("rebus_generator.platform.llm.lm_runtime.get_available_model_ids")
    @patch("rebus_generator.platform.llm.lm_runtime.get_loaded_model_instances")
    @patch("rebus_generator.platform.llm.lm_runtime.load_model")
    def test_activate_raises_after_failed_retry(
        self,
        mock_load,
        mock_instances,
        mock_available,
        mock_unload,
        _mock_sleep,
    ):
        mock_available.return_value = {PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id}
        mock_instances.side_effect = [{}, {}, {}]
        mock_load.side_effect = RuntimeError("500")

        runtime = LmRuntime(multi_model=True)

        with self.assertRaisesRegex(RuntimeError, "500"):
            runtime.activate_primary()

        self.assertEqual(2, mock_load.call_count)
        mock_unload.assert_not_called()

    @patch("rebus_generator.platform.llm.lm_runtime.unload_instance")
    @patch("rebus_generator.platform.llm.lm_runtime.get_available_model_ids")
    @patch("rebus_generator.platform.llm.lm_runtime.get_loaded_model_instances")
    @patch("rebus_generator.platform.llm.lm_runtime.load_model")
    def test_activate_missing_target_fails_before_unloading_active_model(
        self,
        mock_load,
        mock_instances,
        mock_available,
        mock_unload,
    ):
        mock_instances.return_value = {"unrelated/model": "inst-unrelated"}
        mock_available.return_value = {"unrelated/model"}

        runtime = LmRuntime(multi_model=True)
        with self.assertRaisesRegex(RuntimeError, PRIMARY_MODEL.model_id):
            runtime.activate_primary()

        mock_unload.assert_not_called()
        mock_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
