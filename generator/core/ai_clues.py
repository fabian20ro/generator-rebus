"""LM Studio helpers for clue generation, verification, rewrite, and rating."""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass

from openai import OpenAI

from ..config import LMSTUDIO_BASE_URL
from .clue_family import clue_uses_same_family, forbidden_definition_stems
from .diacritics import normalize
from .quality import ENGLISH_HOMOGRAPH_HINTS, PRESET_DEFINITIONS


DEFINITION_SYSTEM_PROMPT = (
    "Ești autor de definiții de rebus în limba română.\n"
    "IMPORTANT: Toate cuvintele sunt exclusiv în limba ROMÂNĂ. "
    "Chiar dacă arată ca un cuvânt englezesc, definește-l DOAR cu sensul românesc.\n"
    "Reguli:\n"
    "- Răspunzi cu o singură definiție scurtă.\n"
    "- Tot textul este exclusiv în română. Nu folosești engleză.\n"
    "- Nu incluzi răspunsul și nici derivate evidente ale lui.\n"
    "- Sunt interzise forme din aceeași familie lexicală cu răspunsul.\n"
    "- Dacă sensul direct ar necesita un cuvânt interzis, folosește o perifrază creativă sau o descriere indirectă.\n"
    "- Nu inventezi sensuri. Dacă nu ești sigur, răspunzi exact: [NECLAR]\n"
    "- Preferi definiții precise, naturale, maxim 12 cuvinte.\n"
    "- Pentru cuvinte scurte, abrevieri și forme gramaticale fii literal și exact.\n"
    "- Dacă sensul îți vine doar în engleză sau altă limbă, răspunzi [NECLAR].\n"
    "Exemple corecte:\n"
    "OS -> Țesut dur al scheletului\n"
    "AN -> Unitate de timp egală cu 12 luni\n"
    "OF -> Interjecție care exprimă durere sau regret\n"
    "IN -> Plantă textilă cu flori albastre\n"
    "AT -> Domeniul online al Austriei\n"
    "AI -> Formă a verbului a avea\n"
    "FAR -> Lumină de semnalizare pe coastă\n"
    "CLOU -> Moment culminant\n"
    "Contra-exemple (GREȘIT - sensuri englezești):\n"
    "AN -> Articol nehotărât [GREȘIT]\n"
    "OF -> Prepoziție de posesie [GREȘIT]\n"
    "IN -> Prepoziție de loc [GREȘIT]\n"
    "AT -> Prepoziție de loc [GREȘIT]"
)

REWRITE_SYSTEM_PROMPT = (
    "Ești editor de definiții de rebus în limba română.\n"
    "IMPORTANT: Definește cuvintele DOAR cu sensul lor românesc, nu englezesc.\n"
    "Reguli:\n"
    "- Răspunzi doar cu definiția finală.\n"
    "- Tot textul este exclusiv în română. Nu folosești engleză.\n"
    "- Nu incluzi răspunsul și nici derivate evidente ale lui.\n"
    "- Sunt interzise forme din aceeași familie lexicală cu răspunsul.\n"
    "- Dacă sensul direct ar necesita un cuvânt interzis, folosește o perifrază creativă sau o descriere indirectă.\n"
    "- Fă definiția mai precisă decât cea veche.\n"
    "- Max 12 cuvinte.\n"
    "- Dacă termenul este obscur și nu poți scrie onest, răspunzi exact: [NECLAR]"
)

