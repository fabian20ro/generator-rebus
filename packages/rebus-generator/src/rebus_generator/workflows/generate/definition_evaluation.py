from __future__ import annotations

from openai import OpenAI

from rebus_generator.domain.clue_family import words_share_family
from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.pipeline_state import (
    ClueFailureReason,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    working_clue_from_entry,
)
from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.ai_clues import (
    DefinitionRating,
    RATE_MIN_REBUS,
    RATE_MIN_SEMANTIC,
    combine_definition_ratings,
    compute_rebus_score,
    contains_english_markers,
    rate_definition,
    verify_definition_candidates,
)
from rebus_generator.platform.llm.llm_dispatch import (
    WorkConclusion,
    WorkItem,
    WorkStep,
    WorkVote,
    run_llm_workload,
)
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import get_active_model_labels


def pair_runtime(runtime: LmRuntime | None) -> LmRuntime:
    if runtime is not None and getattr(runtime, "multi_model", True):
        return runtime
    return LmRuntime(multi_model=True)


def pair_labels() -> str:
    return " + ".join(get_active_model_labels(multi_model=True))


def pair_verify_working_puzzle(
    puzzle: WorkingPuzzle,
    client: OpenAI,
    *,
    runtime: LmRuntime | None,
    skip_words: set[str] | None,
    max_guesses: int = VERIFY_CANDIDATE_COUNT,
) -> tuple[int, int]:
    active_runtime = pair_runtime(runtime)
    model_ids, pair_label = run_pair_verify(
        puzzle,
        client,
        runtime=active_runtime,
        skip_words=skip_words,
        max_guesses=max_guesses,
    )
    puzzle.horizontal_clues = finalize_pair_verification(
        puzzle.horizontal_clues,
        model_order=model_ids,
        model_label=pair_label,
    )
    puzzle.vertical_clues = finalize_pair_verification(
        puzzle.vertical_clues,
        model_order=model_ids,
        model_label=pair_label,
    )
    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    passed = sum(1 for clue in all_working_clues(puzzle) if clue.current.assessment.verified)
    return passed, total


def pair_rate_working_puzzle(
    puzzle: WorkingPuzzle,
    client: OpenAI,
    *,
    runtime: LmRuntime | None,
    skip_words: set[str] | None,
    dex: DexProvider | None,
) -> tuple[float, float, int]:
    active_runtime = pair_runtime(runtime)
    model_ids, pair_label = run_pair_rate(
        puzzle,
        client,
        runtime=active_runtime,
        skip_words=skip_words,
        dex=dex,
    )
    finalize_pair_rating(
        puzzle.horizontal_clues,
        model_order=model_ids,
        model_label=pair_label,
    )
    finalize_pair_rating(
        puzzle.vertical_clues,
        model_order=model_ids,
        model_label=pair_label,
    )
    return rating_summary(puzzle)


def rating_summary(puzzle: WorkingPuzzle) -> tuple[float, float, int]:
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


def build_failure_reason(clue: WorkingClue) -> ClueFailureReason | None:
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


def combine_verify_candidates(votes: dict[str, list[str]], model_order: list[str]) -> list[str]:
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


def available_model_order(votes: dict[str, object], model_order: list[str]) -> list[str]:
    return [model_id for model_id in model_order if model_id in votes]


def related_guess_for_candidates(word: str, candidates: list[str]) -> str:
    return next(
        (
            guess for guess in candidates
            if (normalized_guess := normalize(guess)) and words_share_family(word, normalized_guess)
        ),
        "",
    )


def combine_rating_feedback(votes: dict[str, DefinitionRating], model_order: list[str]) -> str:
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


def pair_clues(puzzle: WorkingPuzzle, skip_words: set[str] | None = None) -> list[WorkingClue]:
    clues: list[WorkingClue] = []
    for clue in all_working_clues(puzzle):
        if not isinstance(clue, WorkingClue):
            clue = working_clue_from_entry(clue)
        if skip_words and clue.word_normalized in skip_words:
            continue
        definition = clue.current.definition
        if not definition or definition.startswith("["):
            continue
        clues.append(clue)
    return clues


def verify_runner(client: OpenAI, *, max_guesses: int):
    def _run(item: WorkItem[WorkingClue, list[str]], model) -> WorkVote[list[str]]:
        clue = item.payload
        try:
            result = verify_definition_candidates(
                client,
                clue.current.definition,
                len(clue.word_normalized),
                word_type=clue.word_type,
                max_guesses=max_guesses,
                model=model.model_id,
            )
        except Exception as exc:
            return WorkVote(
                model_id=model.model_id,
                value=[f"[Eroare: {exc}]"],
                source="error",
                terminal=True,
                terminal_reason="verify_error",
            )
        terminal = not result.candidates and result.response_source == "no_thinking_retry"
        return WorkVote(
            model_id=model.model_id,
            value=list(result.candidates),
            source=result.response_source,
            terminal=terminal,
            terminal_reason="verify_empty_after_retry" if terminal else "",
        )

    return _run


