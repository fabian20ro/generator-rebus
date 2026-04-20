import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.workflows.canonicals.puzzle_definition_audit import run_audit


def _grid_2x2() -> str:
    return json.dumps([[True, True], [True, True]])


def _puzzle(
    puzzle_id: str = "p1",
    *,
    published: bool = True,
    title: str = "Puzzle",
    grid_template: str | None = None,
) -> dict:
    return {
        "id": puzzle_id,
        "title": title,
        "published": published,
        "grid_size": 2,
        "grid_template": _grid_2x2() if grid_template is None else grid_template,
        "created_at": "2026-04-05T10:00:00+00:00",
        "repaired_at": None,
    }


def _clue(
    puzzle_id: str,
    clue_id: str,
    direction: str,
    start_row: int,
    start_col: int,
    *,
    definition: str = "def",
    clue_number: int = 1,
    length: int = 2,
    canonical_definition_id: str | None = None,
) -> dict:
    return {
        "id": clue_id,
        "puzzle_id": puzzle_id,
        "direction": direction,
        "start_row": start_row,
        "start_col": start_col,
        "length": length,
        "clue_number": clue_number,
        "definition": definition,
        "canonical_definition_id": canonical_definition_id,
    }


def _canonical(
    canonical_id: str,
    *,
    word_normalized: str = "WORD",
    definition: str = "def",
    superseded_by: str | None = None,
) -> dict:
    return {
        "id": canonical_id,
        "word_normalized": word_normalized,
        "definition": definition,
        "superseded_by": superseded_by,
    }


class _FakeStore:
    def __init__(self, puzzles: list[dict], clues: list[dict], canonicals: list[dict] | None = None):
        self.puzzles = puzzles
        self.clues = clues
        self.canonicals = list(canonicals or [])
        self.fetch_puzzle_rows_calls: list[dict] = []
        self.fetch_clue_rows_for_puzzle_ids_calls: list[list[str]] = []
        self.fetch_raw_clue_rows_calls = 0
        self.fetch_canonical_rows_calls = 0

    def fetch_puzzle_rows(self, **kwargs):
        self.fetch_puzzle_rows_calls.append(kwargs)
        rows = list(self.puzzles)
        if kwargs.get("published_only"):
            rows = [row for row in rows if row.get("published")]
        if kwargs.get("puzzle_id"):
            rows = [row for row in rows if row.get("id") == kwargs["puzzle_id"]]
        limit = kwargs.get("limit")
        if limit is not None:
            rows = rows[:limit]
        return rows

    def fetch_clue_rows_for_puzzle_ids(self, puzzle_ids, **_kwargs):
        self.fetch_clue_rows_for_puzzle_ids_calls.append(list(puzzle_ids))
        wanted = set(puzzle_ids)
        return [row for row in self.clues if row.get("puzzle_id") in wanted]

    def fetch_raw_clue_rows(self, **_kwargs):
        self.fetch_raw_clue_rows_calls += 1
        return list(self.clues)

    def fetch_canonical_rows(self, **kwargs):
        self.fetch_canonical_rows_calls += 1
        rows = list(self.canonicals)
        limit = kwargs.get("limit")
        if limit is not None:
            rows = rows[:limit]
        return rows


