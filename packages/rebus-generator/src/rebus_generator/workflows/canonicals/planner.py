from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Any


@dataclass
class CanonicalInput:
    word_normalized: str
    definition: str
    clue_id: Optional[str] = None
    word_original: Optional[str] = None
    word_type: Optional[str] = None
    verified: bool = False
    semantic_score: Optional[int] = None
    rebus_score: Optional[int] = None
    creativity_score: Optional[int] = None
    verify_note: Optional[str] = None
    current_payload: Optional[dict[str, Any]] = None


@dataclass
class PlannedCluePersistence:
    clue_id: Optional[str]
    canonical_definition_id: str
    canonical_definition: str
    candidate_definition: str
    payload: dict[str, Any]
    action: str
    detail: Optional[str] = None


@dataclass
class CanonicalPersistencePlan:
    clue_persistences: List[PlannedCluePersistence]
    touched_canonical_ids: List[str] = field(default_factory=list)


class CanonicalResolverPort(Protocol):
    def resolve_definition(
        self,
        *,
        word_normalized: str,
        word_original: Optional[str],
        definition: str,
        word_type: Optional[str],
        verified: bool,
        semantic_score: Optional[int],
        rebus_score: Optional[int],
        creativity_score: Optional[int],
    ) -> Any:
        ...


class CluePayloadBuilderPort(Protocol):
    def build_clue_definition_payload(
        self,
        *,
        canonical_definition_id: str,
        verify_note: str,
        verified: bool,
    ) -> dict[str, Any]:
        ...


class CanonicalPersistencePlanner:
    def __init__(self, resolver: CanonicalResolverPort, builder: CluePayloadBuilderPort):
        self.resolver = resolver
        self.builder = builder

    def plan(self, inputs: List[CanonicalInput]) -> CanonicalPersistencePlan:
        persistences = []
        touched_ids = []
        decisions = self._resolve(inputs)
        
        for inp, decision in zip(inputs, decisions):
            payload = self.builder.build_clue_definition_payload(
                canonical_definition_id=decision.canonical_definition_id,
                verify_note=inp.verify_note or "",
                verified=inp.verified,
            )
            
            if inp.current_payload:
                comparable_current = {field: inp.current_payload.get(field) for field in payload}
                if comparable_current == payload:
                    continue
            
            persistences.append(
                PlannedCluePersistence(
                    clue_id=inp.clue_id,
                    canonical_definition_id=decision.canonical_definition_id,
                    canonical_definition=decision.canonical_definition,
                    candidate_definition=inp.definition,
                    payload=payload,
                    action=decision.action,
                    detail=getattr(decision, "decision_note", None),
                )
            )
            if getattr(decision, "created_new", False):
                touched_ids.append(decision.canonical_definition_id)
            
        return CanonicalPersistencePlan(
            clue_persistences=persistences,
            touched_canonical_ids=list(set(touched_ids))
        )

    def _resolve(self, inputs: List[CanonicalInput]) -> list[Any]:
        bulk_method = getattr(type(self.resolver), "resolve_definitions", None)
        if callable(bulk_method):
            return list(self.resolver.resolve_definitions(inputs))
        return [
            self.resolver.resolve_definition(
                word_normalized=inp.word_normalized,
                word_original=inp.word_original,
                definition=inp.definition,
                word_type=inp.word_type,
                verified=inp.verified,
                semantic_score=inp.semantic_score,
                rebus_score=inp.rebus_score,
                creativity_score=inp.creativity_score,
            )
            for inp in inputs
        ]