def verify_clue_with_model(
    clue: WorkingClue,
    client: OpenAI,
    *,
    model_id: str,
    max_guesses: int,
) -> list[str]:
    vote = verify_runner(client, max_guesses=max_guesses)(
        WorkItem(
            item_id=f"verify:{clue.word_normalized}",
            task_kind="verify",
            payload=clue,
            pending_models={model_id},
        ),
        type("_ModelRef", (), {"model_id": model_id})(),
    )
    candidates = list(vote.value or [])
    clue.current.assessment.verify_votes[model_id] = candidates
    clue.current.assessment.verify_vote_sources[model_id] = vote.source
    return candidates


def verify_conclusion(model_order: list[str]):
    model_set = set(model_order)

    def _conclude(item: WorkItem[WorkingClue, list[str]]) -> WorkConclusion:
        if any(vote.terminal for vote in item.votes.values()):
            return WorkConclusion(
                failed=True,
                skip_models=set(item.pending_models),
                terminal_reason=next(
                    (vote.terminal_reason for vote in item.votes.values() if vote.terminal_reason),
                    "verify_terminal",
                ),
            )
        clue = item.payload
        for model_id, vote in item.votes.items():
            candidates = list(vote.value or [])
            normalized_candidates = [normalize(candidate) for candidate in candidates]
            if clue.word_normalized not in normalized_candidates:
                return WorkConclusion(
                    complete=True,
                    skip_models=model_set - {model_id},
                    terminal_reason="verify_negative",
                )
        if len(item.votes) == len(model_order):
            return WorkConclusion(complete=True)
        return WorkConclusion()

    return _conclude


def rate_runner(client: OpenAI, *, dex: DexProvider | None):
    def _run(item: WorkItem[WorkingClue, DefinitionRating], model) -> WorkVote[DefinitionRating]:
        clue = item.payload
        dex_defs = (dex.get(clue.word_normalized, clue.word_original) if dex else None) or ""
        rating = rate_definition(
            client,
            clue.word_normalized,
            clue.word_original,
            clue.current.definition,
            len(clue.word_normalized),
            word_type=clue.word_type,
            dex_definitions=dex_defs,
            model=model.model_id,
        )
        if rating is None:
            return WorkVote(
                model_id=model.model_id,
                value=None,
                source="parse_error",
            )
        return WorkVote(
            model_id=model.model_id,
            value=rating,
            source=rating.response_source,
        )

    return _run


def rate_clue_with_model(
    clue: WorkingClue,
    client: OpenAI,
    *,
    dex: DexProvider | None,
    model_id: str,
) -> DefinitionRating | None:
    vote = rate_runner(client, dex=dex)(
        WorkItem(
            item_id=f"rate:{clue.word_normalized}",
            task_kind="rate",
            payload=clue,
            pending_models={model_id},
        ),
        type("_ModelRef", (), {"model_id": model_id})(),
    )
    clue.current.assessment.rating_vote_sources[model_id] = vote.source
    if vote.value is not None:
        clue.current.assessment.rating_votes[model_id] = vote.value
    return vote.value


def rate_conclusion(model_order: list[str]):
    def _conclude(item: WorkItem[WorkingClue, DefinitionRating]) -> WorkConclusion:
        if len(item.votes) == len(model_order):
            return WorkConclusion(complete=True)
        return WorkConclusion()

    return _conclude


def run_pair_verify(
    puzzle: WorkingPuzzle,
    client: OpenAI,
    *,
    runtime: LmRuntime,
    skip_words: set[str] | None,
    max_guesses: int,
) -> tuple[list[str], str]:
    models = [runtime.primary, runtime.secondary]
    model_ids = [model.model_id for model in models]
    pair_label = " + ".join(model.display_name for model in models) or pair_labels()
    items = [
        WorkItem[WorkingClue, list[str]](
            item_id=f"verify:{index}:{clue.word_normalized}",
            task_kind="verify",
            payload=clue,
            pending_models=set(model_ids),
        )
        for index, clue in enumerate(pair_clues(puzzle, skip_words), start=1)
    ]
    run_llm_workload(
        runtime=runtime,
        models=models,
        items=items,
        steps=[
            WorkStep(
                model_id=model.model_id,
                purpose="definition_verify",
                runner=verify_runner(client, max_guesses=max_guesses),
                can_conclude=verify_conclusion(model_ids),
            )
            for model in models
        ],
        task_label="definition_verify",
    )
    for item in items:
        clue = item.payload
        clue.current.assessment.verify_votes = {
            model_id: list(vote.value or [])
            for model_id, vote in item.votes.items()
        }
        clue.current.assessment.verify_vote_sources = dict(item.sources)
    return model_ids, pair_label


