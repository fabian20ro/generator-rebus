"""Helpers for storing and parsing clue quality notes."""

from __future__ import annotations


SEMANTIC_LABEL = "Scor semantic:"
GUESSABILITY_LABEL = "Scor ghicibilitate:"
VERIFY_GUESS_LABEL = "AI a ghicit:"


def append_rating_to_note(
    existing_note: str,
    *,
    semantic_score: int,
    guessability_score: int,
    feedback: str = "",
) -> str:
    parts = []
    if existing_note:
        parts.append(existing_note)
    parts.append(f"{SEMANTIC_LABEL} {semantic_score}/10")
    parts.append(f"{GUESSABILITY_LABEL} {guessability_score}/10")
    if feedback:
        parts.append(feedback.strip())
    return " | ".join(parts)


def _extract_labeled_score(note: str, label: str) -> int | None:
    if not note or label not in note:
        return None
    try:
        return int(note.split(label, 1)[1].split("/", 1)[0].strip())
    except (ValueError, IndexError):
        return None


def extract_semantic_score(note: str) -> int | None:
    return _extract_labeled_score(note, SEMANTIC_LABEL)


def extract_guessability_score(note: str) -> int | None:
    return _extract_labeled_score(note, GUESSABILITY_LABEL)


def extract_feedback(note: str) -> str:
    if not note:
        return ""
    parts = note.split(" | ")
    for part in parts:
        if (
            not part.startswith(SEMANTIC_LABEL)
            and not part.startswith(GUESSABILITY_LABEL)
            and not part.startswith(VERIFY_GUESS_LABEL)
        ):
            return part
    return ""


def extract_wrong_guess(note: str) -> str:
    if not note or VERIFY_GUESS_LABEL not in note:
        return ""
    return note.split(VERIFY_GUESS_LABEL, 1)[1].split("|", 1)[0].strip()
