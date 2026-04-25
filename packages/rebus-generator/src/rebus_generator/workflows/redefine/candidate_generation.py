from dataclasses import dataclass
from typing import Literal, Protocol, Sequence, Mapping

from rebus_generator.platform.llm.models import ModelConfig
from rebus_generator.platform.llm.ai_clues import RewriteAttemptResult
from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.guards.definition_guards import validate_definition_text_with_details

@dataclass(frozen=True)
class RewriteCandidateRequest:
    word: str
    word_original: str
    word_type: str | None
    theme: str
    current_definition: str
    wrong_guess: str = ""
    wrong_guesses: tuple[str, ...] = ()
    rating_feedback: str = ""
    bad_example_definition: str = ""
    bad_example_reason: str = ""
    failure_history: tuple[tuple[str, tuple[str, ...]], ...] = ()
    dex_definitions: str = ""
    canonical_examples: tuple[str, ...] = ()
    use_hybrid_fresh_generate: bool = False

@dataclass(frozen=True)
class RewriteCandidate:
    source: Literal["rewrite", "generate"]
    definition: str
    generated_by: str
    strategy_label: Literal["rewrite", "fresh_generate", "fresh_only"]
    model_id: str

@dataclass(frozen=True)
class GuardRejection:
    word: str
    model_id: str
    reason: str
    definition_preview: str
    matched_token: str | None = None
    matched_stem: str | None = None
    leak_kind: str | None = None

@dataclass(frozen=True)
class RewriteCandidateResult:
    word: str
    candidates: tuple[RewriteCandidate, ...]
    had_error: bool = False
    rejection_reason: str = ""
    guard_rejections: tuple[GuardRejection, ...] = ()

class RewriteCandidateGenerator(Protocol):
    def generate(
        self,
        requests: Sequence[RewriteCandidateRequest],
        *,
        model: ModelConfig,
    ) -> Mapping[str, RewriteCandidateResult]: ...

class RewriteLLMPort(Protocol):
    def rewrite(self, request: RewriteCandidateRequest, *, model_id: str) -> RewriteAttemptResult | str: ...
    def generate(self, request: RewriteCandidateRequest, *, model_id: str) -> str: ...

class RewriteAuditPort(Protocol):
    def candidate_rejected(self, rejection: GuardRejection) -> None: ...

class RewriteCandidateGeneratorImpl:
    def __init__(
        self,
        llm_port: RewriteLLMPort,
        audit_port: RewriteAuditPort,
    ) -> None:
        self.llm_port = llm_port
        self.audit_port = audit_port

    def generate(
        self,
        requests: Sequence[RewriteCandidateRequest],
        *,
        model: ModelConfig,
    ) -> Mapping[str, RewriteCandidateResult]:
        results: dict[str, RewriteCandidateResult] = {}

        for req in requests:
            candidates: list[RewriteCandidate] = []
            guard_rejections: list[GuardRejection] = []
            seen_keys: set[str] = set()
            had_error = False
            rewrite_rejection_reason = ""

            def _add_candidate(definition: str, source: Literal["rewrite", "generate"], strategy: Literal["rewrite", "fresh_generate", "fresh_only"]) -> None:
                nonlocal rewrite_rejection_reason
                cleaned = definition.strip()
                if not cleaned or cleaned == req.current_definition:
                    return

                rejection_details = validate_definition_text_with_details(req.word, cleaned)
                if rejection_details:
                    if not rewrite_rejection_reason:
                        rewrite_rejection_reason = rejection_details.reason

                    guard_rejection = GuardRejection(
                        word=req.word,
                        model_id=model.model_id,
                        reason=rejection_details.reason,
                        definition_preview=cleaned,
                        matched_token=rejection_details.matched_token,
                        matched_stem=rejection_details.matched_stem,
                        leak_kind=rejection_details.leak_kind,
                    )
                    guard_rejections.append(guard_rejection)
                    self.audit_port.candidate_rejected(guard_rejection)
                    return

                key = " ".join(normalize(cleaned).lower().split())
                if key in seen_keys:
                    return
                seen_keys.add(key)
                candidates.append(
                    RewriteCandidate(
                        source=source,
                        definition=cleaned,
                        generated_by=model.display_name,
                        strategy_label=strategy,
                        model_id=model.model_id,
                    )
                )

            if req.current_definition.startswith("["):
                try:
                    generated = self.llm_port.generate(req, model_id=model.model_id)
                    if generated:
                        _add_candidate(generated, "generate", "fresh_only")
                except Exception:
                    had_error = True
            else:
                try:
                    rewrite_result = self.llm_port.rewrite(req, model_id=model.model_id)
                    if isinstance(rewrite_result, RewriteAttemptResult):
                        _add_candidate(rewrite_result.definition, "rewrite", "rewrite")
                        if not candidates and not rewrite_rejection_reason:
                            rewrite_rejection_reason = rewrite_result.last_rejection
                    else:
                        _add_candidate(str(rewrite_result or ""), "rewrite", "rewrite")
                except Exception:
                    had_error = True

                if req.use_hybrid_fresh_generate:
                    try:
                        fresh = self.llm_port.generate(req, model_id=model.model_id)
                        if fresh:
                            _add_candidate(fresh, "generate", "fresh_generate")
                    except Exception:
                        had_error = True

            results[req.word] = RewriteCandidateResult(
                word=req.word,
                candidates=tuple(candidates),
                had_error=had_error,
                rejection_reason=rewrite_rejection_reason,
                guard_rejections=tuple(guard_rejections),
            )

        return results
