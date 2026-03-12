"""LM Studio helpers for clue generation, verification, and rewrite."""

from __future__ import annotations

import re
import time

from openai import OpenAI

from ..config import LMSTUDIO_BASE_URL
from .diacritics import normalize


DEFINITION_SYSTEM_PROMPT = (
    "Ești autor de definiții de rebus în limba română.\n"
    "Reguli:\n"
    "- Răspunzi cu o singură definiție scurtă, firească și exactă.\n"
    "- Nu incluzi cuvântul-răspuns și nici o formă flexionată evidentă a lui.\n"
    "- Nu inventezi sensuri. Dacă nu ești sigur, răspunzi exact: [NECLAR]\n"
    "- Preferi stilul de rebus: concis, concret, ușor de ghicit.\n"
    "- Pentru substantive: definești prin categorie, rol sau trăsătură distinctivă.\n"
    "- Pentru adjective: folosești formulări de tipul 'Care ...'.\n"
    "- Pentru verbe la infinitiv: folosești formulări de tipul 'A ...'.\n"
    "- Pentru interjecții, pronume, forme gramaticale, simboluri, abrevieri sau domenii internet: explici exact ce sunt.\n"
    "- Pentru cuvinte de 2-3 litere fii foarte precis.\n"
    "Exemple bune:\n"
    "OS -> Țesut dur al scheletului\n"
    "AT -> Domeniul online al Austriei\n"
    "AI -> Formă a verbului a avea\n"
    "CLOU -> Moment culminant"
)

REWRITE_SYSTEM_PROMPT = (
    "Ești editor de definiții de rebus în limba română.\n"
    "Primești un răspuns corect, o definiție care a eșuat și răspunsul greșit ghicit de un rezolvitor AI.\n"
    "Sarcina ta este să rescrii definiția astfel încât să conducă mai precis la răspunsul corect.\n"
    "Reguli:\n"
    "- Răspunzi doar cu definiția finală.\n"
    "- Nu incluzi cuvântul-răspuns și nici o formă evident derivată din el.\n"
    "- Fii mai specific decât definiția veche.\n"
    "- Dacă termenul este obscur și nu poți scrie o definiție onestă, răspunzi exact: [NECLAR]"
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
        f"Lungime: {length}\n"
        f"Tema curentă: {theme}\n\n"
        "Scrie o definiție de rebus pentru acest cuvânt. "
        "Definiția trebuie să fie scurtă, exactă și să poată duce la răspunsul corect. "
        "Răspunde doar cu definiția finală."
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
                max_tokens=120,
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
) -> str:
    """Rewrite a failed clue using the wrong guess as feedback."""
    display_word = original if original else word.lower()
    prompt = (
        f"Răspuns corect: {display_word}\n"
        f"Formă normalizată: {word}\n"
        f"Tema curentă: {theme}\n"
        f"Definiția anterioară: {previous_definition}\n"
        f"Rezolvitorul a ghicit: {wrong_guess or '[gol]'}\n\n"
        "Scrie o definiție mai clară și mai specifică, care să distingă răspunsul corect de răspunsul ghicit."
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
                max_tokens=120,
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
        max_tokens=160,
    )
    guess = _clean_response(response.choices[0].message.content)
    if ":" in guess:
        guess = guess.split(":", 1)[1].strip()
    return guess.split()[0] if guess.split() else guess
