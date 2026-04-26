"""Phase 6: Verify definitions by asking AI to guess the word, then rate quality."""

from __future__ import annotations

from types import SimpleNamespace
from openai import OpenAI

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.markdown_io import parse_markdown, write_with_definitions
from rebus_generator.platform.llm.llm_client import create_client
from rebus_generator.platform.llm.ai_clues import (
    DefinitionRating,
    RATE_MIN_REBUS,
    RATE_MIN_SEMANTIC,
    compute_rebus_score,
    rate_definition,
    RateDefinitionRequest,
    verify_definition_candidates,
    contains_english_markers,
)
from rebus_generator.domain.clue_family import words_share_family
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.domain.pipeline_state import (
    ClueAssessment,
    ClueFailureReason,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    puzzle_from_working_state,
    working_clue_from_entry,
    working_puzzle_from_puzzle,
)
from rebus_generator.domain.diacritics import normalize
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.workflows.generate import definition_evaluation


def _build_failure_reason(clue: WorkingClue) -> ClueFailureReason | None:
    assessment = clue.current.assessment
    if assessment.scores.family_leakage:
        return ClueFailureReason("same_family", "Definiția folosește aceeași familie lexicală.")
    if assessment.form_mismatch:
        return ClueFailureReason(
            "related_form",
            assessment.form_mismatch_detail or "Definiția duce la o altă formă a aceluiași cuvânt.",
        )
    if assessment.wrong_guess:
        return ClueFailureReason("wrong_guess", f"AI a ghicit: {assessment.wrong_guess}")
    if assessment.scores.semantic_exactness is not None and assessment.scores.semantic_exactness < RATE_MIN_SEMANTIC:
        return ClueFailureReason("low_semantic", "Definiția nu este suficient de exactă.")
    if assessment.scores.rebus_score is not None and assessment.scores.rebus_score < RATE_MIN_REBUS:
        return ClueFailureReason("low_targeting", "Definiția duce spre alt răspuns sau este prea vagă.")
    if assessment.feedback:
        return ClueFailureReason("feedback", assessment.feedback)
    return None


def _model_vote_key(model_name: str | None, model_label: str) -> str:
    return str(model_name or model_label or "").strip()


def _verify_clues(
    clues: list[WorkingClue],
    client: OpenAI,
    skip_words: set[str] | None = None,
    *,
    model_label: str = "",
    model_name: str | None = None,
    max_guesses: int = VERIFY_CANDIDATE_COUNT,
    store_vote_only: bool = False,
) -> list[WorkingClue]:
    """Verify each clue by asking AI to guess the word."""
    result = []
    for clue in clues:
        if not isinstance(clue, WorkingClue):
            clue = working_clue_from_entry(clue)
        if skip_words and clue.word_normalized in skip_words:
            result.append(clue)
            continue
        definition = clue.current.definition
        if not definition or definition.startswith("["):
            clue.current.assessment = ClueAssessment(
                verified=False,
                feedback="Definiție lipsă",
                failure_reason=ClueFailureReason("missing_definition", "Definiție lipsă"),
                verified_by=model_label,
                verify_complete=True,
                rating_complete=True,
                scores=ClueScores(
                    semantic_exactness=1,
                    answer_targeting=1,
                    ambiguity_risk=10,
                    family_leakage=False,
                    language_integrity=10,
                    creativity=1,
                    rebus_score=1,
                ),
            )
            result.append(clue)
            continue

        log(f"  Verifying: {clue.word_normalized} - {definition[:50]}...")
        try:
            verify_kwargs = dict(
                word_type=clue.word_type,
                max_guesses=max_guesses,
            )
            if model_name is not None:
                verify_kwargs["model"] = model_name
            verify_result = verify_definition_candidates(
                client,
                definition,
                len(clue.word_normalized),
                **verify_kwargs,
            )
        except Exception as e:
            verify_result = None
            guess_candidates = [f"[Eroare: {e}]"]
        else:
            guess_candidates = verify_result.candidates
        normalized_candidates = [normalize(guess) for guess in guess_candidates]
        matched = clue.word_normalized in normalized_candidates
        vote_key = _model_vote_key(model_name, model_label)

        if store_vote_only:
            if vote_key:
                clue.current.assessment.verify_votes[vote_key] = guess_candidates
                clue.current.assessment.verify_vote_sources[vote_key] = (
                    verify_result.response_source if verify_result is not None else "error"
                )
            result.append(clue)
            continue

        clue.current.assessment.verify_candidates = guess_candidates
        if matched:
            clue.current.assessment.verified = True
            clue.current.assessment.wrong_guess = ""
            clue.current.assessment.form_mismatch = False
            clue.current.assessment.form_mismatch_detail = ""
            clue.current.assessment.verified_by = model_label
            clue.current.assessment.failure_reason = None
            log(f"    ✓ AI a inclus răspunsul corect: {', '.join(guess_candidates)}")
        else:
            first_guess = guess_candidates[0] if guess_candidates else ""
            related_guess = next(
                (
                    guess for guess, normalized_guess in zip(guess_candidates, normalized_candidates)
                    if normalized_guess and words_share_family(clue.word_normalized, normalized_guess)
                ),
                "",
            )
            related_form = bool(related_guess)
            clue.current.assessment.verified = False
            clue.current.assessment.wrong_guess = first_guess
            clue.current.assessment.form_mismatch = related_form
            clue.current.assessment.form_mismatch_detail = (
                f"AI a ghicit o formă înrudită: {related_guess}" if related_form else ""
            )
            clue.current.assessment.verified_by = model_label
            clue.current.assessment.failure_reason = ClueFailureReason(
                "related_form" if related_form else "wrong_guess",
                (
                    clue.current.assessment.form_mismatch_detail
                    if related_form
                    else f"AI a propus: {', '.join(guess_candidates)}"
                ),
            )
            log(
                f"    ✗ AI a propus: {', '.join(guess_candidates) or '[nimic]'} "
                f"(expected: {clue.word_normalized})"
            )

        result.append(clue)

    return result


