"""Shared types for canonical clue deduplication and reuse."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClueDefinitionRecord:
    id: str
    word_normalized: str
    word_original: str
    definition: str
    definition_norm: str
    word_type: str = ""
    usage_label: str = ""
    verified: bool = False
    semantic_score: int | None = None
    rebus_score: int | None = None
    creativity_score: int | None = None
    verify_note: str = ""
    canonical_definition_id: str | None = None


@dataclass(frozen=True)
class CanonicalDefinition:
    id: str
    word_normalized: str
    word_original_seed: str
    definition: str
    definition_norm: str
    word_type: str = ""
    usage_label: str = ""
    verified: bool = False
    semantic_score: int | None = None
    rebus_score: int | None = None
    creativity_score: int | None = None
    usage_count: int = 0
    superseded_by: str | None = None


@dataclass(frozen=True)
class DefinitionComparisonVote:
    model_id: str
    same_meaning: bool
    better: str
    reason: str = ""


@dataclass(frozen=True)
class DefinitionComparisonAttempt:
    model_id: str
    model_role: str
    valid_vote: bool
    parse_status: str
    latency_seconds: float = 0.0
    vote: DefinitionComparisonVote | None = None
    error_message: str = ""


@dataclass(frozen=True)
class DefinitionRefereeInput:
    request_id: str
    word: str
    answer_length: int
    definition_a: str
    definition_b: str


@dataclass(frozen=True)
class DefinitionRefereeDiagnostics:
    request_id: str
    attempts: list[DefinitionComparisonAttempt]
    primary_valid_votes: int = 0
    secondary_valid_votes: int = 0

    @property
    def missing_model_roles(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.primary_valid_votes <= 0:
            missing.append("primary")
        if self.secondary_valid_votes <= 0:
            missing.append("secondary")
        return tuple(missing)

    @property
    def has_both_model_contributions(self) -> bool:
        return self.primary_valid_votes > 0 and self.secondary_valid_votes > 0


@dataclass(frozen=True)
class DefinitionRefereeResult:
    same_meaning_votes: int
    better_a_votes: int
    better_b_votes: int
    equal_votes: int
    votes: list[DefinitionComparisonVote]
    diagnostics: DefinitionRefereeDiagnostics | None = None

    @property
    def winner_votes(self) -> int:
        return max(self.better_a_votes, self.better_b_votes)

    @property
    def winner(self) -> str:
        if self.better_a_votes > self.better_b_votes:
            return "A"
        if self.better_b_votes > self.better_a_votes:
            return "B"
        return "equal"

    @property
    def merge_allowed(self) -> bool:
        return self.same_meaning_votes >= 2 and self.winner in {"A", "B"} and self.winner_votes >= 2

    @property
    def disagreement(self) -> bool:
        return self.same_meaning_votes >= 2 and self.winner == "equal"


@dataclass(frozen=True)
class NearDuplicateCandidate:
    left: ClueDefinitionRecord
    right: ClueDefinitionRecord
    shared_tokens: int = 0
    similarity: float = 0.0


@dataclass(frozen=True)
class CanonicalDecision:
    canonical_definition: str
    canonical_definition_norm: str
    canonical_definition_id: str | None
    action: str
    same_meaning_votes: int | None = None
    winner_votes: int | None = None
    decision_note: str = ""
    created_new: bool = False


@dataclass
class WorkingCluster:
    primary: ClueDefinitionRecord
    members: list[ClueDefinitionRecord] = field(default_factory=list)
    canonical_id: str | None = None
    same_meaning_votes: int | None = None
    winner_votes: int | None = None
    decision_note: str = ""


@dataclass(frozen=True)
class ComparisonOutcome:
    kind: str
    request_id: str = ""
    existing_index: int | None = None
    missing_model_roles: tuple[str, ...] = ()
    diagnostics: DefinitionRefereeDiagnostics | None = None
    result: DefinitionRefereeResult | None = None


@dataclass
class WordReducerState:
    word: str
    clusters: list[WorkingCluster]
    selected: list[WorkingCluster] = field(default_factory=list)
    boilerplate_tokens: tuple[str, ...] = field(default_factory=tuple)
    next_cluster_index: int = 0
    current: WorkingCluster | None = None
    candidate_indexes: list[int] = field(default_factory=list)
    compare_index: int = 0
    waiting: bool = False
    pending_request_id: str = ""

    def finished(self) -> bool:
        return (
            not self.waiting
            and self.current is None
            and self.next_cluster_index >= len(self.clusters)
        )


@dataclass
class QueuedWordState:
    merge_state: WordReducerState
    input_count: int
    comparisons_done: int = 0
    unresolved: bool = False
    deferred: bool = False
    defer_reason: str = ""
    defer_remaining_clusters: int = 0
    candidate_pairs_considered: int = 0
    referee_requests_submitted: int = 0
    consecutive_non_merge_comparisons: int = 0
    last_merge_comparison: int = 0
    blocked_on_referee_error: bool = False
    referee_error_count: int = 0
    last_referee_error: dict[str, object] = field(default_factory=dict)
    resume_stale_wait_info: dict[str, object] = field(default_factory=dict)

    @property
    def word(self) -> str:
        return self.merge_state.word


@dataclass
class BackfillStats:
    total_rows: int = 0
    eligible_rows: int = 0
    verified_null_rows: int = 0
    unverified_null_rows: int = 0
    eligible_words: int = 0
    already_canonicalized_rows_skipped: int = 0
    exact_merges: int = 0
    near_merges: int = 0
    disagreement_3_of_6: int = 0
    disagreement_4_of_6: int = 0
    standalone_canonicals: int = 0
    comparison_requests: int = 0
    total_votes: int = 0
    singleton_words: int = 0
    resumed_words: int = 0
    unresolved_words: int = 0
    committed_words: int = 0
    deferred_words: int = 0
    deferred_due_to_stagnation: int = 0
    deferred_due_to_resume_stale_wait: int = 0
    referee_error_words: int = 0
    missing_model_vote_errors: int = 0
    keep_separate_decisions: int = 0
    merge_decisions: int = 0
    promote_decisions: int = 0
    referee_batches_launched: int = 0
    referee_phase1_requests: int = 0
    referee_phase2_requests: int = 0
    referee_merges: int = 0
    referee_keep_separate: int = 0
    referee_errors: int = 0
    invalid_compare_json_primary: int = 0
    invalid_compare_json_secondary: int = 0
    resume_stale_wait_words: int = 0
    resume_pending_words_dropped: int = 0
    resume_active_words_dropped: int = 0
    resume_words_deduped: int = 0
    canonical_prefetch_batches: int = 0
    clue_attach_batches: int = 0
    alias_insert_batches: int = 0
    candidate_pairs_considered: int = 0
    referee_requests_submitted: int = 0
    verified_attached_rows: int = 0
    unverified_attached_rows: int = 0
    unverified_singleton_canonicals_created: int = 0
    unverified_exact_reuses: int = 0
    reduced_words: list[tuple[str, int]] = field(default_factory=list)
