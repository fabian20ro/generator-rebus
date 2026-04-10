from __future__ import annotations

import copy
from pathlib import Path

from rebus_generator.platform.io.markdown_io import write_grid_template, write_with_definitions
from rebus_generator.platform.io.metrics import PuzzleMetric, WordMetric
from rebus_generator.platform.io.rust_bridge import _template_fingerprint
from rebus_generator.platform.llm.models import get_active_model_labels
from rebus_generator.domain.pipeline_state import ClueAssessment, all_working_clues, puzzle_from_working_state
from rebus_generator.domain.puzzle_metrics import build_puzzle_description, puzzle_metadata_payload
from rebus_generator.domain.score_helpers import _needs_rewrite
from rebus_generator.workflows.generate.activate import set_published
from rebus_generator.workflows.generate.upload import upload_puzzle

from .models import PreparedPuzzle
from .quality_gate import _compute_difficulty


def _collect_word_metrics(puzzle) -> list[WordMetric]:
    metrics = []
    for clue in all_working_clues(puzzle):
        initial_version = clue.history[0] if clue.history else clue.current
        version = clue.active_version()
        failure_reason = version.assessment.failure_reason
        semantic = version.assessment.scores.semantic_exactness
        targeting = version.assessment.scores.answer_targeting
        creativity = version.assessment.scores.creativity
        rebus = version.assessment.scores.rebus_score
        initial_semantic = initial_version.assessment.scores.semantic_exactness
        initial_rebus = initial_version.assessment.scores.rebus_score
        rewrite_attempted = any(v.round_index > 0 for v in clue.history)
        metrics.append(
            WordMetric(
                word=clue.word_normalized,
                length=len(clue.word_normalized),
                word_type=clue.word_type,
                definition_rounds=len(clue.history),
                initial_verified=initial_version.assessment.verified,
                final_verified=version.assessment.verified is True,
                semantic_score=semantic,
                guessability_score=targeting,
                creativity_score=creativity,
                rebus_score=rebus,
                semantic_delta=(semantic - initial_semantic)
                if semantic is not None and initial_semantic is not None
                else None,
                rebus_delta=(rebus - initial_rebus)
                if rebus is not None and initial_rebus is not None
                else None,
                rewrite_attempted=rewrite_attempted,
                rewrite_changed_definition=version.definition != initial_version.definition,
                rewrite_rescued_verify=(
                    initial_version.assessment.verified is False
                    and version.assessment.verified is True
                ),
                was_blocker=_needs_rewrite(clue),
                english_meaning_detected=False,
                wrong_guess=version.assessment.wrong_guess,
                verify_candidates=list(version.assessment.verify_candidates),
                failure_kind=failure_reason.kind if failure_reason else "",
                failure_message=failure_reason.message if failure_reason else "",
                rarity_only_override=version.assessment.rarity_only_override,
                form_mismatch=version.assessment.form_mismatch,
                form_mismatch_detail=version.assessment.form_mismatch_detail,
                model_generated=version.generated_by,
                model_verified=version.assessment.verified_by,
                model_rated=version.assessment.rated_by,
            )
        )
    return metrics


