#!/usr/bin/env python3
"""Automated prompt experiment runner (autoresearch-style hill climbing).

Runs 100 prompt experiments against the multi-model assessment
pipeline, keeping improvements and reverting regressions. The campaign starts
with removals/simplifications, alternates prompt files to reduce overfitting,
and keeps attribution clear with single-file edits.

Usage:
    python3 scripts/run_experiments.py [--preset pilot] [--start-from N] [--end-at N] [--dry-run]
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
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from generator.assessment.benchmark_policy import PILOT_EXPERIMENT_RANGE, UNCERTAINTY_DELTA
from generator.core.runtime_logging import install_process_logging, path_timestamp

PROMPTS_DIR = PROJECT_ROOT / "generator" / "prompts"
RESULTS_TSV = PROJECT_ROOT / "generator" / "assessment" / "results.tsv"
DEFAULT_EXPERIMENT_LOG = PROJECT_ROOT / "generator" / "assessment" / "experiment_log.json"
BEST_BACKUP_DIR = Path("/tmp/prompt_experiment_best")
BEST_ASSESSMENT_JSON = "best_assessment.json"
EXPERIMENT_PRESETS = {
    "full": (1, 100),
    "pilot": PILOT_EXPERIMENT_RANGE,
}

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
@dataclass(frozen=True)
class PromptEdit:
    file: str  # relative to PROMPTS_DIR
    find: str
    replace: str


@dataclass
class Experiment:
    name: str
    desc: str
    edits: list[PromptEdit]

    @property
    def files(self) -> list[str]:
        return list(dict.fromkeys(edit.file for edit in self.edits))

    @property
    def file(self) -> str:
        return ", ".join(self.files)

    @property
    def find(self) -> str:
        return self.edits[0].find if self.edits else ""

    @property
    def replace(self) -> str:
        return self.edits[0].replace if self.edits else ""


def _insert_before(marker: str, new_line: str) -> tuple[str, str]:
    """Helper: returns (find, replace) to insert new_line before marker."""
    return marker, f"{new_line}\n{marker}"


def _insert_after(marker: str, new_line: str) -> tuple[str, str]:
    """Helper: returns (find, replace) to insert new_line after marker."""
    return marker, f"{marker}\n{new_line}"


def _edit(file: str, find: str, replace: str) -> PromptEdit:
    return PromptEdit(file=file, find=find, replace=replace)


def _edit_remove(file: str, text: str) -> PromptEdit:
    return _edit(file, text, "")


def _edit_before(file: str, marker: str, new_line: str) -> PromptEdit:
    find, replace = _insert_before(marker, new_line)
    return _edit(file, find, replace)


def _edit_after(file: str, marker: str, new_line: str) -> PromptEdit:
    find, replace = _insert_after(marker, new_line)
    return _edit(file, find, replace)


# ── 100 Experiments ───────────────────────────────────────────────
# Design:
# - removal / simplification experiments first
# - prompt files alternated aggressively to reduce overfitting
# - single-file edits only, to keep attribution clean under noisy assessments

EXPERIMENTS: list[Experiment] = []


def _exp(desc: str, file: str, find: str, replace: str) -> None:
    name = f"exp{len(EXPERIMENTS) + 1:03d}"
    EXPERIMENTS.append(Experiment(name, desc, [_edit(file, find, replace)]))


def _exp_multi(desc: str, edits: list[PromptEdit]) -> None:
    name = f"exp{len(EXPERIMENTS) + 1:03d}"
    EXPERIMENTS.append(Experiment(name, desc, edits))


def _exp_before(desc: str, file: str, marker: str, new_line: str) -> None:
    find, replace = _insert_before(marker, new_line)
    _exp(desc, file, find, replace)


def _exp_after(desc: str, file: str, marker: str, new_line: str) -> None:
    find, replace = _insert_after(marker, new_line)
    _exp(desc, file, find, replace)


def _exp_remove(desc: str, file: str, text: str) -> None:
    _exp(desc, file, text, "")


# ── 100 Experiments ───────────────────────────────────────────────

VERIFY_FIRST_EXAMPLE = "Definiție: Domeniul online al Austriei\nRăspuns: AT\n"
VERIFY_EXAMPLES_HEADER = "Exemple:"
DEFINITION_EXAMPLES_HEADER = "Exemple corecte:"
DEFINITION_COUNTEREXAMPLES_HEADER = "Contra-exemple (GREȘIT - sensuri englezești):"
RATE_FEEDBACK_MARKER = "- feedback-ul este exclusiv în română, scurt și concret"
RATE_JSON_MARKER = "Răspunzi STRICT cu un singur obiect JSON, fără text înainte sau după:"
REWRITE_MAX_WORDS_MARKER = "- Max 15 cuvinte."
REWRITE_NECLAR_MARKER = "- Dacă termenul este obscur și nu poți scrie onest, răspunzi exact: [NECLAR]"
USER_VERIFY_RESPONSE_HEADER = "Răspunsuri:"


def _block(*lines: str) -> str:
    return "\n".join(lines)


def _verify_example(definition: str, answer: str) -> str:
    return _block(f"Definiție: {definition}", f"Răspuns: {answer}")


def _exp_add_verify_example(desc: str, definition: str, answer: str) -> None:
    _exp_before(desc, SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _verify_example(definition, answer))


def _exp_add_definition_example(desc: str, answer: str, definition: str) -> None:
    _exp_before(desc, SYS_DEFINITION, DEFINITION_COUNTEREXAMPLES_HEADER, f"{answer} -> {definition}")


def _exp_add_definition_counterexample(desc: str, answer: str, definition: str, note: str) -> None:
    _exp_after(
        desc,
        SYS_DEFINITION,
        DEFINITION_COUNTEREXAMPLES_HEADER,
        f"{answer} -> {definition} [GREȘIT - {note}]",
    )


def _exp_add_rate_rule(desc: str, line: str) -> None:
    _exp_before(desc, SYS_RATE, RATE_FEEDBACK_MARKER, line)


def _exp_add_rate_example(desc: str, line: str) -> None:
    _exp_before(desc, SYS_RATE, RATE_JSON_MARKER, line)


# ── ROUND 1: cleanup/removal pass ─────────────────────────────────

_exp(
    "shorten verify user counting sentence",
    USR_VERIFY,
    "Numără literele fiecărei variante înainte de a răspunde. Dacă nu are exact {answer_length} litere, nu o include.",
    "Excluzi orice variantă care nu are exact {answer_length} litere.",
)
_exp(
    "compress Romanian-only line in verify system",
    SYS_VERIFY,
    "- Gândești și răspunzi exclusiv în română.\n",
    "- Gândești și răspunzi numai în română.\n",
)
_exp_remove(
    "remove do-not-rephrase line from verify system",
    SYS_VERIFY,
    "- Nu reformulezi definiția.\n",
)
_exp(
    "compress rare-sense lines in definition",
    SYS_DEFINITION,
    "- Dacă DEX oferă mai multe sensuri valide, poți alege și un sens mai rar, dar trebuie să fie un sens românesc real și formulat exact.\n- Nu alege automat sensul cel mai comun dacă alt sens valid duce mai exact la cuvântul cerut.\n",
    "- Dacă există mai multe sensuri românești valide, poți alege unul mai rar dacă duce mai exact la răspuns.\n",
)
_exp(
    "compress rare-sense lines in rewrite",
    SYS_REWRITE,
    "- Dacă termenul are mai multe sensuri românești valide, poți trece la un alt sens DEX mai exact sau mai ghicibil; nu rămâi blocat pe sensul cel mai comun.\n- Nu rescrie definiția spre un alt cuvânt mai uzual; rescrie spre același răspuns, chiar dacă sensul lui bun este mai rar.\n",
    "- Dacă există mai multe sensuri românești valide, poți trece la unul mai rar dacă duce mai exact la răspuns.\n",
)
_exp(
    "shorten creativity explanation in rate but keep RIAL",
    SYS_RATE,
    '- creativity_score: cât de ingenios exploatează definiția un joc de domenii sau o ambiguitate surprinzătoare — o definiție directă de dicționar primește 3-4, o perifrază care face rezolvitorul să se gândească inițial la alt domeniu primește 8-10 (ex: RIAL -> "Se plătește la șah" = surpriză domeniu)',
    '- creativity_score: cât de nebanal dar util este indiciul; o definiție directă primește 3-4, iar o surpriză de domeniu exactă poate primi 8-10 (ex: RIAL -> "Se plătește la șah")',
)
_exp(
    "shorten generate user final instruction",
    USR_GENERATE,
    "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
    "Scrie o definiție exactă. Răspunde doar cu definiția.",
)
_exp(
    "remove alternate-valid-sense sentence from rewrite user",
    USR_REWRITE,
    "Rescrie definiția mai precis și mai scurt. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
    "Rescrie definiția mai precis și mai scurt.",
)
_exp(
    "fold diacritics counting into verify length step",
    SYS_VERIFY,
    "- Diacriticele nu contează la numărarea lungimii.\nProces de rezolvare:\n1. Citește definiția atent.\n2. Gândește la 1-3 cuvinte românești care se potrivesc.\n3. Verifică pentru fiecare: are exact lungimea cerută?\n4. Păstrează doar variantele care respectă lungimea.\n",
    "Proces de rezolvare:\n1. Citește definiția atent.\n2. Gândește la 1-3 cuvinte românești care se potrivesc.\n3. Verifică pentru fiecare: are exact lungimea cerută, fără să numeri diacriticele separat?\n4. Păstrează doar variantele care respectă lungimea.\n",
)
_exp(
    "shorten precise-natural-max12 line in definition",
    SYS_DEFINITION,
    "- Preferi definiții precise, naturale, maxim 12 cuvinte.\n",
    "- Preferi definiții precise și naturale.\n",
)
_exp(
    "shorten numar variante in verify user",
    USR_VERIFY,
    "Număr variante: maximum {max_guesses}",
    "Variante: maximum {max_guesses}",
)
_exp(
    "replace rewrite more-precise-than-old line with exact",
    SYS_REWRITE,
    "- Fă definiția mai precisă decât cea veche.\n",
    "- Fă definiția mai exactă.\n",
)

# ── ROUND 2: verify example refresh for short/function words ─────

for answer, definition in [
    ("OF", "Interjecție de durere ori regret"),
    ("UZ", "Folosire curentă a unui lucru"),
    ("AZ", "Astăzi"),
    ("AN", "Unitate de timp de 12 luni"),
    ("EU", "Pronumele persoanei care vorbește"),
    ("AUT", "Minge ieșită din teren"),
    ("DAR", "Conjuncție adversativă"),
    ("FI", "A exista"),
    ("IN", "Plantă textilă cu flori albastre"),
    ("ATU", "Carte de joc cu valoare maximă"),
    ("LA", "Prepoziție de direcție sau destinație"),
    ("AR", "Unitate de suprafață de 100 m²"),
]:
    _exp_add_verify_example(f"add verify example {answer}", definition, answer)

# ── ROUND 3: verify example refresh for rare / technical senses ──

for answer, definition in [
    ("FLU", "Cu contururi estompate"),
    ("ITI", "A se arăta puțin, pe furiș"),
    ("CATA", "Băț cu cârlig pentru prins oi"),
    ("UMEZITOR", "Aparat care crește umiditatea aerului"),
    ("HOTAR", "Linie de demarcație între proprietăți"),
    ("TRONARE", "Faptul de a sta pe scaun domnesc"),
    ("EPIGASTRU", "Partea superioară a abdomenului"),
    ("IMN", "Cântec solemn de preamărire"),
    ("RUT", "Perioada împerecherii la animale"),
    ("OSTRACA", "Ciob atic folosit la vot de exil"),
    ("STIMULAT", "Îndemnat la randament mai mare"),
    ("LECTURAT", "Citit pentru pregătirea tiparului"),
]:
    _exp_add_verify_example(f"add verify example {answer}", definition, answer)

# ── ROUND 4: rewrite anti-distractor pass ────────────────────────

_exp_before(
    "add rewrite rule to exclude common grouped distractors",
    SYS_REWRITE,
    REWRITE_MAX_WORDS_MARKER,
    "- Dacă ghicirile greșite se grupează în jurul unui sinonim comun, adaugi un detaliu care îl exclude.",
)
_exp_before(
    "add rewrite rule to beat all proposed candidates",
    SYS_REWRITE,
    REWRITE_MAX_WORDS_MARKER,
    "- Dacă AI a propus 2-3 răspunsuri apropiate, rescrii contra tuturor.",
)
_exp_before(
    "frame failure history as distractors to avoid",
    USR_REWRITE,
    "{bad_example_text}{failure_history_text}",
    "Privește răspunsurile greșite ca distractori de evitat.",
)
_exp_before(
    "add thin-dex-gloss rewrite rule",
    SYS_REWRITE,
    REWRITE_NECLAR_MARKER,
    "- Dacă DEX-ul dă doar o etichetă lexicografică, desfaci sensul concret, nu copiezi eticheta.",
)
_exp_before(
    "add action-fact rewrite rule",
    SYS_REWRITE,
    REWRITE_MAX_WORDS_MARKER,
    "- La substantive de acțiune sau fapt, definești actul ori rezultatul concret, nu formula «faptul de a».",
)
_exp_before(
    "add participle-adjective rewrite rule",
    SYS_REWRITE,
    REWRITE_MAX_WORDS_MARKER,
    "- La participii și adjective rezultate, pornești de la consecința observabilă, nu de la verbul-bază.",
)
_exp_before(
    "add short-word canonical-meaning rewrite rule",
    SYS_REWRITE,
    REWRITE_MAX_WORDS_MARKER,
    "- Pentru răspunsuri scurte, pornești de la sensul rebusistic românesc cel mai fixat.",
)
_exp_before(
    "add empty-guess rewrite fallback",
    USR_REWRITE,
    "Rescrie definiția mai precis și mai scurt.",
    "Dacă toate ghicirile au fost goale, devii mai literal și mai concret.",
)
_exp_before(
    "add rewrite steer-away from english and common competitor guesses",
    USR_REWRITE,
    "Rescrie definiția mai precis și mai scurt.",
    "Eviți formulările care trimit la un concurent englezesc sau mai comun.",
)
_exp(
    "rename rewrite bad-example lead to duce la alt raspuns",
    USR_REWRITE,
    "{bad_example_text}{failure_history_text}",
    "Duce la alt răspuns:\n{bad_example_text}{failure_history_text}",
)
_exp_before(
    "add concrete rewrite pair OF versus AH AI",
    SYS_REWRITE,
    REWRITE_MAX_WORDS_MARKER,
    "- Exemplu: pentru OF, adaugi exact ce exclude AH sau AI.",
)
_exp_before(
    "add concrete rewrite pair UZ versus UT",
    SYS_REWRITE,
    REWRITE_MAX_WORDS_MARKER,
    "- Exemplu: pentru UZ, adaugi exact ce exclude UT.",
)

# ── ROUND 5: definition counterexamples and positive examples ────

_exp_add_definition_counterexample("add definition negative example UZ", "UZ", "Utilizare", "prea abstract")
_exp_add_definition_counterexample("add definition negative example ATU", "ATU", "Carte de joc", "prea larg")
_exp_add_definition_counterexample("add definition negative example FLU", "FLU", "Neclar", "prea vag")
_exp_add_definition_counterexample("add definition negative example CATA", "CATA", "Persoană rea", "prea larg")
_exp_add_definition_example("add definition positive example OF", "OF", "Interjecție de durere ori regret")
_exp_add_definition_example("add definition positive example UZ", "UZ", "Folosire curentă a unui lucru")
_exp_add_definition_example("add definition positive example ATU", "ATU", "Carte de joc cu valoare maximă")
_exp_add_definition_example("add definition positive example FLU", "FLU", "Cu contururi estompate")
_exp_add_definition_example("add definition positive example ITI", "ITI", "A se arăta puțin, pe furiș")
_exp_before(
    "add thin-lexicographic-first-def rule to definition",
    SYS_DEFINITION,
    DEFINITION_EXAMPLES_HEADER,
    "- Dacă prima definiție DEX e doar etichetă lexicografică, o reformulezi în sens concret.",
)
_exp_before(
    "add short-word canonical-meaning rule to definition",
    SYS_DEFINITION,
    DEFINITION_EXAMPLES_HEADER,
    "- Pentru răspunsuri de 2-3 litere, țintești mai întâi sensul rebusistic românesc cel mai fixat.",
)
_exp(
    "replace flexion-confusion line with competitor-based form detail",
    SYS_DEFINITION,
    "- Dacă există risc de confuzie de gen, număr sau flexiune, formulează definiția pentru forma exactă cerută.\n",
    "- Adaugi detaliu de formă sau domeniu numai când elimină un concurent real.\n",
)

# ── ROUND 6: rate calibration for exact-bad failures ─────────────

_exp_add_rate_rule(
    "add OF-type ambiguity counterexample to rate",
    "- dacă definiția pentru OF lasă la fel de plauzibile AH sau AI: guessability_score mic",
)
_exp_add_rate_rule(
    "add ATU-type broad clue counterexample to rate",
    "- dacă definiția pentru ATU ar putea descrie orice carte de joc: guessability_score mic",
)
_exp_add_rate_rule(
    "add FLU semantically-right exact-bad counterexample to rate",
    "- dacă definiția e corectă pentru sensul lui FLU, dar rezolvitorul nu ajunge exact la cuvânt: guessability_score mic",
)
_exp_add_rate_rule(
    "add one-word-synonym UZ UT trap to rate",
    "- dacă definiția lui UZ trimite mai firesc la UT sau la un sinonim scurt apropiat: guessability_score mic",
)
_exp_add_rate_rule(
    "add near-synonym alternatives rule to rate",
    "- dacă toate alternativele sunt aproape sinonime dar răspunsul lipsește, guessability_score nu trece de 6",
)
_exp_add_rate_rule(
    "add DEX-correct but common competitor rule to rate",
    "- dacă definiția este corectă DEX, dar duce mai firesc la un concurent mai comun: guessability_score mic",
)
_exp_add_rate_rule(
    "add lexicographic-transform rule to rate",
    "- definițiile de tip «faptul de a» sau alte transformări lexicografice primesc guessability_score mic dacă nu spun sensul concret",
)
_exp_add_rate_rule(
    "add empty-verify penalty to rate",
    "- dacă verify nu propune nimic, guessability_score nu trece de 4",
)
_exp_add_rate_rule(
    "add stricter short-word exactness to rate",
    "- la 2-3 litere, o ambiguitate mică scade puternic guessability_score",
)
_exp_add_rate_example(
    "add low-score OF ambiguity JSON example to rate",
    'Exemplu scor mic:\n{"semantic_score": 8, "guessability_score": 3, "creativity_score": 4, "feedback": "Definiția este corectă, dar poate duce la AH, AI sau OF."}\n',
)
_exp_add_rate_example(
    "add medium-score rare technical noun JSON example to rate",
    'Exemplu scor mediu:\n{"semantic_score": 9, "guessability_score": 6, "creativity_score": 5, "feedback": "Definiția este exactă, dar termenul rămâne greu fără un detaliu distinctiv."}\n',
)
_exp_add_rate_rule(
    "add banal-but-correct rule to rate",
    "- o definiție banală dar corectă poate avea semantic_score mare și guessability_score doar mediu",
)

# ── ROUND 7: paired verify bundles ───────────────────────────────

_exp_multi(
    "paired verify bundle OF UZ AZ",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Interjecție de durere ori regret", "OF"),
            _verify_example("Folosire curentă a unui lucru", "UZ"),
            _verify_example("Astăzi", "AZ"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La interjecții și răspunsuri de 2-3 litere, elimini concurenții scurți mai comuni."),
    ],
)
_exp_multi(
    "paired verify bundle AN EU FI DAR AUT",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Unitate de timp de 12 luni", "AN"),
            _verify_example("Pronumele persoanei care vorbește", "EU"),
            _verify_example("A exista", "FI"),
            _verify_example("Conjuncție adversativă", "DAR"),
            _verify_example("Minge ieșită din teren", "AUT"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La cuvinte funcționale și forme gramaticale, sensul exact bate asemănarea vagă."),
    ],
)
_exp_multi(
    "paired verify bundle ATU IMN RUT",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Carte de joc cu valoare maximă", "ATU"),
            _verify_example("Cântec solemn de preamărire", "IMN"),
            _verify_example("Perioada împerecherii la animale", "RUT"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La sensuri consacrate dar nu triviale, compari toate variantele înainte să alegi."),
    ],
)
_exp_multi(
    "paired verify bundle EPIGASTRU OSTRACA",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Partea superioară a abdomenului", "EPIGASTRU"),
            _verify_example("Ciob atic folosit la vot de exil", "OSTRACA"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La termeni tehnici, păstrezi doar varianta care respectă domeniul exact."),
    ],
)
_exp_multi(
    "paired verify bundle STIMULAT LECTURAT",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Îndemnat la randament mai mare", "STIMULAT"),
            _verify_example("Citit pentru pregătirea tiparului", "LECTURAT"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La participii și rezultate, cauți forma exactă, nu verbul sau adjectivul vecin."),
    ],
)
_exp_multi(
    "paired verify bundle TRONARE ETALARE",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Faptul de a sta pe scaun domnesc", "TRONARE"),
            _verify_example("Așezare la vedere", "ETALARE"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La substantive de acțiune, cauți actul sau rezultatul, nu verbul gol."),
    ],
)
_exp_multi(
    "paired verify bundle HOTAR ALAI FERMENT",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Linie de demarcație între proprietăți", "HOTAR"),
            _verify_example("Procesiune festivă", "ALAI"),
            _verify_example("Agent al fermentării", "FERMENT"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La substantive comune cu mulți concurenți, alegi sensul cel mai distinctiv."),
    ],
)
_exp_multi(
    "paired verify bundle FLU AMETITOR",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Cu contururi estompate", "FLU"),
            _verify_example("Care provoacă amețeală", "AMETITOR"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La adjective și caracterizări, excluzi întâi sinonimul mai uzual."),
    ],
)
_exp_multi(
    "paired verify bundle CATA CASISOARA",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Băț cu cârlig pentru prins oi", "CATA"),
            _verify_example("Loc mic pentru brânză sau fructe", "CASISOARA"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La regionalisme și diminutive, nu sari la cuvântul comun dacă definiția cere altceva."),
    ],
)
_exp_multi(
    "paired verify bundle ATAS UMEZITOR",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Reprezentant diplomatic special", "ATAS"),
            _verify_example("Aparat care crește umiditatea aerului", "UMEZITOR"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La obiecte și roluri funcționale, păstrezi termenul exact, nu categoria largă."),
    ],
)
_exp_multi(
    "paired verify bundle FLIS OSTRACA rare nouns",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Fâșie subțire din rocă", "FLIS"),
            _verify_example("Ciob atic folosit la vot de exil", "OSTRACA"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La substantive rare de domeniu, păstrezi indiciul tehnic și elimini cuvântul comun apropiat."),
    ],
)
_exp_multi(
    "paired verify bundle AN EU ZI AR short controls",
    [
        _edit_before(SYS_VERIFY, VERIFY_FIRST_EXAMPLE, _block(
            _verify_example("Unitate de timp de 12 luni", "AN"),
            _verify_example("Pronumele persoanei care vorbește", "EU"),
            _verify_example("Interval de 24 de ore", "ZI"),
            _verify_example("Unitate de suprafață de 100 m²", "AR"),
        )),
        _edit_before(USR_VERIFY, USER_VERIFY_RESPONSE_HEADER, "La controale scurte, nu inventezi alternative doar pentru că lungimea se potrivește."),
    ],
)

# ── ROUND 8: paired definition + rewrite bundles ─────────────────

for desc, definition_line, rewrite_line in [
    (
        "paired definition rewrite bundle UZ AN OF EU short glosses",
        "- Pentru definiții DEX-scurte ca UZ, AN, OF sau EU, transformi eticheta într-un sens concret de rebus.",
        "- Dacă DEX-ul dă doar o etichetă scurtă, rescrii în sens concret, nu copiezi formula lexicografică.",
    ),
    (
        "paired definition rewrite bundle ADEVARA FI STIMULAT TRONARE action forms",
        "- La substantive de acțiune sau forme apropiate, descrii actul ori rezultatul concret, nu formula «faptul de a».",
        "- La substantive de acțiune sau fapt, rescrii spre actul ori rezultatul concret, nu spre formula dicționarului.",
    ),
    (
        "paired definition rewrite bundle ATU FLU ITI short rare senses",
        "- Pentru sensuri scurte dar rare, alegi detaliul care le separă de concurentul mai comun.",
        "- Pentru sensuri scurte dar rare, adaugi exact detaliul care scoate concurentul mai comun din joc.",
    ),
    (
        "paired definition rewrite bundle CATA OSTRACA EPIGASTRU obscure nouns",
        "- La substantive obscure, un detaliu de domeniu ori de poziție e mai util decât categoria largă.",
        "- La substantive obscure, rescrii cu detaliu de domeniu sau poziție înainte de orice stilizare.",
    ),
    (
        "paired definition rewrite bundle HOTAR ALAI FERMENT common competitors",
        "- Dacă un cuvânt are concurenți comuni apropiați, adaugi exact detaliul care îi exclude.",
        "- Dacă definiția ar trimite la un concurent comun, adaugi exact detaliul care îl exclude.",
    ),
    (
        "paired definition rewrite bundle LECTURAT AMETITOR INNOURAT OFIT participles",
        "- La participii și rezultate, descrii starea finală observabilă, nu verbul-bază.",
        "- La participii și rezultate, rescrii dinspre starea finală observabilă, nu dinspre verbul-bază.",
    ),
    (
        "paired definition rewrite bundle ATAS UMEZITOR device object nouns",
        "- La obiecte sau roluri funcționale, numești funcția exactă, nu clasa largă.",
        "- La obiecte și roluri funcționale, rescrii spre funcția exactă, nu spre categoria largă.",
    ),
    (
        "paired definition rewrite bundle SOCOLATA TOCANITA VAR food material nouns",
        "- La alimente și materiale, eviți eticheta generică și alegi trăsătura distinctivă.",
        "- La alimente și materiale, rescrii cu trăsătura distinctivă, nu cu eticheta generică.",
    ),
]:
    _exp_multi(
        desc,
        [
            _edit_before(SYS_DEFINITION, DEFINITION_EXAMPLES_HEADER, definition_line),
            _edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, rewrite_line),
        ],
    )

# ── ROUND 9: paired definition + rate bundles ────────────────────

for desc, definition_line, rate_line in [
    (
        "paired definition rate bundle broad category penalties",
        "- Dacă indiciul numește doar categoria largă, îl strângi până rămâne răspunsul exact.",
        "- dacă definiția rămâne la categoria largă pentru ATU, OF sau FLU: guessability_score mic",
    ),
    (
        "paired definition rate bundle rare valid sense protection",
        "- Un sens rar dar real rămâne bun dacă definiția îl face clar și unic.",
        "- nu cobori semantic_score doar pentru că sensul ales e rar, dacă definiția este exactă și românească",
    ),
    (
        "paired definition rate bundle one-word synonym traps",
        "- Eviți definițiile de un singur sinonim dacă acel sinonim trimite mai firesc la alt cuvânt.",
        "- un sinonim de un cuvânt care trimite la alt răspuns mai firesc nu merită guessability_score mare",
    ),
    (
        "paired definition rate bundle short exactness",
        "- La 2-3 litere, alegi sensul rebusistic fixat, nu o parafrază largă.",
        "- la 2-3 litere, dacă rămân două opțiuni naturale, guessability_score nu poate fi mare",
    ),
]:
    _exp_multi(
        desc,
        [
            _edit_before(SYS_DEFINITION, DEFINITION_EXAMPLES_HEADER, definition_line),
            _edit_before(SYS_RATE, RATE_FEEDBACK_MARKER, rate_line),
        ],
    )

# ── ROUND 10: confirmatory three-file bundles ────────────────────

for desc, definition_line, verify_line, rewrite_line in [
    (
        "three-file confirm bundle OF AZ UZ",
        "- Pentru OF, AZ și UZ, alegi sensul scurt românesc fixat și excluzi concurentul mai comun.",
        "- La OF, AZ și UZ, compari toate variantele scurte și păstrezi doar sensul exact cerut.",
        "- Pentru OF, AZ și UZ, adaugi exact detaliul care scoate concurentul scurt mai comun din joc.",
    ),
    (
        "three-file confirm bundle ADEVARA STIMULAT TRONARE UMEZITOR",
        "- La substantive de acțiune și participii, formulezi sensul concret al actului, rezultatului ori funcției.",
        "- La forme de acțiune, participii și funcții, elimini imediat verbul-bază sau categoria largă.",
        "- La substantive de acțiune și funcții, rescrii spre rezultatul concret, nu spre formula dicționarului.",
    ),
    (
        "three-file confirm bundle EPIGASTRU OSTRACA CATA FLIS",
        "- La substantive tehnice sau rare, un detaliu de domeniu ori de poziție trebuie să apară în definiție.",
        "- La termeni tehnici sau rari, nu alegi răspunsul fără domeniul exact.",
        "- La substantive tehnice sau rare, adaugi domeniul exact înainte de stil sau scurtare.",
    ),
    (
        "three-file confirm bundle HOTAR ALAI FERMENT SOCOLATA",
        "- Dacă un cuvânt are concurenți naturali apropiați, introduci direct detaliul care îi exclude.",
        "- La cuvinte cu mulți concurenți apropiați, sensul exact bate varianta mai comună.",
        "- La cuvinte cu concurenți apropiați, rescrii împotriva sinonimului firesc, nu împotriva unui adversar imaginar.",
    ),
]:
    _exp_multi(
        desc,
        [
            _edit_before(SYS_DEFINITION, DEFINITION_EXAMPLES_HEADER, definition_line),
            _edit_before(SYS_VERIFY, VERIFY_EXAMPLES_HEADER, verify_line),
            _edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, rewrite_line),
        ],
    )


def _validate_experiments() -> None:
    seen_names = set()
    for exp in EXPERIMENTS:
        assert exp.name not in seen_names, exp.name
        seen_names.add(exp.name)
        assert exp.edits, exp.name
        for edit in exp.edits:
            assert edit.find, f"{exp.name} has empty find text"
            assert edit.find != edit.replace, f"{exp.name} has no-op replacement"
            assert (PROMPTS_DIR / edit.file).exists(), f"{exp.name} targets missing file {edit.file}"


_validate_experiments()
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


def load_best_result_summary(backup_dir: Path) -> dict | None:
    path = backup_dir / BEST_ASSESSMENT_JSON
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_best_result_summary(backup_dir: Path, result: dict) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / BEST_ASSESSMENT_JSON).write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def protected_regression(current: dict, incumbent: dict | None) -> bool:
    if not incumbent:
        return False
    current_controls = current.get("protected_control_summary", {})
    best_controls = incumbent.get("protected_control_summary", {})
    for tier_name, best_tier in best_controls.items():
        current_tier = current_controls.get(tier_name)
        if not current_tier:
            continue
        if float(current_tier.get("pass_rate", 0.0)) < float(best_tier.get("pass_rate", 0.0)):
            return True
    return False


def classify_experiment_result(
    current: dict,
    incumbent: dict | None,
    best_composite: float,
) -> tuple[str, float, bool, bool]:
    composite = float(current["composite"])
    delta = composite - best_composite
    has_regression = protected_regression(current, incumbent)
    pass_regression = bool(
        incumbent and float(current["pass_rate"]) < float(incumbent.get("pass_rate", 0.0))
    )
    if composite > best_composite and not has_regression and not pass_regression:
        return "keep", delta, has_regression, pass_regression
    if abs(delta) <= UNCERTAINTY_DELTA or has_regression or pass_regression:
        return "uncertain", delta, has_regression, pass_regression
    return "discard", delta, has_regression, pass_regression


def apply_experiment(exp: Experiment) -> bool:
    """Apply an experiment's edits atomically. Returns True if applied."""
    new_contents: dict[Path, str] = {}

    for edit in exp.edits:
        filepath = PROMPTS_DIR / edit.file
        if filepath not in new_contents:
            if not filepath.exists():
                print(f"  [SKIP] File not found: {filepath}")
                return False
            new_contents[filepath] = filepath.read_text(encoding="utf-8")

        content = new_contents[filepath]
        if edit.find in edit.replace and edit.replace in content:
            print(f"  [SKIP] Replacement text already present in {edit.file}")
            return False
        if edit.find not in content:
            print(f"  [SKIP] Find text not found in {edit.file}")
            return False

        updated = content.replace(edit.find, edit.replace, 1)
        if updated == content:
            print(f"  [SKIP] No change after replacement in {edit.file}")
            return False
        new_contents[filepath] = updated

    for filepath, updated in new_contents.items():
        filepath.write_text(updated, encoding="utf-8")
    return True


