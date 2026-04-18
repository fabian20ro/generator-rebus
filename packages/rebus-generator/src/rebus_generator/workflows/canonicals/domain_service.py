"""Pure helpers for canonical clue deduplication and comparison."""

from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
from itertools import combinations
import re

from rebus_generator.domain.clue_canon_ranking import canonical_reset_safe_sort_key
from rebus_generator.domain.selection_engine import stable_tie_rng
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.domain.clue_canon_types import (
    BackfillStats,
    CanonicalDecision,
    CanonicalDefinition,
    ClueDefinitionRecord,
    DefinitionRefereeInput,
    DefinitionRefereeResult,
    NearDuplicateCandidate,
)
from rebus_generator.domain.diacritics import normalize
from rebus_generator.platform.io.runtime_logging import audit


ROMANIAN_STOPWORDS = {
    "a", "ai", "al", "ale", "au", "ca", "care", "cu", "de", "din", "este", "fi",
    "in", "intr", "intrun", "intrunul", "la", "o", "pe", "pentru", "prin", "sau",
    "si", "spre", "un", "una", "unei", "unor", "unui", "cea", "cel", "cei", "cele",
}
_USAGE_LABEL_RE = re.compile(r"\((?:arh|inv|reg|tehn|pop|fam|arg|livr)\.\)", flags=re.IGNORECASE)


def normalize_definition_text(text: str) -> str:
    tokens = tokenize_definition(text)
    return " ".join(tokens)


def tokenize_definition(text: str) -> list[str]:
    normalized = normalize(text or "").lower()
    return [token for token in __import__("re").findall(r"[a-z0-9]+", normalized) if token]


def content_tokens(text: str) -> list[str]:
    return [token for token in tokenize_definition(text) if token not in ROMANIAN_STOPWORDS]


def build_definition_record(row: dict) -> ClueDefinitionRecord:
    definition = (row.get("definition") or "").strip()
    return ClueDefinitionRecord(
        id=str(row.get("id") or ""),
        word_normalized=str(row.get("word_normalized") or ""),
        word_original=str(row.get("word_original") or ""),
        definition=definition,
        definition_norm=normalize_definition_text(definition),
        word_type=str(row.get("word_type") or ""),
        usage_label=str(row.get("usage_label") or _extract_usage_label(definition)),
        verified=bool(row.get("verified")),
        semantic_score=_to_int(row.get("semantic_score")),
        rebus_score=_to_int(row.get("rebus_score")),
        creativity_score=_to_int(row.get("creativity_score")),
        verify_note=str(row.get("verify_note") or ""),
        canonical_definition_id=row.get("canonical_definition_id"),
    )


def choose_canonical_winner(rows: list[ClueDefinitionRecord]) -> ClueDefinitionRecord:
    if not rows:
        raise ValueError("rows must not be empty")
    return sorted(rows, key=_canonical_sort_key)[0]


def build_exact_groups(rows: list[ClueDefinitionRecord]) -> list[list[ClueDefinitionRecord]]:
    grouped: dict[tuple[str, str, str, str], list[ClueDefinitionRecord]] = defaultdict(list)
    for row in rows:
        grouped[(row.word_normalized, row.word_type, row.usage_label, row.definition_norm)].append(row)
    return list(grouped.values())


def generate_near_duplicate_candidates(rows: list[ClueDefinitionRecord]) -> list[NearDuplicateCandidate]:
    if len(rows) < 2:
        return []

    indexed: dict[str, set[int]] = defaultdict(set)
    for index, row in enumerate(rows):
        for token in set(content_tokens(row.definition)):
            indexed[token].add(index)

    pair_support: Counter[tuple[int, int]] = Counter()
    for indexes in indexed.values():
        if len(indexes) < 2:
            continue
        for left, right in combinations(sorted(indexes), 2):
            pair_support[(left, right)] += 1

    candidates: list[NearDuplicateCandidate] = []
    seen: set[tuple[int, int]] = set()
    for (left_index, right_index), shared_tokens in pair_support.items():
        if (left_index, right_index) in seen:
            continue
        left = rows[left_index]
        right = rows[right_index]
        if left.word_normalized != right.word_normalized:
            continue
        similarity = lexical_similarity(left.definition_norm, right.definition_norm)
        if shared_tokens < 2 and similarity < 0.82:
            continue
        if shared_tokens < 1 and similarity < 0.9:
            continue
        seen.add((left_index, right_index))
        candidates.append(
            NearDuplicateCandidate(
                left=left,
                right=right,
                shared_tokens=shared_tokens,
                similarity=similarity,
            )
        )
    candidates.sort(
        key=lambda candidate: (
            candidate.left.word_normalized,
            -candidate.shared_tokens,
            -candidate.similarity,
            candidate.left.id,
            candidate.right.id,
        )
    )
    return candidates


def lexical_similarity(left_norm: str, right_norm: str) -> float:
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(a=left_norm, b=right_norm).ratio()


def _extract_usage_label(definition: str) -> str:
    matches = _USAGE_LABEL_RE.findall(definition or "")
    if not matches:
        return ""
    return matches[-1].lower()