VERIFY_SYSTEM_PROMPT = (
    "Ești rezolvitor de rebusuri românești.\n"
    "Reguli:\n"
    "- Răspunzi cu un singur cuvânt, fără explicații.\n"
    "- Gândești și răspunzi exclusiv în română.\n"
    "- Dacă primul cuvânt care îți vine este în engleză, îl traduci mental și răspunzi în română.\n"
    "- Dacă definiția indică o abreviere, un simbol, un domeniu internet, o interjecție sau o formă gramaticală, răspunzi exact cu forma scurtă cerută.\n"
    "- Nu reformulezi definiția.\n"
    "- Nu răspunzi cu propoziții.\n"
    "- Nu incluzi taguri, marcaje tehnice sau caractere speciale.\n"
    "- Răspunsul conține doar litere românești.\n"
    "Exemple:\n"
    "Definiție: Domeniul online al Austriei\n"
    "Răspuns: AT\n"
    "Definiție: Țesut dur al scheletului\n"
    "Răspuns: OS\n"
    "Definiție: Formă a verbului a avea\n"
    "Răspuns: AI\n"
    "Definiție: Substanță gazoasă pe care o respirăm\n"
    "Răspuns: AER"
)

RATE_SYSTEM_PROMPT = (
    "Evaluezi o definiție de rebus pe scara 1-10.\n"
    "Întorci trei scoruri distincte:\n"
    "- semantic_score: cât de corectă și onestă este definiția pentru răspunsul dat\n"
    "- guessability_score: cât de probabil este ca un rezolvitor să dea exact răspunsul cerut, de exact lungimea indicată, nu un sinonim mai comun\n"
    "- creativity_score: cât de ingenios exploatează definiția un joc de domenii sau o "
    "ambiguitate surprinzătoare — o definiție directă de dicționar primește 3-4, "
    "o perifrază care face rezolvitorul să se gândească inițial la alt domeniu "
    "primește 8-10 (ex: RIAL -> \"Se plătește la șah\" = surpriză domeniu)\n"
    "Criterii:\n"
    "- dacă include răspunsul, o derivată clară sau aceeași familie lexicală: ambele scoruri foarte mici\n"
    "- dacă duce spre alt răspuns sau spre un sinonim mai uzual: guessability_score mic\n"
    "- dacă e precisă și scurtă: scoruri mari\n"
    "- dacă e banală dar corectă: semantic mediu, guessability mediu sau mic\n"
    "- nu penaliza doar pentru că răspunsul este rar; penalizezi doar dacă definiția este vagă sau duce firesc la alt răspuns mai comun\n"
    "- feedback-ul este exclusiv în română, scurt și concret\n"
    "Răspunzi STRICT JSON: "
    "{\"semantic_score\": <1-10>, \"guessability_score\": <1-10>, \"creativity_score\": <1-10>, \"feedback\": \"<motiv scurt>\"}"
)

CLUE_TIEBREAKER_SYSTEM_PROMPT = (
    "Compari două definiții de rebus românești pentru același răspuns.\n"
    "Alegi varianta mai bună pentru un rebus românesc.\n"
    "Criterii, în ordine:\n"
    "- text exclusiv în română\n"
    "- să nu folosească aceeași familie lexicală cu răspunsul\n"
    "- să fie exactă pentru răspunsul intenționat\n"
    "- să ducă mai probabil la răspunsul exact, nu la un sinonim\n"
    "- la calitate egală, preferă varianta mai scurtă\n"
    "Răspunzi strict cu A sau B."
)

PUZZLE_TIEBREAKER_SYSTEM_PROMPT = (
    "Compari două variante de rebus românesc aproape egale ca scor.\n"
    "Alegi varianta mai bună pentru publicare.\n"
    "Criterii:\n"
    "- definiții mai naturale în română\n"
    "- fără familie lexicală evidentă între răspuns și definiție\n"
    "- vocabular mai prietenos, mai puțin obscur\n"
    "- potențial mai bun de coeziune și titlu final\n"
    "Răspunzi strict cu A sau B."
)

WORD_TYPE_LABELS: dict[str, str] = {"V": "verb", "N": "substantiv", "A": "adjectiv"}

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


@dataclass(frozen=True)
class DefinitionRating:
    semantic_score: int
    guessability_score: int
    feedback: str
    creativity_score: int = 5
    rarity_only_override: bool = False


def compute_rebus_score(guessability: int, creativity: int) -> int:
    return round(0.75 * guessability + 0.25 * creativity)


