from __future__ import annotations

import json
from pathlib import Path

from .types import SupervisorWorkItem

CHECKPOINT_VERSION = 1


def serialize_work_item(item: SupervisorWorkItem) -> dict[str, object]:
    return {
        "item_id": item.item_id,
        "topic": item.topic,
        "task_kind": item.task_kind,
        "preferred_model_id": item.preferred_model_id,
        "target_models": list(item.target_models),
        "payload": item.payload,
        "puzzle_id": item.puzzle_id,
        "words": sorted(item.words),
        "attempts": item.attempts,
    }


def deserialize_work_item(payload: dict[str, object]) -> SupervisorWorkItem:
    return SupervisorWorkItem(
        item_id=str(payload["item_id"]),
        topic=str(payload["topic"]),
        task_kind=str(payload["task_kind"]),
        preferred_model_id=str(payload.get("preferred_model_id") or ""),
        target_models=tuple(str(value) for value in payload.get("target_models", [])),
        payload=dict(payload.get("payload") or {}),
        puzzle_id=str(payload.get("puzzle_id") or "") or None,
        words={str(value) for value in payload.get("words", [])},
        attempts=int(payload.get("attempts") or 0),
        available_after=0.0,
    )


def write_checkpoint(path: Path, payload: dict[str, object]) -> None:
    document = {"version": CHECKPOINT_VERSION, **payload}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_checkpoint(path: Path, *, topics: list[str]) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if int(document.get("version") or 0) != CHECKPOINT_VERSION:
        raise ValueError(f"unsupported run_all checkpoint version: {document.get('version')}")
    saved_topics = [str(topic) for topic in document.get("topics", [])]
    if saved_topics != topics:
        raise ValueError(f"checkpoint topics differ: saved={saved_topics}, requested={topics}")
    return document