def aggregate_referee_votes(votes) -> DefinitionRefereeResult:
    same_meaning_votes = sum(1 for vote in votes if vote.same_meaning)
    better_a_votes = sum(1 for vote in votes if vote.better == "A")
    better_b_votes = sum(1 for vote in votes if vote.better == "B")
    equal_votes = sum(1 for vote in votes if vote.better == "equal")
    return DefinitionRefereeResult(
        same_meaning_votes=same_meaning_votes,
        better_a_votes=better_a_votes,
        better_b_votes=better_b_votes,
        equal_votes=equal_votes,
        votes=list(votes),
    )


def classify_disagreement_bucket(result: DefinitionRefereeResult) -> int | None:
    if not result.disagreement:
        return None
    return result.winner_votes


def update_reduction_stats(stats: BackfillStats, *, word: str, before: int, after: int) -> None:
    reduction = before - after
    if reduction <= 0:
        return
    stats.reduced_words.append((word, reduction))
    stats.reduced_words.sort(key=lambda item: (-item[1], item[0]))
    del stats.reduced_words[20:]


def _canonical_sort_key(row: ClueDefinitionRecord) -> tuple[object, ...]:
    return canonical_reset_safe_sort_key(row)


def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ClueCanonService:
    def __init__(self, *, store: ClueCanonStore | None = None, client=None, runtime=None, multi_model: bool = True):
        self.store = store or ClueCanonStore()
        self.client = client
        self.runtime = runtime
        self.multi_model = multi_model

    def fetch_prompt_examples(self, word_normalized: str, *, limit: int = 3) -> list[str]:
        rows = self._scored_active_canonicals(word_normalized)
        return [row.definition for row in rows[:limit]]

    def select_scored_fallback(
        self,
        *,
        word_normalized: str,
        word_type: str = "",
        usage_label: str = "",
        seed_parts: tuple[object, ...] = (),
    ) -> CanonicalDefinition | None:
        rows = self._scored_active_canonicals(
            word_normalized,
            word_type=word_type,
            usage_label=usage_label,
        )
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        snapshot = "|".join(
            f"{row.id}:{row.semantic_score}:{row.rebus_score}:{row.creativity_score}:{row.usage_count}"
            for row in rows
        )
        rng = stable_tie_rng(
            "canonical_scored_fallback",
            word_normalized,
            word_type,
            usage_label,
            snapshot,
            *seed_parts,
        )
        weights = [_canonical_fallback_weight(row) for row in rows]
        return rows[rng.choices(range(len(rows)), weights=weights, k=1)[0]]

    def resolve_definition(
        self,
        *,
        word_normalized: str,
        word_original: str,
        definition: str,
        word_type: str = "",
        clue_id: str | None = None,
        puzzle_id: str | None = None,
        verified: bool = False,
        semantic_score: int | None = None,
        rebus_score: int | None = None,
        creativity_score: int | None = None,
    ) -> CanonicalDecision:
        record = ClueDefinitionRecord(
            id=clue_id or "",
            word_normalized=str(word_normalized or "").strip().upper(),
            word_original=str(word_original or "").strip(),
            definition=(definition or "").strip(),
            definition_norm=normalize_definition_text(definition),
            word_type=str(word_type or "").strip().upper(),
            usage_label=_extract_usage_label(definition),
            verified=verified,
            semantic_score=semantic_score,
            rebus_score=rebus_score,
            creativity_score=creativity_score,
        )
        exact = self.store.find_exact_canonical(
            record.word_normalized,
            record.definition_norm,
            word_type=record.word_type,
            usage_label=record.usage_label,
        )
        if exact is not None:
            exact = self.store.bump_usage(exact.id, record.word_normalized) or exact
            self._attach_if_possible(clue_id, puzzle_id, exact.id)
            return CanonicalDecision(
                canonical_definition=exact.definition,
                canonical_definition_norm=exact.definition_norm,
                canonical_definition_id=exact.id,
                action="reuse_exact",
            )

        for canonical in self._likely_matches(record):
            result = self._run_referee(record, canonical)
            audit(
                "clue_canon_referee",
                component="clue_canon",
                payload={
                    "word": record.word_normalized,
                    "definition_a": record.definition,
                    "definition_b": canonical.definition,
                    "same_meaning_votes": result.same_meaning_votes,
                    "better_a_votes": result.better_a_votes,
                    "better_b_votes": result.better_b_votes,
                    "equal_votes": result.equal_votes,
                    "winner": result.winner,
                    "winner_votes": result.winner_votes,
                },
            )
            if result.merge_allowed and result.winner == "B":
                self.store.bump_usage(canonical.id, record.word_normalized)
                self._attach_if_possible(clue_id, puzzle_id, canonical.id)
                return CanonicalDecision(
                    canonical_definition=canonical.definition,
                    canonical_definition_norm=canonical.definition_norm,
                    canonical_definition_id=canonical.id,
                    action="reuse_near",
                    same_meaning_votes=result.same_meaning_votes,
                    winner_votes=result.winner_votes,
                    decision_note="existing canonical kept",
                )
            if result.merge_allowed and result.winner == "A":
                created = self.store.create_canonical_definition(record)
                promoted = created or canonical
                self._attach_if_possible(clue_id, puzzle_id, promoted.id)
                return CanonicalDecision(
                    canonical_definition=promoted.definition,
                    canonical_definition_norm=promoted.definition_norm,
                    canonical_definition_id=promoted.id,
                    action="promote_new",
                    same_meaning_votes=result.same_meaning_votes,
                    winner_votes=result.winner_votes,
                    decision_note="new immutable canonical created; existing canonical retained",
                )
            if result.disagreement:
                continue

        created = self.store.create_canonical_definition(record)
        canonical_id = created.id if created is not None else None
        canonical_text = created.definition if created is not None else record.definition
        self._attach_if_possible(clue_id, puzzle_id, canonical_id)
        return CanonicalDecision(
            canonical_definition=canonical_text,
            canonical_definition_norm=record.definition_norm,
            canonical_definition_id=canonical_id,
            action="create_new",
        )

    def _likely_matches(self, record: ClueDefinitionRecord) -> list[CanonicalDefinition]:
        rows = self.store.fetch_canonical_variants(record.word_normalized)
        matches: list[tuple[float, CanonicalDefinition]] = []
        record_tokens = set(content_tokens(record.definition))
        for row in rows:
            if row.word_type != record.word_type:
                continue
            if row.usage_label != record.usage_label:
                continue
            shared = len(record_tokens & set(content_tokens(row.definition)))
            similarity = lexical_similarity(record.definition_norm, row.definition_norm)
            if shared < 2 and similarity < 0.82:
                continue
            matches.append((similarity + shared, row))
        matches.sort(key=lambda item: (-item[0], _canonical_match_key(item[1])))
        return [row for _score, row in matches[:3]]

    def _scored_active_canonicals(
        self,
        word_normalized: str,
        *,
        word_type: str | None = None,
        usage_label: str | None = None,
    ) -> list[CanonicalDefinition]:
        rows = self.store.fetch_canonical_variants(word_normalized)
        exact_word_type = None if word_type is None else str(word_type or "").strip().upper()
        exact_usage_label = None if usage_label is None else str(usage_label or "").strip().lower()
        eligible = [
            row
            for row in rows
            if row.superseded_by is None
            and _canonical_has_complete_scores(row)
            and (exact_word_type is None or row.word_type == exact_word_type)
            and (exact_usage_label is None or row.usage_label.strip().lower() == exact_usage_label)
        ]
        eligible.sort(key=canonical_reset_safe_sort_key)
        return eligible

    def _run_referee(self, record: ClueDefinitionRecord, canonical: CanonicalDefinition) -> DefinitionRefereeResult:
        if self.client is None:
            from rebus_generator.platform.llm.llm_client import create_client
            self.client = create_client()
        from rebus_generator.platform.llm.definition_referee import run_definition_referee

        return run_definition_referee(
            self.client,
            self.runtime,
            record.word_normalized,
            len(record.word_normalized),
            record.definition,
            canonical.definition,
            multi_model=self.multi_model,
        )

    def _run_referee_batch(
        self,
        requests: list[DefinitionRefereeInput],
    ) -> dict[str, DefinitionRefereeResult]:
        if not requests:
            return {}
        if self.client is None:
            from rebus_generator.platform.llm.llm_client import create_client
            self.client = create_client()
        from rebus_generator.platform.llm.definition_referee import run_definition_referee_batch

        return run_definition_referee_batch(
            self.client,
            self.runtime,
            requests,
            multi_model=self.multi_model,
        )

    def _run_referee_adaptive_batch(
        self,
        requests: list[DefinitionRefereeInput],
    ):
        if not requests:
            return None
        if self.client is None:
            from rebus_generator.platform.llm.llm_client import create_client
            self.client = create_client()
        from rebus_generator.platform.llm.definition_referee import run_definition_referee_adaptive_batch

        return run_definition_referee_adaptive_batch(
            self.client,
            self.runtime,
            requests,
            multi_model=self.multi_model,
        )

    def _attach_if_possible(
        self,
        clue_id: str | None,
        puzzle_id: str | None,
        canonical_definition_id: str | None,
    ) -> None:
        if not clue_id or not puzzle_id or not canonical_definition_id:
            return
        self.store.attach_clue(
            clue_id,
            puzzle_id,
            canonical_definition_id=canonical_definition_id,
        )


def _canonical_match_key(row: CanonicalDefinition) -> tuple[object, ...]:
    return canonical_reset_safe_sort_key(row)


def _canonical_has_complete_scores(row: CanonicalDefinition) -> bool:
    return (
        getattr(row, "semantic_score", None) is not None
        and getattr(row, "rebus_score", None) is not None
        and getattr(row, "creativity_score", None) is not None
    )


def _canonical_fallback_weight(row: CanonicalDefinition) -> float:
    score_sum = (
        int(getattr(row, "semantic_score", 0) or 0)
        + int(getattr(row, "rebus_score", 0) or 0)
        + int(getattr(row, "creativity_score", 0) or 0)
    )
    usage_count = max(0, int(getattr(row, "usage_count", 0) or 0))
    return score_sum / (usage_count + 1)
