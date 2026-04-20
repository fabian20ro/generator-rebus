"""Audit puzzle clue integrity for UI-safe loading."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.platform.io.runtime_logging import audit, install_process_logging, log, path_timestamp
from rebus_generator.domain.slot_extractor import extract_slots

DEFAULT_REPORT_DIR = Path("build/puzzle_definition_audit")
_UNREFERENCED_CANONICAL_SAMPLE_LIMIT = 25


def _slot_key(direction: str, start_row: Any, start_col: Any) -> tuple[str, int, int]:
    return (str(direction or "").upper(), int(start_row), int(start_col))


def _definition_preview(value: str, *, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _parse_grid_template(raw: str) -> list[list[bool]]:
    data = json.loads(str(raw or "[]"))
    if not isinstance(data, list):
        raise ValueError("grid_template must decode to a list")
    grid: list[list[bool]] = []
    for row in data:
        if not isinstance(row, list):
            raise ValueError("grid_template rows must be lists")
        grid.append([bool(cell) for cell in row])
    return grid


def _finding(
    puzzle: dict,
    *,
    issue_type: str,
    direction: str | None = None,
    start_row: int | None = None,
    start_col: int | None = None,
    length: int | None = None,
    clue_id: str | None = None,
    clue_number: int | None = None,
    definition_preview: str = "",
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "puzzle_id": str(puzzle.get("id") or ""),
        "title": str(puzzle.get("title") or ""),
        "published": bool(puzzle.get("published")),
        "issue_type": issue_type,
        "direction": direction or "",
        "start_row": start_row,
        "start_col": start_col,
        "length": length,
        "clue_id": clue_id or "",
        "clue_number": clue_number,
        "definition_preview": definition_preview,
    }
    payload.update(extra)
    return payload


def _canonical_finding(row: dict) -> dict[str, object]:
    return {
        "puzzle_id": "",
        "title": "",
        "published": False,
        "issue_type": "unreferenced_canonical_definition",
        "direction": "",
        "start_row": None,
        "start_col": None,
        "length": None,
        "clue_id": "",
        "clue_number": None,
        "definition_preview": _definition_preview(str(row.get("definition") or "")),
        "canonical_id": str(row.get("id") or ""),
        "word_normalized": str(row.get("word_normalized") or ""),
        "superseded_by": str(row.get("superseded_by") or ""),
    }


def _audit_puzzle(puzzle: dict, clue_rows: list[dict]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    try:
        grid = _parse_grid_template(str(puzzle.get("grid_template") or ""))
    except Exception as exc:
        findings.append(
            _finding(
                puzzle,
                issue_type="invalid_grid_template",
                definition_preview=_definition_preview(str(exc)),
            )
        )
        return findings

    expected_slots = extract_slots(grid)
    expected_by_key = {
        _slot_key(slot.direction, slot.start_row, slot.start_col): slot
        for slot in expected_slots
    }
    actual_by_key: dict[tuple[str, int, int], list[dict]] = defaultdict(list)
    for row in clue_rows:
        actual_by_key[_slot_key(row.get("direction"), row.get("start_row"), row.get("start_col"))].append(row)

    for key, slot in sorted(expected_by_key.items()):
        rows = actual_by_key.get(key, [])
        if not rows:
            findings.append(
                _finding(
                    puzzle,
                    issue_type="missing_slot_row",
                    direction=slot.direction,
                    start_row=slot.start_row,
                    start_col=slot.start_col,
                    length=slot.length,
                )
            )

    for key, rows in sorted(actual_by_key.items()):
        direction, start_row, start_col = key
        slot = expected_by_key.get(key)
        if slot is None:
            for row in rows:
                findings.append(
                    _finding(
                        puzzle,
                        issue_type="orphan_clue_row",
                        direction=direction,
                        start_row=start_row,
                        start_col=start_col,
                        length=_to_int(row.get("length")),
                        clue_id=str(row.get("id") or ""),
                        clue_number=_to_int(row.get("clue_number")),
                        definition_preview=_definition_preview(str(row.get("definition") or "")),
                    )
                )
            continue
        if len(rows) > 1:
            for row in rows[1:]:
                findings.append(
                    _finding(
                        puzzle,
                        issue_type="duplicate_slot_row",
                        direction=direction,
                        start_row=start_row,
                        start_col=start_col,
                        length=slot.length,
                        clue_id=str(row.get("id") or ""),
                        clue_number=_to_int(row.get("clue_number")),
                        definition_preview=_definition_preview(str(row.get("definition") or "")),
                    )
                )
        for row in rows:
            definition = str(row.get("definition") or "")
            if not definition.strip():
                findings.append(
                    _finding(
                        puzzle,
                        issue_type="blank_definition",
                        direction=direction,
                        start_row=start_row,
                        start_col=start_col,
                        length=slot.length,
                        clue_id=str(row.get("id") or ""),
                        clue_number=_to_int(row.get("clue_number")),
                        definition_preview="",
                    )
                )

    if len(expected_slots) != len(clue_rows):
        findings.append(
            _finding(
                puzzle,
                issue_type="puzzle_count_mismatch",
                definition_preview="",
                expected_slot_count=len(expected_slots),
                actual_clue_row_count=len(clue_rows),
            )
        )
    return findings


def _to_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _audit_unreferenced_canonicals(store: ClueCanonStore) -> list[dict[str, object]]:
    referenced_ids = {
        str(row.get("canonical_definition_id") or "").strip()
        for row in store.fetch_raw_clue_rows()
        if str(row.get("canonical_definition_id") or "").strip()
    }
    findings: list[dict[str, object]] = []
    for row in store.fetch_canonical_rows():
        canonical_id = str(row.get("id") or "").strip()
        if not canonical_id or canonical_id in referenced_ids:
            continue
        finding = _canonical_finding(row)
        audit("puzzle_definition_issue", payload=finding)
        findings.append(finding)
    return findings


def _build_summary(*, puzzles: list[dict], clue_rows: list[dict], findings: list[dict], output: Path, details: Path) -> dict[str, object]:
    issue_types = [str(finding.get("issue_type") or "") for finding in findings]
    puzzles_with_issues = sorted({
        str(finding.get("puzzle_id") or "")
        for finding in findings
        if str(finding.get("puzzle_id") or "").strip()
    })
    canonical_findings = [
        finding
        for finding in findings
        if str(finding.get("issue_type") or "") == "unreferenced_canonical_definition"
    ]
    return {
        "ok": not findings,
        "total_puzzles_scanned": len(puzzles),
        "total_clue_rows_scanned": len(clue_rows),
        "puzzles_with_issues": len(puzzles_with_issues),
        "missing_slot_rows": issue_types.count("missing_slot_row"),
        "blank_definitions": issue_types.count("blank_definition"),
        "duplicate_slot_rows": issue_types.count("duplicate_slot_row"),
        "orphan_clue_rows": issue_types.count("orphan_clue_row"),
        "puzzle_count_mismatches": issue_types.count("puzzle_count_mismatch"),
        "invalid_grid_templates": issue_types.count("invalid_grid_template"),
        "unreferenced_canonical_definitions": len(canonical_findings),
        "unreferenced_canonical_samples": [
            {
                "id": str(finding.get("canonical_id") or ""),
                "word_normalized": str(finding.get("word_normalized") or ""),
                "definition_preview": str(finding.get("definition_preview") or ""),
                "superseded_by": str(finding.get("superseded_by") or ""),
            }
            for finding in canonical_findings[:_UNREFERENCED_CANONICAL_SAMPLE_LIMIT]
        ],
        "output": str(output),
        "details": str(details),
        "issue_puzzle_ids": puzzles_with_issues,
    }


def _write_findings(path: Path, findings: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    lines = [json.dumps(finding, ensure_ascii=False) for finding in findings]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def run_audit(
    *,
    published_only: bool = False,
    puzzle_id: str | None = None,
    limit: int | None = None,
    output: str | None = None,
    details: str | None = None,
    store: ClueCanonStore | None = None,
) -> int:
    store = store or ClueCanonStore()

    summary_path = Path(output) if output else DEFAULT_REPORT_DIR / f"audit_{path_timestamp()}.json"
    details_path = Path(details) if details else summary_path.with_name(f"{summary_path.stem}_details.jsonl")

    puzzles = store.fetch_puzzle_rows(
        published_only=published_only,
        puzzle_id=puzzle_id,
        limit=limit,
    )
    clue_rows = store.fetch_clue_rows_for_puzzle_ids([str(row.get("id") or "") for row in puzzles])
    clue_rows_by_puzzle: dict[str, list[dict]] = defaultdict(list)
    for row in clue_rows:
        clue_rows_by_puzzle[str(row.get("puzzle_id") or "")].append(row)

    findings: list[dict[str, object]] = []
    for puzzle in puzzles:
        puzzle_findings = _audit_puzzle(puzzle, clue_rows_by_puzzle.get(str(puzzle.get("id") or ""), []))
        if puzzle_findings:
            log(
                f"[puzzle-audit] puzzle_id={puzzle.get('id')} "
                f"title={json.dumps(str(puzzle.get('title') or ''), ensure_ascii=False)} "
                f"issues={len(puzzle_findings)}"
            )
        for finding in puzzle_findings:
            audit("puzzle_definition_issue", payload=finding)
        findings.extend(puzzle_findings)
    canonical_findings = _audit_unreferenced_canonicals(store)
    if canonical_findings:
        log(
            "[puzzle-audit] "
            f"unreferenced_canonical_definitions={len(canonical_findings)} "
            f"sample={json.dumps([finding.get('canonical_id') for finding in canonical_findings[:5]])}"
        )
    findings.extend(canonical_findings)

    summary = _build_summary(
        puzzles=puzzles,
        clue_rows=clue_rows,
        findings=findings,
        output=summary_path,
        details=details_path,
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_findings(details_path, findings)
    log(json.dumps(summary, ensure_ascii=False, indent=2))
    log(f"summary_report={summary_path}")
    log(f"details_report={details_path}")
    return 0 if summary["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit puzzle clue integrity for UI-safe loading.")
    parser.add_argument("--published-only", action="store_true", help="Only scan published puzzles.")
    parser.add_argument("--puzzle-id", help="Only scan one puzzle id.")
    parser.add_argument("--limit", type=int, help="Limit number of puzzles scanned.")
    parser.add_argument("--output", help="Write summary JSON here.")
    parser.add_argument("--details", help="Write detailed findings here (.json or .jsonl).")
    return parser


def main(argv: list[str] | None = None) -> int:
    run_dir = DEFAULT_REPORT_DIR / path_timestamp()
    log_path = run_dir / "run.log"
    audit_path = run_dir / "audit.jsonl"
    summary_path = run_dir / "summary.json"
    details_path = run_dir / "details.jsonl"
    handle = install_process_logging(
        run_id=f"puzzle_definition_audit_{path_timestamp()}",
        component="puzzle_definition_audit",
        log_path=log_path,
        audit_path=audit_path,
        tee_console=True,
    )
    try:
        args = build_parser().parse_args(argv)
        log(f"Run log: {log_path}")
        log(f"Audit log: {audit_path}")
        return run_audit(
            published_only=args.published_only,
            puzzle_id=args.puzzle_id,
            limit=args.limit,
            output=args.output or str(summary_path),
            details=args.details or str(details_path),
        )
    except RuntimeError as exc:
        log(f"Error: {exc}")
        return 2
    finally:
        handle.restore()


if __name__ == "__main__":
    raise SystemExit(main())
