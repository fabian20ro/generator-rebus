import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from generator.batch_publish import (
    Candidate,
    LOCKED_REBUS,
    PreparedPuzzle,
    SizeSettings,
    _best_candidate,
    _better_prepared_puzzle,
    _generate_candidate,
    _is_publishable,
    _merge_best_clue_variants,
    _needs_rewrite,
    _preparation_attempts_for_size,
    _prepare_puzzle_for_publication,
    _synthesize_failure_reason,
    _template_fingerprint,
    build_parser as build_batch_parser,
    run_batch,
)
from generator.core.clue_rating import append_rating_to_note
from generator.core.markdown_io import ClueEntry
from generator.core.pipeline_state import working_clue_from_entry
from generator.core.quality import QualityReport
from generator.core.size_tuning import get_size_settings
from generator.rebus import build_parser as build_rebus_parser


class BatchPublishTests(unittest.TestCase):
    def test_high_scores_do_not_force_rewrite_even_if_verify_failed(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="TUN",
            word_original="",
            definition="Recipient mare pentru vin",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: BARIL",
                semantic_score=9,
                guessability_score=7,
                feedback="definiție bună, dar există sinonim concurent",
            ),
        )

        self.assertFalse(_needs_rewrite(clue))

    def test_low_guessability_triggers_rewrite(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="TUN",
            word_original="",
            definition="Recipient mare pentru vin",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: BARIL",
                semantic_score=9,
                guessability_score=4,
                feedback="trimite spre un sinonim mai uzual",
            ),
        )

        self.assertTrue(_needs_rewrite(clue))

    def test_missing_scores_trigger_rewrite(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="TUN",
            word_original="",
            definition="Recipient mare pentru vin",
            verified=None,
            verify_note="AI a ghicit: BARIL",
        )

        self.assertTrue(_needs_rewrite(clue))

    def test_rarity_only_override_prevents_rewrite(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="",
            definition="Bete de sprijin pentru vita",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: PARI",
                semantic_score=9,
                guessability_score=4,
                feedback="Răspunsul este rar.",
            ),
        )
        working = working_clue_from_entry(clue)
        working.current.assessment.rarity_only_override = True

        self.assertFalse(_needs_rewrite(working))

    def test_rarity_only_override_false_still_triggers_rewrite(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="",
            definition="Bete de sprijin pentru vita",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: PARI",
                semantic_score=9,
                guessability_score=4,
                feedback="Duce spre un sinonim mai uzual.",
            ),
        )
        working = working_clue_from_entry(clue)
        working.current.assessment.rarity_only_override = False

        self.assertTrue(_needs_rewrite(working))

    def test_preset_word_never_needs_rewrite(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="FI",
            word_original="fi",
            definition="",
            verified=None,
            verify_note="",
        )

        self.assertFalse(_needs_rewrite(clue))

    def test_preset_word_bypasses_even_with_low_scores(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="AT",
            word_original="at",
            definition="Monedă din Laos",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: HAT",
                semantic_score=3,
                guessability_score=2,
                feedback="greșit",
            ),
        )

        self.assertFalse(_needs_rewrite(clue))

    def test_merge_best_clue_variants_keeps_higher_scored_definition(self):
        best = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din atmosferă",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: AIR",
                semantic_score=8,
                guessability_score=4,
                feedback="duce spre sinonim străin",
            ),
        )
        current = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Substanță gazoasă pe care o respirăm",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="clar și natural",
            ),
        )

        merged = _merge_best_clue_variants([best], [current])

        self.assertEqual("Substanță gazoasă pe care o respirăm", merged[0].definition)

    @patch("generator.batch_publish.choose_better_clue_variant")
    def test_merge_best_clue_variants_uses_llm_tiebreak_on_equal_scores(self, mock_tiebreak):
        mock_tiebreak.return_value = "B"
        best = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din jurul nostru",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="bună",
            ),
        )
        current = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Amestec gazos din atmosferă",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="bună",
            ),
        )

        with patch("sys.stdout", new=StringIO()) as captured:
            merged = _merge_best_clue_variants([best], [current], client=object())

        self.assertEqual("Amestec gazos din atmosferă", merged[0].definition)
        mock_tiebreak.assert_called_once()
        log_line = captured.getvalue()
        self.assertIn("A='Gaz din jurul nostru'", log_line)
        self.assertIn("B='Amestec gazos din atmosferă'", log_line)
        self.assertIn("aleasă='Amestec gazos din atmosferă'", log_line)

    def test_nine_nine_clue_is_locked(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din atmosferă",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: VANT",
                semantic_score=9,
                guessability_score=9,
                feedback="clară și exactă",
            ),
        )

        self.assertFalse(_needs_rewrite(clue))

    def test_nine_eight_clue_is_locked(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din atmosferă",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="bună",
            ),
        )

        self.assertFalse(_needs_rewrite(clue))

    def test_eight_seven_clue_not_locked(self):
        from generator.batch_publish import _update_best_clue_version
        clue = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din atmosferă",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=8,
                guessability_score=7,
                feedback="duce spre sinonim",
            ),
        ))

        _update_best_clue_version(clue)

        self.assertFalse(clue.locked)

    def test_nine_eight_clue_gets_locked_via_update(self):
        from generator.batch_publish import _update_best_clue_version
        clue = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din atmosferă",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="bună",
            ),
        ))

        _update_best_clue_version(clue)

        self.assertTrue(clue.locked)

    def test_large_sizes_get_more_preparation_attempts(self):
        self.assertEqual(5, _preparation_attempts_for_size(7, 5))
        self.assertEqual(24, _preparation_attempts_for_size(10, 5))
        self.assertEqual(40, _preparation_attempts_for_size(12, 5))

    def test_batch_cli_accepts_all_supported_mid_sizes(self):
        parser = build_batch_parser()
        args = parser.parse_args(["--sizes", "8", "9", "11"])

        self.assertEqual([8, 9, 11], args.sizes)

    def test_rebus_cli_accepts_all_supported_mid_sizes(self):
        parser = build_rebus_parser()
        args = parser.parse_args(["generate-grid", "-", "out.md", "--size", "11"])

        self.assertEqual(11, args.size)

    def test_central_size_settings_cover_all_supported_overnight_sizes(self):
        for size in (7, 8, 9, 10, 11, 12):
            settings = get_size_settings(size)
            self.assertGreater(settings.max_backtracks, 0)
            self.assertGreater(settings.template_attempts, 0)
            self.assertGreater(settings.min_preparation_attempts, 0)

    @patch("generator.batch_publish.choose_better_puzzle_variant")
    def test_prepared_puzzle_tiebreak_uses_llm_for_near_equal_scores(self, mock_tiebreak):
        mock_tiebreak.return_value = "B"
        best = _prepared_puzzle(title="A", definition_score=8.0, blocking_words=[])
        candidate = _prepared_puzzle(title="B", definition_score=8.2, blocking_words=[])

        with patch("sys.stdout", new=StringIO()) as captured:
            winner = _better_prepared_puzzle(best, candidate, client=object())

        self.assertEqual("B", winner.title)
        mock_tiebreak.assert_called_once()
        self.assertIn("Puzzle tie-break:", captured.getvalue())
        self.assertIn("câștigă B", captured.getvalue())

    @patch("generator.batch_publish.score_words")
    @patch("generator.batch_publish.solve")
    @patch("generator.batch_publish._slot_capacity_ok")
    @patch("generator.batch_publish.extract_slots")
    @patch("generator.batch_publish.generate_procedural_template")
    @patch("generator.batch_publish.parse_template")
    @patch("generator.batch_publish.validate_template", return_value=(True, ""))
    def test_generate_candidate_for_seven_uses_procedural_templates_only(
        self,
        mock_validate,
        mock_parse_template,
        mock_generate_template,
        mock_extract_slots,
        mock_slot_ok,
        mock_solve,
        mock_score_words,
    ):
        template = [
            [True, True, True, False, True, True, True],
            [True, True, True, True, True, True, True],
            [True, True, True, False, True, True, True],
            [False, True, True, True, True, True, False],
            [True, True, True, False, True, True, True],
            [True, True, True, True, True, True, True],
            [True, True, True, False, True, True, True],
        ]
        settings = SizeSettings(3, 50000, 6, 1, 1, 4, 16, template_attempts=777)
        slot = type("Slot", (), {
            "id": 1,
            "direction": "H",
            "length": 3,
            "start_row": 0,
            "start_col": 0,
            "cells": [(0, 0), (0, 1), (0, 2)],
        })()
        word = type("WordEntry", (), {"normalized": "AER", "original": "aer"})()

        mock_generate_template.return_value = template
        mock_extract_slots.return_value = [slot]
        mock_slot_ok.return_value = True
        mock_solve.return_value = {1: word}
        mock_score_words.return_value = QualityReport(
            score=10.0,
            word_count=1,
            average_length=3.0,
            average_rarity=1.0,
            two_letter_words=0,
            three_letter_words=1,
            high_rarity_words=0,
            uncommon_letter_words=0,
            friendly_words=1,
        )

        candidate = _generate_candidate(
            7,
            settings,
            word_index=object(),
            metadata={"AER": {"rarity_level": 1}},
            title="Test",
            seen_template_fingerprints=set(),
        )

        self.assertIsNotNone(candidate)
        mock_parse_template.assert_not_called()
        self.assertEqual(777, mock_generate_template.call_args.kwargs["max_attempts"])

    @patch("generator.batch_publish.score_words")
    @patch("generator.batch_publish.solve")
    @patch("generator.batch_publish._slot_capacity_ok")
    @patch("generator.batch_publish.extract_slots")
    @patch("generator.batch_publish.generate_procedural_template")
    def test_duplicate_seven_template_fingerprint_is_rejected(
        self,
        mock_generate_template,
        mock_extract_slots,
        mock_slot_ok,
        mock_solve,
        mock_score_words,
    ):
        template = [
            [True, True, True],
            [True, False, True],
            [True, True, True],
        ]
        settings = SizeSettings(3, 50000, 1, 1, 1, 4, 16)
        slot = type("Slot", (), {
            "id": 1,
            "direction": "H",
            "length": 3,
            "start_row": 0,
            "start_col": 0,
            "cells": [(0, 0), (0, 1), (0, 2)],
        })()
        word = type("WordEntry", (), {"normalized": "AER", "original": "aer"})()

        mock_generate_template.return_value = template
        mock_extract_slots.return_value = [slot]
        mock_slot_ok.return_value = True
        mock_solve.return_value = {1: word}
        mock_score_words.return_value = QualityReport(
            score=10.0,
            word_count=1,
            average_length=3.0,
            average_rarity=1.0,
            two_letter_words=0,
            three_letter_words=1,
            high_rarity_words=0,
            uncommon_letter_words=0,
            friendly_words=1,
        )

        seen = {_template_fingerprint(template)}
        candidate = _generate_candidate(
            7,
            settings,
            word_index=object(),
            metadata={"AER": {"rarity_level": 1}},
            title="Test",
            seen_template_fingerprints=seen,
        )

        self.assertIsNone(candidate)

    @patch("generator.batch_publish._generate_candidate")
    @patch("generator.batch_publish._build_index")
    @patch("generator.batch_publish.build_relaxed_variants")
    def test_best_candidate_returns_after_first_solved(
        self,
        mock_variants,
        mock_build_index,
        mock_generate_candidate,
    ):
        settings_a = SizeSettings(3, 80_000, 6, 3, 2, 4, 16, template_attempts=500)
        mock_variants.return_value = [settings_a]
        mock_build_index.return_value = (object(), {})
        candidate_a = Candidate(
            score=10.0,
            report=QualityReport(
                score=10.0,
                word_count=1,
                average_length=3.0,
                average_rarity=1.0,
                two_letter_words=0,
                three_letter_words=1,
                high_rarity_words=0,
                uncommon_letter_words=0,
                friendly_words=1,
            ),
            template=[[True]],
            markdown="# A\n",
        )
        mock_generate_candidate.return_value = candidate_a

        best = _best_candidate(
            7,
            "Test",
            raw_words=[],
            rng=SimpleNamespace(),
            seen_template_fingerprints=set(),
        )

        self.assertEqual(10.0, best.score)
        self.assertEqual(1, mock_generate_candidate.call_count)

    @patch("generator.batch_publish._generate_candidate")
    @patch("generator.batch_publish._build_index")
    @patch("generator.batch_publish.build_relaxed_variants")
    def test_best_candidate_skips_none_then_returns_first_solved(
        self,
        mock_variants,
        mock_build_index,
        mock_generate_candidate,
    ):
        settings_a = SizeSettings(3, 80_000, 6, 3, 2, 4, 16, template_attempts=500)
        mock_variants.return_value = [settings_a]
        mock_build_index.return_value = (object(), {})
        candidate_b = Candidate(
            score=25.0,
            report=QualityReport(
                score=25.0,
                word_count=1,
                average_length=4.0,
                average_rarity=1.0,
                two_letter_words=0,
                three_letter_words=0,
                high_rarity_words=0,
                uncommon_letter_words=0,
                friendly_words=1,
            ),
            template=[[True]],
            markdown="# B\n",
        )
        mock_generate_candidate.side_effect = [None, candidate_b]

        best = _best_candidate(
            7,
            "Test",
            raw_words=[],
            rng=SimpleNamespace(),
            seen_template_fingerprints=set(),
        )

        self.assertEqual(25.0, best.score)
        self.assertEqual(2, mock_generate_candidate.call_count)

    @patch("generator.batch_publish.generate_title_for_final_puzzle")
    @patch("generator.batch_publish._rewrite_failed_clues")
    @patch("generator.batch_publish.generate_definitions_for_puzzle")
    @patch("generator.batch_publish.parse_markdown")
    @patch("generator.batch_publish._best_candidate")
    def test_final_title_is_generated_after_definitions_stabilize(
        self,
        mock_best_candidate,
        mock_parse_markdown,
        mock_generate_definitions,
        mock_rewrite_failed,
        mock_final_title,
    ):
        puzzle = type("Puzzle", (), {
            "title": "",
            "horizontal_clues": [ClueEntry(1, "AER", "", "", verified=None, verify_note="")],
            "vertical_clues": [],
        })()
        mock_best_candidate.return_value = Candidate(
            score=12.0,
            report=QualityReport(
                score=12.0,
                word_count=1,
                average_length=3.0,
                average_rarity=1.0,
                two_letter_words=0,
                three_letter_words=1,
                high_rarity_words=0,
                uncommon_letter_words=0,
                friendly_words=1,
            ),
            template=[[True, True, True]],
            markdown="# Rebus\n",
        )
        mock_parse_markdown.return_value = puzzle

        def _fill_defs(puzzle_obj, client, metadata=None):
            puzzle_obj.horizontal_clues[0].definition = "Gaz din atmosferă"

        def _rewrite(puzzle_obj, client, rounds, multi_model=False):
            puzzle_obj.horizontal_clues[0].current.definition = "Substanță gazoasă din atmosferă"
            return (1, 1)

        def _title_from_final(puzzle_obj, client=None):
            return puzzle_obj.horizontal_clues[0].definition

        mock_generate_definitions.side_effect = _fill_defs
        mock_rewrite_failed.side_effect = _rewrite
        mock_final_title.side_effect = _title_from_final

        prepared = _prepare_puzzle_for_publication(
            index=1,
            total_puzzles=1,
            size=7,
            raw_words=[],
            client=object(),
            rewrite_rounds=1,
            preparation_attempts=1,
            seen_template_fingerprints=set(),
        )

        self.assertEqual("Substanță gazoasă din atmosferă", prepared.title)
        self.assertEqual("Substanță gazoasă din atmosferă", prepared.puzzle.title)

    def test_failure_reason_prefers_wrong_guess(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="",
            definition="Prezintă un fapt în mod clar și convingător.",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: EXPLICA",
                semantic_score=8,
                guessability_score=4,
                feedback="Duce la alt răspuns mai comun.",
            ),
        )

        reason = _synthesize_failure_reason(clue)

        self.assertEqual("Duce la alt răspuns: EXPLICA.", reason)

    def test_failure_reason_ignores_rarity_as_primary_defect(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="",
            definition="Bețe de sprijin pentru viță",
            verified=False,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=5,
                feedback="Răspunsul este rar și mai puțin comun.",
            ),
        )

        reason = _synthesize_failure_reason(clue)

        self.assertIn("exactă", reason)

    @patch("generator.batch_publish.ensure_model_loaded")
    @patch("generator.batch_publish.upload_puzzle")
    @patch("generator.batch_publish._prepare_puzzle_for_publication")
    @patch("generator.batch_publish._load_words")
    def test_run_batch_rejects_blocked_puzzle_before_upload(
        self,
        mock_load_words,
        mock_prepare,
        mock_upload,
        mock_ensure_model,
    ):
        mock_load_words.return_value = []
        mock_prepare.return_value = PreparedPuzzle(
            title="Titlu de Test",
            candidate=Candidate(
                score=100.0,
                report=QualityReport(
                    score=100.0,
                    word_count=0,
                    average_length=0.0,
                    average_rarity=0.0,
                    two_letter_words=0,
                    three_letter_words=0,
                    high_rarity_words=0,
                    uncommon_letter_words=0,
                    friendly_words=0,
                ),
                template=[[True, True], [True, True]],
                markdown="# Rebus\n",
            ),
            puzzle=object(),
            passed=0,
            total=0,
            definition_score=0.0,
            blocking_words=["TAC", "ATASARE"],
        )

        with TemporaryDirectory() as tmp_dir:
            with self.assertRaises(RuntimeError):
                run_batch(
                    sizes=[7],
                    output_root=Path(tmp_dir),
                    words_path=Path(tmp_dir) / "words.json",
                    rewrite_rounds=2,
                    preparation_attempts=1,
                    run_dir=Path(tmp_dir) / "run",
                )

        mock_upload.assert_not_called()


    def test_low_scores_but_definitions_present_is_publishable(self):
        prepared = _prepared_puzzle(
            title="Test",
            definition_score=4.0,
            blocking_words=[],
        )

        self.assertTrue(_is_publishable(prepared))

    def test_missing_definition_blocks_publication(self):
        prepared = _prepared_puzzle(
            title="Test",
            definition_score=4.0,
            blocking_words=["AER"],
        )

        self.assertFalse(_is_publishable(prepared))

    def test_nine_eight_clue_uses_locked_rebus(self):
        self.assertEqual(8, LOCKED_REBUS)


    def test_new_11x11_templates_are_valid(self):
        from generator.core.grid_template import TEMPLATES_11x11, parse_template, validate_template
        for i, t in enumerate(TEMPLATES_11x11):
            grid = parse_template(t)
            valid, msg = validate_template(grid)
            self.assertTrue(valid, f"11x11 template {i} invalid: {msg}")

    def test_new_12x12_templates_are_valid(self):
        from generator.core.grid_template import TEMPLATES_12x12, parse_template, validate_template
        for i, t in enumerate(TEMPLATES_12x12):
            grid = parse_template(t)
            valid, msg = validate_template(grid)
            self.assertTrue(valid, f"12x12 template {i} invalid: {msg}")

    def test_easy_11_template_is_valid(self):
        from generator.batch_publish import _easy_11_template
        from generator.core.grid_template import validate_template
        grid = _easy_11_template(11)
        self.assertIsNotNone(grid)
        valid, msg = validate_template(grid)
        self.assertTrue(valid, f"easy_11_template invalid: {msg}")

    def test_easy_11_template_has_few_full_width_slots(self):
        from generator.batch_publish import _easy_11_template
        from generator.core.slot_extractor import extract_slots
        grid = _easy_11_template(11)
        slots = extract_slots(grid)
        full_width = sum(1 for s in slots if s.length == 11)
        self.assertLessEqual(full_width, 5, f"easy_11 has {full_width} full-width slots")

    def test_easy_11_template_two_letter_slots_within_limit(self):
        from generator.batch_publish import _easy_11_template
        grid = _easy_11_template(11)
        count = _count_two_letter_slots(grid)
        self.assertLessEqual(count, 18)

    def test_easy_medium_template_is_valid(self):
        from generator.batch_publish import _easy_medium_template
        from generator.core.grid_template import validate_template
        grid = _easy_medium_template(12)
        self.assertIsNotNone(grid)
        valid, msg = validate_template(grid)
        self.assertTrue(valid, f"easy_medium_template invalid: {msg}")

    def test_easy_medium_template_has_few_full_width_slots(self):
        from generator.batch_publish import _easy_medium_template
        from generator.core.slot_extractor import extract_slots
        grid = _easy_medium_template(12)
        slots = extract_slots(grid)
        full_width = sum(1 for s in slots if s.length == 12)
        self.assertLessEqual(full_width, 5, f"easy_medium has {full_width} full-width slots")

    def test_easy_template_by_name_medium_11(self):
        from generator.batch_publish import _easy_template_by_name
        grid = _easy_template_by_name("medium_11", 11)
        self.assertIsNotNone(grid)

    def test_size_11_settings_mixed_policy(self):
        settings = get_size_settings(11)
        self.assertEqual("mixed", settings.template_policy)
        self.assertEqual(18, settings.max_two_letter_slots)
        self.assertEqual("medium_11", settings.easy_template)

    def test_size_12_max_two_letter_slots_increased(self):
        settings = get_size_settings(12)
        self.assertEqual(22, settings.max_two_letter_slots)

    def test_size_11_has_full_width_slot_limit(self):
        settings = get_size_settings(11)
        self.assertEqual(5, settings.max_full_width_slots)

    def test_size_12_has_full_width_slot_limit(self):
        settings = get_size_settings(12)
        self.assertEqual(5, settings.max_full_width_slots)

    def test_size_7_has_no_full_width_slot_limit(self):
        settings = get_size_settings(7)
        self.assertIsNone(settings.max_full_width_slots)

    def test_working_clue_has_word_type_field(self):
        from generator.core.pipeline_state import WorkingClue
        clue = WorkingClue(row_number=1, word_normalized="LOVI", word_original="lovi")
        self.assertEqual("", clue.word_type)
        clue.word_type = "V"
        self.assertEqual("V", clue.word_type)


