"""Romanian diacritic normalization for crossword grids."""

DIACRITIC_MAP: dict[str, str] = {
    "Ă": "A", "ă": "A", "Â": "A", "â": "A",
    "Î": "I", "î": "I",
    "Ș": "S", "ș": "S", "Ş": "S", "ş": "S",  # comma + cedilla
    "Ț": "T", "ț": "T", "Ţ": "T", "ţ": "T",  # comma + cedilla
}


def normalize(word: str) -> str:
    """Normalize a Romanian word to ASCII uppercase for the grid."""
    return "".join(DIACRITIC_MAP.get(ch, ch) for ch in word.upper())
