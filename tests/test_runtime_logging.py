import json
import sys
import unittest
from io import StringIO
from tempfile import TemporaryDirectory
from pathlib import Path

from generator.core.runtime_logging import (
    TimestampedWriter,
    audit,
    install_process_logging,
)


class TimestampedWriterTests(unittest.TestCase):
    def test_prefixes_plain_lines_once(self):
        target = StringIO()
        writer = TimestampedWriter(target)

        writer.write("hello\n")
        writer.flush()

        output = target.getvalue()
        self.assertIn("hello", output)
        self.assertTrue(output.startswith("["))

    def test_does_not_double_prefix_existing_timestamp(self):
        target = StringIO()
        writer = TimestampedWriter(target)

        writer.write("[2026-03-21T10:11:12+02:00] already stamped\n")
        writer.flush()

        self.assertEqual("[2026-03-21T10:11:12+02:00] already stamped\n", target.getvalue())


class AuditTests(unittest.TestCase):
    def test_audit_writes_jsonl_record(self):
        with TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            handle = install_process_logging(
                run_id="test-run",
                component="test_component",
                audit_path=audit_path,
                tee_console=False,
            )
            try:
                audit("sample_event", payload={"word": "FIRISOR"})
            finally:
                handle.restore()

            lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(1, len(lines))
            record = json.loads(lines[0])
            self.assertEqual("sample_event", record["event"])
            self.assertEqual("test-run", record["run_id"])
            self.assertEqual("test_component", record["component"])
            self.assertEqual({"word": "FIRISOR"}, record["payload"])


if __name__ == "__main__":
    unittest.main()