def _rate_clues(
    clues: list[WorkingClue],
    client: OpenAI,
    skip_words: set[str] | None = None,
    dex: DexProvider | None = None,
    *,
    model_label: str = "",
    model_name: str | None = None,
    store_vote_only: bool = False,
) -> None:
    """Rate each usable clue definition quality in-place."""
    for clue in clues:
        if not isinstance(clue, WorkingClue):
            clue = working_clue_from_entry(clue)
        if skip_words and clue.word_normalized in skip_words:
            continue
        definition = clue.current.definition
        if not definition or definition.startswith("["):
            continue

        dex_defs = (dex.get(clue.word_normalized, clue.word_original) if dex else None) or ""
        try:
            req = RateDefinitionRequest(
                word=clue.word_normalized,
                original=clue.word_original,
                definition=definition,
                answer_length=len(clue.word_normalized),
                word_type=clue.word_type,
                dex_definitions=dex_defs,
            )
            rating = rate_definition(
                client,
                req,
                model=model_name,
            )
        except Exception:
            rating = None

        vote_key = _model_vote_key(model_name, model_label)
        if store_vote_only:
            if rating is not None and vote_key:
                clue.current.assessment.rating_votes[vote_key] = rating
                clue.current.assessment.rating_vote_sources[vote_key] = rating.response_source
            continue

        if rating is None:
            # Unrated — use None scores so the word enters the rewrite queue
            clue.current.assessment.feedback = ""
            clue.current.assessment.rarity_only_override = False
            clue.current.assessment.rated_by = model_label
            clue.current.assessment.rating_resolution = ""
            clue.current.assessment.rating_resolution_models = []
            clue.current.assessment.scores = ClueScores(
                semantic_exactness=None,
                answer_targeting=None,
                ambiguity_risk=None,
                family_leakage=False,
                language_integrity=10,
                creativity=None,
                rebus_score=None,
            )
            clue.current.assessment.failure_reason = ClueFailureReason(
                "unrated", "Evaluarea nu a putut fi parsată (JSON invalid).",
            )
            log(f"    ⚠ {clue.word_normalized}: evaluare eșuată (JSON invalid)")
            continue
        semantic_score = rating.semantic_score
        guessability_score = rating.guessability_score
        creativity_score = rating.creativity_score
        feedback = rating.feedback
        rarity_override = rating.rarity_only_override
        rebus = compute_rebus_score(guessability_score, creativity_score)
        clue.current.assessment.feedback = feedback
        clue.current.assessment.rarity_only_override = rarity_override
        clue.current.assessment.rated_by = model_label
        clue.current.assessment.rating_resolution = "single_model_direct"
        clue.current.assessment.rating_resolution_models = [vote_key] if vote_key else []
        clue.current.assessment.scores = ClueScores(
            semantic_exactness=semantic_score,
            answer_targeting=guessability_score,
            ambiguity_risk=11 - guessability_score,
            family_leakage=False,
            language_integrity=1 if contains_english_markers(definition) else 10,
            creativity=creativity_score,
            rebus_score=rebus,
        )
        clue.current.assessment.failure_reason = _build_failure_reason(clue)

        semantic_ok = semantic_score >= RATE_MIN_SEMANTIC
        rebus_ok = rebus >= RATE_MIN_REBUS
        symbol = "★" if semantic_ok and rebus_ok else "⚠"
        log(
            f"    {symbol} {clue.word_normalized}: "
            f"'{definition}' -> "
            f"semantic {semantic_score}/10, rebus {rebus}/10"
            f" — {feedback or 'fără feedback'}"
        )


