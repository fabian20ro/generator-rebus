"""Helper functions for building LLM prompts for different actions."""

import re

from rebus_generator.domain.clue_family import forbidden_definition_stems
from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.quality import ENGLISH_HOMOGRAPH_HINTS
from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.prompts.loader import load_user_template

WORD_TYPE_LABELS: dict[str, str] = {"V": "verb", "N": "substantiv", "A": "adjectiv"}
USAGE_SUFFIX_PRECEDENCE: list[tuple[str, tuple[str, ...]]] = [
    ("(arh.)", (r"\bARHAIC\b", r"\bARHAISM\b", r"\bARH\.\b", r"\bIN LIMBAJ ARHAIC\b")),
    ("(inv.)", (r"\bINVECHIT\b", r"\bIESIT DIN UZ\b", r"\bINV\.\b")),
    ("(reg.)", (r"\bREGIONAL\b", r"\bREGIONALISM\b", r"\bREG\.\b")),
    ("(tehn.)", (r"\bTEHNIC\b", r"\bTERMEN TEHNIC\b", r"\bTEHN\.\b")),
    ("(pop.)", (r"\bPOPULAR\b", r"\bPOP\.\b")),
    ("(fam.)", (r"\bFAMILIAR\b", r"\bFAM\.\b")),
    ("(arg.)", (r"\bARGOTIC\b", r"\bARGOU\b", r"\bARG\.\b")),
    ("(livr.)", (r"\bLIVRESC\b", r"\bLIVR\.\b")),
]
USAGE_SUFFIXES = {suffix for suffix, _markers in USAGE_SUFFIX_PRECEDENCE}
_TRAILING_USAGE_SUFFIX_RE = re.compile(
    r"(?:\s+\((?:arh|inv|reg|tehn|pop|fam|arg|livr)\.\))+\s*$",
    flags=re.IGNORECASE,
)

_ENGLISH_MEANING_PATTERNS: dict[str, list[str]] = {
    "AN": ["articol nehotărât", "articol nehotarat"],
    "OF": [
        "prepoziție de posesie",
        "prepozitie de posesie",
        "indică posesia",
        "indica posesia",
    ],
    "IN": [
        "prepoziție de loc",
        "prepozitie de loc",
        "indică poziția",
        "indica pozitia",
        "prepoziție care indică",
    ],
    "AT": [
        "prepoziție care indică locul",
        "prepozitie care indica locul",
        "prepoziție de loc",
    ],
    "HAT": ["pălărie", "palarie"],
    "NAT": ["network address", "traducere a adreselor", "adreselor ip"],
    "IDE": ["dezvoltare software", "editor și compilator", "mediu de dezvoltare"],
    "REF": ["referință", "referinta"],
}

_PROMPT_RESIDUE_MARKERS = (
    "definiția:",
    "definitia:",
    "propusă:",
    "propusa:",
    "```",
    "{\"",
)

def _strip_trailing_usage_suffixes(definition: str) -> str:
    return _TRAILING_USAGE_SUFFIX_RE.sub("", definition or "").strip()


def _extract_definition_usage_suffix(definition: str) -> str | None:
    matches = re.findall(
        r"\((?:arh|inv|reg|tehn|pop|fam|arg|livr)\.\)",
        definition or "",
        flags=re.IGNORECASE,
    )
    if not matches:
        return None
    return matches[-1].lower()


def _extract_usage_suffix_from_dex(dex_definitions: str) -> str | None:
    if not dex_definitions:
        return None
    normalized_text = normalize(dex_definitions)
    for suffix, patterns in USAGE_SUFFIX_PRECEDENCE:
        if any(re.search(pattern, normalized_text) for pattern in patterns):
            return suffix
    return None


def _normalize_definition_usage_suffix(
    definition: str, required_suffix: str | None
) -> str:
    base = _strip_trailing_usage_suffixes(definition)
    if not required_suffix or required_suffix not in USAGE_SUFFIXES:
        return base
    if not base:
        return required_suffix
    return f"{base} {required_suffix}"


