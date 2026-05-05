from pathlib import Path

import pytest

from app.services.path_safety import UnsafePathError, resolve_under


def test_resolve_under_accepts_child():
    out = resolve_under(Path('/tmp/base'), 'a/b.jsonl')
    assert str(out).endswith('/tmp/base/a/b.jsonl')


def test_resolve_under_rejects_escape():
    with pytest.raises(UnsafePathError):
        resolve_under(Path('/tmp/base'), '../secret.txt')
