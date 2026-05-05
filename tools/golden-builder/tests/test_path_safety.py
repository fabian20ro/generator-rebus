import pytest

from app.services.path_safety import UnsafePathError, sanitize_name


def test_sanitize_name_accepts_simple_file():
    assert sanitize_name('a-1.jsonl', default='x.jsonl') == 'a-1.jsonl'


def test_sanitize_name_drops_path_parts():
    assert sanitize_name('../abc.jsonl', default='x.jsonl') == 'abc.jsonl'


def test_sanitize_name_rejects_unsafe_chars():
    with pytest.raises(UnsafePathError):
        sanitize_name('bad$name.jsonl', default='x.jsonl')
