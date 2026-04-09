from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..core.clue_canon_simplify import build_candidate_pairs
from ..core.llm_dispatch import initial_generation_model
from ..core.model_manager import PRIMARY_MODEL
from ..loop_controller import select_auto_size
from ..redefine import fetch_puzzles as fetch_redefine_puzzles
from ..retitle import fetch_puzzles as fetch_retitle_puzzles, select_puzzles_for_retitle
from .types import SupervisorWorkItem

if TYPE_CHECKING:
    from .scheduler import RunAllSupervisor


def poll_generate(supervisor: "RunAllSupervisor") -> SupervisorWorkItem | None:
    size = select_auto_size(
        client=supervisor.ctx.supabase,
        excluded_sizes=supervisor.active_generate_size_exclusions(),
        size_penalties=supervisor.generate_size_penalty_map(),
    )
    preferred_model = initial_generation_model(supervisor.ctx.runtime).model_id
    item = SupervisorWorkItem(
        item_id=f"generate:size:{size}:{int(time.time() * 1000)}",
        topic="generate",
        task_kind="generate",
        preferred_model_id=preferred_model,
        target_models=supervisor._targets_for_topic("generate"),
        payload={"size": size, "index": supervisor.completed + supervisor.failed + len(supervisor.pending_items) + 1},
    )
    supervisor._admit_item(item)
    return supervisor._next_pending_for_topic("generate")


def poll_redefine(supervisor: "RunAllSupervisor") -> SupervisorWorkItem | None:
    rows = fetch_redefine_puzzles(supervisor.ctx.supabase)
    for row in rows:
        puzzle_id = str(row.get("id") or "")
        if supervisor.claims.has_puzzle(puzzle_id):
            continue
        words = supervisor._fetch_puzzle_words(puzzle_id)
        if supervisor.claims.puzzle_word_conflict(words):
            continue
        item = SupervisorWorkItem(
            item_id=f"redefine:puzzle:{puzzle_id}",
            topic="redefine",
            task_kind="redefine",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=supervisor._targets_for_topic("redefine"),
            payload={"puzzle_row": row},
            puzzle_id=puzzle_id,
            words=words,
        )
        supervisor._admit_item(item)
        return supervisor._next_pending_for_topic("redefine")
    return None


def poll_retitle(supervisor: "RunAllSupervisor") -> SupervisorWorkItem | None:
    rows = select_puzzles_for_retitle(fetch_retitle_puzzles(supervisor.ctx.supabase))
    for row in rows:
        puzzle_id = str(row.get("id") or "")
        if supervisor.claims.has_puzzle(puzzle_id):
            continue
        words = supervisor._fetch_puzzle_words(puzzle_id)
        if supervisor.claims.puzzle_word_conflict(words):
            continue
        item = SupervisorWorkItem(
            item_id=f"retitle:puzzle:{puzzle_id}",
            topic="retitle",
            task_kind="retitle",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=supervisor._targets_for_topic("retitle"),
            payload={"puzzle_row": row},
            puzzle_id=puzzle_id,
            words=words,
        )
        supervisor._admit_item(item)
        return supervisor._next_pending_for_topic("retitle")
    return None


def poll_simplify(supervisor: "RunAllSupervisor") -> SupervisorWorkItem | None:
    pairs = build_candidate_pairs(
        [
            row
            for row in supervisor.ctx.store.fetch_active_canonical_variants()
            if row.word_normalized not in supervisor.claims.simplify_words
            and not supervisor.claims.simplify_word_conflict({row.word_normalized})
        ]
    )
    seen_words: set[str] = set()
    for pair in pairs:
        if pair.word in seen_words:
            continue
        words = {pair.word}
        if supervisor.claims.simplify_word_conflict(words):
            continue
        item = SupervisorWorkItem(
            item_id=f"simplify:word:{pair.word}:{pair.left_id}:{pair.right_id}",
            topic="simplify",
            task_kind="simplify",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=supervisor._targets_for_topic("simplify"),
            payload={"word": pair.word},
            words=words,
        )
        supervisor._admit_item(item)
        return supervisor._next_pending_for_topic("simplify")
    return None
