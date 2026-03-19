#!/usr/bin/env python3
"""Automated prompt experiment runner (autoresearch-style hill climbing).

Runs 100 prompt experiments against the multi-model assessment
pipeline, keeping improvements and reverting regressions. The campaign starts
with removals/simplifications, alternates prompt files to reduce overfitting,
and keeps attribution clear with single-file edits.

Usage:
    python3 scripts/run_experiments.py [--start-from N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "generator" / "prompts"
RESULTS_TSV = PROJECT_ROOT / "generator" / "assessment" / "results.tsv"
DEFAULT_EXPERIMENT_LOG = PROJECT_ROOT / "generator" / "assessment" / "experiment_log.json"
BEST_BACKUP_DIR = Path("/tmp/prompt_experiment_best")

# ── Prompt file paths ─────────────────────────────────────────────
SYS_DEFINITION = "system/definition.md"
SYS_VERIFY = "system/verify.md"
SYS_RATE = "system/rate.md"
SYS_REWRITE = "system/rewrite.md"
USR_GENERATE = "user/generate.md"
USR_VERIFY = "user/verify.md"
USR_RATE = "user/rate.md"
USR_REWRITE = "user/rewrite.md"


# ── Experiment definition ─────────────────────────────────────────
@dataclass
class Experiment:
    name: str
    desc: str
    file: str  # relative to PROMPTS_DIR
    find: str
    replace: str


def _insert_before(marker: str, new_line: str) -> tuple[str, str]:
    """Helper: returns (find, replace) to insert new_line before marker."""
    return marker, f"{new_line}\n{marker}"


def _insert_after(marker: str, new_line: str) -> tuple[str, str]:
    """Helper: returns (find, replace) to insert new_line after marker."""
    return marker, f"{marker}\n{new_line}"


# ── 100 Experiments ───────────────────────────────────────────────
# Design:
# - removal / simplification experiments first
# - prompt files alternated aggressively to reduce overfitting
# - single-file edits only, to keep attribution clean under noisy assessments

EXPERIMENTS: list[Experiment] = []


def _exp(desc: str, file: str, find: str, replace: str) -> None:
    name = f"exp{len(EXPERIMENTS) + 1:03d}"
    EXPERIMENTS.append(Experiment(name, desc, file, find, replace))


def _exp_before(desc: str, file: str, marker: str, new_line: str) -> None:
    find, replace = _insert_before(marker, new_line)
    _exp(desc, file, find, replace)


def _exp_after(desc: str, file: str, marker: str, new_line: str) -> None:
    find, replace = _insert_after(marker, new_line)
    _exp(desc, file, find, replace)


def _exp_remove(desc: str, file: str, text: str) -> None:
    _exp(desc, file, text, "")


# ── ROUND 1-3: removals and simplifications ──────────────────────

_exp_remove("remove duplicate english-only fallback in definition",
            SYS_DEFINITION,
            "- Dacă sensul îți vine doar în engleză sau altă limbă, răspunzi [NECLAR].\n")
_exp_remove("remove low-creativity DEX anchor from rate",
            SYS_RATE,
            "- dacă definiția este aproape identică cu o definiție DEX: creativity_score mic (3-4)\n")
_exp_remove("remove technical-word hint from verify",
            SYS_VERIFY,
            "- Dacă definiția pare tehnică sau neobișnuită, gândește-te la termeni de specialitate.\n")
_exp_remove("remove max-15-words cap from rewrite",
            SYS_REWRITE,
            "- Max 15 cuvinte.\n")
_exp_remove("remove normalized-form line from generate user",
            USR_GENERATE,
            "Formă normalizată: {word}\n")
_exp_remove("remove duplicate length reminder from verify user",
            USR_VERIFY,
            "Verifică lungimea înainte de a scrie.\n")
_exp_remove("remove normalized-form line from rate user",
            USR_RATE,
            "Formă normalizată: {word}\n")
_exp_remove("remove normalized-form line from rewrite user",
            USR_REWRITE,
            "Formă normalizată: {word}\n")

_exp_remove("remove creative-paraphrase rule from definition",
            SYS_DEFINITION,
            "- Dacă sensul direct ar necesita un cuvânt interzis, folosește o perifrază creativă sau o descriere indirectă.\n")
_exp_remove("remove high-creativity dictionary-distance rule from rate",
            SYS_RATE,
            "- dacă definiția e creativă și diferită de definițiile de dicționar: creativity_score mare\n")
_exp_remove("remove domain-switch flexibility line from verify",
            SYS_VERIFY,
            "- Definiția poate folosi un sens figurat sau o referință din alt domeniu. Gândește flexibil.\n")
_exp_remove("remove english-only reminder from rewrite",
            SYS_REWRITE,
            "IMPORTANT: Definește cuvintele DOAR cu sensul lor românesc, nu englezesc.\n")
_exp_remove("remove answer-length line from generate user",
            USR_GENERATE,
            "Lungime: {length}\n")
_exp_remove("remove long length-check sentence from verify user",
            USR_VERIFY,
            "Numără literele răspunsului tău înainte de a răspunde. Dacă nu are exact {answer_length} litere, gândește-te la alt cuvânt.\n")
_exp_remove("remove answer-length line from rate user",
            USR_RATE,
            "Lungime răspuns: {answer_length}\n")
_exp("remove failure-history placeholder from rewrite user",
     USR_REWRITE,
     "{bad_example_text}{failure_history_text}\n",
     "{bad_example_text}\n")

_exp("simplify precision line in definition",
     SYS_DEFINITION,
     "- Preferi definiții precise, naturale, maxim 12 cuvinte.\n",
     "- Preferi definiții precise.\n")
_exp("shorten creativity-score explanation in rate",
     SYS_RATE,
     '- creativity_score: cât de ingenios exploatează definiția un joc de domenii sau o ambiguitate surprinzătoare — o definiție directă de dicționar primește 3-4, o perifrază care face rezolvitorul să se gândească inițial la alt domeniu primește 8-10 (ex: RIAL -> "Se plătește la șah" = surpriză domeniu)',
     "- creativity_score: cât de ingenios și nebanal este indiciul, fără a sacrifica exactitatea")
_exp_remove("remove longest verify example",
            SYS_VERIFY,
            "Definiție: Se trage un semnal de pericol\nRăspuns: ALARMA\n")
_exp_remove("remove precision-versus-old-clue line from rewrite",
            SYS_REWRITE,
            "- Fă definiția mai precisă decât cea veche.\n")
_exp("simplify generate user final instruction",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție exactă. Răspunde doar cu definiția.")
_exp("remove word-type line from verify user",
     USR_VERIFY,
     "{word_type_line}Definiție: {definition}\n",
     "Definiție: {definition}\n")
_exp("simplify final rate user instruction",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează semantic, ghicibilitate și creativitate. Răspunde STRICT cu JSON.")
_exp_remove("remove bad-example placeholder from rewrite user",
            USR_REWRITE,
            "{bad_example_text}{failure_history_text}\n")

# ── ROUND 4-10: light additions, alternating files ───────────────

_exp_before("add exact-form detail to definition",
            SYS_DEFINITION,
            "Exemple corecte:",
            "- Dacă două forme apropiate sunt posibile, alege detaliul care identifică exact forma din grilă.")
_exp_before("add lemma-vs-form penalty to rate",
            SYS_RATE,
            "- feedback-ul este exclusiv în română, scurt și concret",
            "- dacă definiția ar putea duce la lemmă sau la o altă flexiune: guessability_score mic")
_exp_before("add exact-sense guard to verify",
            SYS_VERIFY,
            "Exemple:",
            "- Nu alege un cuvânt doar fiindcă e apropiat; sensul trebuie să se potrivească exact definiției.")
_exp_before("add sure-over-clever rule to rewrite",
            SYS_REWRITE,
            "- Max 15 cuvinte.",
            "- Dacă alegi între o formulare ingenioasă și una sigură, preferi varianta sigură.")
_exp("add distinctive-sense reminder to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Descrie sensul distinctiv, nu doar categoria largă. Răspunde doar cu definiția.")
_exp_before("add exact-form reminder to verify user",
            USR_VERIFY,
            "Răspuns:",
            "Dacă definiția cere o formă flexionată, răspunde exact cu acea formă.")
_exp("add close-form penalty to rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Penalizează formele apropiate dar greșite. Răspunde STRICT cu JSON.")
_exp("add exact-form target to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis și mai scurt, pentru forma exactă.")

_exp_before("add single-answer detail to definition",
            SYS_DEFINITION,
            "Exemple corecte:",
            "- Dacă definiția poate duce la mai multe răspunsuri, adaugă un detaliu care lasă unul singur.")
_exp_before("add uniqueness criterion to rate",
            SYS_RATE,
            "- feedback-ul este exclusiv în română, scurt și concret",
            "- dacă două răspunsuri rămân la fel de plauzibile, guessability_score nu trece de 6")
_exp_after("add tie-break commonness to verify",
           SYS_VERIFY,
           "- Răspunsul conține doar litere românești (inclusiv ă, â, î, ș, ț).",
           "- Dacă două răspunsuri par la fel de plauzibile, alege cuvântul mai uzual.")
_exp_before("add anti-common-synonym rule to rewrite",
            SYS_REWRITE,
            "- Max 15 cuvinte.",
            "- Elimină formulările care trimit firesc la un sinonim mai comun.")
_exp("add one-clear-sense reminder to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Alege un singur sens clar. Răspunde doar cu definiția.")
_exp_before("add anti-first-guess reminder to verify user",
            USR_VERIFY,
            "Răspuns:",
            "Nu răspunde cu primul cuvânt vag potrivit.")
_exp("add grid-solver framing to rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea ca pentru un rezolvitor cu exact {answer_length} căsuțe. Răspunde STRICT cu JSON.")
_exp("add distinctiveness reminder to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis, mai scurt și mai distinctiv.")

_exp("add broad-clue counterexample to definition",
     SYS_DEFINITION,
     "Contra-exemple (GREȘIT - sensuri englezești):",
     "Contra-exemple (GREȘIT - sensuri englezești și prea largi):\nRIAL -> Monedă [GREȘIT - prea multe răspunsuri]")
_exp_before("cap vague-but-correct guessability in rate",
            SYS_RATE,
            "- feedback-ul este exclusiv în română, scurt și concret",
            "- dacă definiția e prea largă dar corectă, guessability_score nu trece de 6")
_exp_before("add broad-definition caution to verify",
            SYS_VERIFY,
            "Exemple:",
            "- Dacă definiția este largă, caută termenul cel mai fixat în rebus, nu o aproximare comodă.")
_exp_before("remove decorative wording in rewrite",
            SYS_REWRITE,
            "- Max 15 cuvinte.",
            "- Taie adjectivele decorative care nu ajută la identificare.")
_exp("add anti-generic reminder to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Evită formulările prea generale. Răspunde doar cu definiția.")
_exp_before("add specificity reminder to verify user",
            USR_VERIFY,
            "Răspuns:",
            "Dacă răspunsul nu e clar, caută unul mai specific, nu mai ornamental.")
_exp("add no-reward-for-vagueness to rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Nu recompensa definițiile vagi doar pentru că sunt corecte semantic. Răspunde STRICT cu JSON.")
_exp("add concrete-detail reminder to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis, mai scurt și cu un detaliu concret.")

_exp_before("add honest-[NECLAR]-when-not-distinctive to definition",
            SYS_DEFINITION,
            "Exemple corecte:",
            "- Dacă nu poți diferenția onest răspunsul de variante apropiate, răspunzi [NECLAR].")
_exp_before("add invented-sense penalty to rate",
            SYS_RATE,
            "- feedback-ul este exclusiv în română, scurt și concret",
            "- dacă definiția pare inventată, forțată sau nesigură: semantic_score mic")
_exp_before("add real-word-not-improvised reminder to verify",
            SYS_VERIFY,
            "Exemple:",
            "- Dacă definiția pare ciudată, tot cauți un cuvânt românesc real, nu improvizezi.")
_exp_before("add cannot-save-honestly rule to rewrite",
            SYS_REWRITE,
            "- Dacă termenul este obscur și nu poți scrie onest, răspunzi exact: [NECLAR]",
            "- Dacă nu poți salva definiția onest, răspunzi exact: [NECLAR]")
_exp("add [NECLAR] fallback to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Dacă nu știi sensul românesc, răspunde [NECLAR].")
_exp_before("add no-improvisation reminder to verify user",
            USR_VERIFY,
            "Răspuns:",
            "Nu improviza un cuvânt doar ca să iasă lungimea.")
_exp("add implausible-definition penalty to rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Dacă definiția pare neverosimilă pentru cuvânt, scade semantic_score. Răspunde STRICT cu JSON.")
_exp("add honest-fallback to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis și mai scurt. Dacă nu poate fi reparată onest, răspunde [NECLAR].")

_exp_before("add short-word literal rule to definition",
            SYS_DEFINITION,
            "Exemple corecte:",
            "- Pentru răspunsuri de 2-3 litere, preferă indiciul exact, nu perifraza spectaculoasă.")
_exp_before("add short-word ambiguity penalty to rate",
            SYS_RATE,
            "- feedback-ul este exclusiv în română, scurt și concret",
            "- pentru răspunsuri de 2-3 litere, o mică ambiguitate scade puternic guessability_score")
_exp_before("add short-word elimination rule to verify",
            SYS_VERIFY,
            "Exemple:",
            "- La 2-3 litere, exclude rapid variantele mai uzuale care au aceeași lungime.")
_exp_before("add short-word-sense-first rule to rewrite",
            SYS_REWRITE,
            "- Max 15 cuvinte.",
            "- Pentru răspunsuri scurte, corectează întâi sensul, apoi stilul.")
_exp("add short-word literal reminder to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Pentru cuvinte scurte, fii foarte literal. Răspunde doar cu definiția.")
_exp_before("add short-word caution to verify user",
            USR_VERIFY,
            "Răspuns:",
            "La cuvinte scurte, o literă sau un sens vag schimbă tot.")
_exp("add short-word near-unique rule to rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Pentru răspunsuri scurte, cere potrivire aproape unică. Răspunde STRICT cu JSON.")
_exp("add short-word exactness to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis și mai scurt. Pentru cuvinte scurte, preferă varianta cea mai literală și exactă.")

_exp_before("add verb-form naturalness to definition",
            SYS_DEFINITION,
            "Exemple corecte:",
            "- Dacă răspunsul este verb sau formă gramaticală, definiția trebuie să sune firesc pentru exact acea formă.")
_exp_before("add grammar-category penalty to rate",
            SYS_RATE,
            "- feedback-ul este exclusiv în română, scurt și concret",
            "- dacă definiția descrie altă categorie gramaticală decât răspunsul: semantic_score și guessability_score mici")
_exp_before("add grammar-category elimination to verify",
            SYS_VERIFY,
            "Exemple:",
            "- Dacă definiția sugerează o categorie gramaticală, elimină variantele din altă categorie.")
_exp_before("add category-mismatch reset to rewrite",
            SYS_REWRITE,
            "- Dacă definiția veche sugerează alt gen, alt număr sau altă formă flexionară, corectează forma înainte de stil.",
            "- Dacă definiția veche descrie altă categorie gramaticală, rescrii de la zero.")
_exp("add verb-form reminder to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Dacă e verb sau formă gramaticală, descrie exact acea formă. Răspunde doar cu definiția.")
_exp_before("add grammar-clue reminder to verify user",
            USR_VERIFY,
            "Răspuns:",
            "Folosește și indiciile de categorie gramaticală, nu doar sensul general.")
_exp("add grammar-flexion penalty to rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Penalizează dacă definiția descrie altă categorie gramaticală sau altă flexiune. Răspunde STRICT cu JSON.")
_exp("add form-before-style reminder to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis și mai scurt. Repară forma gramaticală înainte de stil.")

_exp_before("retry no-ambiguity wording in definition",
            SYS_DEFINITION,
            "Exemple corecte:",
            "- Dacă o formulare poate lăsa două răspunsuri naturale, reformulezi înainte să răspunzi.")
_exp("retry stronger JSON-only wording in rate",
     SYS_RATE,
     "Răspunzi STRICT cu un singur obiect JSON, fără text înainte sau după:",
     "Răspunzi STRICT cu un singur obiect JSON, fără absolut niciun text în afara JSON-ului:")
_exp_before("retry compare-candidates wording in verify",
            SYS_VERIFY,
            "Exemple:",
            "- Dacă nu ești sigur, compară mental 2-3 candidate și păstrează doar varianta cea mai exactă.")
_exp_before("add exact-then-short rule to rewrite",
            SYS_REWRITE,
            "- Max 15 cuvinte.",
            "- După ce devine exactă, mai scurtezi definiția.")
_exp("retry light specificity in generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Alege detaliul cel mai distinctiv. Răspunde doar cu definiția.")
_exp_before("add form-plus-length reminder to verify user",
            USR_VERIFY,
            "Răspuns:",
            "Forma și lungimea trebuie să se potrivească simultan.")
_exp("retry exact-guessing framing in rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea ca pe un test de ghicire exactă. Răspunde STRICT cu JSON.")
_exp("add guessability emphasis to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis, mai scurt și mai ușor de ghicit exact.")

# ── ROUND 11-13: targeted retries, still alternating ─────────────

_exp_before("add anchored-domain rule to definition",
            SYS_DEFINITION,
            "Exemple corecte:",
            "- O surpriză de domeniu este bună doar dacă răspunsul rămâne unic și exact.")
_exp("add lighter guessability-first wording to rate",
     SYS_RATE,
     "Întorci trei scoruri distincte:",
     "Întorci trei scoruri distincte; pentru utilitatea practică, guessability cântărește cel mai mult:")
_exp("add solver-first phrasing to verify",
     SYS_VERIFY,
     "Ești rezolvitor de rebusuri românești.",
     "Ești rezolvitor de rebusuri românești. Scopul tău este să găsești forma exactă avută în minte de autor.")
_exp_before("add anti-ambiguity self-check to rewrite",
            SYS_REWRITE,
            "- Dacă termenul este obscur și nu poți scrie onest, răspunzi exact: [NECLAR]",
            "- Dacă noua definiție ar lăsa două răspunsuri naturale, o refaci.")
_exp("add exact-form reminder to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Verifică să descrie forma exactă. Răspunde doar cu definiția.")
_exp_before("add compare-candidates reminder to verify user",
            USR_VERIFY,
            "Răspuns:",
            "Dacă eziti, compară 2-3 candidate și păstrează-o pe cea mai exactă.")
_exp("add single-answer evaluation to rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Verifică dacă definiția indică un singur răspuns natural. Răspunde STRICT cu JSON.")
_exp("add only-useful-words reminder to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis și mai scurt, păstrând doar cuvintele care ajută la ghicire.")

_exp("add multi-answer counterexample to definition",
     SYS_DEFINITION,
     "ARDE -> Ceva care se întâmplă [GREȘIT - prea vag]",
     "ARDE -> Ceva care se întâmplă [GREȘIT - prea vag]\nAI -> Pronume posesiv [GREȘIT - prea multe răspunsuri]")
_exp_before("allow simple-but-correct high semantic in rate",
            SYS_RATE,
            "- feedback-ul este exclusiv în română, scurt și concret",
            "- o definiție simplă și corectă poate primi semantic_score mare chiar dacă nu e creativă")
_exp_before("add cell-count framing to verify",
            SYS_VERIFY,
            "Exemple:",
            "- Gândește-te la casetele din grilă: fiecare literă trebuie justificată de definiție.")
_exp_before("add anti-common-synonym rule to rewrite second pass",
            SYS_REWRITE,
            "- Dacă termenul este obscur și nu poți scrie onest, răspunzi exact: [NECLAR]",
            "- Dacă formularea ar sugera un sinonim mai comun, o faci mai distinctivă.")
_exp("add anti-category-generic reminder to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus scurtă și exactă. Evită definițiile de categorie prea largă. Răspunde doar cu definiția.")
_exp_before("add exact-meaning-over-length reminder to verify user",
            USR_VERIFY,
            "Răspuns:",
            "Dacă două cuvinte au lungimea bună, alege-l pe cel care respectă exact sensul.")
_exp("add short-word uniqueness check to rate user",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. La răspunsuri scurte, verifică dacă definiția duce aproape unic la cuvânt. Răspunde STRICT cu JSON.")
_exp("add remove-dead-words reminder to rewrite user",
     USR_REWRITE,
     "Rescrie definiția mai precis și mai scurt.",
     "Rescrie definiția mai precis și mai scurt. Scoate orice cuvânt care nu ajută la identificare.")

_exp_before("add anti-common-synonym rule to definition",
            SYS_DEFINITION,
            "Exemple corecte:",
            "- Dacă indiciul ar duce mai natural la un sinonim mai comun, îl reformulezi.")
_exp_before("add common-synonym cap to rate",
            SYS_RATE,
            "- feedback-ul este exclusiv în română, scurt și concret",
            "- dacă un sinonim mai comun pare răspunsul firesc, guessability_score nu poate fi mare")
_exp_remove("remove first verify example to test leaner examples",
            SYS_VERIFY,
            "Definiție: Domeniul online al Austriei\nRăspuns: AT\n")
_exp_before("add exact-surface-form target to rewrite",
            SYS_REWRITE,
            "- Dacă termenul este obscur și nu poți scrie onest, răspunzi exact: [NECLAR]",
            "- Țintești forma exactă din grilă înainte de orice rafinare stilistică.")

assert len(EXPERIMENTS) == 100, len(EXPERIMENTS)

# ── Runner infrastructure ─────────────────────────────────────────

def backup_prompts(backup_dir: Path = BEST_BACKUP_DIR) -> None:
    """Copy all prompt files to backup directory."""
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(PROMPTS_DIR, backup_dir)


def restore_prompts(backup_dir: Path = BEST_BACKUP_DIR) -> None:
    """Restore all prompt files from backup."""
    if not backup_dir.exists():
        raise FileNotFoundError(f"Backup not found: {backup_dir}")
    shutil.rmtree(PROMPTS_DIR)
    shutil.copytree(backup_dir, PROMPTS_DIR)


def save_best_prompts(backup_dir: Path = BEST_BACKUP_DIR) -> None:
    """Update the best backup with current prompt state."""
    backup_prompts(backup_dir)


def get_last_composite() -> float:
    """Read the last kept composite score from results TSV."""
    lines = RESULTS_TSV.read_text().strip().split("\n")
    if len(lines) < 2:
        raise ValueError("No results in TSV")
    for line in reversed(lines[1:]):
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        if fields[5] != "keep":
            continue
        return float(fields[1])
    raise ValueError("No kept results in TSV")


def snapshot_results_tsv() -> str | None:
    """Capture the current TSV contents for rollback on discard/interruption."""
    if not RESULTS_TSV.exists():
        return None
    return RESULTS_TSV.read_text(encoding="utf-8")


def restore_results_tsv(snapshot: str | None) -> None:
    """Restore prior TSV contents after a discarded or interrupted experiment."""
    if snapshot is None:
        if RESULTS_TSV.exists():
            RESULTS_TSV.unlink()
        return
    RESULTS_TSV.write_text(snapshot, encoding="utf-8")


def get_result_by_description(description: str) -> dict:
    """Read the newest TSV row for a specific assessment description."""
    lines = RESULTS_TSV.read_text(encoding="utf-8").strip().split("\n")
    if len(lines) < 2:
        raise ValueError("No results in TSV")

    for line in reversed(lines[1:]):
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        if fields[6] != description:
            continue
        return {
            "composite": float(fields[1]),
            "pass_rate": float(fields[2]),
            "avg_semantic": float(fields[3]),
            "avg_rebus": float(fields[4]),
            "error": False,
        }

    raise ValueError(f"No TSV row found for description={description!r}")


def git_short_hash() -> str:
    """Best-effort current git short hash for manual TSV appends."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def append_results_row(description: str, status: str, result: dict) -> None:
    """Append an experiment outcome row to the shared results TSV."""
    header = "commit\tcomposite\tpass_rate\tavg_semantic\tavg_rebus\tstatus\tdescription\n"

    if not RESULTS_TSV.exists():
        RESULTS_TSV.write_text(header, encoding="utf-8")

    with RESULTS_TSV.open("a", encoding="utf-8") as f:
        f.write(
            f"{git_short_hash()}\t{result['composite']:.1f}\t{result['pass_rate']:.3f}\t"
            f"{result['avg_semantic']:.1f}\t{result['avg_rebus']:.1f}\t"
            f"{status}\t{description}\n"
        )