def _build_usage_label_line(required_suffix: str | None, *, purpose: str) -> str:
    if not required_suffix:
        return ""
    if purpose == "generate":
        return (
            f"Marcaj DEX explicit: {required_suffix}\n"
            f"Dacă definești sensul marcat de DEX, încheie definiția exact cu {required_suffix}. "
            "Folosești maximum un singur sufix de acest tip.\n"
        )
    if purpose == "rewrite":
        return (
            f"Marcaj DEX explicit: {required_suffix}\n"
            f"Păstrează sau restaurează exact sufixul final {required_suffix} dacă rescrii sensul marcat de DEX. "
            "Folosești maximum un single sufix de acest tip.\n"
        )
    if purpose == "verify":
        return f"Marcaj de uz explicit în definiție: {required_suffix}\n"
    if purpose == "rate":
        return f"Marcaj DEX permis: {required_suffix}\n"
    return ""


def _definition_describes_english_meaning(word: str, definition: str) -> bool:
    if not definition:
        return False
    lower_def = definition.lower()
    if "engleză" in lower_def or "engleza" in lower_def or "english" in lower_def:
        return True
    patterns = _ENGLISH_MEANING_PATTERNS.get(word.upper(), [])
    return any(pattern in lower_def for pattern in patterns)


def _family_exclusion_note(word: str) -> str:
    """Build a prompt note listing forbidden word forms for family leakage prevention."""
    stems = forbidden_definition_stems(word)
    if not stems:
        return ""
    joined = ", ".join(stems)
    return (
        f"\nATENȚIE — Cuvinte complet interzise în definiție: {joined}.\n"
        "Orice cuvânt care conține aceste rădăcini este interzis.\n"
        "Folosește o perifrază creativă, fără nicio legătură lexicală cu răspunsul."
    )


def _build_generate_prompt(
    display_word: str,
    word: str,
    length: int,
    word_type: str = "",
    dex_definitions: str = "",
) -> str:
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    prompt = load_user_template("generate").format(
        display_word=display_word,
        word=word,
        length=length,
        usage_label_line=_build_usage_label_line(required_suffix, purpose="generate"),
    )
    prompt += (
        "\nDefiniția trebuie să fie o formulare completă, nu un singur cuvânt izolat."
    )
    label = WORD_TYPE_LABELS.get(word_type)
    if label:
        prompt = prompt.replace(
            f"Lungime: {length}", f"Lungime: {length}\nCategorie gramaticală: {label}"
        )
    hint = ENGLISH_HOMOGRAPH_HINTS.get(word.upper())
    if hint:
        prompt += (
            f"\nATENȚIE: Cuvântul {word} este în limba ROMÂNĂ. "
            f"Sensul corect: {hint}. "
            f"NU defini ca și cum ar fi un cuvânt englezesc."
        )
    if dex_definitions:
        prompt += (
            f"\nDefiniții DEX (referință):\n{dex_definitions}\n"
            "Folosește aceste sensuri ca bază, dar reformulează creativ pentru rebus."
        )
    prompt += _family_exclusion_note(word)
    return prompt


def _append_existing_canonical_definitions(
    prompt: str, existing_definitions: list[str] | None
) -> str:
    if not existing_definitions:
        return prompt
    lines = [f"- {definition}" for definition in existing_definitions if definition]
    if not lines:
        return prompt
    return (
        prompt
        + "\nDefiniții canonice deja folosite pentru același cuvânt:\n"
        + "\n".join(lines)
        + "\nEvită să reformulezi aceeași idee aproape identic. "
        "Dacă poți, alege un alt unghi semantic clar distinct."
    )


def _build_rewrite_prompt(
    display_word: str,
    word: str,
    previous_definition: str,
    feedback_text: str,
    bad_example_text: str,
    word_type: str = "",
    dex_definitions: str = "",
    failure_history: list[tuple[str, list[str]]] | None = None,
) -> str:
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    label = WORD_TYPE_LABELS.get(word_type)
    word_type_line = f"Categorie gramaticală: {label}\n" if label else ""
    history_text = ""
    if failure_history:
        recent = failure_history[-5:]
        lines = [
            f"{i}. '{defn}' → propus: {', '.join(guesses) if guesses else '[nimic]'}"
            for i, (defn, guesses) in enumerate(recent, 1)
        ]
        history_text = "\nÎncercări anterioare eșuate:\n" + "\n".join(lines) + "\n"
    prompt = load_user_template("rewrite").format(
        display_word=display_word,
        word=word,
        word_type_line=word_type_line,
        previous_definition=previous_definition,
        feedback_text=feedback_text,
        bad_example_text=bad_example_text,
        failure_history_text=history_text,
        usage_label_line=_build_usage_label_line(required_suffix, purpose="rewrite"),
    )
    prompt += "\nDefiniția nouă trebuie să fie completă și naturală, nu un singur cuvânt izolat."
    hint = ENGLISH_HOMOGRAPH_HINTS.get(word.upper())
    if hint:
        prompt += (
            f"\nATENȚIE: Cuvântul {word} este în limba ROMÂNĂ. "
            f"Sensul corect: {hint}. "
            f"NU defini ca și cum ar fi un cuvânt englezesc."
        )
    if dex_definitions:
        prompt += (
            f"\nDefiniții DEX (referință):\n{dex_definitions}\n"
            "Folosește aceste sensuri ca bază, dar reformulează creativ pentru rebus."
        )
    prompt += _family_exclusion_note(word)
    return prompt


