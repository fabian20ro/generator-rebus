from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol


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


@dataclass
class PlannedCanonicalEvent:
    action: str
    candidate_definition: str
    canonical_definition: str
    detail: Optional[str] = None


@dataclass
class PlannedNewPuzzleClue:
    record: dict[str, Any]
    canonical_event: PlannedCanonicalEvent


@dataclass
class NewPuzzleCanonicalPlan:
    clues: list[PlannedNewPuzzleClue]
    touched_canonical_ids: list[str] = field(default_factory=list)


@dataclass
class ExistingPuzzleClueInput:
    row: dict[str, Any]
    word_normalized: str
    definition: str
    word_original: Optional[str] = None
    word_type: Optional[str] = None
    verified: bool = False
    semantic_score: Optional[int] = None
    rebus_score: Optional[int] = None
    creativity_score: Optional[int] = None
    verify_note: Optional[str] = None
    context: Any = None


@dataclass
class PlannedExistingPuzzleClue:
    row: dict[str, Any]
    persistence: PlannedCluePersistence
    canonical_event: PlannedCanonicalEvent
    context: Any = None


@dataclass
class ExistingPuzzleCanonicalPlan:
    clues: list[PlannedExistingPuzzleClue]
    touched_canonical_ids: list[str] = field(default_factory=list)


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
            touched_canonical_ids=sorted(set(touched_ids))
        )

    def plan_new_puzzle_clues(self, clue_records: list[dict[str, Any]]) -> NewPuzzleCanonicalPlan:
        inputs: list[CanonicalInput] = []
        source_records: list[dict[str, Any]] = []
        candidate_definitions: list[str] = []
        for record in clue_records:
            resolved_record = dict(record)
            candidate_definition = str(resolved_record.pop("_candidate_definition", "") or "")
            source_records.append(resolved_record)
            candidate_definitions.append(candidate_definition)
            inputs.append(
                CanonicalInput(
                    word_normalized=str(resolved_record.get("word_normalized") or ""),
                    word_original=str(resolved_record.get("word_original") or "") or None,
                    definition=candidate_definition,
                    word_type=str(resolved_record.get("word_type") or "") or None,
                )
            )

        plan = self.plan(inputs)
        planned_clues: list[PlannedNewPuzzleClue] = []
        for record, candidate_definition, persistence in zip(
            source_records,
            candidate_definitions,
            plan.clue_persistences,
        ):
            resolved_record = dict(record)
            resolved_record.update(persistence.payload)
            planned_clues.append(
                PlannedNewPuzzleClue(
                    record=resolved_record,
                    canonical_event=_canonical_event(persistence, candidate_definition),
                )
            )
        return NewPuzzleCanonicalPlan(
            clues=planned_clues,
            touched_canonical_ids=plan.touched_canonical_ids,
        )

    def plan_existing_puzzle_clues(
        self,
        clues: list[ExistingPuzzleClueInput],
        *,
        touched_canonical_ids: list[str] | None = None,
    ) -> ExistingPuzzleCanonicalPlan:
        inputs = [
            CanonicalInput(
                word_normalized=clue.word_normalized,
                word_original=clue.word_original,
                definition=clue.definition,
                word_type=clue.word_type,
                clue_id=str(clue.row["id"]),
                verified=clue.verified,
                semantic_score=clue.semantic_score,
                rebus_score=clue.rebus_score,
                creativity_score=clue.creativity_score,
                verify_note=clue.verify_note,
                current_payload=_current_clue_payload(clue.row),
            )
            for clue in clues
        ]
        plan = self.plan(inputs)
        clue_by_id = {str(clue.row["id"]): clue for clue in clues}
        planned_clues: list[PlannedExistingPuzzleClue] = []
        for persistence in plan.clue_persistences:
            clue = clue_by_id.get(str(persistence.clue_id))
            if clue is None:
                continue
            planned_clues.append(
                PlannedExistingPuzzleClue(
                    row=clue.row,
                    persistence=persistence,
                    canonical_event=_canonical_event(persistence, clue.definition),
                    context=clue.context,
                )
            )
        return ExistingPuzzleCanonicalPlan(
            clues=planned_clues,
            touched_canonical_ids=sorted(set(list(touched_canonical_ids or []) + plan.touched_canonical_ids)),
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


def _canonical_event(
    persistence: PlannedCluePersistence,
    candidate_definition: str,
) -> PlannedCanonicalEvent:
    return PlannedCanonicalEvent(
        action=persistence.action,
        candidate_definition=candidate_definition,
        canonical_definition=persistence.canonical_definition,
        detail=persistence.detail,
    )


def _current_clue_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "definition": row.get("definition", "") or "",
        "verify_note": row.get("verify_note", "") or "",
        "verified": bool(row.get("verified")),
        "canonical_definition_id": row.get("canonical_definition_id"),
    }
