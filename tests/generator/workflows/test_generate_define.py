from unittest.mock import MagicMock, patch

from rebus_generator.domain.pipeline_state import WorkingClue
from rebus_generator.workflows.generate.define import generate_definition_for_working_clue


def test_generate_definition_for_working_clue_uses_original_for_dex_and_prompt():
    clue = WorkingClue(row_number=1, word_normalized="IT", word_original="iț")
    dex = MagicMock()
    dex.get.return_value = "- Copil neastâmpărat."

    with patch("rebus_generator.workflows.generate.define.generate_definition", return_value="def") as generate:
        result = generate_definition_for_working_clue(
            clue,
            MagicMock(),
            theme="test",
            dex=dex,
            clue_canon=MagicMock(),
        )

    assert result == "def"
    dex.get.assert_called_once_with("IT", "iț")
    assert generate.call_args.args[1] == "IT"
    assert generate.call_args.args[2] == "iț"