def _count_two_letter_slots(grid: list[list[bool]]) -> int:
    rows, cols = len(grid), len(grid[0])
    count = 0
    for r in range(rows):
        run = 0
        for c in range(cols + 1):
            if c < cols and grid[r][c]:
                run += 1
            else:
                if run == 2:
                    count += 1
                run = 0
    for c in range(cols):
        run = 0
        for r in range(rows + 1):
            if r < rows and grid[r][c]:
                run += 1
            else:
                if run == 2:
                    count += 1
                run = 0
    return count


def _prepared_puzzle(title: str, definition_score: float, blocking_words: list[str]) -> PreparedPuzzle:
    clue = ClueEntry(
        row_number=1,
        word_normalized="AER",
        word_original="",
        definition="Gaz din atmosferă",
        verified=True,
        verify_note=append_rating_to_note(
            "",
            semantic_score=9,
            guessability_score=8,
            feedback="clară",
        ),
    )
    puzzle = type("Puzzle", (), {
        "title": title,
        "horizontal_clues": [clue],
        "vertical_clues": [],
    })()
    return PreparedPuzzle(
        title=title,
        candidate=Candidate(
            score=definition_score,
            report=QualityReport(
                score=definition_score,
                word_count=1,
                average_length=3.0,
                average_rarity=1.0,
                two_letter_words=0,
                three_letter_words=1,
                high_rarity_words=0,
                uncommon_letter_words=0,
                friendly_words=1,
            ),
            template=[[True, True, True]],
            markdown="# Rebus\n",
        ),
        puzzle=puzzle,
        passed=1,
        total=1,
        definition_score=definition_score,
        blocking_words=blocking_words,
    )


if __name__ == "__main__":
    unittest.main()
