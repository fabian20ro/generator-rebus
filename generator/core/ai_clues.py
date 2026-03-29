"""LM Studio helpers for clue generation, verification, rewrite, and rating."""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass

from openai import OpenAI

from ..config import LMSTUDIO_BASE_URL, VERIFY_CANDIDATE_COUNT
from ..prompts.loader import load_system_prompt, load_user_template
from .clue_family import clue_uses_same_family, forbidden_definition_stems
from .diacritics import normalize
from .llm_text import clean_llm_text_response
from .model_manager import PRIMARY_MODEL, chat_reasoning_options
from .quality import ENGLISH_HOMOGRAPH_HINTS

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

RATE_MIN_SEMANTIC = 7
RATE_MIN_REBUS = 5
ENGLISH_MARKERS = {
    "accurate",
    "accurately",
    "actually",
    "answer",
    "attached",
    "big",
    "common",
    "correct",
    "definition",
    "english",
    "fantasy",
    "feedback",
    "file",
    "for",
    "get",
    "guess",
    "guessability",
    "law",
    "length",
    "numerical",
    "precise",
    "precisely",
    "response",
    "semantic",
    "the",
    "very",
    "with",
    "without",
    "word",
}
RARITY_MARKERS = {
    "rar",
    "rară",
    "rare",
    "raritate",
    "neuzual",
    "neobișnuit",
    "neobisnuit",
    "puțin",
    "putin",
    "comun",
    "uzual",
    "obisnuit",
}
AMBIGUITY_MARKERS = {
    "alt",
    "altul",
    "ambig",
    "ambigua",
    "ambiguu",
    "sinonim",
    "vag",
    "vagă",
    "vaga",
    "firesc",
    "duce",
    "răspuns",
    "raspuns",
    "familie",
    "lexical",
}

DANGLING_ENDING_MARKERS = {
    "a",
    "ai",
    "al",
    "ale",
    "asupra",
    "ca",
    "că",
    "cu",
    "de",
    "din",
    "după",
    "dupa",
    "fără",
    "fara",
    "in",
    "în",
    "la",
    "o",
    "ori",
    "pe",
    "pentru",
    "prin",
    "sau",
    "si",
    "spre",
    "un",
    "unei",
    "unor",
    "unui",
    "și",
}


@dataclass(frozen=True)
class DefinitionRating:
    semantic_score: int
    guessability_score: int
    feedback: str
    creativity_score: int = 5
    rarity_only_override: bool = False


@dataclass(frozen=True)
class VerifyResult:
    candidates: list[str]

    @property
    def primary_guess(self) -> str:
        return self.candidates[0] if self.candidates else ""


def compute_rebus_score(guessability: int, creativity: int) -> int:
    return round(0.75 * guessability + 0.25 * creativity)


def create_client() -> OpenAI:
    return OpenAI(
        base_url=f"{LMSTUDIO_BASE_URL}/v1",
        api_key="not-needed",
        timeout=120.0,
        max_retries=1,
    )


def _resolve_model_name(model: str | None) -> str:
    if not model or not str(model).strip():
        raise ValueError("Explicit LM Studio model_id required")
    return str(model).strip()


def _clean_response(text: str | None) -> str:
    return clean_llm_text_response(text)


def _chat_completion_create(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    purpose: str = "default",
):
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **chat_reasoning_options(model, purpose=purpose),
    )


def contains_english_markers(text: str | None) -> bool:
    if not text:
        return False
    tokens = {token.lower() for token in re.findall(r"[A-Za-z]+", text)}
    return any(token in ENGLISH_MARKERS for token in tokens)


def _definition_mentions_answer(answer: str, definition: str) -> bool:
    if not definition:
        return False
    normalized_definition = normalize(definition).lower()
    pattern = rf"\b{re.escape(answer.lower())}\b"
    return re.search(pattern, normalized_definition) is not None


def _definition_is_invalid(answer: str, definition: str) -> bool:
    return _definition_mentions_answer(answer, definition) or clue_uses_same_family(answer, definition)


def _same_family_feedback() -> str:
    return "Definiția folosește aceeași familie lexicală ca răspunsul."


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț]+", normalize(text))}


def _last_word(text: str) -> str:
    tokens = re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", normalize(text))
    return tokens[-1].lower() if tokens else ""


def _feedback_is_rarity_only(feedback: str) -> bool:
    if not feedback:
        return False
    tokens = _tokens(feedback)
    return bool(tokens & RARITY_MARKERS) and not bool(tokens & AMBIGUITY_MARKERS)


def _strip_trailing_usage_suffixes(definition: str) -> str:
    return _TRAILING_USAGE_SUFFIX_RE.sub("", definition or "").strip()