def run_pair_rate(
    puzzle: WorkingPuzzle,
    client: OpenAI,
    *,
    runtime: LmRuntime,
    skip_words: set[str] | None,
    dex: DexProvider | None,
) -> tuple[list[str], str]:
    models = [runtime.primary, runtime.secondary]
    model_ids = [model.model_id for model in models]
    pair_label = " + ".join(model.display_name for model in models) or pair_labels()
    items = [
        WorkItem[WorkingClue, DefinitionRating](
            item_id=f"rate:{index}:{clue.word_normalized}",
            task_kind="rate",
            payload=clue,
            pending_models=set(model_ids),
        )
        for index, clue in enumerate(pair_clues(puzzle, skip_words), start=1)
    ]
    run_llm_workload(
        runtime=runtime,
        models=models,
        items=items,
        steps=[
            WorkStep(
                model_id=model.model_id,
                purpose="definition_rate",
                runner=rate_runner(client, dex=dex),
                can_conclude=rate_conclusion(model_ids),
            )
            for model in models
        ],
        task_label="definition_rate",
    )
    for item in items:
        clue = item.payload
        clue.current.assessment.rating_votes = {
            model_id: vote.value
            for model_id, vote in item.votes.items()
            if vote.value is not None
        }
        clue.current.assessment.rating_vote_sources = dict(item.sources)
    return model_ids, pair_label


def finalize_pair_verification(
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
        active_order = available_model_order(votes, model_order)
        negative_votes = [
            model_id
            for model_id, candidates in votes.items()
            if clue.word_normalized not in [normalize(candidate) for candidate in candidates]
        ]
        clue.current.assessment.verify_complete = len(votes) == len(model_order) or bool(negative_votes)
        combined_candidates = combine_verify_candidates(votes, model_order)
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

        matched_all = len(votes) == len(model_order) and all(
            clue.word_normalized in [normalize(candidate) for candidate in votes[model_id]]
            for model_id in active_order
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
                for model_id in active_order
                if clue.word_normalized not in [normalize(candidate) for candidate in votes[model_id]]
            ),
            combined_candidates,
        )
        first_guess = failing_candidates[0] if failing_candidates else ""
        related_guess = related_guess_for_candidates(clue.word_normalized, failing_candidates)
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


def finalize_pair_rating(
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
        active_order = available_model_order(votes, model_order)
        clue.current.assessment.rated_by = model_label
        if not active_order:
            mark_pair_rating_incomplete(clue)
            continue

        if len(active_order) == len(model_order):
            first_vote = votes[active_order[0]]
            second_vote = votes[active_order[1]]
            if first_vote is None or second_vote is None:
                mark_pair_rating_incomplete(clue)
                continue
            rating = combine_definition_ratings(first_vote, second_vote)
            clue.current.assessment.rating_complete = True
            clue.current.assessment.rating_resolution = "pair_consensus"
            clue.current.assessment.rating_resolution_models = list(active_order)
            clue.current.assessment.rarity_only_override = (
                first_vote.rarity_only_override and second_vote.rarity_only_override
            )
            resolution_label = "pair"
        else:
            fallback_vote = votes[active_order[0]]
            if fallback_vote is None:
                mark_pair_rating_incomplete(clue)
                continue
            rating = fallback_vote
            clue.current.assessment.rating_complete = True
            clue.current.assessment.rating_resolution = "single_model_fallback"
            clue.current.assessment.rating_resolution_models = list(active_order)
            clue.current.assessment.rarity_only_override = fallback_vote.rarity_only_override
            resolution_label = "single-model fallback"

        feedback = combine_rating_feedback(votes, model_order)
        semantic_score = rating.semantic_score
        guessability_score = rating.guessability_score
        creativity_score = rating.creativity_score
        rebus = compute_rebus_score(guessability_score, creativity_score)
        clue.current.assessment.feedback = feedback
        clue.current.assessment.scores = ClueScores(
            semantic_exactness=semantic_score,
            answer_targeting=guessability_score,
            ambiguity_risk=11 - guessability_score,
            family_leakage=False,
            language_integrity=1 if contains_english_markers(definition) else 10,
            creativity=creativity_score,
            rebus_score=rebus,
        )
        clue.current.assessment.failure_reason = build_failure_reason(clue)

        semantic_ok = semantic_score >= RATE_MIN_SEMANTIC
        rebus_ok = rebus >= RATE_MIN_REBUS
        symbol = "★" if semantic_ok and rebus_ok else "⚠"
        log(
            f"    {symbol} {clue.word_normalized}: "
            f"'{definition}' -> semantic {semantic_score}/10, rebus {rebus}/10"
            f" [{resolution_label}]"
            f" — {feedback or 'fără feedback'}"
        )


def mark_pair_rating_incomplete(clue: WorkingClue) -> None:
    clue.current.assessment.rating_complete = False
    clue.current.assessment.rating_resolution = ""
    clue.current.assessment.rating_resolution_models = []
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
