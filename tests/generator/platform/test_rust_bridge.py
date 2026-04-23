from rebus_generator.platform.io.markdown_io import parse_markdown
from rebus_generator.platform.io.rust_bridge import _render_markdown_from_rust_payload


def test_rust_payload_preserves_original_form_in_markdown():
    markdown = _render_markdown_from_rust_payload(
        "test",
        [[True, True]],
        [["I", "T"]],
        [{"id": 1, "direction": "H", "start_row": 0, "start_col": 0}],
        [{"slot_id": 1, "normalized": "IT", "original": "iț"}],
    )
    puzzle = parse_markdown(markdown)
    assert puzzle.horizontal_clues[0].word_normalized == "IT"
    assert puzzle.horizontal_clues[0].word_original == "iț"
