"""Phase 6: Verify definitions by asking AI to guess the word, then rate quality."""

from __future__ import annotations

from openai import OpenAI

from ..config import VERIFY_CANDIDATE_COUNT
from ..core.markdown_io import parse_markdown, write_with_definitions
from ..core.llm_client import create_client
from ..core.ai_clues import (
    DefinitionRating,
    RATE_MIN_REBUS,
    RATE_MIN_SEMANTIC,
    combine_definition_ratings,
    compute_rebus_score,
    rate_definition,
    verify_definition_candidates,
    contains_english_markers,
)
from ..core.clue_family import words_share_family
from ..core.dex_cache import DexProvider
from ..core.lm_runtime import LmRuntime
from ..core.model_manager import get_active_model_labels
from ..core.pipeline_state import (
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
from ..core.diacritics import normalize
from ..core.runtime_logging import log


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


def _pair_runtime(runtime: LmRuntime | None) -> LmRuntime:
    if runtime is not None and getattr(runtime, "multi_model", True):
        return runtime
    return LmRuntime(multi_model=True)


def _pair_labels() -> str:
    return " + ".join(get_active_model_labels(multi_model=True))


def _combine_verify_candidates(votes: dict[str, list[str]], model_order: list[str]) -> list[str]:
    combined: list[str] = []
    seen: set[str] = set()
    for model_id in model_order:
        for candidate in votes.get(model_id, []):
            normalized = normalize(candidate)
            key = normalized or candidate
            if key in seen:
                continue
            seen.add(key)
            combined.append(candidate)
    return combined


def _related_guess_for_candidates(word: str, candidates: list[str]) -> str:
    return next(
        (
            guess for guess in candidates
            if (normalized_guess := normalize(guess)) and words_share_family(word, normalized_guess)
        ),
        "",
    )


def _combine_rating_feedback(votes: dict[str, DefinitionRating], model_order: list[str]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for model_id in model_order:
        rating = votes.get(model_id)
        if rating is None:
            continue
        feedback = str(rating.feedback or "").strip()
        if not feedback:
            continue
        key = normalize(feedback)
        if key in seen:
            continue
        seen.add(key)
        parts.append(feedback)
    return " / ".join(parts)


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
            rating = rate_definition(
                client,
                clue.word_normalized,
                clue.word_original,
                definition,
                len(clue.word_normalized),
                word_type=clue.word_type,
                dex_definitions=dex_defs,
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


def _finalize_pair_verification(
    clues: list[WorkingClue],
    *,
    model_order: list[str],
    model_label: str,
) -> list[WorkingClue]:
    result: list[WorkingClue] = []
    for clue in clues:
        if not isinstance(clue, WorkingClue):
            clue = working_clue_from_entry(clue)
        definition = clue.current.definition
        if not definition or definition.startswith("["):
            result.append(clue)
            continue

        votes = {
            model_id: list(clue.current.assessment.verify_votes.get(model_id, []))
            for model_id in model_order
            if model_id in clue.current.assessment.verify_votes
        }
        clue.current.assessment.verify_complete = len(votes) == len(model_order)
        combined_candidates = _combine_verify_candidates(votes, model_order)
        clue.current.assessment.verify_candidates = combined_candidates
        clue.current.assessment.verified_by = model_label

        if not clue.current.assessment.verify_complete:
            clue.current.assessment.verified = False
            clue.current.assessment.wrong_guess = combined_candidates[0] if combined_candidates else ""
            clue.current.assessment.form_mismatch = False
            clue.current.assessment.form_mismatch_detail = ""
            clue.current.assessment.failure_reason = ClueFailureReason(
                "incomplete_pair",
                "Verificarea în pereche este incompletă.",
            )
            result.append(clue)
            continue

        matched_all = all(
            clue.word_normalized in [normalize(candidate) for candidate in votes[model_id]]
            for model_id in model_order
        )
        if matched_all:
            clue.current.assessment.verified = True
            clue.current.assessment.wrong_guess = ""
            clue.current.assessment.form_mismatch = False
            clue.current.assessment.form_mismatch_detail = ""
            clue.current.assessment.failure_reason = None
            log(f"    ✓ {clue.word_normalized}: ambele modele au inclus răspunsul corect")
            result.append(clue)
            continue

        failing_candidates = next(
            (
                votes[model_id]
                for model_id in model_order
                if clue.word_normalized not in [normalize(candidate) for candidate in votes[model_id]]
            ),
            combined_candidates,
        )
        first_guess = failing_candidates[0] if failing_candidates else ""
        related_guess = _related_guess_for_candidates(clue.word_normalized, failing_candidates)
        related_form = bool(related_guess)
        clue.current.assessment.verified = False
        clue.current.assessment.wrong_guess = first_guess
        clue.current.assessment.form_mismatch = related_form
        clue.current.assessment.form_mismatch_detail = (
            f"AI a ghicit o formă înrudită: {related_guess}" if related_form else ""
        )
        clue.current.assessment.failure_reason = ClueFailureReason(
            "related_form" if related_form else "wrong_guess",
            (
                clue.current.assessment.form_mismatch_detail
                if related_form
                else f"AI a propus: {', '.join(combined_candidates)}"
            ),
        )
        log(
            f"    ✗ {clue.word_normalized}: perechea a propus "
            f"{', '.join(combined_candidates) or '[nimic]'}"
        )
        result.append(clue)

    return result


def _finalize_pair_rating(
    clues: list[WorkingClue],
    *,
    model_order: list[str],
    model_label: str,
) -> None:
    for clue in clues:
        if not isinstance(clue, WorkingClue):
            clue = working_clue_from_entry(clue)
        definition = clue.current.definition
        if not definition or definition.startswith("["):
            continue

        votes = {
            model_id: clue.current.assessment.rating_votes.get(model_id)
            for model_id in model_order
            if model_id in clue.current.assessment.rating_votes
        }
        clue.current.assessment.rating_complete = len(votes) == len(model_order)
        clue.current.assessment.rated_by = model_label

        if not clue.current.assessment.rating_complete:
            clue.current.assessment.feedback = ""
            clue.current.assessment.rarity_only_override = False
            clue.current.assessment.scores = ClueScores(
                semantic_exactness=None,
                answer_targeting=None,
                ambiguity_risk=None,
                family_leakage=False,
                language_integrity=10,
                creativity=None,
                rebus_score=None,
            )
            if clue.current.assessment.failure_reason is None:
                clue.current.assessment.failure_reason = ClueFailureReason(
                    "unrated",
                    "Evaluarea în pereche este incompletă.",
                )
            log(f"    ⚠ {clue.word_normalized}: evaluare în pereche incompletă")
            continue

        first_vote = votes[model_order[0]]
        second_vote = votes[model_order[1]]
        if first_vote is None or second_vote is None:
            clue.current.assessment.rating_complete = False
            clue.current.assessment.feedback = ""
            clue.current.assessment.rarity_only_override = False
            clue.current.assessment.scores = ClueScores(
                semantic_exactness=None,
                answer_targeting=None,
                ambiguity_risk=None,
                family_leakage=False,
                language_integrity=10,
                creativity=None,
                rebus_score=None,
            )
            if clue.current.assessment.failure_reason is None:
                clue.current.assessment.failure_reason = ClueFailureReason(
                    "unrated",
                    "Evaluarea în pereche este incompletă.",
                )
            log(f"    ⚠ {clue.word_normalized}: evaluare în pereche incompletă")
            continue

        rating = combine_definition_ratings(first_vote, second_vote)
        feedback = _combine_rating_feedback(votes, model_order)
        semantic_score = rating.semantic_score
        guessability_score = rating.guessability_score
        creativity_score = rating.creativity_score
        rebus = compute_rebus_score(guessability_score, creativity_score)
        clue.current.assessment.feedback = feedback
        clue.current.assessment.rarity_only_override = (
            first_vote.rarity_only_override and second_vote.rarity_only_override
        )
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
            f"'{definition}' -> semantic {semantic_score}/10, rebus {rebus}/10"
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
        pair_runtime = _pair_runtime(runtime)
        active_models = [pair_runtime.activate_primary(), pair_runtime.activate_secondary()]
        model_ids = [model.model_id for model in active_models]
        pair_label = " + ".join(model.display_name for model in active_models) or _pair_labels()
        for model in active_models:
            log(f"Verifying horizontal definitions [{model.display_name}]...")
            puzzle.horizontal_clues = _verify_clues(
                puzzle.horizontal_clues,
                client,
                skip_words=skip_words,
                model_label=model.display_name,
                model_name=model.model_id,
                max_guesses=max_guesses,
                store_vote_only=True,
            )
            log(f"Verifying vertical definitions [{model.display_name}]...")
            puzzle.vertical_clues = _verify_clues(
                puzzle.vertical_clues,
                client,
                skip_words=skip_words,
                model_label=model.display_name,
                model_name=model.model_id,
                max_guesses=max_guesses,
                store_vote_only=True,
            )
        puzzle.horizontal_clues = _finalize_pair_verification(
            puzzle.horizontal_clues,
            model_order=model_ids,
            model_label=pair_label,
        )
        puzzle.vertical_clues = _finalize_pair_verification(
            puzzle.vertical_clues,
            model_order=model_ids,
            model_label=pair_label,
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
        pair_runtime = _pair_runtime(runtime)
        active_models = [pair_runtime.activate_primary(), pair_runtime.activate_secondary()]
        model_ids = [model.model_id for model in active_models]
        pair_label = " + ".join(model.display_name for model in active_models) or _pair_labels()
        for model in active_models:
            log(f"Rating horizontal definitions [{model.display_name}]...")
            _rate_clues(
                puzzle.horizontal_clues,
                client,
                skip_words=skip_words,
                dex=dex,
                model_label=model.display_name,
                model_name=model.model_id,
                store_vote_only=True,
            )
            log(f"Rating vertical definitions [{model.display_name}]...")
            _rate_clues(
                puzzle.vertical_clues,
                client,
                skip_words=skip_words,
                dex=dex,
                model_label=model.display_name,
                model_name=model.model_id,
                store_vote_only=True,
            )
        _finalize_pair_rating(
            puzzle.horizontal_clues,
            model_order=model_ids,
            model_label=pair_label,
        )
        _finalize_pair_rating(
            puzzle.vertical_clues,
            model_order=model_ids,
            model_label=pair_label,
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