def _clear_verification_state(puzzle):
    clean = copy.deepcopy(puzzle)
    for clue in all_working_clues(clean):
        clue.active_version().assessment = ClueAssessment()
    return clean


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def publish_prepared_puzzle(
    *,
    prepared: PreparedPuzzle,
    index: int,
    total_puzzles: int,
    size: int,
    puzzle_dir: Path,
    multi_model: bool,
) -> tuple[dict[str, object], PuzzleMetric, list[WordMetric]]:
    template_path = puzzle_dir / "template.md"
    filled_path = puzzle_dir / "filled.md"
    rendered_puzzle = puzzle_from_working_state(prepared.puzzle)
    _write_text(template_path, write_grid_template(size, prepared.candidate.template))
    _write_text(filled_path, write_with_definitions(rendered_puzzle))

    defs_puzzle = _clear_verification_state(prepared.puzzle)
    defs_path = puzzle_dir / "defs.md"
    verified_path = puzzle_dir / "verified.md"
    _write_text(defs_path, write_with_definitions(puzzle_from_working_state(defs_puzzle)))
    _write_text(verified_path, write_with_definitions(rendered_puzzle))

    non_preset_rebus = [
        c.active_version().assessment.scores.rebus_score or 0
        for c in all_working_clues(prepared.puzzle)
    ]
    min_rebus = min(non_preset_rebus) if non_preset_rebus else 10
    models_used_desc = get_active_model_labels(multi_model=multi_model)
    description = build_puzzle_description(prepared.assessment, models_used_desc)
    difficulty = _compute_difficulty(size, prepared.candidate.report)
    puzzle_id = upload_puzzle(
        puzzle_from_working_state(defs_puzzle),
        difficulty=difficulty,
        description=description,
        metadata={
            **puzzle_metadata_payload(prepared.assessment, description=description),
            "title_score": prepared.title_score,
        },
    )
    set_published(puzzle_id, True)

    word_metrics = _collect_word_metrics(prepared.puzzle)
    clues = all_working_clues(prepared.puzzle)
    rewrite_attempted_words = sum(1 for wm in word_metrics if wm.rewrite_attempted)
    rewrite_changed_words = sum(1 for wm in word_metrics if wm.rewrite_changed_definition)
    rewrite_rescued_words = sum(1 for wm in word_metrics if wm.rewrite_rescued_verify)
    semantic_scores = [c.active_version().assessment.scores.semantic_exactness or 0 for c in clues]
    guess_scores = [c.active_version().assessment.scores.answer_targeting or 0 for c in clues]
    rebus_scores = [c.active_version().assessment.scores.rebus_score or 0 for c in clues]
    creativity_scores = [c.active_version().assessment.scores.creativity or 0 for c in clues]
    phase1_elapsed_ms = int(prepared.candidate.stats.get("elapsed_ms", 0) or 0)
    puzzle_metric = PuzzleMetric(
        size=size,
        fill_elapsed_ms=phase1_elapsed_ms,
        word_count=len(clues),
        avg_word_length=prepared.candidate.report.average_length,
        avg_rarity=prepared.candidate.report.average_rarity,
        definition_first_pass_rate=prepared.first_passed / prepared.total if prepared.total else 0.0,
        definition_final_pass_rate=prepared.final_passed / prepared.total if prepared.total else 0.0,
        avg_semantic=sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0,
        avg_guessability=sum(guess_scores) / len(guess_scores) if guess_scores else 0.0,
        avg_creativity=sum(creativity_scores) / len(creativity_scores) if creativity_scores else 0.0,
        avg_rebus=sum(rebus_scores) / len(rebus_scores) if rebus_scores else 0.0,
        min_rebus=min_rebus,
        rewrite_attempted_words=rewrite_attempted_words,
        rewrite_changed_words=rewrite_changed_words,
        rewrite_rescued_words=rewrite_rescued_words,
        blocker_count=len(prepared.blocking_words),
        blocker_words=prepared.blocking_words,
        model_switches=int(prepared.puzzle.metadata.get("rewrite_model_switches", 0) or 0),
        total_elapsed_ms=0,
    )
    manifest_item = {
        "index": index,
        "size": size,
        "title": prepared.title,
        "puzzle_id": puzzle_id,
        "score": prepared.candidate.score,
        "quality": prepared.candidate.report.to_dict(),
        "phase1_stats": prepared.candidate.stats,
        "verification_first_passed": prepared.first_passed,
        "verification_final_passed": prepared.final_passed,
        "verification_passed": prepared.final_passed,
        "verification_total": prepared.total,
        "output_dir": str(puzzle_dir),
        "template_path": str(template_path),
        "template_fingerprint": _template_fingerprint(prepared.candidate.template),
        "puzzle_index": index,
        "puzzle_total": total_puzzles,
    }
    return manifest_item, puzzle_metric, word_metrics
