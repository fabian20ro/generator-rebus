"""LM Studio helpers for clue generation, verification, rewrite, and rating."""

from __future__ import annotations

import json
import re
import time

from openai import OpenAI

from ..config import LMSTUDIO_BASE_URL
from .diacritics import normalize


DEFINITION_SYSTEM_PROMPT = (
    "Ești autor de definiții de rebus în limba română.\n"
    "Reguli:\n"
    "- Răspunzi cu o singură definiție scurtă.\n"
    "- Nu incluzi răspunsul și nici derivate evidente ale lui.\n"
    "- Nu inventezi sensuri. Dacă nu ești sigur, răspunzi exact: [NECLAR]\n"
    "- Preferi definiții precise, naturale, maxim 12 cuvinte.\n"
    "- Pentru cuvinte scurte, abrevieri și forme gramaticale fii literal și exact.\n"
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
    "- Nu incluzi răspunsul și nici derivate evidente ale lui.\n"
    "- Fă definiția mai precisă decât cea veche.\n"
    "- Max 12 cuvinte.\n"
    "- Dacă termenul este obscur și nu poți scrie onest, răspunzi exact: [NECLAR]"
)

VERIFY_SYSTEM_PROMPT = (
    "Ești rezolvitor de rebusuri românești.\n"
    "Reguli:\n"
    "- Răspunzi cu un singur cuvânt, fără explicații.\n"
    "- Dacă definiția indică o abreviere, un simbol, un domeniu internet, o interjecție sau o formă gramaticală, răspunzi exact cu forma scurtă cerută.\n"
    "- Nu reformulezi definiția.\n"
    "- Nu răspunzi cu propoziții.\n"
    "Exemple:\n"
    "Definiție: Domeniul online al Austriei\n"
    "Răspuns: AT\n"
    "Definiție: Țesut dur al scheletului\n"
    "Răspuns: OS\n"
    "Definiție: Formă a verbului a avea\n"
    "Răspuns: AI"
)

RATE_SYSTEM_PROMPT = (
    "Evaluezi o definiție de rebus pe scara 1-10.\n"
    "Criterii de evaluare:\n"
    "- dacă include răspunsul sau o derivată clară: scor 1\n"
    "- dacă duce spre alt răspuns: scor mic\n"
    "- dacă e precisă și scurtă: scor mare\n"
    "- dacă e banală dar corectă: scor mediu\n"
    "Răspunzi STRICT JSON: {\"score\": <1-10>, \"feedback\": \"<motiv scurt>\"}"
)

RATE_MIN_QUALITY = 7


def create_client() -> OpenAI:
    return OpenAI(
        base_url=f"{LMSTUDIO_BASE_URL}/v1",
        api_key="not-needed",
        timeout=120.0,
        max_retries=1,
    )


def _clean_response(text: str | None) -> str:
    return (text or "").strip().strip('"').strip("'")


def _definition_mentions_answer(answer: str, definition: str) -> bool:
    if not definition:
        return False
    normalized_definition = normalize(definition).lower()
    pattern = rf"\b{re.escape(answer.lower())}\b"
    return re.search(pattern, normalized_definition) is not None


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
            if _definition_mentions_answer(word, definition):
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
            if _definition_mentions_answer(word, definition):
                continue
            return definition
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise

    return previous_definition


def verify_definition(client: OpenAI, definition: str) -> str:
    """Ask AI to guess the word from a clue definition."""
    prompt = (
        f"Definiție: {definition}\n"
        "Răspuns:"
    )

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
    return guess.split()[0] if guess.split() else guess


def rate_definition(
    client: OpenAI,
    word: str,
    original: str,
    definition: str,
) -> tuple[int, str]:
    """Rate a definition's quality. Returns (score 1-10, feedback)."""
    display_word = original if original else word.lower()
    prompt = (
        f"Cuvânt-răspuns: {display_word}\n"
        f"Formă normalizată: {word}\n"
        f"Definiție: {definition}\n\n"
        "Evaluează calitatea definiției. Răspunde STRICT cu JSON: "
        '{\"score\": <1-10>, \"feedback\": \"<motiv scurt>\"}'
    )

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
        # Extract JSON from response (handles extra text around it)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            score = int(data.get("score", 5))
            score = max(1, min(10, score))
            feedback = str(data.get("feedback", ""))
            return score, feedback
    except Exception:
        pass

    return 5, ""