class PuzzleDefinitionAuditTests(unittest.TestCase):
    def test_complete_puzzle_passes(self):
        store = _FakeStore(
            [_puzzle()],
            [
                _clue("p1", "c1", "H", 0, 0),
                _clue("p1", "c2", "H", 1, 0),
                _clue("p1", "c3", "V", 0, 0),
                _clue("p1", "c4", "V", 0, 1),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.jsonl"
            exit_code = run_audit(store=store, output=str(output), details=str(details))
            summary = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, exit_code)
        self.assertTrue(summary["ok"])
        self.assertEqual(1, len(store.fetch_puzzle_rows_calls))
        self.assertEqual([["p1"]], store.fetch_clue_rows_for_puzzle_ids_calls)

    def test_canonical_audit_passes_when_all_referenced(self):
        store = _FakeStore(
            [_puzzle()],
            [
                _clue("p1", "c1", "H", 0, 0, canonical_definition_id="canon-1"),
                _clue("p1", "c2", "H", 1, 0, canonical_definition_id="canon-2"),
                _clue("p1", "c3", "V", 0, 0, canonical_definition_id="canon-3"),
                _clue("p1", "c4", "V", 0, 1, canonical_definition_id="canon-4"),
            ],
            [
                _canonical("canon-1", word_normalized="A"),
                _canonical("canon-2", word_normalized="B"),
                _canonical("canon-3", word_normalized="C"),
                _canonical("canon-4", word_normalized="D"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.jsonl"
            exit_code = run_audit(store=store, output=str(output), details=str(details))
            summary = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, exit_code)
        self.assertTrue(summary["ok"])
        self.assertEqual(0, summary["unreferenced_canonical_definitions"])
        self.assertEqual([], summary["unreferenced_canonical_samples"])

    def test_detects_missing_slot_row(self):
        store = _FakeStore(
            [_puzzle()],
            [
                _clue("p1", "c1", "H", 0, 0),
                _clue("p1", "c2", "H", 1, 0),
                _clue("p1", "c3", "V", 0, 0),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.json"
            exit_code = run_audit(store=store, output=str(output), details=str(details))
            summary = json.loads(output.read_text(encoding="utf-8"))
            findings = json.loads(details.read_text(encoding="utf-8"))

        self.assertEqual(1, exit_code)
        self.assertEqual(1, summary["missing_slot_rows"])
        self.assertIn("missing_slot_row", [finding["issue_type"] for finding in findings])

    def test_detects_blank_definition(self):
        store = _FakeStore(
            [_puzzle()],
            [
                _clue("p1", "c1", "H", 0, 0, definition=""),
                _clue("p1", "c2", "H", 1, 0),
                _clue("p1", "c3", "V", 0, 0),
                _clue("p1", "c4", "V", 0, 1),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.json"
            run_audit(store=store, output=str(output), details=str(details))
            summary = json.loads(output.read_text(encoding="utf-8"))
            findings = json.loads(details.read_text(encoding="utf-8"))

        self.assertEqual(1, summary["blank_definitions"])
        self.assertIn("blank_definition", [finding["issue_type"] for finding in findings])

    def test_detects_duplicate_slot_row(self):
        store = _FakeStore(
            [_puzzle()],
            [
                _clue("p1", "c1", "H", 0, 0),
                _clue("p1", "c1b", "H", 0, 0),
                _clue("p1", "c2", "H", 1, 0),
                _clue("p1", "c3", "V", 0, 0),
                _clue("p1", "c4", "V", 0, 1),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.json"
            run_audit(store=store, output=str(output), details=str(details))
            summary = json.loads(output.read_text(encoding="utf-8"))
            findings = json.loads(details.read_text(encoding="utf-8"))

        self.assertEqual(1, summary["duplicate_slot_rows"])
        self.assertIn("duplicate_slot_row", [finding["issue_type"] for finding in findings])

    def test_detects_orphan_clue_row(self):
        store = _FakeStore(
            [_puzzle()],
            [
                _clue("p1", "c1", "H", 0, 0),
                _clue("p1", "c2", "H", 1, 0),
                _clue("p1", "c3", "V", 0, 0),
                _clue("p1", "c4", "V", 0, 1),
                _clue("p1", "c5", "H", 5, 5),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.json"
            run_audit(store=store, output=str(output), details=str(details))
            summary = json.loads(output.read_text(encoding="utf-8"))
            findings = json.loads(details.read_text(encoding="utf-8"))

        self.assertEqual(1, summary["orphan_clue_rows"])
        self.assertIn("orphan_clue_row", [finding["issue_type"] for finding in findings])

    def test_detects_puzzle_count_mismatch(self):
        store = _FakeStore(
            [_puzzle()],
            [
                _clue("p1", "c1", "H", 0, 0),
                _clue("p1", "c2", "H", 1, 0),
                _clue("p1", "c3", "V", 0, 0),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.json"
            run_audit(store=store, output=str(output), details=str(details))
            summary = json.loads(output.read_text(encoding="utf-8"))
            findings = json.loads(details.read_text(encoding="utf-8"))

        self.assertEqual(1, summary["puzzle_count_mismatches"])
        self.assertIn("puzzle_count_mismatch", [finding["issue_type"] for finding in findings])

    def test_published_only_filters(self):
        store = _FakeStore(
            [_puzzle("p1", published=True), _puzzle("p2", published=False)],
            [
                _clue("p1", "c1", "H", 0, 0),
                _clue("p1", "c2", "H", 1, 0),
                _clue("p1", "c3", "V", 0, 0),
                _clue("p1", "c4", "V", 0, 1),
                _clue("p2", "c5", "H", 0, 0),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.json"
            run_audit(
                store=store,
                published_only=True,
                output=str(output),
                details=str(details),
            )
            summary = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(1, summary["total_puzzles_scanned"])
        self.assertEqual([["p1"]], store.fetch_clue_rows_for_puzzle_ids_calls)

    def test_detects_unreferenced_canonical_definition(self):
        store = _FakeStore(
            [_puzzle()],
            [
                _clue("p1", "c1", "H", 0, 0, canonical_definition_id="canon-1"),
                _clue("p1", "c2", "H", 1, 0, canonical_definition_id="canon-2"),
                _clue("p1", "c3", "V", 0, 0, canonical_definition_id="canon-3"),
                _clue("p1", "c4", "V", 0, 1, canonical_definition_id="canon-4"),
            ],
            [
                _canonical("canon-1", word_normalized="A"),
                _canonical("canon-2", word_normalized="B"),
                _canonical("canon-3", word_normalized="C"),
                _canonical("canon-4", word_normalized="D"),
                _canonical("canon-orphan", word_normalized="ORFAN", definition="unused canonical"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "summary.json"
            details = Path(tmpdir) / "details.json"
            exit_code = run_audit(store=store, output=str(output), details=str(details))
            summary = json.loads(output.read_text(encoding="utf-8"))
            findings = json.loads(details.read_text(encoding="utf-8"))

        self.assertEqual(1, exit_code)
        self.assertFalse(summary["ok"])
        self.assertEqual(1, summary["unreferenced_canonical_definitions"])
        self.assertEqual(
            [
                {
                    "id": "canon-orphan",
                    "word_normalized": "ORFAN",
                    "definition_preview": "unused canonical",
                    "superseded_by": "",
                }
            ],
            summary["unreferenced_canonical_samples"],
        )
        self.assertIn(
            "unreferenced_canonical_definition",
            [finding["issue_type"] for finding in findings],
        )

    def test_wrapper_forwards_args_to_python_module(self):
        wrapper = Path(__file__).resolve().parents[3] / "run_puzzle_definition_audit.sh"
        with tempfile.TemporaryDirectory() as tmpdir:
            capture = Path(tmpdir) / "args.txt"
            stub = Path(tmpdir) / "python-stub.sh"
            stub.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\n' \"$@\" > \"$CAPTURE_ARGS_PATH\"\n",
                encoding="utf-8",
            )
            os.chmod(stub, 0o755)
            env = os.environ.copy()
            env["PYTHON_BIN"] = str(stub)
            env["CAPTURE_ARGS_PATH"] = str(capture)
            completed = subprocess.run(
                [str(wrapper), "--published-only", "--limit", "3"],
                cwd=wrapper.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            captured_args = capture.read_text(encoding="utf-8").splitlines()

        self.assertEqual(0, completed.returncode)
        self.assertEqual(
            ["-m", "rebus_generator.workflows.canonicals.puzzle_definition_audit", "--published-only", "--limit", "3"],
            captured_args,
        )


class ClueCanonStoreBulkFetchTests(unittest.TestCase):
    def test_fetch_puzzle_rows_pages_and_filters(self):
        client = mock.MagicMock()
        query = mock.MagicMock()
        query.eq.return_value = query
        query.order.return_value = query
        query.range.return_value = query
        query.execute.side_effect = [
            mock.Mock(data=[{"id": "p1"}, {"id": "p2"}]),
            mock.Mock(data=[]),
        ]
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        rows = store.fetch_puzzle_rows(published_only=True)

        self.assertEqual([{"id": "p1"}, {"id": "p2"}], rows)
        client.table.assert_called_with("crossword_puzzles")
        query.eq.assert_called_with("published", True)

    def test_fetch_clue_rows_for_puzzle_ids_uses_bulk_in_filter(self):
        client = mock.MagicMock()
        query = mock.MagicMock()
        query.in_.return_value = query
        query.order.return_value = query
        query.range.return_value = query
        query.execute.side_effect = [
            mock.Mock(data=[{"id": "c1", "puzzle_id": "p1"}]),
            mock.Mock(data=[]),
        ]
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        rows = store.fetch_clue_rows_for_puzzle_ids(["p1", "p2"])

        self.assertEqual([{"id": "c1", "puzzle_id": "p1"}], rows)
        client.table.assert_called_with("crossword_clue_effective")
        query.in_.assert_called_once_with("puzzle_id", ["p1", "p2"])


if __name__ == "__main__":
    unittest.main()