def _extract_definition_usage_suffix(definition: str) -> str | None:
    matches = re.findall(r"\((?:arh|inv|reg|tehn|pop|fam|arg|livr)\.\)", definition or "", flags=re.IGNORECASE)
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


def _normalize_definition_usage_suffix(definition: str, required_suffix: str | None) -> str:
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
            "Folosești maximum un singur sufix de acest tip.\n"
        )
    if purpose == "verify":
        return f"Marcaj de uz explicit în definiție: {required_suffix}\n"
    if purpose == "rate":
        return f"Marcaj DEX permis: {required_suffix}\n"
    return ""


_ENGLISH_MEANING_PATTERNS: dict[str, list[str]] = {
    "AN": ["articol nehotărât", "articol nehotarat"],
    "OF": ["prepoziție de posesie", "prepozitie de posesie", "indică posesia", "indica posesia"],
    "IN": ["prepoziție de loc", "prepozitie de loc", "indică poziția", "indica pozitia", "prepoziție care indică"],
    "AT": ["prepoziție care indică locul", "prepozitie care indica locul", "prepoziție de loc"],
    "HAT": ["pălărie", "palarie"],
    "NAT": ["network address", "traducere a adreselor", "adreselor ip"],
    "IDE": ["dezvoltare software", "editor și compilator", "mediu de dezvoltare"],
    "REF": ["referință", "referinta"],
}


def _definition_describes_english_meaning(word: str, definition: str) -> bool:
    if not definition:
        return False
    lower_def = definition.lower()
    if "engleză" in lower_def or "engleza" in lower_def or "english" in lower_def:
        return True
    patterns = _ENGLISH_MEANING_PATTERNS.get(word.upper(), [])
    return any(pattern in lower_def for pattern in patterns)


def _guard_english_meaning_rating(
    word: str, definition: str, rating: DefinitionRating,
) -> DefinitionRating:
    if not _definition_describes_english_meaning(word, definition):
        return rating
    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback="Definiția descrie sensul englezesc, nu cel românesc.",
        creativity_score=1,
    )


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


def _build_generate_prompt(display_word: str, word: str, length: int, word_type: str = "", dex_definitions: str = "") -> str:
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    prompt = load_user_template("generate").format(
        display_word=display_word,
        word=word,
        length=length,
        usage_label_line=_build_usage_label_line(required_suffix, purpose="generate"),
    )
    prompt += "\nDefiniția trebuie să fie o formulare completă, nu un singur cuvânt izolat."
    label = WORD_TYPE_LABELS.get(word_type)
    if label:
        prompt = prompt.replace(f"Lungime: {length}", f"Lungime: {length}\nCategorie gramaticală: {label}")
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


def _build_rate_prompt(display_word: str, word: str, definition: str, answer_length: int, word_type: str = "", dex_definitions: str = "") -> str:
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


def _build_clue_tiebreak_prompt(word: str, answer_length: int, definition_a: str, definition_b: str) -> str:
    return load_user_template("clue_tiebreak").format(
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


def _guard_same_family_rating(word: str, definition: str, rating: DefinitionRating) -> DefinitionRating:
    if not clue_uses_same_family(word, definition):
        return rating
    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback=_same_family_feedback(),
        creativity_score=1,
    )


def _guard_definition_centric_rating(rating: DefinitionRating) -> DefinitionRating:
    if rating.semantic_score < 8:
        return rating
    if not _feedback_is_rarity_only(rating.feedback):
        return rating
    return DefinitionRating(
        semantic_score=rating.semantic_score,
        guessability_score=rating.guessability_score,
        feedback=rating.feedback,
        creativity_score=rating.creativity_score,
        rarity_only_override=True,
    )


def _pick_tiebreak_winner(raw: str) -> str:
    cleaned = _clean_response(raw).upper()
    if cleaned.startswith("B"):
        return "B"
    return "A"


def _clamp_score(value: int | str | None, default: int = 5) -> int:
    try:
        score = int(value if value is not None else default)
    except (TypeError, ValueError):
        score = default
    return max(1, min(10, score))