def apply_experiment(exp: Experiment) -> bool:
    """Apply an experiment's edit. Returns True if the edit was applied."""
    filepath = PROMPTS_DIR / exp.file
    if not filepath.exists():
        print(f"  [SKIP] File not found: {filepath}")
        return False

    content = filepath.read_text(encoding="utf-8")
    if exp.find in exp.replace and exp.replace in content:
        print(f"  [SKIP] Replacement text already present in {exp.file}")
        return False
    if exp.find not in content:
        print(f"  [SKIP] Find text not found in {exp.file}")
        return False

    new_content = content.replace(exp.find, exp.replace, 1)
    if new_content == content:
        print(f"  [SKIP] No change after replacement in {exp.file}")
        return False

    filepath.write_text(new_content, encoding="utf-8")
    return True


def build_assessment_description(prefix: str, exp: Experiment) -> str:
    """Human-readable experiment label stored in TSV/logs."""
    base = f"{prefix}{exp.name}" if prefix else exp.name
    return f"{base} | {exp.desc} | {exp.file}"


def git_current_branch() -> str:
    """Return current branch name, or empty string if unavailable."""
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def git_stage_commit_push(
    paths: list[Path],
    message: str,
    *,
    push: bool,
    remote: str,
    branch: str,
) -> bool:
    """Stage selected paths, commit, and optionally push. Best-effort only."""
    stage_targets = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if not resolved.exists():
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        stage_targets.append(str(resolved.relative_to(PROJECT_ROOT)))

    if not stage_targets:
        print(f"  [git] No existing paths to stage for: {message}")
        return False

    add_result = subprocess.run(
        ["git", "add", *stage_targets],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if add_result.returncode != 0:
        print(f"  [git] add failed for '{message}': {add_result.stderr.strip()}")
        return False

    commit_result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    combined_output = (commit_result.stdout or "") + (commit_result.stderr or "")
    if commit_result.returncode != 0:
        if "nothing to commit" in combined_output.lower():
            print(f"  [git] Nothing to commit for: {message}")
            return True
        print(f"  [git] commit failed for '{message}': {combined_output.strip()}")
        return False

    print(f"  [git] committed: {message}")
    if not push:
        return True

    push_result = subprocess.run(
        ["git", "push", remote, branch],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if push_result.returncode != 0:
        push_output = (push_result.stdout or "") + (push_result.stderr or "")
        print(f"  [git] push failed for '{message}': {push_output.strip()}")
        return False

    print(f"  [git] pushed: {remote}/{branch}")
    return True


def run_assessment(
    description: str,
    assessment_log_path: Path | None = None,
    stream_output: bool = False,
) -> dict:
    """Run the multi-model assessment and return parsed results."""
    cmd = [
        sys.executable, "-u", "-m", "generator.assessment.run_assessment",
        "--description", description,
    ]
    print(f"  Running assessment: {description}")
    if assessment_log_path is not None:
        assessment_log_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  Assessment log: {assessment_log_path}")
    start = time.monotonic()

    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = start + 2400  # 40 min timeout
    log_file = assessment_log_path.open("w", encoding="utf-8") if assessment_log_path is not None else None

    try:
        while True:
            if process.stdout is None:
                break

            line = process.stdout.readline()
            if line:
                if log_file is not None:
                    log_file.write(line)
                    log_file.flush()
                if stream_output:
                    print(line, end="")
                continue

            returncode = process.poll()
            if returncode is not None:
                break

            if time.monotonic() > deadline:
                process.kill()
                raise subprocess.TimeoutExpired(cmd, 2400)
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if log_file is not None:
            log_file.close()

    result = process.wait()

    elapsed = time.monotonic() - start
    print(f"  Assessment completed in {elapsed:.0f}s")

    if result != 0:
        print(f"  [ERROR] Assessment failed with exit code {result}")
        return {"composite": 0.0, "pass_rate": 0.0, "avg_semantic": 0.0, "avg_rebus": 0.0, "error": True}

    return get_result_by_description(description)


def load_log(log_path: Path) -> list[dict]:
    """Load experiment log from JSON."""
    if log_path.exists():
        return json.loads(log_path.read_text(encoding="utf-8"))
    return []


def save_log(log_path: Path, log: list[dict]) -> None:
    """Save experiment log to JSON."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 100 prompt experiments")
    parser.add_argument("--start-from", type=int, default=1,
                        help="Resume from experiment N (1-indexed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show experiments without running")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_EXPERIMENT_LOG,
                        help="Path to experiment log JSON")
    parser.add_argument("--reset-log", action="store_true",
                        help="Ignore and overwrite any existing experiment log")
    parser.add_argument("--description-prefix", default="",
                        help="Prefix added to assessment descriptions, e.g. campaign-a/")
    parser.add_argument("--backup-dir", type=Path, default=BEST_BACKUP_DIR,
                        help="Directory used for the current run's best prompt snapshot")
    parser.add_argument("--assessment-logs-dir", type=Path,
                        help="Directory for per-experiment assessment logs")
    parser.add_argument("--stream-assessment-output", action="store_true",
                        help="Also print inner assessment logs to stdout")
    parser.add_argument("--git-live-commit", action="store_true",
                        help="Commit experiment state immediately after applying each edit")
    parser.add_argument("--git-live-push", action="store_true",
                        help="Push live experiment commits to the remote branch")
    parser.add_argument("--git-live-remote", default="origin",
                        help="Remote used by --git-live-push")
    parser.add_argument("--git-live-branch",
                        help="Branch used by --git-live-push (default: current branch)")
    args = parser.parse_args()

    if args.dry_run:
        for i, exp in enumerate(EXPERIMENTS, 1):
            print(f"{i:3d}. [{exp.name}] {exp.desc}")
            print(f"     File: {exp.file}")
        print(f"\nTotal: {len(EXPERIMENTS)} experiments")
        return

    if args.assessment_logs_dir is None:
        args.assessment_logs_dir = args.log_path.parent / f"{args.log_path.stem}_logs"
    if args.git_live_push:
        args.git_live_commit = True
    if (args.git_live_commit or args.git_live_push) and not args.git_live_branch:
        args.git_live_branch = git_current_branch()

    # Initialize
    if args.reset_log and args.log_path.exists():
        args.log_path.unlink()
    log = load_log(args.log_path)
    completed_names = {entry["name"] for entry in log}

    # Get current best composite
    best_composite = get_last_composite()
    print(f"Starting composite: {best_composite:.1f}")
    print(f"Total experiments: {len(EXPERIMENTS)}")
    print(f"Starting from: experiment {args.start_from}")
    print(f"Experiment log: {args.log_path}")
    print(f"Best-prompt backup: {args.backup_dir}")
    print(f"Assessment logs dir: {args.assessment_logs_dir}")
    if args.git_live_commit:
        print(f"Git live commit: enabled")
    if args.git_live_push:
        print(f"Git live push: {args.git_live_remote}/{args.git_live_branch}")
    if args.description_prefix:
        print(f"Description prefix: {args.description_prefix}")

    # Back up current prompts as "best"
    backup_prompts(args.backup_dir)
    print(f"Best prompts backed up to {args.backup_dir}")

    kept = 0
    skipped = 0
    discarded = 0
    total_start = time.monotonic()

    for i, exp in enumerate(EXPERIMENTS, 1):
        if i < args.start_from:
            continue

        if exp.name in completed_names:
            print(f"\n[{i}/{len(EXPERIMENTS)}] {exp.name} — already completed, skipping")
            skipped += 1
            continue

        print(f"\n{'='*60}")
        print(f"[{i}/{len(EXPERIMENTS)}] {exp.name}: {exp.desc}")
        print(f"{'='*60}")

        # Restore to best state before applying this experiment
        restore_prompts(args.backup_dir)

        # Apply the experiment edit
        applied = apply_experiment(exp)
        assessment_description = build_assessment_description(args.description_prefix, exp)
        if not applied:
            entry = {
                "name": exp.name,
                "assessment_description": assessment_description,
                "file": exp.file,
                "find": exp.find,
                "replace": exp.replace,
                "desc": exp.desc,
                "status": "skipped",
                "reason": "find text not found",
                "best_composite": best_composite,
            }
            log.append(entry)
            save_log(args.log_path, log)
            skipped += 1
            continue

        results_snapshot = snapshot_results_tsv()
        assessment_log_path = args.assessment_logs_dir / f"{exp.name}.log"
        prompt_path = PROMPTS_DIR / exp.file

        if args.git_live_commit:
            git_stage_commit_push(
                [prompt_path],
                assessment_description,
                push=args.git_live_push,
                remote=args.git_live_remote,
                branch=args.git_live_branch or "",
            )

        # Run assessment
        try:
            result = run_assessment(
                assessment_description,
                assessment_log_path=assessment_log_path,
                stream_output=args.stream_assessment_output,
            )
        except KeyboardInterrupt:
            print("\n  [INTERRUPTED] Restoring best prompts and discarding partial results")
            restore_prompts(args.backup_dir)
            restore_results_tsv(results_snapshot)
            if args.git_live_commit:
                git_stage_commit_push(
                    [prompt_path],
                    f"restore interrupted | {assessment_description}",
                    push=args.git_live_push,
                    remote=args.git_live_remote,
                    branch=args.git_live_branch or "",
                )
            raise SystemExit(130) from None

        if result.get("error"):
            entry = {
                "name": exp.name,
                "assessment_description": assessment_description,
                "assessment_log": str(assessment_log_path),
                "file": exp.file,
                "find": exp.find,
                "replace": exp.replace,
                "desc": exp.desc,
                "status": "error",
                "best_composite": best_composite,
            }
            log.append(entry)
            save_log(args.log_path, log)
            skipped += 1
            # Restore best prompts
            restore_prompts(args.backup_dir)
            restore_results_tsv(results_snapshot)
            append_results_row(assessment_description, "error", result)
            if args.git_live_commit:
                git_stage_commit_push(
                    [prompt_path, RESULTS_TSV],
                    f"error | {assessment_description}",
                    push=args.git_live_push,
                    remote=args.git_live_remote,
                    branch=args.git_live_branch or "",
                )
            continue

        composite = result["composite"]
        improved = composite > best_composite

        status = "keep" if improved else "discard"
        symbol = "✓ IMPROVED" if improved else "✗ No improvement"
        print(f"  {symbol}: {best_composite:.1f} → {composite:.1f} "
              f"(pass={result['pass_rate']:.3f} sem={result['avg_semantic']:.1f} reb={result['avg_rebus']:.1f})")

        entry = {
            "name": exp.name,
            "assessment_description": assessment_description,
            "assessment_log": str(assessment_log_path),
            "file": exp.file,
            "find": exp.find,
            "replace": exp.replace,
            "desc": exp.desc,
            "status": status,
            "composite": composite,
            "pass_rate": result["pass_rate"],
            "avg_semantic": result["avg_semantic"],
            "avg_rebus": result["avg_rebus"],
            "prev_best": best_composite,
        }
        log.append(entry)
        save_log(args.log_path, log)

        if improved:
            best_composite = composite
            save_best_prompts(args.backup_dir)
            kept += 1
            print(f"  New best: {best_composite:.1f}")
            if args.git_live_commit:
                git_stage_commit_push(
                    [RESULTS_TSV],
                    f"keep | {assessment_description}",
                    push=args.git_live_push,
                    remote=args.git_live_remote,
                    branch=args.git_live_branch or "",
                )
        else:
            restore_prompts(args.backup_dir)
            restore_results_tsv(results_snapshot)
            append_results_row(assessment_description, "discard", result)
            discarded += 1
            if args.git_live_commit:
                git_stage_commit_push(
                    [RESULTS_TSV],
                    f"discard | {assessment_description}",
                    push=args.git_live_push,
                    remote=args.git_live_remote,
                    branch=args.git_live_branch or "",
                )

    # Final restore of best prompts
    restore_prompts(args.backup_dir)

    total_elapsed = time.monotonic() - total_start
    print(f"\n{'='*60}")
    print(f"EXPERIMENT RUN COMPLETE")
    print(f"{'='*60}")
    print(f"Total time: {total_elapsed/3600:.1f}h")
    print(f"Final best composite: {best_composite:.1f}")
    print(f"Kept: {kept}, Discarded: {discarded}, Skipped: {skipped}")

    # Summary of kept experiments
    kept_entries = [e for e in log if e.get("status") == "keep"]
    if kept_entries:
        print(f"\nKept experiments:")
        for e in kept_entries:
            print(f"  {e['name']}: {e['desc']} — composite={e['composite']:.1f}")


if __name__ == "__main__":
    main()
