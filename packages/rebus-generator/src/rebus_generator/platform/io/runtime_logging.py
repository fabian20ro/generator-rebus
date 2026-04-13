"""Shared runtime logging with timestamps, tee support, and audit events."""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


_TIMESTAMP_PREFIX_RE = re.compile(
    r"^\[?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?\]?(?:\s+(?:DEBUG|INFO|WARN|ERROR))?\s?"
)
_LEVEL_PREFIX_RE = re.compile(r"^(\s*)(?:\[(DEBUG|INFO|WARN|ERROR)\]|(DEBUG|INFO|WARN|ERROR))\s+")


def human_timestamp() -> str:
    """Timestamp for human-facing logs in local time."""
    return datetime.now().astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


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
_LLM_DEBUG_ENABLED = False


class TimestampedWriter:
    """Immediate tee writer that prefixes human timestamps once per line."""

    def __init__(self, *streams: TextIO, failure_stream: TextIO | None = None):
        self._streams = list(streams)
        self._failure_stream = failure_stream
        self._at_line_start = True

    def write(self, data: str) -> int:
        if not data:
            return 0
        cursor = 0
        while cursor < len(data):
            newline_index = data.find("\n", cursor)
            if newline_index == -1:
                self._emit(data[cursor:])
                break
            self._emit(data[cursor : newline_index + 1])
            cursor = newline_index + 1
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()
        if self._failure_stream is not None:
            self._failure_stream.flush()

    def isatty(self) -> bool:
        return any(getattr(stream, "isatty", lambda: False)() for stream in self._streams)

    def _emit(self, text: str) -> None:
        if not text:
            return
        rendered = text
        if self._at_line_start:
            rendered = _ensure_level_prefix(text)
            if text != "\n" and not _TIMESTAMP_PREFIX_RE.match(text):
                rendered = f"{human_timestamp()} {rendered}"
        
        for stream in self._streams:
            stream.write(rendered)
            stream.flush()
            
        if self._failure_stream is not None and ("WARN" in rendered or "ERROR" in rendered or "CRITICAL" in rendered):
            self._failure_stream.write(rendered)
            self._failure_stream.flush()
            
        self._at_line_start = text.endswith("\n")


@dataclass
class LoggingHandle:
    stdout: TextIO
    stderr: TextIO
    stdout_wrapper: TimestampedWriter
    stderr_wrapper: TimestampedWriter
    log_file: TextIO | None = None
    audit_file: TextIO | None = None
    failure_file: TextIO | None = None

    def restore(self) -> None:
        sys.stdout = self.stdout
        sys.stderr = self.stderr
        if self.log_file is not None:
            self.log_file.flush()
            self.log_file.close()
        if self.audit_file is not None:
            self.audit_file.flush()
            self.audit_file.close()
        if self.failure_file is not None:
            self.failure_file.flush()
            self.failure_file.close()


def install_process_logging(
    *,
    run_id: str,
    component: str,
    log_path: Path | None = None,
    audit_path: Path | None = None,
    failure_path: Path | None = None,
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
        
    failure_file = None
    if failure_path is not None:
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_file = failure_path.open("a", encoding="utf-8")

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

    stdout_wrapper = TimestampedWriter(*stdout_streams, failure_stream=failure_file)
    stderr_wrapper = TimestampedWriter(*stderr_streams, failure_stream=failure_file)
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
        failure_file=failure_file,
    )
    return handle


def _ensure_level_prefix(message: str, *, default_level: str = "INFO") -> str:
    text = str(message)
    if not text or text == "\n":
        return text
    if _TIMESTAMP_PREFIX_RE.match(text):
        return text
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    match = _LEVEL_PREFIX_RE.match(text)
    if match:
        level = match.group(2) or match.group(3) or default_level
        rest = text[match.end():]
        return f"{level} {leading}{rest}"
    return f"{default_level} {leading}{stripped}"


def format_human_log_line(message: str, *, level: str = "INFO") -> str:
    return f"{human_timestamp()} {_ensure_level_prefix(message, default_level=level)}"


def log(message: str, *, level: str = "INFO") -> None:
    print(_ensure_level_prefix(message, default_level=level))


def add_llm_debug_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose streamed LM Studio reasoning/output logs.",
    )


def set_llm_debug_enabled(enabled: bool) -> None:
    global _LLM_DEBUG_ENABLED
    _LLM_DEBUG_ENABLED = bool(enabled)


def llm_debug_enabled() -> bool:
    return _LLM_DEBUG_ENABLED


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
        state.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with state.audit_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
