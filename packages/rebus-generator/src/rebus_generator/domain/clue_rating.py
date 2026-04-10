"""Helpers for storing and parsing clue quality notes."""

from __future__ import annotations


SEMANTIC_LABEL = "Scor semantic:"
GUESSABILITY_LABEL = "Scor ghicibilitate:"
REBUS_LABEL = "Scor rebus:"
CREATIVITY_LABEL = "Scor creativitate:"
VERIFY_GUESS_LABEL = "AI a ghicit:"
VERIFY_CANDIDATES_LABEL = "AI a propus:"


def append_rating_to_note(
    existing_note: str,
    *,
    semantic_score: int,
    guessability_score: int,
    feedback: str = "",
    creativity_score: int | None = None,
    rebus_score: int | None = None,
) -> str:
    parts = []
    if existing_note:
        parts.append(existing_note)
    parts.append(f"{SEMANTIC_LABEL} {semantic_score}/10")
    if rebus_score is not None:
        parts.append(f"{REBUS_LABEL} {rebus_score}/10")
    else:
        parts.append(f"{GUESSABILITY_LABEL} {guessability_score}/10")
    if creativity_score is not None:
        parts.append(f"{CREATIVITY_LABEL} {creativity_score}/10")
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
    """Extract guessability from old-format notes (backward compat)."""
    score = _extract_labeled_score(note, GUESSABILITY_LABEL)
    if score is not None:
        return score
    return _extract_labeled_score(note, REBUS_LABEL)


def extract_rebus_score(note: str) -> int | None:
    score = _extract_labeled_score(note, REBUS_LABEL)
    if score is not None:
        return score
    return _extract_labeled_score(note, GUESSABILITY_LABEL)


def extract_creativity_score(note: str) -> int | None:
    return _extract_labeled_score(note, CREATIVITY_LABEL)


def extract_feedback(note: str) -> str:
    if not note:
        return ""
    parts = note.split(" | ")
    for part in parts:
        if (
            not part.startswith(SEMANTIC_LABEL)
            and not part.startswith(GUESSABILITY_LABEL)
            and not part.startswith(REBUS_LABEL)
            and not part.startswith(CREATIVITY_LABEL)
            and not part.startswith(VERIFY_GUESS_LABEL)
            and not part.startswith(VERIFY_CANDIDATES_LABEL)
        ):
            return part
    return ""


def extract_verify_candidates(note: str) -> list[str]:
    if not note:
        return []
    if VERIFY_CANDIDATES_LABEL in note:
        raw = note.split(VERIFY_CANDIDATES_LABEL, 1)[1].split("|", 1)[0].strip()
        return [candidate.strip() for candidate in raw.split(",") if candidate.strip()]
    if VERIFY_GUESS_LABEL in note:
        wrong_guess = note.split(VERIFY_GUESS_LABEL, 1)[1].split("|", 1)[0].strip()
        return [wrong_guess] if wrong_guess else []
    return []


def extract_wrong_guess(note: str) -> str:
    candidates = extract_verify_candidates(note)
    return candidates[0] if candidates else ""