def _validate_definition(word: str, definition: str) -> str | None:
    """Return rejection reason, or None if acceptable."""
    clean_definition = _strip_trailing_usage_suffixes(definition)
    if len(clean_definition) < 5:
        return f"too short ({len(clean_definition)} chars)"
    if len(re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", clean_definition)) < 2:
        return "single-word gloss"
    if _last_word(clean_definition) in DANGLING_ENDING_MARKERS:
        return "dangling ending"
    if _definition_is_invalid(word, clean_definition):
        return "contains answer or family word"
    if contains_english_markers(clean_definition):
        return "English markers detected"
    if _definition_describes_english_meaning(word, clean_definition):
        return "English meaning"
    return None


def _augment_definition_retry_prompt(prompt: str, rejection: str) -> str:
    return (
        prompt
        + f"\nRăspunsul anterior a fost respins: {rejection}."
        + "\nRăspunde cu o definiție completă, naturală, de minimum 2 cuvinte."
        + "\nNu te opri la un gloss minimal și nu lăsa ultimul cuvânt neterminat."
    )


def _clean_verify_chunk(text: str | None) -> str:
    chunk = (text or "").strip().strip('"').strip("'")
    chunk = re.sub(r"<\|[^|]*\|>", "", chunk).strip()
    chunk = re.sub(
        r"^\s*(?:[-*•]+|\d+[.)]\s*|(?:Răspunsuri|Raspunsuri|Răspuns|Raspuns|Cuvinte):\s*)",
        "",
        chunk,
        flags=re.IGNORECASE,
    ).strip()
    token_match = re.search(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", chunk)
    return token_match.group(0) if token_match else ""


def _extract_verify_candidates(raw: str, answer_length: int, max_guesses: int) -> list[str]:
    pieces = re.split(r"[\n,;/|]+", raw or "")
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(candidate: str) -> None:
        normalized = normalize(candidate)
        if not normalized or len(normalized) != answer_length:
            return
        if contains_english_markers(candidate) or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(candidate.upper())

    for piece in pieces:
        candidate = _clean_verify_chunk(piece)
        if candidate:
            _append(candidate)
        if len(candidates) >= max_guesses:
            return candidates[:max_guesses]

    if candidates:
        return candidates[:max_guesses]

    fallback_tokens = re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", raw or "")
    for token in fallback_tokens:
        _append(token)
        if len(candidates) >= max_guesses:
            break
    return candidates[:max_guesses]


def generate_definition(
    client: OpenAI,
    word: str,
    original: str,
    theme: str,
    retries: int = 3,
    word_type: str = "",
    dex_definitions: str = "",
    temperature: float | None = None,
    model: str | None = None,
) -> str:
    """Generate a single clue definition."""
    display_word = original if original else word.lower()
    length = len(word)
    prompt = _build_generate_prompt(display_word, word, length, word_type=word_type, dex_definitions=dex_definitions)
    system_prompt = load_system_prompt("definition")
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    print(f"  [LLM prompt] word={word} system={len(system_prompt)} chars")
    print(f"  [LLM user prompt]\n{prompt}")

    for attempt in range(retries):
        try:
            resolved_model = _resolve_model_name(model)
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature if temperature is not None else 0.2,
                max_tokens=2000,
                purpose="definition_generate",
            )
            definition = _clean_response(response.choices[0].message.content)
            if definition == "[NECLAR]":
                return definition
            definition = _normalize_definition_usage_suffix(definition, required_suffix)
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            rejection = _validate_definition(word, definition)
            if rejection:
                print(f"    [rejected {word}: {rejection}]")
                prompt = _augment_definition_retry_prompt(prompt, rejection)
                continue
            return definition
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise

    return "[Definiție negenerată]"


def rewrite_definition(
    client: OpenAI,
    word: str,
    original: str,
    theme: str,
    previous_definition: str,
    wrong_guess: str,
    retries: int = 2,
    rating_feedback: str = "",
    bad_example_definition: str = "",
    bad_example_reason: str = "",
    word_type: str = "",
    dex_definitions: str = "",
    failure_history: list[tuple[str, list[str]]] | None = None,
    wrong_guesses: list[str] | None = None,
    temperature: float | None = None,
    model: str | None = None,
) -> str:
    """Rewrite a failed or low-rated clue using feedback."""
    display_word = original if original else word.lower()
    feedback_parts = []
    if wrong_guesses:
        feedback_parts.append(f"Rezolvitorul a propus: {', '.join(wrong_guesses)}")
    elif wrong_guess:
        feedback_parts.append(f"Rezolvitorul a ghicit: {wrong_guess}")
    if rating_feedback:
        feedback_parts.append(f"Feedback calitate: {rating_feedback}")
    feedback_text = "\n".join(feedback_parts) if feedback_parts else "[niciun feedback]"
    bad_example_text = ""
    if bad_example_definition and bad_example_reason:
        bad_example_text = (
            "\nExemplu de definiție rea de evitat:\n"
            f"- Definiție respinsă: {bad_example_definition}\n"
            f"- Motiv: {bad_example_reason}\n"
            "- Nu produce ceva similar cu această definiție respinsă.\n"
        )
    prompt = _build_rewrite_prompt(
        display_word, word, previous_definition, feedback_text, bad_example_text,
        word_type=word_type, dex_definitions=dex_definitions,
        failure_history=failure_history,
    )
    system_prompt = load_system_prompt("rewrite")
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    print(f"  [LLM rewrite prompt] word={word} system={len(system_prompt)} chars")
    print(f"  [LLM user prompt]\n{prompt}")

    for attempt in range(retries):
        try:
            resolved_model = _resolve_model_name(model)
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature if temperature is not None else 0.3,
                max_tokens=220,
                purpose="definition_rewrite",
            )
            definition = _clean_response(response.choices[0].message.content)
            if definition == "[NECLAR]":
                return definition
            definition = _normalize_definition_usage_suffix(definition, required_suffix)
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            rejection = _validate_definition(word, definition)
            if rejection:
                print(f"    [rewrite rejected {word}: {rejection}]")
                prompt = _augment_definition_retry_prompt(prompt, rejection)
                continue
            return definition
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise

    return previous_definition


