import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from generator.batch_publish import (
    Candidate,
    LOCKED_REBUS,
    MAX_REWRITE_ROUNDS,
    PLATEAU_LOOKBACK,
    PreparedPuzzle,
    _backfill_generated_model,
    _best_candidate,
    _better_prepared_puzzle,
    _clear_verification_state,
    _collect_word_metrics,
    _compute_difficulty,
    _choose_metadata_variants_for_puzzle,
    _is_publishable,
    _merge_best_clue_variants,
    _needs_rewrite,
    _update_best_clue_version,
    _preparation_attempts_for_size,
    _prepare_puzzle_for_publication,
    _synthesize_failure_reason,
    _template_fingerprint,
    build_parser as build_batch_parser,
    run_batch,
)
from generator.core.clue_rating import append_rating_to_note
from generator.core.markdown_io import ClueEntry, write_with_definitions
from generator.core.model_manager import PRIMARY_MODEL
from generator.core.pipeline_state import (
    ClueScores,
    PuzzleAssessment,
    WorkingPuzzle,
    puzzle_from_working_state,
    set_current_definition,
    update_current_assessment,
    working_clue_from_entry,
)
from generator.core.quality import QualityReport
from generator.phases.theme import TitleGenerationResult
from generator.rebus import build_parser as build_rebus_parser


