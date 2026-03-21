"""Shared runtime logging with timestamps, tee support, and audit events."""

from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


_TIMESTAMP_PREFIX_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)\]\s?"
)


def human_timestamp() -> str:
    """Timestamp for human-facing logs in local time."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def utc_timestamp() -> str:
    """Timestamp for persisted artifacts in UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_timestamp() -> str:
    """Filesystem-safe timestamp for run directories and filenames."""
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


@dataclass
class _LoggingState:
    run_id: str
    component: str
    audit_path: Path | None
    lock: threading.Lock


_STATE: _LoggingState | None = None


class TimestampedWriter:
    """Line-buffered tee writer that prefixes human timestamps once."""

    def __init__(self, *streams: TextIO):
        self._streams = streams
        self._buffer = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buffer += data
        while True:
            newline_index = self._buffer.find("\n")
            if newline_index == -1:
                break
            line = self._buffer[: newline_index + 1]
            self._buffer = self._buffer[newline_index + 1 :]
            self._emit(line)
        return len(data)

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""
        for stream in self._streams:
            stream.flush()

    def isatty(self) -> bool:
        return any(getattr(stream, "isatty", lambda: False)() for stream in self._streams)

    def _emit(self, line: str) -> None:
        if line == "\n":
            rendered = line
        elif _TIMESTAMP_PREFIX_RE.match(line):
            rendered = line
        else:
            rendered = f"[{human_timestamp()}] {line}"
        for stream in self._streams:
            stream.write(rendered)
            stream.flush()


@dataclass
class LoggingHandle:
    stdout: TextIO
    stderr: TextIO
    stdout_wrapper: TimestampedWriter
    stderr_wrapper: TimestampedWriter
    log_file: TextIO | None = None
    audit_file: TextIO | None = None

    def restore(self) -> None:
        sys.stdout = self.stdout
        sys.stderr = self.stderr
        if self.log_file is not None:
            self.log_file.flush()
            self.log_file.close()
        if self.audit_file is not None:
            self.audit_file.flush()
            self.audit_file.close()


def install_process_logging(
    *,
    run_id: str,
    component: str,
    log_path: Path | None = None,
    audit_path: Path | None = None,
    tee_console: bool = True,
) -> LoggingHandle:
    """Install timestamped stdout/stderr wrappers for the current process."""
    global _STATE

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    log_file = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")

    audit_file = None
    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_file = audit_path.open("a", encoding="utf-8")

    stdout_streams: list[TextIO] = []
    stderr_streams: list[TextIO] = []
    if tee_console:
        stdout_streams.append(original_stdout)
        stderr_streams.append(original_stderr)
    if log_file is not None:
        stdout_streams.append(log_file)
        stderr_streams.append(log_file)
    if not stdout_streams:
        stdout_streams.append(original_stdout)
    if not stderr_streams:
        stderr_streams.append(original_stderr)

    stdout_wrapper = TimestampedWriter(*stdout_streams)
    stderr_wrapper = TimestampedWriter(*stderr_streams)
    sys.stdout = stdout_wrapper
    sys.stderr = stderr_wrapper

    _STATE = _LoggingState(
        run_id=run_id,
        component=component,
        audit_path=audit_path,
        lock=threading.Lock(),
    )

    handle = LoggingHandle(
        stdout=original_stdout,
        stderr=original_stderr,
        stdout_wrapper=stdout_wrapper,
        stderr_wrapper=stderr_wrapper,
        log_file=log_file,
        audit_file=audit_file,
    )
    return handle


def log(message: str) -> None:
    print(message)


def audit(event: str, *, component: str | None = None, payload: dict | None = None) -> None:
    """Append a structured audit event if audit logging is enabled."""
    state = _STATE
    if state is None or state.audit_path is None:
        return
    record = {
        "ts": utc_timestamp(),
        "event": event,
        "run_id": state.run_id,
        "component": component or state.component,
        "payload": payload or {},
    }
    line = json.dumps(record, ensure_ascii=False)
    with state.lock:
        with state.audit_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