def create_client() -> OpenAI:
    return OpenAI(
        base_url=f"{LMSTUDIO_BASE_URL}/v1",
        api_key="not-needed",
        timeout=120.0,
        max_retries=1,
    )


def _clean_response(text: str | None) -> str:
    text = (text or "").strip().strip('"').strip("'")
    text = re.sub(r"<\|[^|]*\|>", "", text).strip()
    text = re.sub(
        r"^\*{0,2}(Definiție|Definitie|Răspuns|Raspuns):?\*{0,2}\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if "\n" in text:
        text = text.split("\n")[0].strip()
    return text


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


def _feedback_is_rarity_only(feedback: str) -> bool:
    if not feedback:
        return False
    tokens = _tokens(feedback)
    return bool(tokens & RARITY_MARKERS) and not bool(tokens & AMBIGUITY_MARKERS)


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


def _build_generate_prompt(display_word: str, word: str, length: int, word_type: str = "") -> str:
    prompt = (
        f"Cuvânt: {display_word}\n"
        f"Formă normalizată: {word}\n"
        f"Lungime: {length}\n"
    )
    label = WORD_TYPE_LABELS.get(word_type)
    if label:
        prompt += f"Categorie gramaticală: {label}\n"
    prompt += (
        "\nScrie o definiție de rebus scurtă și exactă. "
        "Răspunde doar cu definiția."
    )
    hint = ENGLISH_HOMOGRAPH_HINTS.get(word.upper())
    if hint:
        prompt += (
            f"\nATENȚIE: Cuvântul {word} este în limba ROMÂNĂ. "
            f"Sensul corect: {hint}. "
            f"NU defini ca și cum ar fi un cuvânt englezesc."
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
) -> str:
    header = (
        f"Răspuns corect: {display_word}\n"
        f"Formă normalizată: {word}\n"
    )
    label = WORD_TYPE_LABELS.get(word_type)
    if label:
        header += f"Categorie gramaticală: {label}\n"
    prompt = (
        f"{header}"
        f"Definiția anterioară: {previous_definition}\n"
        f"{feedback_text}\n"
        f"{bad_example_text}\n"
        "Rescrie definiția mai precis și mai scurt."
    )
    hint = ENGLISH_HOMOGRAPH_HINTS.get(word.upper())
    if hint:
        prompt += (
            f"\nATENȚIE: Cuvântul {word} este în limba ROMÂNĂ. "
            f"Sensul corect: {hint}. "
            f"NU defini ca și cum ar fi un cuvânt englezesc."
        )
    prompt += _family_exclusion_note(word)
    return prompt


def _build_verify_prompt(definition: str, answer_length: int) -> str:
    return (
        f"Definiție: {definition}\n"
        f"Lungime răspuns: {answer_length}\n"
        "Răspuns:"
    )


def _build_rate_prompt(display_word: str, word: str, definition: str, answer_length: int, word_type: str = "") -> str:
    header = (
        f"Cuvânt-răspuns: {display_word}\n"
        f"Formă normalizată: {word}\n"
        f"Lungime răspuns: {answer_length}\n"
    )
    label = WORD_TYPE_LABELS.get(word_type)
    if label:
        header += f"Categorie gramaticală: {label}\n"
    return (
        f"{header}"
        f"Definiție: {definition}\n\n"
        "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. "
        "Răspunde STRICT cu JSON."
    )


def _build_clue_tiebreak_prompt(word: str, answer_length: int, definition_a: str, definition_b: str) -> str:
    return (
        f"Răspuns: {word}\n"
        f"Lungime: {answer_length}\n"
        f"Varianta A: {definition_a}\n"
        f"Varianta B: {definition_b}\n\n"
        "Alege varianta mai bună."
    )


def _build_puzzle_tiebreak_prompt(summary_a: str, summary_b: str) -> str:
    return (
        f"Varianta A:\n{summary_a}\n\n"
        f"Varianta B:\n{summary_b}\n\n"
        "Alege varianta mai bună."
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
    if len(definition) < 5:
        return f"too short ({len(definition)} chars)"
    if _definition_is_invalid(word, definition):
        return "contains answer or family word"
    if contains_english_markers(definition):
        return "English markers detected"
    if _definition_describes_english_meaning(word, definition):
        return "English meaning"
    return None


def generate_definition(
    client: OpenAI,
    word: str,
    original: str,
    theme: str,
    retries: int = 3,
    word_type: str = "",
) -> str:
    """Generate a single clue definition."""
    preset = PRESET_DEFINITIONS.get(word.upper())
    if preset:
        return random.choice(preset)
    display_word = original if original else word.lower()
    length = len(word)
    prompt = _build_generate_prompt(display_word, word, length, word_type=word_type)

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="default",
                messages=[
                    {"role": "system", "content": DEFINITION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=160,
            )
            definition = _clean_response(response.choices[0].message.content)
            if definition == "[NECLAR]":
                return definition
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            rejection = _validate_definition(word, definition)
            if rejection:
                print(f"    [rejected {word}: {rejection}]")
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
) -> str:
    """Rewrite a failed or low-rated clue using feedback."""
    preset = PRESET_DEFINITIONS.get(word.upper())
    if preset:
        alternatives = [d for d in preset if d != previous_definition]
        return random.choice(alternatives) if alternatives else preset[0]
    display_word = original if original else word.lower()
    feedback_parts = []
    if wrong_guess:
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
        display_word, word, previous_definition, feedback_text, bad_example_text, word_type=word_type,
    )

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="default",
                messages=[
                    {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=220,
            )
            definition = _clean_response(response.choices[0].message.content)
            if definition == "[NECLAR]":
                return definition
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            rejection = _validate_definition(word, definition)
            if rejection:
                print(f"    [rewrite rejected {word}: {rejection}]")
                continue
            return definition
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise

    return previous_definition


def verify_definition(client: OpenAI, definition: str, answer_length: int) -> str:
    """Ask AI to guess the word from a clue definition."""
    prompt = _build_verify_prompt(definition, answer_length)

    last_guess = ""
    for attempt in range(2):
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=320,
        )
        guess = _clean_response(response.choices[0].message.content)
        if ":" in guess:
            guess = guess.split(":", 1)[1].strip()
        guess = guess.split()[0] if guess.split() else guess
        last_guess = guess
        if not contains_english_markers(guess):
            return guess
        prompt += "\nAtenție: răspunsul anterior nu a fost în română. Răspunde exclusiv în română."

    return last_guess


def rate_definition(
    client: OpenAI,
    word: str,
    original: str,
    definition: str,
    answer_length: int,
    word_type: str = "",
) -> DefinitionRating:
    """Rate a definition's semantic quality and guessability."""
    display_word = original if original else word.lower()
    prompt = _build_rate_prompt(display_word, word, definition, answer_length, word_type=word_type)

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model="default",
                messages=[
                    {"role": "system", "content": RATE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=260,
            )
            raw = _clean_response(response.choices[0].message.content)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
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
        except Exception:
            pass

    rating = _guard_same_family_rating(
        word,
        definition,
        DefinitionRating(semantic_score=5, guessability_score=5, feedback="", creativity_score=5),
    )
    rating = _guard_english_meaning_rating(word, definition, rating)
    return _guard_definition_centric_rating(rating)


def choose_better_clue_variant(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
) -> str:
    prompt = _build_clue_tiebreak_prompt(word, answer_length, definition_a, definition_b)
    try:
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": CLUE_TIEBREAKER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=20,
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"


def choose_better_puzzle_variant(
    client: OpenAI,
    summary_a: str,
    summary_b: str,
) -> str:
    prompt = _build_puzzle_tiebreak_prompt(summary_a, summary_b)
    try:
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": PUZZLE_TIEBREAKER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=20,
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"
