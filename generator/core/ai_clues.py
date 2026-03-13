"""LM Studio helpers for clue generation, verification, rewrite, and rating."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from openai import OpenAI

from ..config import LMSTUDIO_BASE_URL
from .clue_family import clue_uses_same_family
from .diacritics import normalize


DEFINITION_SYSTEM_PROMPT = (
    "Ești autor de definiții de rebus în limba română.\n"
    "Reguli:\n"
    "- Răspunzi cu o singură definiție scurtă.\n"
    "- Tot textul este exclusiv în română. Nu folosești engleză.\n"
    "- Nu incluzi răspunsul și nici derivate evidente ale lui.\n"
    "- Sunt interzise forme din aceeași familie lexicală cu răspunsul.\n"
    "- Nu inventezi sensuri. Dacă nu ești sigur, răspunzi exact: [NECLAR]\n"
    "- Preferi definiții precise, naturale, maxim 12 cuvinte.\n"
    "- Pentru cuvinte scurte, abrevieri și forme gramaticale fii literal și exact.\n"
    "- Dacă sensul îți vine doar în engleză sau altă limbă, răspunzi [NECLAR].\n"
    "Exemple:\n"
    "OS -> Țesut dur al scheletului\n"
    "AT -> Domeniul online al Austriei\n"
    "AI -> Formă a verbului a avea\n"
    "CLOU -> Moment culminant"
)

REWRITE_SYSTEM_PROMPT = (
    "Ești editor de definiții de rebus în limba română.\n"
    "Reguli:\n"
    "- Răspunzi doar cu definiția finală.\n"
    "- Tot textul este exclusiv în română. Nu folosești engleză.\n"
    "- Nu incluzi răspunsul și nici derivate evidente ale lui.\n"
    "- Sunt interzise forme din aceeași familie lexicală cu răspunsul.\n"
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
    "Întorci două scoruri distincte:\n"
    "- semantic_score: cât de corectă și onestă este definiția pentru răspunsul dat\n"
    "- guessability_score: cât de probabil este ca un rezolvitor să dea exact răspunsul cerut, de exact lungimea indicată, nu un sinonim mai comun\n"
    "Criterii:\n"
    "- dacă include răspunsul, o derivată clară sau aceeași familie lexicală: ambele scoruri foarte mici\n"
    "- dacă duce spre alt răspuns sau spre un sinonim mai uzual: guessability_score mic\n"
    "- dacă e precisă și scurtă: scoruri mari\n"
    "- dacă e banală dar corectă: semantic mediu, guessability mediu sau mic\n"
    "- feedback-ul este exclusiv în română, scurt și concret\n"
    "Răspunzi STRICT JSON: "
    "{\"semantic_score\": <1-10>, \"guessability_score\": <1-10>, \"feedback\": \"<motiv scurt>\"}"
)

CLUE_TIEBREAKER_SYSTEM_PROMPT = (
    "Compari două definiții de rebus românești pentru același răspuns.\n"
    "Alegi varianta mai bună pentru un rebus românesc.\n"
    "Criterii, în ordine:\n"
    "- text exclusiv în română\n"
    "- să nu folosească aceeași familie lexicală cu răspunsul\n"
    "- să fie exactă pentru răspunsul intenționat\n"
    "- să ducă mai probabil la răspunsul exact, nu la un sinonim\n"
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

RATE_MIN_SEMANTIC = 7
RATE_MIN_GUESSABILITY = 6
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


@dataclass(frozen=True)
class DefinitionRating:
    semantic_score: int
    guessability_score: int
    feedback: str


def create_client() -> OpenAI:
    return OpenAI(
        base_url=f"{LMSTUDIO_BASE_URL}/v1",
        api_key="not-needed",
        timeout=120.0,
        max_retries=1,
    )


def _clean_response(text: str | None) -> str:
    return (text or "").strip().strip('"').strip("'")


def _contains_english_markers(text: str | None) -> bool:
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


def _guard_same_family_rating(word: str, definition: str, rating: DefinitionRating) -> DefinitionRating:
    if not clue_uses_same_family(word, definition):
        return rating
    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback=_same_family_feedback(),
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


def generate_definition(
    client: OpenAI,
    word: str,
    original: str,
    theme: str,
    retries: int = 3,
) -> str:
    """Generate a single clue definition."""
    display_word = original if original else word.lower()
    length = len(word)
    prompt = (
        f"Cuvânt: {display_word}\n"
        f"Formă normalizată: {word}\n"
        f"Lungime: {length}\n\n"
        "Scrie o definiție de rebus scurtă și exactă. "
        "Răspunde doar cu definiția."
    )

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
            if len(definition) < 5:
                continue
            if definition == "[NECLAR]":
                return definition
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            if _definition_is_invalid(word, definition):
                continue
            if _contains_english_markers(definition):
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
) -> str:
    """Rewrite a failed or low-rated clue using feedback."""
    display_word = original if original else word.lower()
    feedback_parts = []
    if wrong_guess:
        feedback_parts.append(f"Rezolvitorul a ghicit: {wrong_guess}")
    if rating_feedback:
        feedback_parts.append(f"Feedback calitate: {rating_feedback}")
    feedback_text = "\n".join(feedback_parts) if feedback_parts else "[niciun feedback]"
    prompt = (
        f"Răspuns corect: {display_word}\n"
        f"Formă normalizată: {word}\n"
        f"Definiția anterioară: {previous_definition}\n"
        f"{feedback_text}\n\n"
        "Rescrie definiția mai precis și mai scurt."
    )

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="default",
                messages=[
                    {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=220,
            )
            definition = _clean_response(response.choices[0].message.content)
            if len(definition) < 5:
                continue
            if definition == "[NECLAR]":
                return definition
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            if _definition_is_invalid(word, definition):
                continue
            if _contains_english_markers(definition):
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
    prompt = (
        f"Definiție: {definition}\n"
        f"Lungime răspuns: {answer_length}\n"
        "Răspuns:"
    )

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
        if not _contains_english_markers(guess):
            return guess
        prompt += "\nAtenție: răspunsul anterior nu a fost în română. Răspunde exclusiv în română."

    return last_guess


def rate_definition(
    client: OpenAI,
    word: str,
    original: str,
    definition: str,
    answer_length: int,
) -> DefinitionRating:
    """Rate a definition's semantic quality and guessability."""
    display_word = original if original else word.lower()
    prompt = (
        f"Cuvânt-răspuns: {display_word}\n"
        f"Formă normalizată: {word}\n"
        f"Lungime răspuns: {answer_length}\n"
        f"Definiție: {definition}\n\n"
        "Evaluează separat corectitudinea semantică și ghicibilitatea exactă. "
        "Răspunde STRICT cu JSON."
    )

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
                if _contains_english_markers(feedback):
                    prompt += "\nAtenție: feedback-ul anterior nu a fost în română. Refă-l exclusiv în română."
                    continue
                rating = DefinitionRating(
                    semantic_score=_clamp_score(data.get("semantic_score")),
                    guessability_score=_clamp_score(data.get("guessability_score")),
                    feedback=feedback,
                )
                return _guard_same_family_rating(word, definition, rating)
        except Exception:
            pass

    return _guard_same_family_rating(
        word,
        definition,
        DefinitionRating(semantic_score=5, guessability_score=5, feedback=""),
    )


def choose_better_clue_variant(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
) -> str:
    prompt = (
        f"Răspuns: {word}\n"
        f"Lungime: {answer_length}\n"
        f"Varianta A: {definition_a}\n"
        f"Varianta B: {definition_b}\n\n"
        "Alege varianta mai bună."
    )
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
    prompt = (
        f"Varianta A:\n{summary_a}\n\n"
        f"Varianta B:\n{summary_b}\n\n"
        "Alege varianta mai bună."
    )
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