def build_assessment_description(prefix: str, exp: Experiment) -> str:
    """Human-readable experiment label stored in TSV/logs."""
    base = f"{prefix}{exp.name}" if prefix else exp.name
    return f"{base} | {exp.desc} | {exp.file}"


def resolve_experiment_window(
    *,
    start_from: int | None,
    end_at: int | None,
    preset: str,
) -> tuple[int, int]:
    preset_start, preset_end = EXPERIMENT_PRESETS[preset]
    start = preset_start if start_from is None else max(start_from, preset_start)
    end = preset_end if end_at is None else min(end_at, preset_end)
    if start < 1 or end > len(EXPERIMENTS):
        raise ValueError(f"Experiment window out of bounds: {start}-{end}")
    if start > end:
        raise ValueError(f"Empty experiment window: {start}-{end}")
    return start, end


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
    assessment_json_path: Path | None = None,
    stream_output: bool = False,
) -> dict:
    """Run the multi-model assessment and return parsed results."""
    cmd = [
        sys.executable, "-u", "-m", "generator.assessment.run_assessment",
        "--description", description,
        "--no-append-tsv",
    ]
    if assessment_json_path is not None:
        cmd.extend(["--json-out", str(assessment_json_path)])
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
    if assessment_json_path is None or not assessment_json_path.exists():
        return {"composite": 0.0, "pass_rate": 0.0, "avg_semantic": 0.0, "avg_rebus": 0.0, "error": True}
    payload = json.loads(assessment_json_path.read_text(encoding="utf-8"))
    payload["error"] = False
    return payload


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
    handle = install_process_logging(
        run_id=f"experiments_{path_timestamp()}",
        component="run_experiments",
        tee_console=True,
    )
    parser = argparse.ArgumentParser(description="Run prompt experiments")
    parser.add_argument("--preset", choices=sorted(EXPERIMENT_PRESETS), default="full",
                        help="Named experiment slice to run")
    parser.add_argument("--start-from", type=int,
                        help="Resume from experiment N (1-indexed)")
    parser.add_argument("--end-at", type=int,
                        help="Stop after experiment N (1-indexed)")
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
    try:
        args = parser.parse_args()
        start_from, end_at = resolve_experiment_window(
            start_from=args.start_from,
            end_at=args.end_at,
            preset=args.preset,
        )

        if args.dry_run:
            for i, exp in enumerate(EXPERIMENTS, 1):
                if i < start_from or i > end_at:
                    continue
                print(f"{i:3d}. [{exp.name}] {exp.desc}")
                print(f"     File: {exp.file}")
            print(f"\nSelection: experiments {start_from}-{end_at}")
            print(f"Selected: {end_at - start_from + 1} / {len(EXPERIMENTS)} experiments")
            return

        if args.assessment_logs_dir is None:
            args.assessment_logs_dir = args.log_path.parent / f"{args.log_path.stem}_logs"
        if args.git_live_push:
            args.git_live_commit = True
        if (args.git_live_commit or args.git_live_push) and not args.git_live_branch:
            args.git_live_branch = git_current_branch()

        if args.reset_log and args.log_path.exists():
            args.log_path.unlink()
        log = load_log(args.log_path)
        completed_names = {entry["name"] for entry in log}

        best_result_summary = load_best_result_summary(args.backup_dir)
        best_composite = get_last_composite()
        if best_result_summary is not None:
            best_composite = max(best_composite, float(best_result_summary.get("composite", best_composite)))
        print(f"Starting composite: {best_composite:.1f}")
        print(f"Total experiments: {len(EXPERIMENTS)}")
        print(f"Preset: {args.preset}")
        print(f"Selection: experiments {start_from}-{end_at}")
        print(f"Experiment log: {args.log_path}")
        print(f"Best-prompt backup: {args.backup_dir}")
        print(f"Assessment logs dir: {args.assessment_logs_dir}")
        if args.git_live_commit:
            print("Git live commit: enabled")
        if args.git_live_push:
            print(f"Git live push: {args.git_live_remote}/{args.git_live_branch}")
        if args.description_prefix:
            print(f"Description prefix: {args.description_prefix}")

        backup_prompts(args.backup_dir)
        print(f"Best prompts backed up to {args.backup_dir}")

        kept = 0
        skipped = 0
        discarded = 0
        uncertain = 0
        total_start = time.monotonic()

        for i, exp in enumerate(EXPERIMENTS, 1):
            if i < start_from or i > end_at:
                continue

            if exp.name in completed_names:
                print(f"\n[{i}/{len(EXPERIMENTS)}] {exp.name} — already completed, skipping")
                skipped += 1
                continue

            print(f"\n{'='*60}")
            print(f"[{i}/{len(EXPERIMENTS)}] {exp.name}: {exp.desc}")
            print(f"{'='*60}")

            restore_prompts(args.backup_dir)

            applied = apply_experiment(exp)
            assessment_description = build_assessment_description(args.description_prefix, exp)
            if not applied:
                entry = {
                    "name": exp.name,
                    "assessment_description": assessment_description,
                    "file": exp.file,
                    "files": exp.files,
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
            assessment_json_path = args.assessment_logs_dir / f"{exp.name}.json"
            prompt_paths = [PROMPTS_DIR / file for file in exp.files]

            if args.git_live_commit:
                git_stage_commit_push(
                    prompt_paths,
                    assessment_description,
                    push=args.git_live_push,
                    remote=args.git_live_remote,
                    branch=args.git_live_branch or "",
                )

            try:
                result = run_assessment(
                    assessment_description,
                    assessment_log_path=assessment_log_path,
                    assessment_json_path=assessment_json_path,
                    stream_output=args.stream_assessment_output,
                )
            except KeyboardInterrupt:
                print("\n  [INTERRUPTED] Restoring best prompts and discarding partial results")
                restore_prompts(args.backup_dir)
                restore_results_tsv(results_snapshot)
                if args.git_live_commit:
                    git_stage_commit_push(
                        prompt_paths,
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
                    "assessment_json": str(assessment_json_path),
                    "file": exp.file,
                    "files": exp.files,
                    "find": exp.find,
                    "replace": exp.replace,
                    "desc": exp.desc,
                    "status": "error",
                    "best_composite": best_composite,
                }
                log.append(entry)
                save_log(args.log_path, log)
                skipped += 1
                restore_prompts(args.backup_dir)
                restore_results_tsv(results_snapshot)
                append_results_row(assessment_description, "error", result)
                if args.git_live_commit:
                    git_stage_commit_push(
                        [*prompt_paths, RESULTS_TSV],
                        f"error | {assessment_description}",
                        push=args.git_live_push,
                        remote=args.git_live_remote,
                        branch=args.git_live_branch or "",
                    )
                continue

            composite = float(result["composite"])
            status, delta, has_regression, pass_regression = classify_experiment_result(
                result,
                best_result_summary,
                best_composite,
            )
            symbol = {
                "keep": "✓ IMPROVED",
                "uncertain": "? Uncertain",
                "discard": "✗ No improvement",
            }[status]
            print(
                f"  {symbol}: {best_composite:.1f} → {composite:.1f} "
                f"(pass={float(result['pass_rate']):.3f} sem={float(result['avg_semantic']):.1f} reb={float(result['avg_rebus']):.1f})"
            )

            entry = {
                "name": exp.name,
                "assessment_description": assessment_description,
                "assessment_log": str(assessment_log_path),
                "assessment_json": str(assessment_json_path),
                "file": exp.file,
                "files": exp.files,
                "find": exp.find,
                "replace": exp.replace,
                "desc": exp.desc,
                "status": status,
                "composite": composite,
                "pass_rate": float(result["pass_rate"]),
                "avg_semantic": float(result["avg_semantic"]),
                "avg_rebus": float(result["avg_rebus"]),
                "prev_best": best_composite,
                "delta": round(delta, 1),
                "protected_regression": has_regression,
                "pass_regression": pass_regression,
            }
            log.append(entry)
            save_log(args.log_path, log)

            if status == "keep":
                best_composite = composite
                best_result_summary = result
                save_best_prompts(args.backup_dir)
                save_best_result_summary(args.backup_dir, result)
                append_results_row(assessment_description, "keep", result)
                kept += 1
                print(f"  New best: {best_composite:.1f}")
                if args.git_live_commit:
                    git_stage_commit_push(
                        [RESULTS_TSV, assessment_json_path],
                        f"keep | {assessment_description}",
                        push=args.git_live_push,
                        remote=args.git_live_remote,
                        branch=args.git_live_branch or "",
                    )
            else:
                restore_prompts(args.backup_dir)
                restore_results_tsv(results_snapshot)
                append_results_row(assessment_description, status, result)
                if status == "uncertain":
                    uncertain += 1
                else:
                    discarded += 1
                if args.git_live_commit:
                    git_stage_commit_push(
                        [RESULTS_TSV, assessment_json_path],
                        f"{status} | {assessment_description}",
                        push=args.git_live_push,
                        remote=args.git_live_remote,
                        branch=args.git_live_branch or "",
                    )

        restore_prompts(args.backup_dir)

        total_elapsed = time.monotonic() - total_start
        print(f"\n{'='*60}")
        print("EXPERIMENT RUN COMPLETE")
        print(f"{'='*60}")
        print(f"Total time: {total_elapsed/3600:.1f}h")
        print(f"Final best composite: {best_composite:.1f}")
        print(f"Kept: {kept}, Uncertain: {uncertain}, Discarded: {discarded}, Skipped: {skipped}")

        kept_entries = [e for e in log if e.get("status") == "keep"]
        if kept_entries:
            print("\nKept experiments:")
            for e in kept_entries:
                print(f"  {e['name']}: {e['desc']} — composite={e['composite']:.1f}")
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
