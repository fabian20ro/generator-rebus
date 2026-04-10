import json
import unittest
from unittest.mock import patch, MagicMock

from rebus_generator.platform.llm.models import (
    ModelConfig,
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    chat_max_tokens,
    chat_reasoning_options,
    get_active_model_labels,
    get_active_primary_model,
    get_active_secondary_model,
    get_model_config,
    get_model_by_key,
    resolve_reasoning_effort,
)
from rebus_generator.platform.llm.lm_studio_api import (
    LoadedModelInstance,
    ensure_model_loaded,
    get_loaded_models,
    get_loaded_model_instances,
    list_loaded_model_instances,
    switch_model,
    unload_model,
)


class ModelManagerTests(unittest.TestCase):
    def test_model_config_creation(self):
        config = ModelConfig(
            registry_key="test_model",
            model_id="test/model",
            display_name="test-model",
            max_completion_tokens=1234,
            context_length=4096,
        )
        self.assertEqual(config.registry_key, "test_model")
        self.assertEqual(config.model_id, "test/model")
        self.assertEqual(config.display_name, "test-model")
        self.assertEqual(config.max_completion_tokens, 1234)
        self.assertEqual(config.context_length, 4096)
        self.assertEqual({}, dict(config.reasoning_by_purpose))

    def test_primary_model_config(self):
        self.assertEqual("gemma4_26b_a4b", PRIMARY_MODEL.registry_key)
        self.assertIn("gemma", PRIMARY_MODEL.model_id)
        self.assertEqual(4000, PRIMARY_MODEL.max_completion_tokens)
        self.assertEqual(PRIMARY_MODEL.context_length, 8192)
        self.assertEqual("low", PRIMARY_MODEL.reasoning_by_purpose["default"])

    def test_secondary_model_config(self):
        self.assertEqual("eurollm_22b", SECONDARY_MODEL.registry_key)
        self.assertIn("eurollm", SECONDARY_MODEL.model_id)
        self.assertEqual(200, SECONDARY_MODEL.max_completion_tokens)
        self.assertIsNone(SECONDARY_MODEL.reasoning_by_purpose["default"])

    def test_get_model_config_returns_known_model(self):
        self.assertEqual(PRIMARY_MODEL, get_model_config(PRIMARY_MODEL.model_id))

    def test_get_model_by_key_returns_known_model(self):
        self.assertEqual(PRIMARY_MODEL, get_model_by_key("gemma4_26b_a4b"))

    def test_active_model_accessors_follow_central_pair(self):
        self.assertEqual(PRIMARY_MODEL, get_active_primary_model())
        self.assertEqual(SECONDARY_MODEL, get_active_secondary_model())
        self.assertEqual(
            [PRIMARY_MODEL.display_name, SECONDARY_MODEL.display_name],
            get_active_model_labels(multi_model=True),
        )

    def test_active_model_accessors_change_when_pair_constant_changes(self):
        with patch("rebus_generator.platform.llm.models.ACTIVE_MODEL_KEYS", ("gpt_oss_20b", "gemma4_26b_a4b")):
            self.assertEqual("gpt_oss_20b", get_active_primary_model().registry_key)
            self.assertEqual("gemma4_26b_a4b", get_active_secondary_model().registry_key)
            self.assertEqual(
                ["gpt-oss-20b", "gemma-4"],
                get_active_model_labels(multi_model=True),
            )

    def test_chat_reasoning_options_return_low_for_primary_default(self):
        self.assertEqual({"reasoning_effort": "low"}, chat_reasoning_options(PRIMARY_MODEL.model_id))

    def test_chat_reasoning_options_return_low_for_primary_generate(self):
        self.assertEqual(
            {"reasoning_effort": "low"},
            chat_reasoning_options(PRIMARY_MODEL.model_id, purpose="definition_generate"),
        )

    def test_chat_reasoning_options_return_low_for_primary_compare(self):
        self.assertEqual(
            {"reasoning_effort": "low"},
            chat_reasoning_options(PRIMARY_MODEL.model_id, purpose="clue_compare"),
        )

    def test_chat_reasoning_options_empty_for_primary_verify(self):
        self.assertEqual(
            {},
            chat_reasoning_options(PRIMARY_MODEL.model_id, purpose="definition_verify"),
        )

    def test_chat_reasoning_options_empty_for_secondary(self):
        self.assertEqual({}, chat_reasoning_options(SECONDARY_MODEL.model_id))

    def test_chat_max_tokens_return_model_budget(self):
        self.assertEqual(4000, chat_max_tokens(PRIMARY_MODEL))
        self.assertEqual(200, chat_max_tokens(SECONDARY_MODEL.model_id))

    def test_chat_reasoning_options_support_gpt_oss_profiles(self):
        gpt_oss = get_model_by_key("gpt_oss_20b")
        self.assertEqual(2000, chat_max_tokens(gpt_oss))
        self.assertEqual(
            {"reasoning_effort": "medium"},
            chat_reasoning_options(gpt_oss, purpose="definition_generate"),
        )
        self.assertEqual(
            {"reasoning_effort": "low"},
            chat_reasoning_options(gpt_oss, purpose="title_generate"),
        )

    def test_chat_reasoning_options_normalize_legacy_aliases(self):
        config = ModelConfig(
            registry_key="legacy",
            model_id="legacy/model",
            display_name="legacy",
            max_completion_tokens=777,
            reasoning_by_purpose={"default": "off", "definition_generate": "on"},
        )
        self.assertEqual(
            {"reasoning_effort": "none"},
            chat_reasoning_options(config),
        )
        self.assertEqual(
            {"reasoning_effort": "medium"},
            chat_reasoning_options(config, purpose="definition_generate"),
        )

    def test_resolve_reasoning_effort_supports_explicit_none_override(self):
        self.assertIsNone(
            resolve_reasoning_effort(
                PRIMARY_MODEL,
                purpose="definition_generate",
                reasoning_effort_override=None,
            )
        )

    def test_chat_reasoning_options_support_explicit_none_override(self):
        self.assertEqual(
            {},
            chat_reasoning_options(
                PRIMARY_MODEL,
                purpose="definition_generate",
                reasoning_effort_override=None,
            ),
        )

    def test_chat_reasoning_options_raise_for_unsupported_value(self):
        config = ModelConfig(
            registry_key="bad",
            model_id="bad/model",
            display_name="bad",
            max_completion_tokens=777,
            reasoning_by_purpose={"default": "banana"},
        )
        with self.assertRaises(ValueError):
            chat_reasoning_options(config)

    def test_get_loaded_models_returns_empty_on_failure(self):
        with patch("rebus_generator.platform.llm.lm_studio_api._get_json", side_effect=Exception("offline")):
            result = get_loaded_models()
            self.assertEqual(result, [])

    @patch("rebus_generator.platform.llm.lm_studio_api.load_model")
    @patch("rebus_generator.platform.llm.lm_studio_api.get_loaded_model_instances")
    def test_ensure_model_loaded_skips_when_already_loaded(self, mock_inst, mock_load):
        mock_inst.return_value = {PRIMARY_MODEL.model_id: "inst-abc"}

        ensure_model_loaded(PRIMARY_MODEL)

        mock_load.assert_not_called()

    @patch("rebus_generator.platform.llm.lm_studio_api.load_model")
    @patch("rebus_generator.platform.llm.lm_studio_api.get_loaded_model_instances")
    def test_ensure_model_loaded_loads_when_missing(self, mock_inst, mock_load):
        mock_inst.return_value = {}

        ensure_model_loaded(PRIMARY_MODEL)

        mock_load.assert_called_once_with(PRIMARY_MODEL)

    @patch("rebus_generator.platform.llm.lm_studio_api.time.sleep")
    @patch("rebus_generator.platform.llm.lm_studio_api.load_model")
    @patch("rebus_generator.platform.llm.lm_studio_api._post_json")
    @patch("rebus_generator.platform.llm.lm_studio_api.get_loaded_model_instances")
    def test_ensure_model_loaded_unloads_with_instance_id(
        self, mock_inst, mock_post, mock_load, mock_sleep,
    ):
        mock_inst.return_value = {"some-other/model": "inst-xyz"}

        ensure_model_loaded(PRIMARY_MODEL)

        mock_post.assert_called_once_with(
            "/api/v1/models/unload", {"instance_id": "inst-xyz"},
        )
        mock_load.assert_called_once_with(PRIMARY_MODEL)

    @patch("rebus_generator.platform.llm.lm_studio_api._post_json")
    @patch("rebus_generator.platform.llm.lm_studio_api.get_loaded_model_instances")
    def test_unload_model_uses_loaded_instance_id(self, mock_inst, mock_post):
        mock_inst.return_value = {PRIMARY_MODEL.model_id: "inst-primary"}

        unload_model(PRIMARY_MODEL)

        mock_post.assert_called_once_with(
            "/api/v1/models/unload", {"instance_id": "inst-primary"},
        )

    @patch("rebus_generator.platform.llm.lm_studio_api._post_json")
    @patch("rebus_generator.platform.llm.lm_studio_api.get_loaded_model_instances")
    def test_unload_model_skips_when_instance_missing(self, mock_inst, mock_post):
        mock_inst.return_value = {}

        unload_model(PRIMARY_MODEL)

        mock_post.assert_not_called()

    @patch("rebus_generator.platform.llm.lm_studio_api.time.sleep")
    @patch("rebus_generator.platform.llm.lm_studio_api.load_model")
    @patch("rebus_generator.platform.llm.lm_studio_api._post_json")
    @patch("rebus_generator.platform.llm.lm_studio_api.get_loaded_model_instances")
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
    @patch("rebus_generator.platform.llm.lm_studio_api._get_json")
    def test_returns_empty_on_failure(self, mock_get):
        mock_get.side_effect = Exception("offline")
        self.assertEqual(get_loaded_model_instances(), {})

    @patch("rebus_generator.platform.llm.lm_studio_api._get_json")
    def test_dict_style_instances(self, mock_get):
        mock_get.return_value = {
            "models": [
                {
                    "key": PRIMARY_MODEL.model_id,
                    "loaded_instances": [{"identifier": "inst-001"}],
                }
            ]
        }
        result = get_loaded_model_instances()
        self.assertEqual(result, {PRIMARY_MODEL.model_id: "inst-001"})

    @patch("rebus_generator.platform.llm.lm_studio_api._get_json")
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

    @patch("rebus_generator.platform.llm.lm_studio_api._get_json")
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

    @patch("rebus_generator.platform.llm.lm_studio_api._get_json")
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

    @patch("rebus_generator.platform.llm.lm_studio_api._get_json")
    def test_skips_models_without_instances(self, mock_get):
        mock_get.return_value = {
            "models": [
                {"key": "loaded/model", "loaded_instances": [{"identifier": "inst-1"}]},
                {"key": "unloaded/model", "loaded_instances": []},
            ]
        }
        result = get_loaded_model_instances()
        self.assertEqual(result, {"loaded/model": "inst-1"})

    @patch("rebus_generator.platform.llm.lm_studio_api._get_json")
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
