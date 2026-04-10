"""Maintain steady-state canonical clue definitions."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.workflows.canonicals.simplify import (
    DEFAULT_BATCH_SIZE as DEFAULT_SIMPLIFY_BATCH_SIZE,
    DEFAULT_IDLE_SLEEP_SECONDS,
    run_simplify_fanout,
)
from rebus_generator.domain.clue_canon_types import CanonicalDefinition
from rebus_generator.platform.llm.llm_client import create_client
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.io.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


def _is_uuid(value: object) -> bool:
    return bool(_UUID_RE.match(str(value or "").strip()))


def _active_identity_key(row: CanonicalDefinition) -> tuple[str, str, str, str]:
    return (
        row.word_normalized,
        row.word_type,
        row.usage_label,
        row.definition_norm,
    )


def _fanout_bucket_key(row: CanonicalDefinition) -> tuple[str, str, str]:
    return (row.word_normalized, row.word_type, row.usage_label)


def _fanout_limit(word_normalized: str) -> int:
    length = len(str(word_normalized or "").strip())
    if length <= 2:
        return 4
    if length <= 3:
        return 6
    return 10


def _canonical_preview(row: CanonicalDefinition) -> dict[str, object]:
    return {
        "id": row.id,
        "word_normalized": row.word_normalized,
        "word_type": row.word_type,
        "usage_label": row.usage_label,
        "definition": row.definition,
        "definition_norm": row.definition_norm,
        "verified": row.verified,
        "usage_count": row.usage_count,
        "superseded_by": row.superseded_by,
    }


def run_audit(
    *,
    output: str | None = None,
    store: ClueCanonStore | None = None,
) -> int:
    store = store or ClueCanonStore()
    raw_rows = store.fetch_raw_clue_rows(extra_fields=("word_normalized",))
    effective_rows = store.fetch_clue_rows()
    active_canonicals = store.fetch_active_canonical_variants()

    effective_by_id = {
        str(row.get("id") or ""): row
        for row in effective_rows
        if str(row.get("id") or "").strip()
    }
    raw_by_id = {
        str(row.get("id") or ""): row
        for row in raw_rows
        if str(row.get("id") or "").strip()
    }

    null_pointer_rows = []
    bad_pointer_rows = []
    canonical_ids: set[str] = set()
    for row in raw_rows:
        canonical_definition_id = str(row.get("canonical_definition_id") or "").strip()
        if not canonical_definition_id:
            null_pointer_rows.append(
                {
                    "clue_id": str(row.get("id") or ""),
                    "puzzle_id": str(row.get("puzzle_id") or ""),
                    "word_normalized": str(row.get("word_normalized") or ""),
                }
            )
            continue
        if not _is_uuid(canonical_definition_id):
            bad_pointer_rows.append(
                {
                    "clue_id": str(row.get("id") or ""),
                    "puzzle_id": str(row.get("puzzle_id") or ""),
                    "word_normalized": str(row.get("word_normalized") or ""),
                    "canonical_definition_id": canonical_definition_id,
                }
            )
            continue
        canonical_ids.add(canonical_definition_id)

    canonicals_by_id = {
        row.id: row
        for row in store.fetch_canonical_rows_by_ids(sorted(canonical_ids))
    }
    superseded_pointer_rows = []
    dangling_pointer_rows = []
    for row in raw_rows:
        canonical_definition_id = str(row.get("canonical_definition_id") or "").strip()
        if not _is_uuid(canonical_definition_id):
            continue
        canonical = canonicals_by_id.get(canonical_definition_id)
        if canonical is None:
            dangling_pointer_rows.append(
                {
                    "clue_id": str(row.get("id") or ""),
                    "puzzle_id": str(row.get("puzzle_id") or ""),
                    "canonical_definition_id": canonical_definition_id,
                }
            )
            continue
        if canonical.superseded_by:
            superseded_pointer_rows.append(
                {
                    "clue_id": str(row.get("id") or ""),
                    "puzzle_id": str(row.get("puzzle_id") or ""),
                    "canonical_definition_id": canonical.id,
                    "superseded_by": canonical.superseded_by,
                }
            )

    duplicate_active_canonicals = []
    identity_groups: dict[tuple[str, str, str, str], list[CanonicalDefinition]] = defaultdict(list)
    for row in active_canonicals:
        identity_groups[_active_identity_key(row)].append(row)
    for key, rows in sorted(identity_groups.items()):
        if len(rows) < 2:
            continue
        duplicate_active_canonicals.append(
            {
                "word_normalized": key[0],
                "word_type": key[1],
                "usage_label": key[2],
                "definition_norm": key[3],
                "canonical_ids": [row.id for row in rows],
                "count": len(rows),
            }
        )

    oversized_fanout_buckets = []
    fanout_groups: dict[tuple[str, str, str], list[CanonicalDefinition]] = defaultdict(list)
    for row in active_canonicals:
        fanout_groups[_fanout_bucket_key(row)].append(row)
    for key, rows in sorted(fanout_groups.items()):
        limit = _fanout_limit(key[0])
        if len(rows) <= limit:
            continue
        oversized_fanout_buckets.append(
            {
                "word_normalized": key[0],
                "word_type": key[1],
                "usage_label": key[2],
                "active_canonical_count": len(rows),
                "limit": limit,
                "canonical_ids": [row.id for row in rows],
            }
        )

    missing_effective_rows = []
    for clue_id, raw_row in sorted(raw_by_id.items()):
        if clue_id in effective_by_id:
            continue
        missing_effective_rows.append(
            {
                "clue_id": clue_id,
                "puzzle_id": str(raw_row.get("puzzle_id") or ""),
                "canonical_definition_id": str(raw_row.get("canonical_definition_id") or ""),
            }
        )

    summary = {
        "ok": not (
            null_pointer_rows
            or bad_pointer_rows
            or dangling_pointer_rows
            or superseded_pointer_rows
            or duplicate_active_canonicals
            or oversized_fanout_buckets
            or missing_effective_rows
        ),
        "raw_clue_rows": len(raw_rows),
        "effective_view_rows": len(effective_rows),
        "active_canonical_rows": len(active_canonicals),
        "null_canonical_definition_id": len(null_pointer_rows),
        "bad_canonical_definition_id": len(bad_pointer_rows),
        "dangling_canonical_definition_id": len(dangling_pointer_rows),
        "superseded_canonical_links": len(superseded_pointer_rows),
        "duplicate_active_canonical_identities": len(duplicate_active_canonicals),
        "oversized_fanout_buckets": len(oversized_fanout_buckets),
        "missing_effective_rows": len(missing_effective_rows),
        "details": {
            "null_pointer_rows": null_pointer_rows,
            "bad_pointer_rows": bad_pointer_rows,
            "dangling_pointer_rows": dangling_pointer_rows,
            "superseded_pointer_rows": superseded_pointer_rows,
            "duplicate_active_canonicals": duplicate_active_canonicals,
            "oversized_fanout_buckets": oversized_fanout_buckets,
            "missing_effective_rows": missing_effective_rows,
            "active_canonical_samples": [
                _canonical_preview(row)
                for row in sorted(
                    active_canonicals,
                    key=lambda item: (item.word_normalized, item.word_type, item.usage_label, item.id),
                )[:25]
            ],
        },
    }
    report_path = Path(output) if output else Path("build/clue_canon") / f"audit_{path_timestamp()}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(json.dumps(summary, ensure_ascii=False, indent=2))
    log(f"audit_report={report_path}")
    return 0 if summary["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain canonical clue definitions.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Audit canonical clue library health.")
    audit.add_argument("--output", help="Write audit JSON to this path.")

    simplify = subparsers.add_parser("simplify-fanout", help="Continuously simplify canonical fanout.")
    simplify.add_argument("--dry-run", action="store_true", help="Analyze without DB writes.")
    simplify.add_argument("--apply", action="store_true", help="Persist simplifier merges.")
    simplify.add_argument("--batch-size", type=int, default=DEFAULT_SIMPLIFY_BATCH_SIZE, help="Pairs per batch.")
    simplify.add_argument("--state-path", help="Checkpoint path for resumable simplify state.")
    simplify.add_argument("--report-dir", help="Write simplify reports under this directory.")
    simplify.add_argument("--seed", type=int, help="Random seed for pair sampling.")
    simplify.add_argument(
        "--idle-sleep-seconds",
        type=int,
        default=DEFAULT_IDLE_SLEEP_SECONDS,
        help="Sleep this long before retrying when no eligible pairs exist.",
    )
    simplify.add_argument("--word", help="Only simplify one normalized word.")
    add_llm_debug_argument(simplify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    set_llm_debug_enabled(bool(getattr(args, "debug", False)))
    if args.command == "audit":
        handle = install_process_logging(
            run_id=f"clue_canon_audit_{path_timestamp()}",
            component="clue_canon_audit",
            tee_console=True,
        )
        try:
            return run_audit(output=args.output)
        finally:
            handle.restore()
    if args.command == "simplify-fanout":
        report_dir = Path(args.report_dir) if args.report_dir else Path("build/clue_canon_simplify") / path_timestamp()
        log_path = report_dir / "run.log"
        audit_path = report_dir / "audit.jsonl"
        handle = install_process_logging(
            run_id=report_dir.name,
            component="clue_canon_simplify",
            log_path=log_path,
            audit_path=audit_path,
            tee_console=True,
        )
        try:
            store = ClueCanonStore()
            client = create_client()
            runtime = LmRuntime(multi_model=True)
            log(f"Run log: {log_path}")
            log(f"Audit log: {audit_path}")
            log(
                "Simplify config: "
                f"mode={'dry-run' if args.dry_run else 'apply'} batch_size={args.batch_size} "
                f"idle_sleep_seconds={args.idle_sleep_seconds} word={args.word or '-'}"
            )
            return run_simplify_fanout(
                store=store,
                client=client,
                runtime=runtime,
                dry_run=args.dry_run,
                apply=args.apply,
                batch_size=args.batch_size,
                state_path=args.state_path,
                report_dir=str(report_dir),
                seed=args.seed,
                idle_sleep_seconds=args.idle_sleep_seconds,
                word=args.word,
            )
        finally:
            handle.restore()
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