def verify_definition_candidates(
    client: OpenAI,
    definition: str,
    answer_length: int,
    word_type: str = "",
    max_guesses: int = VERIFY_CANDIDATE_COUNT,
    model: str | None = None,
) -> VerifyResult:
    """Ask AI to suggest up to max_guesses candidate answers for a clue definition."""
    prompt = _build_verify_prompt(
        definition,
        answer_length,
        word_type=word_type,
        max_guesses=max_guesses,
    )

    last_candidates: list[str] = []
    for attempt in range(2):
        resolved_model = _resolve_model_name(model)
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("verify")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=320,
            purpose="definition_verify",
        )
        raw = response.choices[0].message.content or ""
        candidates = _extract_verify_candidates(raw, answer_length, max_guesses=max_guesses)
        last_candidates = candidates
        if candidates:
            return VerifyResult(candidates)
        prompt += "\nAtenție: răspunsul anterior nu a fost în română. Răspunde exclusiv în română."

    return VerifyResult(last_candidates)


def verify_definition(
    client: OpenAI,
    definition: str,
    answer_length: int,
    word_type: str = "",
    model: str | None = None,
) -> str:
    """Backward-compatible single-guess wrapper over the multi-candidate verifier."""
    return verify_definition_candidates(
        client,
        definition,
        answer_length,
        word_type=word_type,
        max_guesses=1,
        model=model or PRIMARY_MODEL.model_id,
    ).primary_guess


def rate_definition(
    client: OpenAI,
    word: str,
    original: str,
    definition: str,
    answer_length: int,
    word_type: str = "",
    dex_definitions: str = "",
    model: str | None = None,
) -> DefinitionRating | None:
    """Rate a definition's semantic quality and guessability.

    Returns None when the model's response cannot be parsed as valid JSON,
    signaling that the definition should be treated as unrated.
    """
    display_word = original if original else word.lower()
    prompt = _build_rate_prompt(display_word, word, definition, answer_length, word_type=word_type, dex_definitions=dex_definitions)
    system_prompt = load_system_prompt("rate")

    for attempt in range(2):
        try:
            resolved_model = _resolve_model_name(model)
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=260,
                purpose="definition_rate",
            )
            raw = response.choices[0].message.content or ""
            fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            bare_match = re.search(r"\{.*\}", raw, re.DOTALL)
            match = fence_match or bare_match
            if match:
                json_str = match.group(1) if fence_match and match is fence_match else match.group()
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    prompt += (
                        "\nRăspunsul anterior nu a fost JSON valid. "
                        "Răspunde acum strict cu un singur obiect JSON valid, fără text suplimentar."
                    )
                    continue
                feedback = str(data.get("feedback", "")).strip()
                if contains_english_markers(feedback):
                    prompt += "\nAtenție: feedback-ul anterior nu a fost în română. Refă-l exclusiv în română."
                    continue
                rating = DefinitionRating(
                    semantic_score=_clamp_score(data.get("semantic_score")),
                    guessability_score=_clamp_score(data.get("guessability_score")),
                    feedback=feedback,
                    creativity_score=_clamp_score(data.get("creativity_score")),
                )
                rating = _guard_same_family_rating(word, definition, rating)
                rating = _guard_english_meaning_rating(word, definition, rating)
                return _guard_definition_centric_rating(rating)
            prompt += (
                "\nRăspunsul anterior nu a fost JSON valid. "
                "Răspunde acum strict cu un singur obiect JSON valid, fără text suplimentar."
            )
        except Exception:
            pass

    return None


def choose_better_clue_variant(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    model: str | None = None,
) -> str:
    prompt = _build_clue_tiebreak_prompt(word, answer_length, definition_a, definition_b)
    try:
        resolved_model = _resolve_model_name(model)
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("clue_tiebreaker")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=20,
            purpose="clue_tiebreaker",
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"


def choose_better_puzzle_variant(
    client: OpenAI,
    summary_a: str,
    summary_b: str,
    model: str | None = None,
) -> str:
    prompt = _build_puzzle_tiebreak_prompt(summary_a, summary_b)
    try:
        resolved_model = _resolve_model_name(model)
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("puzzle_tiebreaker")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=20,
            purpose="puzzle_tiebreaker",
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"
