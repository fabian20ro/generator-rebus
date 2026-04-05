import json
import sys
import unittest
from io import StringIO
from tempfile import TemporaryDirectory
from pathlib import Path

from generator.core.runtime_logging import (
    TimestampedWriter,
    audit,
    format_human_log_line,
    human_timestamp,
    install_process_logging,
    log,
)


class TimestampedWriterTests(unittest.TestCase):
    def test_prefixes_plain_lines_once(self):
        target = StringIO()
        writer = TimestampedWriter(target)

        writer.write("hello\n")
        writer.flush()

        output = target.getvalue()
        self.assertIn("hello", output)
        self.assertRegex(output, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} INFO hello\n$")
        self.assertNotIn("[INFO]", output)
        self.assertNotIn("[", output.split(" hello")[0])

    def test_does_not_double_prefix_existing_timestamp(self):
        target = StringIO()
        writer = TimestampedWriter(target)

        writer.write("[2026-03-21T10:11:12+02:00] already stamped\n")
        writer.flush()

        self.assertEqual("[2026-03-21T10:11:12+02:00] already stamped\n", target.getvalue())

    def test_flushes_fragment_writes_without_reprefixing_same_line(self):
        target = StringIO()
        writer = TimestampedWriter(target)

        writer.write("hello")
        first = target.getvalue()
        writer.write(" world")
        second = target.getvalue()
        writer.write("\n")

        self.assertIn("hello", first)
        self.assertEqual(first.count("INFO"), second.count("INFO"))
        self.assertIn("hello world\n", target.getvalue())

    def test_preserves_existing_severity_without_adding_info(self):
        target = StringIO()
        writer = TimestampedWriter(target)

        writer.write("[WARN] problem\n")
        writer.flush()

        output = target.getvalue()
        self.assertRegex(output, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} WARN problem\n$")
        self.assertNotIn("INFO [WARN]", output)

    def test_moves_indentation_after_severity(self):
        target = StringIO()
        writer = TimestampedWriter(target)

        writer.write("  hello\n")
        writer.flush()

        self.assertRegex(target.getvalue(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} INFO   hello\n$")

    def test_human_timestamp_omits_timezone_offset(self):
        stamp = human_timestamp()
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_format_human_log_line_adds_timestamp_and_info(self):
        rendered = format_human_log_line("loop started")
        self.assertRegex(rendered, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} INFO loop started$")


class AuditTests(unittest.TestCase):
    def test_install_process_logging_writes_log_file(self):
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "run.log"
            handle = install_process_logging(
                run_id="test-run",
                component="test_component",
                log_path=log_path,
                tee_console=False,
            )
            try:
                log("hello log")
            finally:
                handle.restore()

            output = log_path.read_text(encoding="utf-8")
            self.assertIn("hello log", output)
            self.assertRegex(output, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} INFO hello log\n$")

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