class BatchPublishTests(unittest.TestCase):
    def test_verify_failure_triggers_rewrite_even_with_high_scores(self):
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

        self.assertTrue(_needs_rewrite(clue))

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

    def test_rarity_only_override_does_not_prevent_rewrite_after_failed_verify(self):
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

        self.assertTrue(_needs_rewrite(working))

    def test_rarity_only_override_still_allows_verified_clue_to_stand(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="",
            definition="Bete de sprijin pentru vita",
            verified=True,
            verify_note=append_rating_to_note(
                "AI a ghicit: ARACI",
                semantic_score=9,
                guessability_score=4,
                feedback="Răspunsul este rar.",
            ),
        )
        working = working_clue_from_entry(clue)
        working.current.assessment.rarity_only_override = True

        self.assertFalse(_needs_rewrite(working))

    def test_failed_verify_high_score_clue_does_not_lock(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="JUPAIRE",
            word_original="jupăire",
            definition="Atingere rapidă cu un obiect dur.",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: PICĂTURĂ",
                semantic_score=9,
                guessability_score=7,
                feedback="Răspunsul este rar.",
            ),
        )
        working = working_clue_from_entry(clue)

        _update_best_clue_version(working)

        self.assertFalse(working.locked)

    def test_verified_high_score_clue_locks(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="SET",
            word_original="",
            definition="Parte dintr-o competiție sportivă.",
            verified=True,
            verify_note=append_rating_to_note(
                "AI a ghicit: SET",
                semantic_score=9,
                guessability_score=9,
                feedback="Definiție bună.",
            ),
        )
        working = working_clue_from_entry(clue)

        _update_best_clue_version(working)

        self.assertTrue(working.locked)

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

    def test_nine_nine_but_failed_verify_still_needs_rewrite(self):
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

        self.assertTrue(_needs_rewrite(clue))

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
        self.assertEqual(5, _preparation_attempts_for_size(13, 5))
        self.assertEqual(5, _preparation_attempts_for_size(14, 5))
        self.assertEqual(5, _preparation_attempts_for_size(15, 5))

    def test_batch_cli_accepts_all_supported_mid_sizes(self):
        parser = build_batch_parser()
        args = parser.parse_args(["--sizes", "8", "9", "11"])

        self.assertEqual([8, 9, 11], args.sizes)

    def test_rebus_cli_rejects_removed_phase1_commands(self):
        parser = build_rebus_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["generate-grid", "-", "out.md"])

    @patch("generator.batch_publish.choose_better_puzzle_variant")
    def test_prepared_puzzle_tiebreak_uses_llm_for_near_equal_scores(self, mock_tiebreak):
        mock_tiebreak.return_value = "B"
        best = _prepared_puzzle(title="A", definition_score=8.0, blocking_words=[], verified_count=1)
        candidate = _prepared_puzzle(title="B", definition_score=8.2, blocking_words=[], verified_count=1)

        with patch("sys.stdout", new=StringIO()) as captured:
            winner = _better_prepared_puzzle(best, candidate, client=object())

        self.assertEqual("B", winner.title)
        mock_tiebreak.assert_called_once()
        self.assertIn("Puzzle tie-break:", captured.getvalue())
        self.assertIn("câștigă B", captured.getvalue())

    @patch("generator.batch_publish._rust_binary_path")
    @patch("generator.batch_publish.subprocess.run")
    def test_best_candidate_uses_rust_binary_when_words_path_present(
        self,
        mock_run,
        mock_binary,
    ):
        mock_binary.return_value = Path("/tmp/crossword_phase1")
        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stderr="variant 1 solved\n",
            stdout=(
                '{"template":[[true,true],[true,true]],'
                '"filled_grid":[["A","B"],["C","D"]],'
                '"slots":['
                '{"id":0,"direction":"H","start_row":0,"start_col":0,"length":2,"cells":[[0,0],[0,1]],"intersections":[]},'
                '{"id":1,"direction":"H","start_row":1,"start_col":0,"length":2,"cells":[[1,0],[1,1]],"intersections":[]},'
                '{"id":2,"direction":"V","start_row":0,"start_col":0,"length":2,"cells":[[0,0],[1,0]],"intersections":[]},'
                '{"id":3,"direction":"V","start_row":0,"start_col":1,"length":2,"cells":[[0,1],[1,1]],"intersections":[]}'
                '],'
                '"words":['
                '{"slot_id":0,"normalized":"AB"},'
                '{"slot_id":1,"normalized":"CD"},'
                '{"slot_id":2,"normalized":"AC"},'
                '{"slot_id":3,"normalized":"BD"}'
                '],'
                '"quality":{"score":321.0,"word_count":4,"average_length":2.0,'
                '"average_rarity":0.0,"two_letter_words":4,"three_letter_words":0,'
                '"high_rarity_words":0,"uncommon_letter_words":0,"friendly_words":0,'
                '"max_rarity":0,"average_definability":5.0},'
                '"stats":{"elapsed_ms":12,"solver_nodes":44,"solved_candidates":2}}'
            ),
        )

        candidate = _best_candidate(
            7,
            "Test",
            raw_words=[{"normalized": "AB", "original": "ab"}],
            rng=SimpleNamespace(randint=lambda *_: 123),
            words_path=Path("generator/output/words.json"),
            word_metadata={"AB": {"normalized": "AB", "original": "ab"}},
        )

        self.assertEqual(321.0, candidate.score)
        self.assertEqual(12, candidate.stats["elapsed_ms"])
        mock_run.assert_called_once()

    @patch("generator.batch_publish.random.choice")
    def test_metadata_variants_are_pinned_once_per_normalized_word(self, mock_choice):
        mock_choice.return_value = {"normalized": "AER", "original": "aer", "word_type": "N"}
        puzzle = SimpleNamespace(
            horizontal_clues=[ClueEntry(1, "AER", "", "")],
            vertical_clues=[ClueEntry(1, "AER", "", "")],
        )

        resolved = _choose_metadata_variants_for_puzzle(
            puzzle,
            {"AER": [
                {"normalized": "AER", "original": "aer", "word_type": "N"},
                {"normalized": "AER", "original": "aerul", "word_type": "N"},
            ]},
        )

        self.assertEqual("aer", puzzle.horizontal_clues[0].word_original)
        self.assertEqual("aer", puzzle.vertical_clues[0].word_original)
        self.assertEqual("N", resolved["AER"]["word_type"])
        mock_choice.assert_called_once()

    def test_best_candidate_requires_words_path(self):
        with self.assertRaises(ValueError):
            _best_candidate(
                7,
                "Test",
                raw_words=[],
                rng=SimpleNamespace(randint=lambda *_: 123),
            )

    def test_prepared_puzzle_prefers_more_verified_clues_before_score(self):
        best = _prepared_puzzle(title="A", definition_score=8.0, blocking_words=[], verified_count=5, total_clues=6)
        candidate = _prepared_puzzle(title="B", definition_score=9.5, blocking_words=[], verified_count=4, total_clues=6)

        winner = _better_prepared_puzzle(best, candidate, client=object())

        self.assertEqual("A", winner.title)

    def test_clear_verification_state_removes_exported_scores_and_notes(self):
        clue = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="TUN",
            word_original="",
            definition="Recipient mare pentru vin",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: BARIL",
                semantic_score=9,
                guessability_score=7,
                feedback="definiție bună, dar ambiguă",
                creativity_score=6,
                rebus_score=7,
            ),
        ))
        puzzle = WorkingPuzzle(
            title="Test",
            size=3,
            grid=[["T", "U", "N"]],
            horizontal_clues=[clue],
            vertical_clues=[],
        )

        clean = _clear_verification_state(puzzle)
        rendered = write_with_definitions(puzzle_from_working_state(clean))

        self.assertNotIn("AI a ghicit", rendered)
        self.assertNotIn("semantic", rendered.lower())
        self.assertIsNone(clean.horizontal_clues[0].active_version().assessment.verified)

    def test_backfill_generated_model_marks_initial_versions(self):
        clue = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="BOL",
            word_original="",
            definition="Vas fără picior",
        ))
        puzzle = WorkingPuzzle(
            title="Test",
            size=3,
            grid=[["B", "O", "L"]],
            horizontal_clues=[clue],
            vertical_clues=[],
        )

        _backfill_generated_model(puzzle, PRIMARY_MODEL.display_name)

        self.assertEqual(PRIMARY_MODEL.display_name, puzzle.horizontal_clues[0].current.generated_by)

    @patch("generator.batch_publish.generate_title_for_final_puzzle_result")
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

        def _fill_defs(puzzle_obj, client, metadata=None, runtime=None, model_config=None):
            puzzle_obj.horizontal_clues[0].definition = "Gaz din atmosferă"

        def _rewrite(puzzle_obj, client, rounds, **kwargs):
            puzzle_obj.horizontal_clues[0].current.definition = "Substanță gazoasă din atmosferă"
            return (0, 1, 1)

        def _title_from_final(puzzle_obj, client=None, rate_client=None, runtime=None, multi_model=False):
            return TitleGenerationResult(puzzle_obj.horizontal_clues[0].definition, 8, "ok")

        mock_generate_definitions.side_effect = _fill_defs
        mock_rewrite_failed.side_effect = _rewrite
        mock_final_title.side_effect = _title_from_final

        prepared = _prepare_puzzle_for_publication(
            index=1,
            total_puzzles=1,
            size=7,
            raw_words=[],
            words_path=Path("generator/output/words.json"),
            client=object(),
            rewrite_rounds=1,
            preparation_attempts=1,
            seen_template_fingerprints=set(),
        )

        self.assertEqual("Substanță gazoasă din atmosferă", prepared.title)
        self.assertEqual("Substanță gazoasă din atmosferă", prepared.puzzle.title)

    def test_failure_reason_prefers_verify_candidates(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="",
            definition="Prezintă un fapt în mod clar și convingător.",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a propus: EXPLICA, DESCRIE, NAREAZA",
                semantic_score=8,
                guessability_score=4,
                feedback="Duce la alt răspuns mai comun.",
            ),
        )

        reason = _synthesize_failure_reason(clue)

        self.assertEqual("Duce la alte răspunsuri: EXPLICA, DESCRIE, NAREAZA.", reason)

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

    def test_failure_reason_uses_rewrite_rejection_when_no_better_signal_exists(self):
        clue = working_clue_from_entry(
            ClueEntry(
                row_number=1,
                word_normalized="ARACI",
                word_original="",
                definition="Bețe de sprijin pentru viță",
                verified=False,
                verify_note="",
            )
        )
        clue.current.assessment.rewrite_rejection_reason = "too short (0 chars)"

        reason = _synthesize_failure_reason(clue)

        self.assertEqual("too short (0 chars)", reason)

    def test_failure_reason_prefers_feedback_over_rewrite_rejection(self):
        clue = working_clue_from_entry(
            ClueEntry(
                row_number=1,
                word_normalized="ARACI",
                word_original="",
                definition="Bețe de sprijin pentru viță",
                verified=False,
                verify_note="",
            )
        )
        clue.current.assessment.feedback = "Definiția este prea vagă."
        clue.current.assessment.rewrite_rejection_reason = "too short (0 chars)"

        reason = _synthesize_failure_reason(clue)

        self.assertEqual("Definiția este prea vagă.", reason)

    @patch("generator.batch_publish.LmRuntime")
    @patch("generator.batch_publish.upload_puzzle")
    @patch("generator.batch_publish._prepare_puzzle_for_publication")
    @patch("generator.batch_publish._load_words")
    def test_run_batch_rejects_blocked_puzzle_before_upload(
        self,
        mock_load_words,
        mock_prepare,
        mock_upload,
        mock_runtime_cls,
    ):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = object()
        mock_load_words.return_value = []
        mock_prepare.return_value = PreparedPuzzle(
            title="Titlu de Test",
            title_score=8,
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
            first_passed=0,
            final_passed=0,
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
            verified_count=1,
            total_clues=1,
        )

        self.assertTrue(_is_publishable(prepared))

    def test_low_pass_rate_blocks_publication_even_without_missing_definitions(self):
        prepared = _prepared_puzzle(
            title="Test",
            definition_score=8.0,
            blocking_words=[],
            verified_count=2,
            total_clues=22,
        )

        self.assertFalse(_is_publishable(prepared))

    def test_missing_definition_blocks_publication(self):
        prepared = _prepared_puzzle(
            title="Test",
            definition_score=4.0,
            blocking_words=["AER"],
        )

        self.assertFalse(_is_publishable(prepared))

    def test_nine_eight_clue_uses_locked_rebus(self):
        self.assertEqual(8, LOCKED_REBUS)

    def test_prepared_puzzle_tracks_first_and_final_pass_counts(self):
        prepared = _prepared_puzzle(
            title="Test",
            definition_score=8.0,
            blocking_words=[],
            verified_count=6,
            total_clues=22,
            first_passed=3,
            final_passed=6,
        )

        self.assertEqual(3, prepared.first_passed)
        self.assertEqual(6, prepared.final_passed)

    def test_collect_word_metrics_tracks_rewrite_churn(self):
        clue = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="MUL",
            word_original="mul",
            definition="Pământ",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: ARG",
                semantic_score=6,
                guessability_score=4,
                feedback="Prea vagă.",
            ),
        ))
        set_current_definition(
            clue,
            "Pământ fertil, brun-închis și afânat.",
            round_index=1,
            source="rewrite",
            generated_by="eurollm-22b",
        )
        update_current_assessment(
            clue,
            verified=True,
            scores=ClueScores(
                semantic_exactness=9,
                answer_targeting=8,
                creativity=4,
                rebus_score=7,
            ),
        )
        puzzle = WorkingPuzzle(title="", size=0, grid=[], horizontal_clues=[clue], vertical_clues=[])

        metrics = _collect_word_metrics(puzzle)

        self.assertEqual(1, len(metrics))
        self.assertFalse(metrics[0].initial_verified)
        self.assertTrue(metrics[0].rewrite_attempted)
        self.assertTrue(metrics[0].rewrite_changed_definition)
        self.assertTrue(metrics[0].rewrite_rescued_verify)
        self.assertEqual(3, metrics[0].semantic_delta)
        self.assertEqual(3, metrics[0].rebus_delta)

    def test_compute_difficulty_ignores_rarity(self):
        low_rarity = QualityReport(
            score=100.0,
            word_count=20,
            average_length=5.2,
            average_rarity=0.0,
            two_letter_words=2,
            three_letter_words=4,
            high_rarity_words=0,
            uncommon_letter_words=1,
            friendly_words=10,
            max_rarity=0,
            average_definability=5.0,
        )
        high_rarity = QualityReport(
            score=100.0,
            word_count=20,
            average_length=5.2,
            average_rarity=5.0,
            two_letter_words=2,
            three_letter_words=4,
            high_rarity_words=12,
            uncommon_letter_words=1,
            friendly_words=10,
            max_rarity=5,
            average_definability=5.0,
        )

        self.assertEqual(_compute_difficulty(9, low_rarity), _compute_difficulty(9, high_rarity))

    def test_overnight_loop_sizes_include_fifteen(self):
        from generator.core.size_tuning import OVERNIGHT_LOOP_SIZES

        self.assertEqual((7, 8, 9, 10, 11, 12, 13, 14, 15), OVERNIGHT_LOOP_SIZES)

    def test_working_clue_has_word_type_field(self):
        from generator.core.pipeline_state import WorkingClue
        clue = WorkingClue(row_number=1, word_normalized="LOVI", word_original="lovi")
        self.assertEqual("", clue.word_type)
        clue.word_type = "V"
        self.assertEqual("V", clue.word_type)

    def test_batch_cli_default_rewrite_rounds_is_30(self):
        parser = build_batch_parser()
        args = parser.parse_args([])
        self.assertEqual(MAX_REWRITE_ROUNDS, args.rewrite_rounds)

    def test_plateau_constants(self):
        self.assertEqual(7, PLATEAU_LOOKBACK)
        self.assertEqual(30, MAX_REWRITE_ROUNDS)

    def test_run_batch_loop_builds_rust_binary_before_python(self):
        script = Path("run_batch_loop.sh").read_text(encoding="utf-8")
        self.assertIn("cargo build --release --manifest-path", script)


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


def _prepared_puzzle(
    title: str,
    definition_score: float,
    blocking_words: list[str],
    *,
    verified_count: int = 1,
    total_clues: int = 1,
    min_rebus: int = 8,
    first_passed: int | None = None,
    final_passed: int | None = None,
) -> PreparedPuzzle:
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
        title_score=8,
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
        first_passed=verified_count if first_passed is None else first_passed,
        final_passed=verified_count if final_passed is None else final_passed,
        total=total_clues,
        definition_score=definition_score,
        blocking_words=blocking_words,
        assessment=PuzzleAssessment(
            definition_score=definition_score,
            avg_rebus=8.0,
            min_rebus=min_rebus,
            blocker_words=list(blocking_words),
            verified_count=verified_count,
            total_clues=total_clues,
            pass_rate=(verified_count / total_clues) if total_clues else 0.0,
        ),
    )


if __name__ == "__main__":
    unittest.main()
