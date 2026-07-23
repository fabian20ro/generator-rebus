from pathlib import Path

import pytest

from rebus_generator.platform.orchestration import WorkItem
from rebus_generator.workflows.run_all.checkpoint import (
    deserialize_work_item,
    load_checkpoint,
    serialize_work_item,
    write_checkpoint,
)


def test_checkpoint_roundtrip_preserves_durable_work_item(tmp_path: Path):
    item = WorkItem(
        item_id="redefine:p1",
        topic="redefine",
        task_kind="redefine_puzzle",
        preferred_model_id="model-a",
        target_models=("model-a", "model-b"),
        payload={"row": {"id": "p1"}},
        puzzle_id="p1",
        words={"AER", "NOR"},
        attempts=1,
    )
    path = tmp_path / "checkpoint.json"
    write_checkpoint(path, {
        "topics": ["redefine"],
        "active_items": [serialize_work_item(item)],
    })

    loaded = load_checkpoint(path, topics=["redefine"])
    recovered = deserialize_work_item(loaded["active_items"][0])

    assert recovered.stable_key() == item.stable_key()
    assert recovered.words == item.words
    assert recovered.attempts == 1
    assert recovered.available_after == 0.0


def test_checkpoint_rejects_different_topic_set(tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    write_checkpoint(path, {"topics": ["generate"]})

    with pytest.raises(ValueError, match="checkpoint topics differ"):
        load_checkpoint(path, topics=["retitle"])
