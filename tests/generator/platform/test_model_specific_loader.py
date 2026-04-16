import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from rebus_generator.prompts.loader import load_system_prompt

class TestModelSpecificLoader(unittest.TestCase):
    @patch("rebus_generator.prompts.loader.get_model_config")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_loader_priority(self, mock_read_text, mock_exists, mock_get_model_config):
        # Setup mocks
        mock_config = MagicMock()
        mock_config.display_name = "gemma-4"
        mock_get_model_config.return_value = mock_config
        
        # Test case: model-specific file exists
        mock_exists.return_value = True
        mock_read_text.return_value = "Gemma prompt"
        
        result = load_system_prompt("definition", model_id="google/gemma-4-26b-a4b")
        self.assertEqual(result, "Gemma prompt")
        
        # Test case: model-specific file DOES NOT exist
        mock_exists.return_value = False
        mock_read_text.return_value = "Default prompt"
        
        # Clear cache for testing
        load_system_prompt.cache_clear()
        result = load_system_prompt("definition", model_id="google/gemma-4-26b-a4b")
        self.assertEqual(result, "Default prompt")

if __name__ == "__main__":
    unittest.main()
