from rebus_generator.platform.io.markdown_io import parse_markdown
import json

from rebus_generator.platform.io.rust_bridge import (
    _materialize_augmented_words,
    _render_markdown_from_rust_payload,
)


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


def test_materialize_augmented_words_adds_answer_supply_rows(tmp_path):
    words_path = tmp_path / "words.json"
    raw_words = [{"normalized": "AA", "original": "aa", "length": 2, "rarity_level": 5}]
    words_path.write_text(json.dumps(raw_words), encoding="utf-8")

    augmented_path, augmented = _materialize_augmented_words(words_path, raw_words)

    assert augmented_path.name == "words.answer_supply.json"
    by_word = {row["normalized"]: row for row in augmented}
    assert by_word["TM"]["source"] == "curated_ro_plate"
    assert by_word["TM"]["enabled_for_grid"] if "enabled_for_grid" in by_word["TM"] else True
    assert by_word["AI"]["source"] == "curated_cc_tld"
