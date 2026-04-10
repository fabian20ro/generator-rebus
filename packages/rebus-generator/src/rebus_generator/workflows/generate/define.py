"""Phase 5: Generate definitions for each word using LM Studio."""

from __future__ import annotations
from openai import OpenAI
from rebus_generator.platform.io.markdown_io import parse_markdown, write_with_definitions, ClueEntry
from rebus_generator.platform.llm.llm_client import create_client
from rebus_generator.platform.llm.ai_clues import generate_definition
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService
from rebus_generator.platform.io.clue_logging import clue_label_from_working_clue, log_definition_event
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.llm.llm_dispatch import WorkItem, WorkVote, run_single_model_workload
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import ModelConfig, PRIMARY_MODEL
from rebus_generator.domain.pipeline_state import (
    WorkingClue,
    WorkingPuzzle,
    puzzle_from_working_state,
    set_current_definition,
    working_puzzle_from_puzzle,
)
from rebus_generator.platform.io.runtime_logging import log


def _split_and_define(
    clues: list[ClueEntry],
    client: OpenAI,
    theme: str,
    model_config: ModelConfig = PRIMARY_MODEL,
) -> list[ClueEntry]:
    """Split compound clue entries and generate definitions for each word."""
    result = []
    for clue in clues:
        # Split "WORD1 - WORD2 - WORD3" into individual clues
        words = [w.strip() for w in clue.word_normalized.split(" - ") if w.strip()]
        originals = [o.strip() for o in clue.word_original.split(" - ")] if clue.word_original else [""] * len(words)

        # Pad originals if shorter
        while len(originals) < len(words):
            originals.append("")

        for word, original in zip(words, originals):
            if clue.definition:
                # Already has a definition, keep it
                result.append(ClueEntry(
                    row_number=clue.row_number,
                    word_normalized=word,
                    word_original=original,
                    definition=clue.definition,
                ))
            else:
                log(f"  Defining: {word} ({original or '?'})...")
                try:
                    definition = generate_definition(
                        client,
                        word,
                        original,
                        theme,
                        model=model_config.model_id,
                    )
                except Exception as e:
                    definition = f"[Definiție lipsă: {e}]"
                log(f"    → {definition}")
                result.append(ClueEntry(
                    row_number=clue.row_number,
                    word_normalized=word,
                    word_original=original,
                    definition=definition,
                ))

    return result


def generate_definitions_for_state(
    state: WorkingPuzzle,
    client: OpenAI,
    dex: DexProvider | None = None,
    *,
    clue_canon: ClueCanonService | None = None,
    runtime: LmRuntime | None = None,
    model_config: ModelConfig | None = None,
) -> None:
    theme = state.title or "Rebus Românesc"
    log(f"Theme: {theme}")
    selected_model = model_config or PRIMARY_MODEL
    clue_canon = clue_canon or ClueCanonService()
    runtime = runtime or LmRuntime(multi_model=False)

    for label, clues in (("horizontal", state.horizontal_clues), ("vertical", state.vertical_clues)):
        log(f"Generating {label} definitions...")
        direction = "H" if label == "horizontal" else "V"
        items: list[WorkItem[WorkingClue, str]] = []
        for index, clue in enumerate(clues, start=1):
            if clue.current.definition:
                continue
            clue_ref = clue_label_from_working_clue(clue, direction=direction)
            dex_defs = dex.get(clue.word_normalized, clue.word_original) if dex else None
            dex_defs = dex_defs or ""
            if dex_defs:
                log(f"  Defining: {clue_ref} ({clue.word_original or '?'}) [DEX context available]")
            else:
                log(f"  Defining: {clue_ref} ({clue.word_original or '?'})...")
            items.append(
                WorkItem(
                    item_id=f"{direction}:{index}:{clue.word_normalized}",
                    task_kind="definition_generate",
                    payload=clue,
                    pending_models={selected_model.model_id},
                )
            )

        def _runner(item: WorkItem[WorkingClue, str], model: ModelConfig) -> WorkVote[str]:
            clue = item.payload
            dex_defs = dex.get(clue.word_normalized, clue.word_original) if dex else None
            dex_defs = dex_defs or ""
            try:
                existing_canonical_definitions = clue_canon.fetch_prompt_examples(clue.word_normalized)
                definition = generate_definition(
                    client,
                    clue.word_normalized,
                    clue.word_original,
                    theme,
                    word_type=clue.word_type,
                    dex_definitions=dex_defs,
                    existing_canonical_definitions=existing_canonical_definitions,
                    model=model.model_id,
                )
            except Exception as exc:
                definition = f"[Definiție lipsă: {exc}]"
            return WorkVote(model_id=model.model_id, value=definition, source="ok")

        if items:
            run_single_model_workload(
                runtime=runtime,
                model=selected_model,
                items=items,
                purpose="definition_generate",
                runner=_runner,
                task_label="definition_generate",
            )
        for item in items:
            clue = item.payload
            definition = str(item.votes[selected_model.model_id].value or "")
            clue_ref = clue_label_from_working_clue(clue, direction=direction)
            log_definition_event(
                "generated",
                clue_ref=clue_ref,
                before="",
                after=definition,
                detail=f"model={selected_model.display_name}",
            )
            set_current_definition(
                clue,
                definition,
                round_index=0,
                source="generate",
                generated_by=selected_model.display_name,
            )
            if clue.best is None:
                clue.best = clue.current


def generate_definitions_for_puzzle(
    puzzle,
    client: OpenAI,
    metadata: dict[str, dict] | None = None,
    *,
    runtime: LmRuntime | None = None,
    model_config: ModelConfig | None = None,
) -> None:
    """Expand clues and generate definitions in-place for the whole puzzle."""
    state = working_puzzle_from_puzzle(puzzle, split_compound=True)
    if metadata:
        from rebus_generator.domain.pipeline_state import all_working_clues as _all_clues
        for clue in _all_clues(state):
            word_meta = metadata.get(clue.word_normalized, {})
            clue.word_type = word_meta.get("word_type", "")
    dex = DexProvider.for_puzzle(state)
    generate_definitions_for_state(
        state,
        client,
        dex=dex,
        clue_canon=ClueCanonService(),
        runtime=runtime,
        model_config=model_config,
    )
    rendered = puzzle_from_working_state(state)
    puzzle.horizontal_clues = rendered.horizontal_clues
    puzzle.vertical_clues = rendered.vertical_clues


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Generate definitions for all words in the puzzle."""
    log(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = create_client()
    state = working_puzzle_from_puzzle(puzzle, split_compound=True)
    dex = DexProvider.for_puzzle(state)
    runtime = LmRuntime(multi_model=False)
    generate_definitions_for_state(
        state,
        client,
        dex=dex,
        clue_canon=ClueCanonService(),
        runtime=runtime,
        model_config=PRIMARY_MODEL,
    )
    puzzle = puzzle_from_working_state(state)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    log(f"Generated {total} definitions. Saved to {output_file}")