def _word_type_line(word_type: str) -> str:
    label = WORD_TYPE_LABELS.get(word_type)
    return f"Categorie gramaticală: {label}\n" if label else ""


def _build_verify_prompt(
    definition: str,
    answer_length: int,
    word_type: str = "",
    max_guesses: int = VERIFY_CANDIDATE_COUNT,
) -> str:
    used_suffix = _extract_definition_usage_suffix(definition)
    return load_user_template("verify").format(
        word_type_line=_word_type_line(word_type),
        usage_label_line=_build_usage_label_line(used_suffix, purpose="verify"),
        definition=definition,
        answer_length=answer_length,
        max_guesses=max_guesses,
    )


def _build_rate_prompt(
    display_word: str,
    word: str,
    definition: str,
    answer_length: int,
    word_type: str = "",
    dex_definitions: str = "",
) -> str:
    allowed_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    used_suffix = _extract_definition_usage_suffix(definition)
    suffix_status_line = ""
    if used_suffix and allowed_suffix == used_suffix:
        suffix_status_line = (
            f"Eticheta folosită în definiție: {used_suffix}\n"
            "Eticheta corespunde explicit unui sens DEX marcat.\n"
        )
    elif used_suffix and allowed_suffix != used_suffix:
        suffix_status_line = (
            f"Eticheta folosită în definiție: {used_suffix}\n"
            "Eticheta din definiție nu este susținută explicit de DEX pentru acest cuvânt.\n"
        )
    elif allowed_suffix:
        suffix_status_line = (
            f"Eticheta permisă de DEX: {allowed_suffix}\n"
            "Definiția putea folosi această etichetă pentru a disambigua sensul marcat.\n"
        )
    prompt = load_user_template("rate").format(
        display_word=display_word,
        word=word,
        answer_length=answer_length,
        word_type_line=_word_type_line(word_type),
        usage_label_line=_build_usage_label_line(allowed_suffix, purpose="rate"),
        suffix_status_line=suffix_status_line,
        definition=definition,
    )
    if dex_definitions:
        prompt += (
            f"\nDefiniții DEX (referință):\n{dex_definitions}\n"
            "Folosește-le pentru a evalua corectitudinea și originalitatea definiției."
        )
    return prompt


def _build_clue_tiebreak_prompt(
    word: str, answer_length: int, definition_a: str, definition_b: str
) -> str:
    return load_user_template("clue_tiebreak").format(
        word=word,
        answer_length=answer_length,
        definition_a=definition_a,
        definition_b=definition_b,
    )


def _build_clue_compare_prompt(
    word: str, answer_length: int, definition_a: str, definition_b: str
) -> str:
    return load_user_template("clue_compare").format(
        word=word,
        answer_length=answer_length,
        definition_a=definition_a,
        definition_b=definition_b,
    )


def _build_puzzle_tiebreak_prompt(summary_a: str, summary_b: str) -> str:
    return load_user_template("puzzle_tiebreak").format(
        summary_a=summary_a,
        summary_b=summary_b,
    )


def has_prompt_residue(text: str | None) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return False
    return any(marker in lower for marker in _PROMPT_RESIDUE_MARKERS)

def _augment_definition_retry_prompt(prompt: str, rejection: str) -> str:
    return (
        prompt
        + f"\nRăspunsul anterior a fost respins: {rejection}."
        + "\nRăspunde cu o definiție completă, naturală, de minimum 2 cuvinte."
        + "\nNu te opri la un gloss minimal și nu lăsa ultimul cuvânt neterminat."
    )
