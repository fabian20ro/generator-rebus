import json
import unittest
from unittest.mock import patch, MagicMock

from generator.core.model_manager import (
    LoadedModelInstance,
    ModelConfig,
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    ensure_model_loaded,
    get_loaded_models,
    get_loaded_model_instances,
    list_loaded_model_instances,
    _post_json,
    switch_model,
    unload_model,
)


class ModelManagerTests(unittest.TestCase):
    def test_model_config_creation(self):
        config = ModelConfig(
            model_id="test/model",
            display_name="test-model",
            context_length=4096,
        )
        self.assertEqual(config.model_id, "test/model")
        self.assertEqual(config.display_name, "test-model")
        self.assertEqual(config.context_length, 4096)

    def test_primary_model_config(self):
        self.assertIn("gpt-oss", PRIMARY_MODEL.model_id)
        self.assertEqual(PRIMARY_MODEL.context_length, 8192)

    def test_secondary_model_config(self):
        self.assertIn("eurollm", SECONDARY_MODEL.model_id)

    def test_get_loaded_models_returns_empty_on_failure(self):
        with patch("generator.core.model_manager._get_json", side_effect=Exception("offline")):
            result = get_loaded_models()
            self.assertEqual(result, [])

    @patch("generator.core.model_manager.load_model")
    @patch("generator.core.model_manager.get_loaded_model_instances")
    def test_ensure_model_loaded_skips_when_already_loaded(self, mock_inst, mock_load):
        mock_inst.return_value = {PRIMARY_MODEL.model_id: "inst-abc"}

        ensure_model_loaded(PRIMARY_MODEL)

        mock_load.assert_not_called()

    @patch("generator.core.model_manager.load_model")
    @patch("generator.core.model_manager.get_loaded_model_instances")
    def test_ensure_model_loaded_loads_when_missing(self, mock_inst, mock_load):
        mock_inst.return_value = {}

        ensure_model_loaded(PRIMARY_MODEL)

        mock_load.assert_called_once_with(PRIMARY_MODEL)

    @patch("generator.core.model_manager.time.sleep")
    @patch("generator.core.model_manager.load_model")
    @patch("generator.core.model_manager._post_json")
    @patch("generator.core.model_manager.get_loaded_model_instances")
    def test_ensure_model_loaded_unloads_with_instance_id(
        self, mock_inst, mock_post, mock_load, mock_sleep,
    ):
        mock_inst.return_value = {"some-other/model": "inst-xyz"}

        ensure_model_loaded(PRIMARY_MODEL)

        mock_post.assert_called_once_with(
            "/api/v1/models/unload", {"instance_id": "inst-xyz"},
        )
        mock_load.assert_called_once_with(PRIMARY_MODEL)

    @patch("generator.core.model_manager._post_json")
    @patch("generator.core.model_manager.get_loaded_model_instances")
    def test_unload_model_uses_loaded_instance_id(self, mock_inst, mock_post):
        mock_inst.return_value = {PRIMARY_MODEL.model_id: "inst-primary"}

        unload_model(PRIMARY_MODEL)

        mock_post.assert_called_once_with(
            "/api/v1/models/unload", {"instance_id": "inst-primary"},
        )

    @patch("generator.core.model_manager._post_json")
    @patch("generator.core.model_manager.get_loaded_model_instances")
    def test_unload_model_skips_when_instance_missing(self, mock_inst, mock_post):
        mock_inst.return_value = {}

        unload_model(PRIMARY_MODEL)

        mock_post.assert_not_called()

    @patch("generator.core.model_manager.time.sleep")
    @patch("generator.core.model_manager.load_model")
    @patch("generator.core.model_manager._post_json")
    @patch("generator.core.model_manager.get_loaded_model_instances")
    def test_switch_model_unloads_with_instance_id_then_loads_target(
        self, mock_inst, mock_post, mock_load, mock_sleep,
    ):
        mock_inst.return_value = {PRIMARY_MODEL.model_id: "inst-primary"}

        switch_model(PRIMARY_MODEL, SECONDARY_MODEL)

        mock_post.assert_called_once_with(
            "/api/v1/models/unload", {"instance_id": "inst-primary"},
        )
        mock_load.assert_called_once_with(SECONDARY_MODEL)


class GetLoadedModelInstancesTests(unittest.TestCase):
    @patch("generator.core.model_manager._get_json")
    def test_returns_empty_on_failure(self, mock_get):
        mock_get.side_effect = Exception("offline")
        self.assertEqual(get_loaded_model_instances(), {})

    @patch("generator.core.model_manager._get_json")
    def test_dict_style_instances(self, mock_get):
        mock_get.return_value = {
            "models": [
                {
                    "key": "openai/gpt-oss-20b",
                    "loaded_instances": [{"identifier": "inst-001"}],
                }
            ]
        }
        result = get_loaded_model_instances()
        self.assertEqual(result, {"openai/gpt-oss-20b": "inst-001"})

    @patch("generator.core.model_manager._get_json")
    def test_dict_style_falls_back_to_id(self, mock_get):
        mock_get.return_value = {
            "models": [
                {
                    "key": "test/model",
                    "loaded_instances": [{"id": "fallback-id"}],
                }
            ]
        }
        result = get_loaded_model_instances()
        self.assertEqual(result, {"test/model": "fallback-id"})

    @patch("generator.core.model_manager._get_json")
    def test_dict_style_skips_missing_instance_id(self, mock_get):
        mock_get.return_value = {
            "models": [
                {
                    "key": "test/model",
                    "loaded_instances": [{"other_field": "whatever"}],
                }
            ]
        }
        result = get_loaded_model_instances()
        self.assertEqual(result, {})

    @patch("generator.core.model_manager._get_json")
    def test_string_style_instances(self, mock_get):
        mock_get.return_value = {
            "models": [
                {
                    "key": "eurollm-22b",
                    "loaded_instances": ["string-inst-id"],
                }
            ]
        }
        result = get_loaded_model_instances()
        self.assertEqual(result, {"eurollm-22b": "string-inst-id"})

    @patch("generator.core.model_manager._get_json")
    def test_skips_models_without_instances(self, mock_get):
        mock_get.return_value = {
            "models": [
                {"key": "loaded/model", "loaded_instances": [{"identifier": "inst-1"}]},
                {"key": "unloaded/model", "loaded_instances": []},
            ]
        }
        result = get_loaded_model_instances()
        self.assertEqual(result, {"loaded/model": "inst-1"})

    @patch("generator.core.model_manager._get_json")
    def test_list_loaded_model_instances_returns_dataclasses(self, mock_get):
        mock_get.return_value = {
            "models": [
                {
                    "key": "loaded/model",
                    "loaded_instances": [{"identifier": "inst-1"}],
                }
            ]
        }
        result = list_loaded_model_instances()
        self.assertEqual([LoadedModelInstance(model_id="loaded/model", instance_id="inst-1")], result)


if __name__ == "__main__":
    unittest.main()