def verify_working_puzzle(
    puzzle: WorkingPuzzle,
    client: OpenAI,
    skip_words: set[str] | None = None,
    *,
    runtime: LmRuntime | None = None,
    model_label: str = "",
    model_name: str | None = None,
    max_guesses: int = VERIFY_CANDIDATE_COUNT,
) -> tuple[int, int]:
    """Verify all clue definitions in-place and return (passed, total)."""
    if model_name is not None:
        log("Verifying horizontal definitions...")
        puzzle.horizontal_clues = _verify_clues(
            puzzle.horizontal_clues,
            client,
            skip_words=skip_words,
            model_label=model_label,
            model_name=model_name,
            max_guesses=max_guesses,
        )

        log("Verifying vertical definitions...")
        puzzle.vertical_clues = _verify_clues(
            puzzle.vertical_clues,
            client,
            skip_words=skip_words,
            model_label=model_label,
            model_name=model_name,
            max_guesses=max_guesses,
        )
    else:
        return definition_evaluation.pair_verify_working_puzzle(
            puzzle,
            client,
            runtime=runtime,
            skip_words=skip_words,
            max_guesses=max_guesses,
        )

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    passed = sum(1 for c in all_working_clues(puzzle) if c.current.assessment.verified)
    return passed, total


def rate_working_puzzle(
    puzzle: WorkingPuzzle,
    client: OpenAI,
    skip_words: set[str] | None = None,
    dex: DexProvider | None = None,
    *,
    runtime: LmRuntime | None = None,
    model_label: str = "",
    model_name: str | None = None,
) -> tuple[float, float, int]:
    """Rate all usable definitions in-place."""
    if model_name is not None:
        log("Rating horizontal definitions...")
        _rate_clues(
            puzzle.horizontal_clues, client, skip_words=skip_words, dex=dex, model_label=model_label, model_name=model_name,
        )

        log("Rating vertical definitions...")
        _rate_clues(
            puzzle.vertical_clues, client, skip_words=skip_words, dex=dex, model_label=model_label, model_name=model_name,
        )
    else:
        return definition_evaluation.pair_rate_working_puzzle(
            puzzle,
            client,
            runtime=runtime,
            skip_words=skip_words,
            dex=dex,
        )

    semantic_scores = []
    guessability_scores = []
    for clue in all_working_clues(puzzle):
        semantic_score = clue.current.assessment.scores.semantic_exactness
        guessability_score = clue.current.assessment.scores.answer_targeting
        if semantic_score is None or guessability_score is None:
            continue
        semantic_scores.append(semantic_score)
        guessability_scores.append(guessability_score)

    avg_semantic = sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0
    avg_guessability = (
        sum(guessability_scores) / len(guessability_scores) if guessability_scores else 0.0
    )
    return avg_semantic, avg_guessability, len(semantic_scores)


def verify_puzzle(puzzle, client: OpenAI, *, max_guesses: int = VERIFY_CANDIDATE_COUNT) -> tuple[int, int]:
    state = working_puzzle_from_puzzle(puzzle, split_compound=False)
    runtime = LmRuntime(multi_model=True)
    passed, total = verify_working_puzzle(
        state,
        client,
        runtime=runtime,
        max_guesses=max_guesses,
    )
    rendered = puzzle_from_working_state(state)
    puzzle.horizontal_clues = rendered.horizontal_clues
    puzzle.vertical_clues = rendered.vertical_clues
    return passed, total


def rate_puzzle(puzzle, client: OpenAI) -> tuple[float, float, int]:
    state = working_puzzle_from_puzzle(puzzle, split_compound=False)
    dex = DexProvider.for_puzzle(state)
    runtime = LmRuntime(multi_model=True)
    avg_semantic, avg_guessability, rated = rate_working_puzzle(
        state,
        client,
        dex=dex,
        runtime=runtime,
    )
    rendered = puzzle_from_working_state(state)
    puzzle.horizontal_clues = rendered.horizontal_clues
    puzzle.vertical_clues = rendered.vertical_clues
    return avg_semantic, avg_guessability, rated


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Verify all definitions by AI guessing, then rate quality."""
    log(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = create_client()
    state = working_puzzle_from_puzzle(puzzle, split_compound=False)
    dex = DexProvider.for_puzzle(state)
    max_guesses = max(1, int(kwargs.get("verify_candidates", VERIFY_CANDIDATE_COUNT)))
    runtime = LmRuntime(multi_model=True)
    passed, total = verify_working_puzzle(
        state,
        client,
        runtime=runtime,
        max_guesses=max_guesses,
    )
    avg_semantic, avg_guessability, rated = rate_working_puzzle(
        state,
        client,
        dex=dex,
        runtime=runtime,
    )
    puzzle = puzzle_from_working_state(state)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    log(
        f"Verification: {passed}/{total} passed. "
        f"Avg semantic: {avg_semantic:.1f}/10. "
        f"Avg rebus: {avg_guessability:.1f}/10. "
        f"({rated} rated). Saved to {output_file}"
    )
