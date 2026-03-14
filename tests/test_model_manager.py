import json
import unittest
from unittest.mock import patch, MagicMock

from generator.core.model_manager import (
    ModelConfig,
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    get_loaded_models,
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
        self.assertIn("qwen", SECONDARY_MODEL.model_id)

    def test_get_loaded_models_returns_empty_on_failure(self):
        with patch("generator.core.model_manager._get_json", side_effect=Exception("offline")):
            result = get_loaded_models()
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
