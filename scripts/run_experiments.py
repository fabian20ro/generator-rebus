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
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from generator.assessment.benchmark_policy import (
    CAMPAIGN_STOP_STALE_FAMILIES,
    CONTROL_WORD_REPEAT_FAIL_ACTION,
    CONTROL_WORD_WATCH,
    DIRECTION_FOLLOWUP_PRESETS,
    EXPERIMENT_COMPARISON_RUNS,
    EXPERIMENT_FAMILY_PRIORITY,
    EXPERIMENT_BLOCK_RANGES,
    FAMILY_STOP_CONSECUTIVE_NON_KEEPS,
    FAMILY_STOP_REPEAT_COLLATERAL,
    FAMILY_STOP_TOTAL_NON_KEEPS,
    FOLLOWUP_PRIORITY,
    NEAR_MISS_PASS_DELTA,
    PRIMARY_FRAGILE_WORD_WATCH,
    RESEARCH_SIGNAL_MIN_GAINED_WORDS,
    SECONDARY_FRAGILE_WORD_WATCH,
    PILOT_EXPERIMENT_RANGE,
    V2_CAMPAIGN_STOP_STALE_FAMILIES,
    UNCERTAINTY_DELTA,
    V4_CAMPAIGN_STOP_STALE_FAMILIES,
    V4_EXPERIMENT_FAMILY_PRIORITY,
    V4_FAMILY_STOP_CONSECUTIVE_NON_KEEPS,
    V4_FAMILY_STOP_REPEAT_PRIMARY,
    V4_FAMILY_STOP_TOTAL_NON_KEEPS,
    V5_CAMPAIGN_STOP_STALE_FAMILIES,
    V5_EXPERIMENT_FAMILY_PRIORITY,
    V5_FAMILY_STOP_CONSECUTIVE_NON_KEEPS,
    V5_FAMILY_STOP_REPEAT_PRIMARY,
    V5_FAMILY_STOP_TOTAL_NON_KEEPS,
    V6_CAMPAIGN_STOP_STALE_FAMILIES,
    V6_EXPERIMENT_FAMILY_PRIORITY,
    V6_FAMILY_STOP_CONSECUTIVE_NON_KEEPS,
    V6_FAMILY_STOP_REPEAT_PRIMARY,
    V6_FAMILY_STOP_TOTAL_NON_KEEPS,
    V2_EXPERIMENT_FAMILY_PRIORITY,
    V2_FAMILY_STOP_CONSECUTIVE_NON_KEEPS,
    V2_FAMILY_STOP_REPEAT_PRIMARY,
    V2_FAMILY_STOP_TOTAL_NON_KEEPS,
    V3_CAMPAIGN_STOP_STALE_FAMILIES,
    V3_EXPERIMENT_FAMILY_PRIORITY,
    V3_FAMILY_STOP_CONSECUTIVE_NON_KEEPS,
    V3_FAMILY_STOP_REPEAT_PRIMARY,
    V3_FAMILY_STOP_TOTAL_NON_KEEPS,
)
from generator.core.runtime_logging import install_process_logging, log, path_timestamp

PROMPTS_DIR = PROJECT_ROOT / "generator" / "prompts"
RESULTS_TSV = PROJECT_ROOT / "generator" / "assessment" / "results.tsv"
DEFAULT_EXPERIMENT_LOG = PROJECT_ROOT / "generator" / "assessment" / "experiment_log.json"
BEST_BACKUP_DIR = Path("/tmp/prompt_experiment_best")
BEST_ASSESSMENT_JSON = "best_assessment.json"
BEST_RESULT_STATE_ROOT = PROJECT_ROOT / "build" / "prompt_experiment_state"
EXPERIMENT_PRESETS = {
    "full": (1, 100),
    "pilot": PILOT_EXPERIMENT_RANGE,
    **EXPERIMENT_BLOCK_RANGES,
}
V2_EXPERIMENT_PRESETS = {
    "full": (1, 40),
    "short-word-exactness": (1, 10),
    "near-neighbor-exclusion": (11, 20),
    "blank-output-concretization": (21, 30),
    "rare-technical-noun-rescue": (31, 40),
}
V3_EXPERIMENT_PRESETS = {
    "full": (1, 16),
    "system-factor-temperatures": (1, 4),
    "verify-minimal-procedural": (5, 8),
    "rewrite-generic-exclusion": (9, 12),
    "prompt-dedup-cleanup": (13, 16),
}
V4_EXPERIMENT_PRESETS = {
    "full": (1, 8),
    "rewrite-rule-readditions": (1, 3),
    "rewrite-header-variants": (4, 5),
    "rewrite-compactness-bias": (6, 8),
}
V5_EXPERIMENT_PRESETS = {
    "full": (1, 8),
    "header-signal-isolation": (1, 3),
    "header-signal-blends": (4, 5),
    "precision-support": (6, 8),
}
V6_EXPERIMENT_PRESETS = {
    "full": (1, 8),
    "verify": (1, 4),
    "rate": (5, 6),
    "definition": (7, 8),
}
EXPERIMENT_PRESETS_BY_SET = {
    "v1": EXPERIMENT_PRESETS,
    "v2": V2_EXPERIMENT_PRESETS,
    "v3": V3_EXPERIMENT_PRESETS,
    "v4": V4_EXPERIMENT_PRESETS,
    "v5": V5_EXPERIMENT_PRESETS,
    "v6": V6_EXPERIMENT_PRESETS,
}
TARGET_DIRECTION_BLOCKS = {
    "verify-examples": "verify",
    "verify-bundles": "verify",
    "rewrite-anti-distractor": "rewrite",
    "definition-rewrite-bundles": "rewrite",
    "rate-exactness-calibration": "rate",
    "definition-rate-bundles": "rate",
}
FAMILY_UNLOCK_REQUIREMENTS = {
    "verify_bundles": ("verify_examples_short", "verify_examples_rare"),
    "definition_rewrite_bundles": (
        "definition_positive_examples",
        "definition_guidance",
        "rewrite_structural_guidance",
        "rewrite_framing",
    ),
    "definition_rate_bundles": (
        "definition_positive_examples",
        "definition_guidance",
        "rate_rules",
        "rate_counterexamples",
    ),
    "confirm_bundles": (
        "definition_positive_examples",
        "definition_guidance",
        "definition_rewrite_bundles",
        "definition_rate_bundles",
        "rewrite_structural_guidance",
        "rewrite_framing",
        "rate_rules",
        "rate_counterexamples",
    ),
}
V2_FAMILY_UNLOCK_REQUIREMENTS: dict[str, tuple[str, ...]] = {}

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
    family: str = "other"
    priority: int = 999
    tags: tuple[str, ...] = ()
    risk_words: tuple[str, ...] = ()
    target_words: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    assessment_overrides: dict[str, float | int | str] | None = None
    scope_label: str = ""

    @property
    def files(self) -> list[str]:
        files = list(dict.fromkeys(edit.file for edit in self.edits))
        if files:
            return files
        if self.scope_label:
            return [self.scope_label]
        return ["[system]"]

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


def _family_priority(family: str, experiment_set: str = "v1") -> int:
    if experiment_set == "v2":
        priority_order = V2_EXPERIMENT_FAMILY_PRIORITY
    elif experiment_set == "v3":
        priority_order = V3_EXPERIMENT_FAMILY_PRIORITY
    elif experiment_set == "v4":
        priority_order = V4_EXPERIMENT_FAMILY_PRIORITY
    elif experiment_set == "v5":
        priority_order = V5_EXPERIMENT_FAMILY_PRIORITY
    elif experiment_set == "v6":
        priority_order = V6_EXPERIMENT_FAMILY_PRIORITY
    else:
        priority_order = EXPERIMENT_FAMILY_PRIORITY
    try:
        return priority_order.index(family) + 1
    except ValueError:
        return len(priority_order) + 1


def _extract_upper_tokens(desc: str) -> tuple[str, ...]:
    tokens = []
    for token in desc.replace("/", " ").replace(",", " ").split():
        token = token.strip("()[]:;.")
        if token.isupper() and any(ch.isalpha() for ch in token):
            tokens.append(token)
    return tuple(dict.fromkeys(tokens))


def _family_for_index(index: int) -> str:
    if 1 <= index <= 12:
        return "cleanup"
    if 13 <= index <= 24:
        return "verify_examples_short"
    if 25 <= index <= 36:
        return "verify_examples_rare"
    if index == 39:
        return "rewrite_framing"
    if 37 <= index <= 48:
        return "rewrite_structural_guidance"
    if 49 <= index <= 52:
        return "definition_negative_examples"
    if 53 <= index <= 57:
        return "definition_positive_examples"
    if 58 <= index <= 60:
        return "definition_guidance"
    if 61 <= index <= 64:
        return "rate_counterexamples"
    if 65 <= index <= 72:
        return "rate_rules"
    if 73 <= index <= 84:
        return "verify_bundles"
    if 85 <= index <= 92:
        return "definition_rewrite_bundles"
    if 93 <= index <= 96:
        return "definition_rate_bundles"
    if 97 <= index <= 100:
        return "confirm_bundles"
    return "other"


def _metadata_for_experiment(index: int, desc: str) -> dict[str, object]:
    family = _family_for_index(index)
    return {
        "family": family,
        "priority": _family_priority(family, "v1"),
        "tags": (family,),
        "risk_words": (),
        "target_words": _extract_upper_tokens(desc),
        "prerequisites": tuple(FAMILY_UNLOCK_REQUIREMENTS.get(family, ())),
    }


# ── 100 Experiments ───────────────────────────────────────────────
# Design:
# - removal / simplification experiments first
# - prompt files alternated aggressively to reduce overfitting
# - single-file edits only, to keep attribution clean under noisy assessments

EXPERIMENTS: list[Experiment] = []


def _exp(desc: str, file: str, find: str, replace: str) -> None:
    index = len(EXPERIMENTS) + 1
    name = f"exp{index:03d}"
    EXPERIMENTS.append(
        Experiment(name, desc, [_edit(file, find, replace)], **_metadata_for_experiment(index, desc))
    )


def _exp_multi(desc: str, edits: list[PromptEdit]) -> None:
    index = len(EXPERIMENTS) + 1
    name = f"exp{index:03d}"
    EXPERIMENTS.append(Experiment(name, desc, edits, **_metadata_for_experiment(index, desc)))


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
V1_EXPERIMENTS = list(EXPERIMENTS)

def _v2_exp(
    name: str,
    desc: str,
    family: str,
    edits: list[PromptEdit],
    *,
    target_words: tuple[str, ...] = (),
    risk_words: tuple[str, ...] = (),
    prerequisites: tuple[str, ...] = (),
    assessment_overrides: dict[str, float | int | str] | None = None,
    scope_label: str = "",
) -> Experiment:
    return Experiment(
        name,
        desc,
        edits,
        family=family,
        priority=_family_priority(family, "v2"),
        tags=(family,),
        risk_words=risk_words,
        target_words=target_words,
        prerequisites=prerequisites,
        assessment_overrides=assessment_overrides,
        scope_label=scope_label,
    )


V2_EXPERIMENTS: list[Experiment] = []

for name, desc, line, targets in [
    ("v2exp001", "rewrite pair OF excludes AH", "- Pentru OF, păstrezi interjecția de durere ori regret și excluzi exclamația vagă de tip AH.", ("OF", "AH")),
    ("v2exp002", "rewrite pair UZ excludes UT", "- Pentru UZ, păstrezi folosirea practică și excluzi utilitatea ori avantajul generic.", ("UZ", "UT")),
    ("v2exp003", "rewrite pair AZ excludes AC", "- Pentru AZ, fixezi ideea de zi de acum și excluzi silaba fără sens temporal.", ("AZ", "AC")),
    ("v2exp004", "rewrite pair ATU excludes generic advantage", "- Pentru ATU, păstrezi cartea decisivă din joc, nu avantajul generic.", ("ATU",)),
    ("v2exp005", "rewrite pair IMN excludes ODA", "- Pentru IMN, păstrezi cântecul solemn colectiv, nu oda literară.", ("IMN", "ODA")),
    ("v2exp006", "rewrite pair AUT excludes automobil abbreviation", "- Pentru AUT, fixezi ieșirea în afara terenului, nu abrevierea pentru automobil.", ("AUT",)),
    ("v2exp007", "rewrite pair FLU excludes vague descriptor", "- Pentru FLU, păstrezi sensul românesc fixat și excluzi eticheta vagă ori neclară.", ("FLU",)),
]:
    V2_EXPERIMENTS.append(
        _v2_exp(
            name,
            desc,
            "short_word_exactness",
            [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, line)],
            target_words=targets,
        )
    )

for name, desc, line, targets in [
    ("v2exp008", "definition example OF exact short sense", "OF -> Interjecție care exprimă durere sau regret", ("OF",)),
    ("v2exp009", "definition example UZ exact short sense", "UZ -> Faptul de a folosi ceva", ("UZ",)),
    ("v2exp010", "definition example AZ exact short sense", "AZ -> Ziua de acum", ("AZ",)),
]:
    V2_EXPERIMENTS.append(
        _v2_exp(
            name,
            desc,
            "short_word_exactness",
            [_edit_before(SYS_DEFINITION, DEFINITION_COUNTEREXAMPLES_HEADER, line)],
            target_words=targets,
        )
    )

for name, desc, line, targets in [
    ("v2exp011", "rewrite pair TAVA excludes VASA", "- Pentru TAVA, păstrezi piesa plată cu margine ridicată și excluzi vasul generic.", ("TAVA", "VASA")),
    ("v2exp012", "rewrite pair MARMOR excludes MARMUR", "- Pentru MARMOR, formulezi sensul pietrei ornamentale fără a aluneca spre MARMUR.", ("MARMOR", "MARMUR")),
    ("v2exp013", "rewrite pair HOTAR excludes LIMITA", "- Pentru HOTAR, păstrezi linia de despărțire între locuri, nu limita abstractă generică.", ("HOTAR", "LIMITA")),
    ("v2exp014", "rewrite pair TRAGACI excludes BUTON", "- Pentru TRAGACI, păstrezi piesa care declanșează focul armei, nu butonul generic.", ("TRAGACI", "BUTON")),
    ("v2exp015", "rewrite pair FERMENT excludes DROJDIE", "- Pentru FERMENT, păstrezi substanța ori procesul fermentării, nu doar drojdia concretă.", ("FERMENT", "DROJDIE")),
    ("v2exp016", "rewrite pair UMEZITOR excludes UMEDITAR", "- Pentru UMEZITOR, păstrezi aparatul ori obiectul care face mai umed, nu adjectivul derivat.", ("UMEZITOR",)),
    ("v2exp017", "rewrite pair STAND excludes BIROU", "- Pentru STAND, păstrezi spațiul de prezentare ori expunere, nu biroul sau masa de lucru.", ("STAND", "BIROU")),
    ("v2exp018", "rewrite pair DEPARTA excludes RETRAGE", "- Pentru DEPARTA, păstrezi îndepărtarea propriu-zisă, nu retragerea generică.", ("DEPARTA", "RETRAGE")),
]:
    V2_EXPERIMENTS.append(
        _v2_exp(
            name,
            desc,
            "near_neighbor_exclusion",
            [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, line)],
            target_words=targets,
        )
    )

V2_EXPERIMENTS.append(
    _v2_exp(
        "v2exp019",
        "user rewrite excludes one near neighbor without broadening",
        "near_neighbor_exclusion",
        [_edit(
            USR_REWRITE,
            "Rescrie definiția mai precis și mai scurt. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
            "Rescrie definiția mai precis și mai scurt. Dacă primul candidat e aproape corect, excluzi doar acel concurent și nu lărgești definiția. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
        )],
    )
)
V2_EXPERIMENTS.append(
    _v2_exp(
        "v2exp020",
        "rewrite rule adds one distinctive near-neighbor detail",
        "near_neighbor_exclusion",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- Dacă verificatorul propune un vecin apropiat, adaugi un singur detaliu distinctiv: funcție, material, epocă sau parte anatomică.")],
    )
)

for name, desc, edits, targets in [
    (
        "v2exp021",
        "rewrite rule blank output uses physical locative functional anchor",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- Dacă verificatorul nu propune nimic, înlocuiești abstracția cu un reper fizic, locativ sau funcțional.")],
        (),
    ),
    (
        "v2exp022",
        "user rewrite blank output becomes object place part role action",
        [_edit(
            USR_REWRITE,
            "Rescrie definiția mai precis și mai scurt. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
            "Rescrie definiția mai precis și mai scurt. Dacă verificatorul nu propune nimic, fă definiția mai concretă: obiect, loc, parte, rol sau acțiune vizibilă. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
        )],
        (),
    ),
    (
        "v2exp023",
        "rewrite rule anatomy blanks need body position",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- La anatomie și părți de corp, pui poziția exactă în corp înainte de orice stilizare.")],
        ("EPIGASTRU",),
    ),
    (
        "v2exp024",
        "rewrite rule rare noun blanks need domain or context",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- La substantive rare fără răspuns, pui domeniul ori contextul concret înaintea oricărei perifraze elegante.")],
        ("ATAS", "RUT", "DRUSA", "FLIS"),
    ),
    (
        "v2exp025",
        "rewrite rule action result avoids fapt de a formula",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- La substantive de acțiune, spui actul sau rezultatul concret, nu formula de dicționar «faptul de a».")],
        ("TRONARE", "ETALARE", "ADEVARA"),
    ),
    (
        "v2exp026",
        "rewrite rule participles use observable consequence",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- La participii și adjective rezultate, numești consecința observabilă, nu operația abstractă.")],
        ("LECTURAT", "OFIT"),
    ),
    (
        "v2exp027",
        "rewrite rule short dex glosses unpack meaning not label",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- Dacă sursa dă doar o etichetă scurtă ori meta-definiție, desfaci sensul concret, nu repeți eticheta.")],
        (),
    ),
    (
        "v2exp028",
        "user rewrite replaces dictionary formula with visible trait",
        [_edit(
            USR_REWRITE,
            "Rescrie definiția mai precis și mai scurt. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
            "Rescrie definiția mai precis și mai scurt. Dacă totul sună ca o formulă de dicționar, schimbă spre o trăsătură, poziție sau funcție vizibilă. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
        )],
        (),
    ),
    (
        "v2exp029",
        "definition example EPIGASTRU with strict body location",
        [_edit_before(SYS_DEFINITION, DEFINITION_COUNTEREXAMPLES_HEADER, "EPIGASTRU -> Regiune superioară a abdomenului")],
        ("EPIGASTRU",),
    ),
    (
        "v2exp030",
        "definition example RUT with animal breeding period",
        [_edit_before(SYS_DEFINITION, DEFINITION_COUNTEREXAMPLES_HEADER, "RUT -> Perioadă de împerechere la animale")],
        ("RUT",),
    ),
]:
    V2_EXPERIMENTS.append(
        _v2_exp(
            name,
            desc,
            "blank_output_concretization",
            edits,
            target_words=targets,
        )
    )

for name, desc, line, targets in [
    ("v2exp031", "definition example ATAS sidecar object", "ATAS -> Vehicul lateral atașat unei motociclete", ("ATAS",)),
    ("v2exp032", "definition example DRUSA crystal cluster", "DRUSA -> Grup natural de cristale pe aceeași rocă", ("DRUSA",)),
    ("v2exp033", "definition example TOR architectural molding", "TOR -> Mulură convexă la baza unei coloane", ("TOR",)),
    ("v2exp034", "definition example OFIT dark green ornamental rock", "OFIT -> Rocă ornamentală verde-închis", ("OFIT",)),
    ("v2exp035", "definition example CEGA sturgeon fish", "CEGA -> Pește de apă dulce din familia sturionilor", ("CEGA",)),
    ("v2exp036", "definition example OSTIRE army host", "OSTIRE -> Totalitatea ostașilor unei țări", ("OSTIRE",)),
    ("v2exp037", "definition example FLIS decorative fringe", "FLIS -> Franjură îngustă decorativă de pe o țesătură", ("FLIS",)),
    ("v2exp038", "definition example ZEU deity sense", "ZEU -> Divinitate din credințele antice", ("ZEU",)),
    ("v2exp039", "definition example TUR organized visit route", "TUR -> Deplasare organizată pentru vizitare", ("TUR",)),
    ("v2exp040", "definition example URATURA New Year recitation", "URATURA -> Text ritmat rostit la Anul Nou", ("URATURA",)),
]:
    V2_EXPERIMENTS.append(
        _v2_exp(
            name,
            desc,
            "rare_technical_noun_rescue",
            [_edit_before(SYS_DEFINITION, DEFINITION_COUNTEREXAMPLES_HEADER, line)],
            target_words=targets,
        )
    )


def _validate_experiment_list(experiments: list[Experiment]) -> None:
    seen_names = set()
    for exp in experiments:
        assert exp.name not in seen_names, exp.name
        seen_names.add(exp.name)
        assert exp.edits or exp.assessment_overrides, exp.name
        for edit in exp.edits:
            assert edit.find, f"{exp.name} has empty find text"
            assert edit.find != edit.replace, f"{exp.name} has no-op replacement"
            assert (PROMPTS_DIR / edit.file).exists(), f"{exp.name} targets missing file {edit.file}"


_validate_experiment_list(V2_EXPERIMENTS)
assert len(V2_EXPERIMENTS) == 40, len(V2_EXPERIMENTS)

V3_EXPERIMENTS = [
    _v2_exp(
        "v3exp001",
        "system baseline temperatures generate 0.20 rewrite 0.30",
        "system_factor_temperatures",
        [],
        scope_label="[system]",
        assessment_overrides={"generate_temperature": 0.20, "rewrite_temperature": 0.30},
    ),
    _v2_exp(
        "v3exp002",
        "system temperatures generate 0.15 rewrite 0.15",
        "system_factor_temperatures",
        [],
        scope_label="[system]",
        assessment_overrides={"generate_temperature": 0.15, "rewrite_temperature": 0.15},
    ),
    _v2_exp(
        "v3exp003",
        "system temperatures generate 0.20 rewrite 0.15",
        "system_factor_temperatures",
        [],
        scope_label="[system]",
        assessment_overrides={"generate_temperature": 0.20, "rewrite_temperature": 0.15},
    ),
    _v2_exp(
        "v3exp004",
        "system temperatures generate 0.15 rewrite 0.20",
        "system_factor_temperatures",
        [],
        scope_label="[system]",
        assessment_overrides={"generate_temperature": 0.15, "rewrite_temperature": 0.20},
    ),
    _v2_exp(
        "v3exp005",
        "verify compress Romanian-only and remove mental translation line",
        "verify_minimal_procedural",
        [_edit(
            SYS_VERIFY,
            "- Gândești și răspunzi numai în română.\n- Dacă primul cuvânt care îți vine este în engleză, îl traduci mental și răspunzi în română.\n",
            "- Lucrezi doar în română.\n",
        )],
    ),
    _v2_exp(
        "v3exp006",
        "verify remove broad flexibility guidance",
        "verify_minimal_procedural",
        [_edit(
            SYS_VERIFY,
            "- Definiția poate folosi un sens figurat sau o referință din alt domeniu. Gândește flexibil.\n",
            "",
        )],
    ),
    _v2_exp(
        "v3exp007",
        "verify shorten process to shortlist and elimination",
        "verify_minimal_procedural",
        [_edit(
            SYS_VERIFY,
            "Proces de rezolvare:\n1. Citește definiția atent.\n2. Gândește la 1-3 cuvinte românești care se potrivesc.\n3. Verifică pentru fiecare: are exact lungimea cerută?\n4. Păstrează doar variantele care respectă lungimea.\n",
            "Proces de rezolvare:\n1. Propui 1-3 variante românești.\n2. Elimini formele cu lungime sau flexiune incompatibilă.\n3. Păstrezi doar variantele care se potrivesc exact.\n",
        )],
    ),
    _v2_exp(
        "v3exp008",
        "verify trim examples to two short canonical cases",
        "verify_minimal_procedural",
        [_edit(
            SYS_VERIFY,
            "Exemple:\nDefiniție: Domeniul online al Austriei\nRăspuns: AT\nDefiniție: Țesut dur al scheletului\nRăspuns: OS\nDefiniție: Formă a verbului a avea\nRăspuns: AI\nDefiniție: Substanță gazoasă pe care o respirăm\nRăspuns: AER\nDefiniție: Se trage un semnal de pericol\nRăspuns: ALARMA\n",
            "Exemple:\nDefiniție: Domeniul online al Austriei\nRăspuns: AT\nDefiniție: Țesut dur al scheletului\nRăspuns: OS\n",
        )],
    ),
    _v2_exp(
        "v3exp009",
        "rewrite add universal near-neighbor exclusion rule",
        "rewrite_generic_exclusion",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- Dacă verificatorul propune un vecin apropiat, adaugi un singur detaliu distinctiv și nu lărgești definiția.")],
    ),
    _v2_exp(
        "v3exp010",
        "rewrite add blank-output concretizer rule",
        "rewrite_generic_exclusion",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- Dacă verificatorul nu propune nimic, concretizezi prin obiect, loc, funcție sau parte.")],
    ),
    _v2_exp(
        "v3exp011",
        "rewrite add action-result anti-dictionary rule",
        "rewrite_generic_exclusion",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- La substantive de acțiune sau rezultat, eviți formula de dicționar și numești efectul concret.")],
    ),
    _v2_exp(
        "v3exp012",
        "user rewrite exclude one competitor only",
        "rewrite_generic_exclusion",
        [_edit(
            USR_REWRITE,
            "Rescrie definiția mai precis și mai scurt. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
            "Rescrie definiția mai precis și mai scurt. Dacă apare un concurent apropiat, îl excluzi printr-un singur detaliu, fără să lărgești definiția. Dacă există mai multe sensuri valide ale răspunsului, poți alege sensul mai exact.",
        )],
    ),
    _v2_exp(
        "v3exp013",
        "definition compress Romanian-only lines",
        "prompt_dedup_cleanup",
        [_edit(
            SYS_DEFINITION,
            "Ești autor de definiții de rebus în limba română.\nIMPORTANT: Toate cuvintele sunt exclusiv în limba ROMÂNĂ. Chiar dacă arată ca un cuvânt englezesc, definește-l DOAR cu sensul românesc.\n",
            "Ești autor de definiții de rebus în limba română.\nIMPORTANT: Lucrezi doar cu sensuri românești reale, nu englezești.\n",
        )],
    ),
    _v2_exp(
        "v3exp014",
        "definition compress exact-form guidance",
        "prompt_dedup_cleanup",
        [_edit(
            SYS_DEFINITION,
            "- Pentru cuvinte scurte, abrevieri și forme gramaticale fii literal și exact.\n- Dacă există risc de confuzie de gen, număr sau flexiune, formulează definiția pentru forma exactă cerută.\n",
            "- Pentru cuvinte scurte sau forme gramaticale, formulezi exact forma cerută.\n",
        )],
    ),
    _v2_exp(
        "v3exp015",
        "rewrite compress rare-valid-sense guidance",
        "prompt_dedup_cleanup",
        [_edit(
            SYS_REWRITE,
            "- Dacă termenul are mai multe sensuri românești valide, poți trece la un alt sens DEX mai exact sau mai ghicibil; nu rămâi blocat pe sensul cel mai comun.\n- Nu rescrie definiția spre un alt cuvânt mai uzual; rescrie spre același răspuns, chiar dacă sensul lui bun este mai rar.\n",
            "- Dacă există mai multe sensuri românești valide, alegi sensul care duce mai exact la același răspuns.\n",
        )],
    ),
    _v2_exp(
        "v3exp016",
        "rewrite compress Romanian-only and family rules",
        "prompt_dedup_cleanup",
        [_edit(
            SYS_REWRITE,
            "IMPORTANT: Definește cuvintele DOAR cu sensul lor românesc, nu englezesc.\nReguli:\n- Răspunzi doar cu definiția finală.\n- Tot textul este exclusiv în română. Nu folosești engleză.\n- Nu incluzi răspunsul și nici derivate evidente ale lui.\n- Sunt interzise forme din aceeași familie lexicală cu răspunsul.\n",
            "IMPORTANT: Rescrii doar cu sens românesc, în română, fără răspuns și fără familie lexicală.\nReguli:\n- Răspunzi doar cu definiția finală.\n",
        )],
    ),
]

_validate_experiment_list(V3_EXPERIMENTS)
assert len(V3_EXPERIMENTS) == 16, len(V3_EXPERIMENTS)

V4_EXPERIMENTS = [
    Experiment(
        "v4exp001",
        "rewrite add positive Romanian-register line",
        [_edit_after(SYS_REWRITE, "- Răspunzi doar cu definiția finală.", "- Formulezi definiția în română firească, de dicționar și rebus.")],
        family="rewrite_rule_readditions",
        priority=_family_priority("rewrite_rule_readditions", "v4"),
        tags=("rewrite_rule_readditions",),
    ),
    Experiment(
        "v4exp002",
        "rewrite add referent-first line instead of answer-ban",
        [_edit_after(SYS_REWRITE, "- Răspunzi doar cu definiția finală.", "- Descrii referentul prin rol, efect, loc, parte sau context distinctiv.")],
        family="rewrite_rule_readditions",
        priority=_family_priority("rewrite_rule_readditions", "v4"),
        tags=("rewrite_rule_readditions",),
    ),
    Experiment(
        "v4exp003",
        "rewrite add positive out-of-family periphrasis line",
        [_edit_after(SYS_REWRITE, "- Răspunzi doar cu definiția finală.", "- Alegi o perifrază din afara familiei lexicale a termenului.")],
        family="rewrite_rule_readditions",
        priority=_family_priority("rewrite_rule_readditions", "v4"),
        tags=("rewrite_rule_readditions",),
    ),
    Experiment(
        "v4exp004",
        "rewrite positive header with sense-first and referent-first rules",
        [_edit(
            SYS_REWRITE,
            "Ești editor de definiții de rebus în limba română.\nIMPORTANT: Rescrii doar cu sens românesc, în română, fără răspuns și fără familie lexicală.\nReguli:\n- Răspunzi doar cu definiția finală.\n",
            "Ești editor de definiții de rebus în limba română.\nIMPORTANT: Livrezi un indiciu românesc natural, orientat strict spre sensul corect.\nReguli:\n- Răspunzi doar cu definiția finală.\n- Descrii referentul prin rol, efect, loc, parte sau context distinctiv.\n- Alegi o perifrază din afara familiei lexicale a termenului.\n",
        )],
        family="rewrite_header_variants",
        priority=_family_priority("rewrite_header_variants", "v4"),
        tags=("rewrite_header_variants",),
    ),
    Experiment(
        "v4exp005",
        "rewrite positive header with Romanian-register and lexical-distance lines",
        [_edit(
            SYS_REWRITE,
            "Ești editor de definiții de rebus în limba română.\nIMPORTANT: Rescrii doar cu sens românesc, în română, fără răspuns și fără familie lexicală.\nReguli:\n- Răspunzi doar cu definiția finală.\n",
            "Ești editor de definiții de rebus în limba română.\nIMPORTANT: Livrezi o definiție românească firească, centrată pe sensul bun.\nReguli:\n- Răspunzi doar cu definiția finală.\n- Formulezi definiția în română firească, de dicționar și rebus.\n- Păstrezi distanță lexicală față de termen prin perifrază.\n",
        )],
        family="rewrite_header_variants",
        priority=_family_priority("rewrite_header_variants", "v4"),
        tags=("rewrite_header_variants",),
    ),
    Experiment(
        "v4exp006",
        "rewrite add explicit redundancy-cut rule",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- Tai orice formulare redundantă înainte de a livra definiția.")],
        family="rewrite_compactness_bias",
        priority=_family_priority("rewrite_compactness_bias", "v4"),
        tags=("rewrite_compactness_bias",),
    ),
    Experiment(
        "v4exp007",
        "rewrite prefer 4-9 words when exactness survives",
        [_edit_before(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- Preferi 4-9 cuvinte dacă sensul rămâne exact.")],
        family="rewrite_compactness_bias",
        priority=_family_priority("rewrite_compactness_bias", "v4"),
        tags=("rewrite_compactness_bias",),
    ),
    Experiment(
        "v4exp008",
        "rewrite tighten hard cap to 12 words",
        [_edit(SYS_REWRITE, REWRITE_MAX_WORDS_MARKER, "- Max 12 cuvinte.")],
        family="rewrite_compactness_bias",
        priority=_family_priority("rewrite_compactness_bias", "v4"),
        tags=("rewrite_compactness_bias",),
    ),
]

_validate_experiment_list(V4_EXPERIMENTS)
assert len(V4_EXPERIMENTS) == 8, len(V4_EXPERIMENTS)

V5_EXPERIMENTS = [
    Experiment(
        "v5exp001",
        "rewrite replace compact ban header with sense-first header only",
        [_edit(
            SYS_REWRITE,
            "IMPORTANT: Rescrii doar cu sens românesc, în română, fără răspuns și fără familie lexicală.",
            "IMPORTANT: Livrezi un indiciu românesc natural, orientat strict spre sensul corect.",
        )],
        family="header_signal_isolation",
        priority=_family_priority("header_signal_isolation", "v5"),
        tags=("header_signal_isolation",),
    ),
    Experiment(
        "v5exp002",
        "rewrite add soft context-distinctive line",
        [_edit_after(SYS_REWRITE, "- Formulezi definiția în română firească, de dicționar și rebus.", "- Descrii sensul prin rol, efect, loc sau context distinctiv.")],
        family="header_signal_isolation",
        priority=_family_priority("header_signal_isolation", "v5"),
        tags=("header_signal_isolation",),
    ),
    Experiment(
        "v5exp003",
        "rewrite add short out-of-family periphrasis line",
        [_edit_after(SYS_REWRITE, "- Formulezi definiția în română firească, de dicționar și rebus.", "- Alegi o perifrază scurtă din afara familiei lexicale a termenului.")],
        family="header_signal_isolation",
        priority=_family_priority("header_signal_isolation", "v5"),
        tags=("header_signal_isolation",),
    ),
    Experiment(
        "v5exp004",
        "rewrite blend sense-first header with soft context line",
        [
            _edit(
                SYS_REWRITE,
                "IMPORTANT: Rescrii doar cu sens românesc, în română, fără răspuns și fără familie lexicală.",
                "IMPORTANT: Livrezi un indiciu românesc natural, orientat strict spre sensul corect.",
            ),
            _edit_after(SYS_REWRITE, "- Formulezi definiția în română firească, de dicționar și rebus.", "- Descrii sensul prin rol, efect, loc sau context distinctiv."),
        ],
        family="header_signal_blends",
        priority=_family_priority("header_signal_blends", "v5"),
        tags=("header_signal_blends",),
    ),
    Experiment(
        "v5exp005",
        "rewrite blend sense-first header with short periphrasis line",
        [
            _edit(
                SYS_REWRITE,
                "IMPORTANT: Rescrii doar cu sens românesc, în română, fără răspuns și fără familie lexicală.",
                "IMPORTANT: Livrezi un indiciu românesc natural, orientat strict spre sensul corect.",
            ),
            _edit_after(SYS_REWRITE, "- Formulezi definiția în română firească, de dicționar și rebus.", "- Alegi o perifrază scurtă din afara familiei lexicale a termenului."),
        ],
        family="header_signal_blends",
        priority=_family_priority("header_signal_blends", "v5"),
        tags=("header_signal_blends",),
    ),
    Experiment(
        "v5exp006",
        "rewrite add explicit flexion-form precision line",
        [_edit_after(SYS_REWRITE, "- Dacă definiția veche sugerează alt gen, alt număr sau altă formă flexionară, corectează forma înainte de stil.", "- Pentru forme flexionare, fixezi persoana, numărul, timpul sau funcția cerută.")],
        family="precision_support",
        priority=_family_priority("precision_support", "v5"),
        tags=("precision_support",),
    ),
    Experiment(
        "v5exp007",
        "rewrite add rare-or-technical specificity line",
        [_edit_after(SYS_REWRITE, "- Fă definiția mai precisă decât cea veche.", "- Când sensul bun este rar sau tehnic, păstrezi detaliul distinctiv și nu-l generalizezi.")],
        family="precision_support",
        priority=_family_priority("precision_support", "v5"),
        tags=("precision_support",),
    ),
    Experiment(
        "v5exp008",
        "rewrite combine flexion precision with rare-sense specificity",
        [
            _edit_after(SYS_REWRITE, "- Dacă definiția veche sugerează alt gen, alt număr sau altă formă flexionară, corectează forma înainte de stil.", "- Pentru forme flexionare, fixezi persoana, numărul, timpul sau funcția cerută."),
            _edit_after(SYS_REWRITE, "- Fă definiția mai precisă decât cea veche.", "- Când sensul bun este rar sau tehnic, păstrezi detaliul distinctiv și nu-l generalizezi."),
        ],
        family="precision_support",
        priority=_family_priority("precision_support", "v5"),
        tags=("precision_support",),
    ),
]

_validate_experiment_list(V5_EXPERIMENTS)
assert len(V5_EXPERIMENTS) == 8, len(V5_EXPERIMENTS)

V6_EXPERIMENTS = [
    Experiment(
        "v6exp001",
        "verify replace translation fallback with Romanian-only line",
        [_edit(
            SYS_VERIFY,
            "- Gândești și răspunzi numai în română.\n- Dacă primul cuvânt care îți vine este în engleză, îl traduci mental și răspunzi în română.\n",
            "- Lucrezi cap-coadă în română.\n",
        )],
        family="verify_romanian_only",
        priority=_family_priority("verify_romanian_only", "v6"),
        tags=("verify_romanian_only",),
    ),
    Experiment(
        "v6exp002",
        "verify compress resolution steps around exact-form elimination",
        [_edit(
            SYS_VERIFY,
            "Proces de rezolvare:\n1. Citește definiția atent.\n2. Gândește la 1-3 cuvinte românești care se potrivesc.\n3. Verifică pentru fiecare: are exact lungimea cerută?\n4. Păstrează doar variantele care respectă lungimea.\n",
            "Proces de rezolvare:\n1. Propui 1-3 variante românești.\n2. Elimini imediat formele cu lungime, gen, număr sau flexiune incompatibilă.\n3. Păstrezi doar varianta care se potrivește exact definiției.\n",
        )],
        family="verify_resolution_compaction",
        priority=_family_priority("verify_resolution_compaction", "v6"),
        tags=("verify_resolution_compaction",),
    ),
    Experiment(
        "v6exp003",
        "verify add targeted fragile-word examples ETAN and FERMENT",
        [_edit_before(
            SYS_VERIFY,
            "Definiție: Se trage un semnal de pericol\nRăspuns: ALARMA\n",
            _block(
                _verify_example("Hidrocarbură saturată cu doi atomi de carbon", "ETAN"),
                _verify_example("Agent al fermentării", "FERMENT"),
            ),
        )],
        family="verify_targeted_examples",
        priority=_family_priority("verify_targeted_examples", "v6"),
        tags=("verify_targeted_examples",),
    ),
    Experiment(
        "v6exp004",
        "verify user makes exact-length and exact-form filtering explicit",
        [_edit(
            USR_VERIFY,
            "Lungime răspuns: EXACT {answer_length} litere\nNumăr variante: maximum {max_guesses}\nExcluzi orice variantă care nu are exact {answer_length} litere.\nScrie fiecare variantă pe rând separat, fără explicații.\n",
            "Lungime răspuns: EXACT {answer_length} litere\nNumăr variante: maximum {max_guesses}\nVerifici mai întâi lungimea exactă, apoi sensul.\nElimini din start orice formă cu altă lungime, alt gen, alt număr sau altă flexiune.\nScrie fiecare variantă pe rând separat, fără explicații.\n",
        )],
        family="verify_user_exactness",
        priority=_family_priority("verify_user_exactness", "v6"),
        tags=("verify_user_exactness",),
    ),
    Experiment(
        "v6exp005",
        "rate tighten guessability around exact answer at this length",
        [_edit(
            SYS_RATE,
            "- guessability_score: dacă un rezolvitor ar citi definiția și ar avea {answer_length} căsuțe de completat, ar scrie exact cuvântul-răspuns? 9-10 = un singur cuvânt posibil la această lungime, 7-8 = probabil corect, 5-6 = mai multe opțiuni, 1-3 = ar scrie altceva cu certitudine\n",
            "- guessability_score: dacă un rezolvitor ar vedea definiția și exact {answer_length} căsuțe, ar scrie chiar răspunsul cerut? 9-10 = răspunsul exact iese clar la această lungime, 7-8 = probabil corect, 5-6 = rămân mai multe opțiuni, 1-3 = ar scrie alt cuvânt cu certitudine\n",
        )],
        family="rate_exact_answer_calibration",
        priority=_family_priority("rate_exact_answer_calibration", "v6"),
        tags=("rate_exact_answer_calibration",),
    ),
    Experiment(
        "v6exp006",
        "rate add rare-sense guessability rule and technical example",
        [
            _edit_before(
                SYS_RATE,
                RATE_FEEDBACK_MARKER,
                "- pentru un sens rar sau tehnic, guessability_score rămâne mare doar dacă definiția fixează domeniul, funcția sau trăsătura distinctivă",
            ),
            _edit_before(
                SYS_RATE,
                RATE_JSON_MARKER,
                'Exemplu sens tehnic:\n{"semantic_score": 9, "guessability_score": 8, "creativity_score": 4, "feedback": "Detaliul de domeniu fixează sensul tehnic și duce la răspunsul exact."}\n',
            ),
        ],
        family="rate_rare_sense_calibration",
        priority=_family_priority("rate_rare_sense_calibration", "v6"),
        tags=("rate_rare_sense_calibration",),
    ),
    Experiment(
        "v6exp007",
        "definition replace negative English framing with positive Romanian-sense framing",
        [
            _edit(
                SYS_DEFINITION,
                "IMPORTANT: Toate cuvintele sunt exclusiv în limba ROMÂNĂ. Chiar dacă arată ca un cuvânt englezesc, definește-l DOAR cu sensul românesc.\n",
                "IMPORTANT: Lucrezi numai cu sensuri românești reale și formulezi totul în română firească.\n",
            ),
            _edit(
                SYS_DEFINITION,
                "- Tot textul este exclusiv în română. Nu folosești engleză.\n",
                "- Formulezi definiția în română firească, de dicționar și rebus.\n",
            ),
            _edit(
                SYS_DEFINITION,
                "- Dacă sensul îți vine doar în engleză sau altă limbă, răspunzi [NECLAR].\n",
                "- Dacă nu găsești un sens românesc real, răspunzi [NECLAR].\n",
            ),
        ],
        family="definition_positive_romanian_sense",
        priority=_family_priority("definition_positive_romanian_sense", "v6"),
        tags=("definition_positive_romanian_sense",),
    ),
    Experiment(
        "v6exp008",
        "definition add broader-neighbor counterexamples for ATU and FERMENT",
        [_edit_before(
            SYS_DEFINITION,
            "ARDE -> Ceva care se întâmplă [GREȘIT - prea vag]\n",
            _block(
                "ATU -> Avantaj la joc [GREȘIT - prea larg; nu fixează cartea decisivă]",
                "FERMENT -> Substanță care schimbă ceva [GREȘIT - categorie prea largă]",
            ) + "\n",
        )],
        family="definition_vague_neighbor_counterexamples",
        priority=_family_priority("definition_vague_neighbor_counterexamples", "v6"),
        tags=("definition_vague_neighbor_counterexamples",),
    ),
]

_validate_experiment_list(V6_EXPERIMENTS)
assert len(V6_EXPERIMENTS) == 8, len(V6_EXPERIMENTS)

EXPERIMENT_SETS = {
    "v1": V1_EXPERIMENTS,
    "v2": V2_EXPERIMENTS,
    "v3": V3_EXPERIMENTS,
    "v4": V4_EXPERIMENTS,
    "v5": V5_EXPERIMENTS,
    "v6": V6_EXPERIMENTS,
}
EXPERIMENTS = V1_EXPERIMENTS

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


def best_result_summary_path(backup_dir: Path) -> Path:
    safe_name = backup_dir.name or "default"
    return BEST_RESULT_STATE_ROOT / safe_name / BEST_ASSESSMENT_JSON


def load_best_result_summary(backup_dir: Path) -> dict | None:
    new_path = best_result_summary_path(backup_dir)
    if new_path.exists():
        return json.loads(new_path.read_text(encoding="utf-8"))
    legacy_path = backup_dir / BEST_ASSESSMENT_JSON
    if legacy_path.exists():
        return json.loads(legacy_path.read_text(encoding="utf-8"))
    return None


def save_best_result_summary(backup_dir: Path, result: dict) -> None:
    path = best_result_summary_path(backup_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _stddev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _run_metric(run: dict, key: str) -> float:
    return float(run.get(key, 0.0))


def _aggregate_candidates(runs: list[dict]) -> list[dict]:
    per_word: dict[str, dict[str, object]] = {}
    run_count = len(runs)
    for run in runs:
        for row in run.get("candidates", []):
            word = row.get("word")
            if not word:
                continue
            entry = per_word.setdefault(
                word,
                {
                    "word": word,
                    "tier": row.get("tier", ""),
                    "verified_runs": 0,
                    "semantic": [],
                    "rebus": [],
                },
            )
            if row.get("verified"):
                entry["verified_runs"] = int(entry["verified_runs"]) + 1
            if "semantic" in row:
                entry["semantic"].append(float(row.get("semantic", 0.0)))
            if "rebus" in row:
                entry["rebus"].append(float(row.get("rebus", 0.0)))

    candidates: list[dict] = []
    for word in sorted(per_word):
        entry = per_word[word]
        verified_runs = int(entry["verified_runs"])
        verified_rate = verified_runs / run_count if run_count else 0.0
        candidates.append(
            {
                "word": word,
                "tier": entry["tier"],
                "verified": verified_runs * 2 >= run_count if run_count else False,
                "verified_runs": verified_runs,
                "verified_rate": round(verified_rate, 3),
                "semantic": round(_mean(entry["semantic"]), 1),
                "rebus": round(_mean(entry["rebus"]), 1),
            }
        )
    return candidates


def aggregate_result_runs(
    runs: list[dict],
    *,
    label: str,
    description_prefix: str,
    raw_results: list[dict[str, object]],
) -> dict:
    if not runs:
        return {
            "label": label,
            "description_prefix": description_prefix,
            "run_count": 0,
            "composite": 0.0,
            "pass_rate": 0.0,
            "tier_balanced_pass_rate": 0.0,
            "avg_semantic": 0.0,
            "avg_rebus": 0.0,
            "metrics": {},
            "tiers": {},
            "protected_control_summary": {},
            "candidates": [],
            "raw_results": raw_results,
        }

    metrics = {
        key: {
            "mean": round(_mean([_run_metric(run, key) for run in runs]), 3 if "rate" in key else 1),
            "stddev": round(_stddev([_run_metric(run, key) for run in runs]), 3 if "rate" in key else 1),
        }
        for key in ("composite", "pass_rate", "tier_balanced_pass_rate", "avg_semantic", "avg_rebus")
    }

    tier_names = sorted(
        {
            tier_name
            for run in runs
            for tier_name in run.get("tiers", {})
        }
    )
    tiers = {}
    for tier_name in tier_names:
        pass_values = [float(run.get("tiers", {}).get(tier_name, {}).get("pass_rate", 0.0)) for run in runs]
        semantic_values = [float(run.get("tiers", {}).get(tier_name, {}).get("avg_semantic", 0.0)) for run in runs]
        rebus_values = [float(run.get("tiers", {}).get(tier_name, {}).get("avg_rebus", 0.0)) for run in runs]
        count = int(runs[0].get("tiers", {}).get(tier_name, {}).get("count", 0))
        tiers[tier_name] = {
            "pass_rate": round(_mean(pass_values), 3),
            "pass_rate_stddev": round(_stddev(pass_values), 3),
            "avg_semantic": round(_mean(semantic_values), 1),
            "avg_semantic_stddev": round(_stddev(semantic_values), 1),
            "avg_rebus": round(_mean(rebus_values), 1),
            "avg_rebus_stddev": round(_stddev(rebus_values), 1),
            "count": count,
        }

    protected = {}
    for tier_name in {"high", "easy", "control"}:
        if tier_name not in tiers:
            continue
        protected[tier_name] = {
            "pass_rate": tiers[tier_name]["pass_rate"],
            "pass_rate_stddev": tiers[tier_name]["pass_rate_stddev"],
            "avg_semantic": tiers[tier_name]["avg_semantic"],
            "avg_rebus": tiers[tier_name]["avg_rebus"],
            "count": tiers[tier_name]["count"],
        }

    return {
        "label": label,
        "description_prefix": description_prefix,
        "run_count": len(runs),
        "composite": round(metrics["composite"]["mean"], 1),
        "composite_stddev": round(metrics["composite"]["stddev"], 1),
        "pass_rate": round(metrics["pass_rate"]["mean"], 3),
        "pass_rate_stddev": round(metrics["pass_rate"]["stddev"], 3),
        "tier_balanced_pass_rate": round(metrics["tier_balanced_pass_rate"]["mean"], 3),
        "tier_balanced_pass_rate_stddev": round(metrics["tier_balanced_pass_rate"]["stddev"], 3),
        "avg_semantic": round(metrics["avg_semantic"]["mean"], 1),
        "avg_semantic_stddev": round(metrics["avg_semantic"]["stddev"], 1),
        "avg_rebus": round(metrics["avg_rebus"]["mean"], 1),
        "avg_rebus_stddev": round(metrics["avg_rebus"]["stddev"], 1),
        "metrics": metrics,
        "tiers": tiers,
        "protected_control_summary": protected,
        "candidates": _aggregate_candidates(runs),
        "raw_results": raw_results,
    }


def comparison_summary_path(assessment_logs_dir: Path, exp_name: str) -> Path:
    return assessment_logs_dir / f"{exp_name}.comparison.json"


def batch_word_signal_details(current: dict, incumbent: dict | None) -> dict[str, dict[str, object]]:
    details: dict[str, dict[str, object]] = {}
    if not incumbent:
        return details
    current_rows = candidate_map(current)
    incumbent_rows = candidate_map(incumbent)
    run_count = int(current.get("run_count") or incumbent.get("run_count") or 1)
    for word, old_row in incumbent_rows.items():
        new_row = current_rows.get(word)
        if not new_row:
            continue
        old_runs = int(old_row.get("verified_runs", int(bool(old_row.get("verified")))))
        new_runs = int(new_row.get("verified_runs", int(bool(new_row.get("verified")))))
        if old_runs == new_runs:
            continue
        details[word] = {
            "tier": str(new_row.get("tier") or old_row.get("tier") or ""),
            "incumbent_verified_runs": old_runs,
            "candidate_verified_runs": new_runs,
            "run_count": run_count,
            "delta_verified_runs": new_runs - old_runs,
        }
    return details


@dataclass(frozen=True)
class WordSignal:
    gained_low_medium: tuple[str, ...] = ()
    lost_low_medium: tuple[str, ...] = ()
    gained_high: tuple[str, ...] = ()
    lost_high: tuple[str, ...] = ()
    lost_protected_controls: tuple[str, ...] = ()
    lost_primary_fragile: tuple[str, ...] = ()
    lost_secondary_fragile: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "gained_low_medium": list(self.gained_low_medium),
            "lost_low_medium": list(self.lost_low_medium),
            "gained_high": list(self.gained_high),
            "lost_high": list(self.lost_high),
            "lost_protected_controls": list(self.lost_protected_controls),
            "lost_primary_fragile": list(self.lost_primary_fragile),
            "lost_secondary_fragile": list(self.lost_secondary_fragile),
        }


@dataclass(frozen=True)
class ClassificationDecision:
    status: str
    delta: float
    protected_regression: bool
    pass_regression: bool
    tier_balanced_regression: bool
    uncertain_reason: str | None
    research_signal: bool
    signal: WordSignal


def candidate_map(result: dict) -> dict[str, dict]:
    return {
        row.get("word"): row
        for row in result.get("candidates", [])
        if row.get("word")
    }


def summarize_word_signal(current: dict, incumbent: dict | None) -> WordSignal:
    if not incumbent:
        return WordSignal()

    current_rows = candidate_map(current)
    incumbent_rows = candidate_map(incumbent)
    gained_low_medium: list[str] = []
    lost_low_medium: list[str] = []
    gained_high: list[str] = []
    lost_high: list[str] = []
    lost_protected_controls: list[str] = []
    lost_primary_fragile: list[str] = []
    lost_secondary_fragile: list[str] = []

    for word, old_row in incumbent_rows.items():
        new_row = current_rows.get(word)
        if not new_row:
            continue
        old_verified = bool(old_row.get("verified"))
        new_verified = bool(new_row.get("verified"))
        tier = str(new_row.get("tier") or old_row.get("tier") or "")

        if not old_verified and new_verified:
            if tier in {"low", "medium"}:
                gained_low_medium.append(word)
            elif tier == "high":
                gained_high.append(word)
        elif old_verified and not new_verified:
            if tier in {"low", "medium"}:
                lost_low_medium.append(word)
            elif tier == "high":
                lost_high.append(word)
            if word in CONTROL_WORD_WATCH:
                lost_protected_controls.append(word)
            if word in PRIMARY_FRAGILE_WORD_WATCH:
                lost_primary_fragile.append(word)
            elif word in SECONDARY_FRAGILE_WORD_WATCH:
                lost_secondary_fragile.append(word)

    return WordSignal(
        gained_low_medium=tuple(sorted(gained_low_medium)),
        lost_low_medium=tuple(sorted(lost_low_medium)),
        gained_high=tuple(sorted(gained_high)),
        lost_high=tuple(sorted(lost_high)),
        lost_protected_controls=tuple(sorted(lost_protected_controls)),
        lost_primary_fragile=tuple(sorted(lost_primary_fragile)),
        lost_secondary_fragile=tuple(sorted(lost_secondary_fragile)),
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
) -> ClassificationDecision:
    composite = float(current["composite"])
    delta = composite - best_composite
    has_regression = protected_regression(current, incumbent)
    incumbent_pass = float(incumbent.get("pass_rate", 0.0)) if incumbent else 0.0
    current_pass = float(current["pass_rate"])
    incumbent_tier_balanced = float(incumbent.get("tier_balanced_pass_rate", 0.0)) if incumbent else 0.0
    current_tier_balanced = float(current.get("tier_balanced_pass_rate", 0.0))
    pass_regression = bool(incumbent and current_pass < incumbent_pass)
    tier_balanced_regression = bool(incumbent and current_tier_balanced < incumbent_tier_balanced)
    signal = summarize_word_signal(current, incumbent)

    improved_core_metric = (
        current_pass > incumbent_pass
        or current_tier_balanced > incumbent_tier_balanced
        or (
            incumbent is not None
            and current_pass == incumbent_pass
            and current_tier_balanced == incumbent_tier_balanced
            and composite > best_composite
        )
    )
    if improved_core_metric and not has_regression and not pass_regression and not tier_balanced_regression:
        if signal.lost_primary_fragile:
            return ClassificationDecision(
                "discard",
                delta,
                has_regression,
                pass_regression,
                tier_balanced_regression,
                None,
                False,
                signal,
            )
        return ClassificationDecision(
            "keep",
            delta,
            has_regression,
            pass_regression,
            tier_balanced_regression,
            None,
            False,
            signal,
        )

    near_miss = (
        incumbent is not None
        and delta >= -UNCERTAINTY_DELTA
        and (current_pass - incumbent_pass) >= NEAR_MISS_PASS_DELTA
        and not has_regression
        and not tier_balanced_regression
        and not signal.lost_protected_controls
        and not signal.lost_high
        and not signal.lost_primary_fragile
    )
    research_signal = (
        incumbent is not None
        and len(signal.gained_low_medium) >= RESEARCH_SIGNAL_MIN_GAINED_WORDS
        and not has_regression
        and not tier_balanced_regression
        and not signal.lost_protected_controls
        and not signal.lost_high
        and not signal.lost_primary_fragile
        and len(signal.lost_low_medium) <= len(signal.gained_low_medium)
    )
    if near_miss:
        return ClassificationDecision(
            "uncertain",
            delta,
            has_regression,
            pass_regression,
            tier_balanced_regression,
            "near_miss",
            False,
            signal,
        )
    if research_signal:
        return ClassificationDecision(
            "uncertain",
            delta,
            has_regression,
            pass_regression,
            tier_balanced_regression,
            "research_signal",
            True,
            signal,
        )
    return ClassificationDecision(
        "discard",
        delta,
        has_regression,
        pass_regression,
        tier_balanced_regression,
        None,
        False,
        signal,
    )


def apply_experiment(exp: Experiment) -> bool:
    """Apply an experiment's edits atomically. Returns True if applied."""
    if not exp.edits:
        return True
    new_contents: dict[Path, str] = {}

    for edit in exp.edits:
        filepath = PROMPTS_DIR / edit.file
        if filepath not in new_contents:
            if not filepath.exists():
                log(f"  [SKIP] File not found: {filepath}")
                return False
            new_contents[filepath] = filepath.read_text(encoding="utf-8")

        content = new_contents[filepath]
        if edit.find in edit.replace and edit.replace in content:
            log(f"  [SKIP] Replacement text already present in {edit.file}")
            return False
        if edit.find not in content and edit.replace and edit.replace in content:
            log(f"  [SKIP] Replacement text already present in {edit.file}")
            return False
        if edit.find not in content:
            log(f"  [SKIP] Find text not found in {edit.file}")
            return False

        updated = content.replace(edit.find, edit.replace, 1)
        if updated == content:
            log(f"  [SKIP] No change after replacement in {edit.file}")
            return False
        new_contents[filepath] = updated

    for filepath, updated in new_contents.items():
        filepath.write_text(updated, encoding="utf-8")
    return True


def build_assessment_description(prefix: str, exp: Experiment) -> str:
    """Human-readable experiment label stored in TSV/logs."""
    base = f"{prefix}{exp.name}" if prefix else exp.name
    return f"{base} | {exp.desc} | {exp.file}"


def experiment_index(exp_name: str) -> int:
    if exp_name.startswith("v2exp"):
        return int(exp_name[5:])
    if exp_name.startswith("v3exp"):
        return int(exp_name[5:])
    if exp_name.startswith("v4exp"):
        return int(exp_name[5:])
    if exp_name.startswith("v5exp"):
        return int(exp_name[5:])
    if exp_name.startswith("v6exp"):
        return int(exp_name[5:])
    if not exp_name.startswith("exp"):
        raise ValueError(f"Unsupported experiment name: {exp_name}")
    return int(exp_name[3:])


def block_name_for_experiment(exp_name: str) -> str:
    if exp_name.startswith("v2exp"):
        return get_experiment(exp_name, "v2").family
    if exp_name.startswith("v3exp"):
        return get_experiment(exp_name, "v3").family
    if exp_name.startswith("v4exp"):
        return get_experiment(exp_name, "v4").family
    if exp_name.startswith("v5exp"):
        return get_experiment(exp_name, "v5").family
    if exp_name.startswith("v6exp"):
        return get_experiment(exp_name, "v6").family
    index = experiment_index(exp_name)
    for block_name, (start, end) in EXPERIMENT_BLOCK_RANGES.items():
        if start <= index <= end:
            return block_name
    raise ValueError(f"Experiment outside declared block ranges: {exp_name}")


def experiments_for_set(experiment_set: str) -> list[Experiment]:
    return EXPERIMENT_SETS[experiment_set]


def get_experiment(name: str, experiment_set: str | None = None) -> Experiment:
    if experiment_set is None:
        if name.startswith("v2exp"):
            experiment_set = "v2"
        elif name.startswith("v3exp"):
            experiment_set = "v3"
        elif name.startswith("v4exp"):
            experiment_set = "v4"
        elif name.startswith("v5exp"):
            experiment_set = "v5"
        elif name.startswith("v6exp"):
            experiment_set = "v6"
        else:
            experiment_set = "v1"
    for exp in experiments_for_set(experiment_set):
        if exp.name == name:
            return exp
    raise KeyError(name)


def experiments_for_family(family: str, experiment_set: str = "v1") -> list[Experiment]:
    return [exp for exp in experiments_for_set(experiment_set) if exp.family == family]


def family_stop_consecutive_non_keeps(experiment_set: str) -> int:
    if experiment_set == "v2":
        return V2_FAMILY_STOP_CONSECUTIVE_NON_KEEPS
    if experiment_set == "v3":
        return V3_FAMILY_STOP_CONSECUTIVE_NON_KEEPS
    if experiment_set == "v4":
        return V4_FAMILY_STOP_CONSECUTIVE_NON_KEEPS
    if experiment_set == "v5":
        return V5_FAMILY_STOP_CONSECUTIVE_NON_KEEPS
    if experiment_set == "v6":
        return V6_FAMILY_STOP_CONSECUTIVE_NON_KEEPS
    return FAMILY_STOP_CONSECUTIVE_NON_KEEPS


def family_stop_total_non_keeps(experiment_set: str) -> int:
    if experiment_set == "v2":
        return V2_FAMILY_STOP_TOTAL_NON_KEEPS
    if experiment_set == "v3":
        return V3_FAMILY_STOP_TOTAL_NON_KEEPS
    if experiment_set == "v4":
        return V4_FAMILY_STOP_TOTAL_NON_KEEPS
    if experiment_set == "v5":
        return V5_FAMILY_STOP_TOTAL_NON_KEEPS
    if experiment_set == "v6":
        return V6_FAMILY_STOP_TOTAL_NON_KEEPS
    return FAMILY_STOP_TOTAL_NON_KEEPS


def campaign_stop_stale_families(experiment_set: str) -> int:
    if experiment_set == "v2":
        return V2_CAMPAIGN_STOP_STALE_FAMILIES
    if experiment_set == "v3":
        return V3_CAMPAIGN_STOP_STALE_FAMILIES
    if experiment_set == "v4":
        return V4_CAMPAIGN_STOP_STALE_FAMILIES
    if experiment_set == "v5":
        return V5_CAMPAIGN_STOP_STALE_FAMILIES
    if experiment_set == "v6":
        return V6_CAMPAIGN_STOP_STALE_FAMILIES
    return CAMPAIGN_STOP_STALE_FAMILIES


def family_stop_repeat_primary(experiment_set: str) -> int:
    if experiment_set == "v2":
        return V2_FAMILY_STOP_REPEAT_PRIMARY
    if experiment_set == "v3":
        return V3_FAMILY_STOP_REPEAT_PRIMARY
    if experiment_set == "v4":
        return V4_FAMILY_STOP_REPEAT_PRIMARY
    if experiment_set == "v5":
        return V5_FAMILY_STOP_REPEAT_PRIMARY
    if experiment_set == "v6":
        return V6_FAMILY_STOP_REPEAT_PRIMARY
    return V2_FAMILY_STOP_REPEAT_PRIMARY


def summarize_cleanup_block(log: list[dict]) -> dict[str, int | bool]:
    summary = {"keep": 0, "uncertain": 0, "discard": 0}
    for entry in log:
        if block_name_for_experiment(entry["name"]) != "cleanup":
            continue
        status = entry.get("status")
        if status in summary:
            summary[status] += 1
    summary["stop_cleanup"] = (summary["uncertain"] + summary["discard"]) > summary["keep"]
    return summary


def summarize_family_outcomes(log: list[dict], family: str, experiment_set: str = "v1") -> dict[str, object]:
    entries = [entry for entry in log if entry.get("family") == family]
    summary = {
        "attempts": len(entries),
        "keeps": 0,
        "uncertains": 0,
        "discards": 0,
        "consecutive_non_keeps": 0,
        "total_non_keeps_since_last_keep": 0,
        "best_delta": 0.0,
        "repeated_collateral_losers": [],
        "stale": False,
        "stale_reason": None,
        "has_signal": False,
    }
    collateral_counter: Counter[str] = Counter()
    primary_fragile_counter: Counter[str] = Counter()

    for entry in entries:
        status = entry.get("status")
        if status == "keep":
            summary["keeps"] += 1
            summary["consecutive_non_keeps"] = 0
            summary["total_non_keeps_since_last_keep"] = 0
            summary["best_delta"] = max(summary["best_delta"], float(entry.get("delta", 0.0)))
            summary["has_signal"] = True
            continue

        if status == "uncertain":
            summary["uncertains"] += 1
            if entry.get("research_signal"):
                summary["has_signal"] = True
        elif status == "discard":
            summary["discards"] += 1

        summary["consecutive_non_keeps"] += 1
        summary["total_non_keeps_since_last_keep"] += 1
        for word in entry.get("word_signal", {}).get("lost_low_medium", []):
            collateral_counter[word] += 1
        for word in entry.get("word_signal", {}).get("lost_high", []):
            collateral_counter[word] += 1
        for word in entry.get("word_signal", {}).get("lost_primary_fragile", []):
            primary_fragile_counter[word] += 1

    repeated = sorted(word for word, count in collateral_counter.items() if count >= FAMILY_STOP_REPEAT_COLLATERAL)
    summary["repeated_collateral_losers"] = repeated
    repeated_primary = sorted(word for word, count in primary_fragile_counter.items() if count >= family_stop_repeat_primary(experiment_set))
    if summary["consecutive_non_keeps"] >= family_stop_consecutive_non_keeps(experiment_set):
        summary["stale"] = True
        summary["stale_reason"] = "consecutive_non_keeps"
    elif summary["total_non_keeps_since_last_keep"] >= family_stop_total_non_keeps(experiment_set):
        summary["stale"] = True
        summary["stale_reason"] = "total_non_keeps"
    elif experiment_set in {"v2", "v3"} and repeated_primary:
        summary["stale"] = True
        summary["stale_reason"] = "repeated_primary_fragile_losers"
    elif repeated:
        summary["stale"] = True
        summary["stale_reason"] = "repeated_collateral_losers"
    return summary


def classify_prompt_direction(log: list[dict]) -> str:
    scores = {"verify": 0.0, "rewrite": 0.0, "rate": 0.0}
    informative_keeps = {"verify": 0, "rewrite": 0, "rate": 0}

    for entry in log:
        block_name = block_name_for_experiment(entry["name"])
        family = TARGET_DIRECTION_BLOCKS.get(block_name)
        if not family or entry.get("status") != "keep":
            continue
        scores[family] += max(float(entry.get("delta", 0.0)), 0.0)
        informative_keeps[family] += 1

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_family, top_score = ranked[0]
    second_score = ranked[1][1]
    if informative_keeps[top_family] == 0 or top_score <= 0.0:
        return "noisy / not yet informative"
    if top_score - second_score <= UNCERTAINTY_DELTA:
        return "noisy / not yet informative"
    return f"{top_family}-led"


def recommend_next_presets(log: list[dict]) -> list[str]:
    direction = classify_prompt_direction(log)
    if direction in DIRECTION_FOLLOWUP_PRESETS:
        return list(DIRECTION_FOLLOWUP_PRESETS[direction])
    return list(FOLLOWUP_PRIORITY[:3])


def load_assessment_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_control_baseline(path: Path | None) -> dict[str, bool] | None:
    if path is None:
        return None
    return {
        word: status["verified"]
        for word, status in summarize_control_watch(load_assessment_payload(path))["words"].items()
    }


def summarize_control_watch(
    assessment_payload: dict,
    baseline_status: dict[str, bool] | None = None,
) -> dict[str, object]:
    candidate_status = {
        row.get("word"): bool(row.get("verified"))
        for row in assessment_payload.get("candidates", [])
        if row.get("word")
    }
    words = {}
    repeat_failures = []
    for word in CONTROL_WORD_WATCH:
        verified = bool(candidate_status.get(word, False))
        repeated_fail = baseline_status is not None and baseline_status.get(word) is False and not verified
        words[word] = {
            "verified": verified,
            "repeated_fail": repeated_fail,
        }
        if repeated_fail:
            repeat_failures.append(word)
    return {
        "words": words,
        CONTROL_WORD_REPEAT_FAIL_ACTION: repeat_failures,
    }


def summarize_log_control_watch(
    log: list[dict],
    baseline_status: dict[str, bool] | None = None,
) -> tuple[dict[str, object] | None, list[str]]:
    latest_summary = None
    repeated_failures = set()

    for entry in log:
        summary = entry.get("control_watch")
        if summary is None:
            assessment_json = entry.get("assessment_json")
            if not assessment_json:
                continue
            assessment_path = Path(assessment_json)
            if not assessment_path.exists():
                continue
            summary = summarize_control_watch(load_assessment_payload(assessment_path), baseline_status)
        latest_summary = summary
        repeated_failures.update(summary.get(CONTROL_WORD_REPEAT_FAIL_ACTION, []))

    return latest_summary, sorted(repeated_failures)


def print_control_watch_summary(summary: dict[str, object]) -> None:
    words = summary["words"]
    status_text = ", ".join(
        f"{word}={'pass' if words[word]['verified'] else 'fail'}"
        for word in CONTROL_WORD_WATCH
    )
    log(f"  control watch: {status_text}")
    repeated = summary.get(CONTROL_WORD_REPEAT_FAIL_ACTION, [])
    if repeated:
        log(f"  {CONTROL_WORD_REPEAT_FAIL_ACTION}: {', '.join(repeated)}")


def print_log_summary(
    entries: list[dict],
    baseline_status: dict[str, bool] | None = None,
) -> None:
    cleanup = summarize_cleanup_block(entries)
    direction = classify_prompt_direction(entries)
    latest_control, repeated_failures = summarize_log_control_watch(entries, baseline_status)
    log("Pilot summary:")
    log(
        "  cleanup:"
        f" keep={cleanup['keep']}"
        f" uncertain={cleanup['uncertain']}"
        f" discard={cleanup['discard']}"
        f" stop_cleanup={'yes' if cleanup['stop_cleanup'] else 'no'}"
    )
    log(f"  direction: {direction}")
    log(f"  next presets: {', '.join(recommend_next_presets(entries))}")
    if latest_control is not None:
        print_control_watch_summary(latest_control)
    if repeated_failures:
        log(f"  action: {CONTROL_WORD_REPEAT_FAIL_ACTION} {', '.join(repeated_failures)}")


def resolve_experiment_window(
    *,
    start_from: int | None,
    end_at: int | None,
    preset: str,
    experiment_set: str = "v1",
) -> tuple[int, int]:
    preset_map = EXPERIMENT_PRESETS_BY_SET[experiment_set]
    preset_start, preset_end = preset_map[preset]
    start = preset_start if start_from is None else max(start_from, preset_start)
    end = preset_end if end_at is None else min(end_at, preset_end)
    experiments = experiments_for_set(experiment_set)
    if start < 1 or end > len(experiments):
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
        log(f"  [git] No existing paths to stage for: {message}")
        return False

    add_result = subprocess.run(
        ["git", "add", *stage_targets],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if add_result.returncode != 0:
        log(f"  [git] add failed for '{message}': {add_result.stderr.strip()}")
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
            log(f"  [git] Nothing to commit for: {message}")
            return True
        log(f"  [git] commit failed for '{message}': {combined_output.strip()}")
        return False

    log(f"  [git] committed: {message}")
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
        log(f"  [git] push failed for '{message}': {push_output.strip()}")
        return False

    log(f"  [git] pushed: {remote}/{branch}")
    return True


def run_assessment(
    description: str,
    assessment_log_path: Path | None = None,
    assessment_json_path: Path | None = None,
    stream_output: bool = False,
    assessment_overrides: dict[str, float | int | str] | None = None,
) -> dict:
    """Run the multi-model assessment and return parsed results."""
    cmd = [
        sys.executable, "-u", "-m", "generator.assessment.run_assessment",
        "--description", description,
        "--no-append-tsv",
    ]
    if assessment_json_path is not None:
        cmd.extend(["--json-out", str(assessment_json_path)])
    for key, value in (assessment_overrides or {}).items():
        flag = f"--{str(key).replace('_', '-')}"
        cmd.extend([flag, str(value)])
    log(f"  Running assessment: {description}")
    if assessment_log_path is not None:
        assessment_log_path.parent.mkdir(parents=True, exist_ok=True)
        log(f"  Assessment log: {assessment_log_path}")
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
                    sys.stdout.write(line)
                    sys.stdout.flush()
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
    log(f"  Assessment completed in {elapsed:.0f}s")

    if result != 0:
        log(f"  [ERROR] Assessment failed with exit code {result}")
        return {"composite": 0.0, "pass_rate": 0.0, "avg_semantic": 0.0, "avg_rebus": 0.0, "error": True}
    if assessment_json_path is None or not assessment_json_path.exists():
        return {"composite": 0.0, "pass_rate": 0.0, "avg_semantic": 0.0, "avg_rebus": 0.0, "error": True}
    payload = json.loads(assessment_json_path.read_text(encoding="utf-8"))
    payload["error"] = False
    return payload


def run_replicated_assessment_series(
    *,
    description: str,
    label: str,
    run_count: int,
    assessment_logs_dir: Path,
    file_stem: str,
    stream_output: bool,
    assessment_overrides: dict[str, float | int | str] | None = None,
) -> dict:
    runs: list[dict] = []
    raw_results: list[dict[str, object]] = []
    for run_index in range(1, run_count + 1):
        run_description = f"{description} [{label} run {run_index}/{run_count}]"
        run_log_path = assessment_logs_dir / f"{file_stem}.{label}.run{run_index}.log"
        run_json_path = assessment_logs_dir / f"{file_stem}.{label}.run{run_index}.json"
        payload = run_assessment(
            run_description,
            assessment_log_path=run_log_path,
            assessment_json_path=run_json_path,
            stream_output=stream_output,
            assessment_overrides=assessment_overrides,
        )
        if payload.get("error"):
            return payload
        runs.append(payload)
        raw_results.append(
            {
                "description": run_description,
                "log_path": str(run_log_path),
                "json_path": str(run_json_path),
                "composite": float(payload["composite"]),
                "pass_rate": float(payload["pass_rate"]),
                "tier_balanced_pass_rate": float(payload.get("tier_balanced_pass_rate", 0.0)),
                "avg_semantic": float(payload["avg_semantic"]),
                "avg_rebus": float(payload["avg_rebus"]),
            }
        )
    return aggregate_result_runs(
        runs,
        label=label,
        description_prefix=description,
        raw_results=raw_results,
    )


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
    parser.add_argument("--experiment-set", choices=sorted(EXPERIMENT_SETS), default="v1",
                        help="Experiment manifest namespace to run")
    parser.add_argument("--summarize-log", type=Path,
                        help="Read an experiment log JSON and print next-step guidance")
    parser.add_argument("--control-baseline-json", type=Path,
                        help="Assessment JSON used as the baseline for watched control words")
    parser.add_argument("--preset", default="full",
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
    parser.add_argument("--comparison-runs", type=int, default=EXPERIMENT_COMPARISON_RUNS,
                        help="Replicated assessment runs for incumbent and candidate comparison")
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
        preset_map = EXPERIMENT_PRESETS_BY_SET[args.experiment_set]
        if args.preset not in preset_map:
            raise ValueError(f"Unknown preset for {args.experiment_set}: {args.preset}")
        control_baseline_status = load_control_baseline(args.control_baseline_json)
        if args.summarize_log is not None:
            print_log_summary(load_log(args.summarize_log), control_baseline_status)
            return
        selected_experiments = experiments_for_set(args.experiment_set)
        start_from, end_at = resolve_experiment_window(
            start_from=args.start_from,
            end_at=args.end_at,
            preset=args.preset,
            experiment_set=args.experiment_set,
        )

        if args.dry_run:
            for i, exp in enumerate(selected_experiments, 1):
                if i < start_from or i > end_at:
                    continue
                log(f"{i:3d}. [{exp.name}] {exp.desc}")
                log(f"     File: {exp.file}")
            log(f"\nSelection: experiments {start_from}-{end_at}")
            log(f"Selected: {end_at - start_from + 1} / {len(selected_experiments)} experiments")
            return

        if args.assessment_logs_dir is None:
            args.assessment_logs_dir = args.log_path.parent / f"{args.log_path.stem}_logs"
        if args.git_live_push:
            args.git_live_commit = True
        if (args.git_live_commit or args.git_live_push) and not args.git_live_branch:
            args.git_live_branch = git_current_branch()

        if args.reset_log and args.log_path.exists():
            args.log_path.unlink()
        experiment_log = load_log(args.log_path)
        completed_names = {entry["name"] for entry in experiment_log}

        best_result_summary = load_best_result_summary(args.backup_dir)
        best_composite = get_last_composite()
        if best_result_summary is not None:
            best_composite = max(best_composite, float(best_result_summary.get("composite", best_composite)))
        log(f"Starting composite: {best_composite:.1f}")
        log(f"Experiment set: {args.experiment_set}")
        log(f"Total experiments: {len(selected_experiments)}")
        log(f"Preset: {args.preset}")
        log(f"Selection: experiments {start_from}-{end_at}")
        log(f"Experiment log: {args.log_path}")
        log(f"Best-prompt backup: {args.backup_dir}")
        log(f"Assessment logs dir: {args.assessment_logs_dir}")
        log(f"Comparison runs per side: {args.comparison_runs}")
        if args.git_live_commit:
            log("Git live commit: enabled")
        if args.git_live_push:
            log(f"Git live push: {args.git_live_remote}/{args.git_live_branch}")
        if args.description_prefix:
            log(f"Description prefix: {args.description_prefix}")

        backup_prompts(args.backup_dir)
        log(f"Best prompts backed up to {args.backup_dir}")

        kept = 0
        skipped = 0
        discarded = 0
        uncertain = 0
        total_start = time.monotonic()

        for i, exp in enumerate(selected_experiments, 1):
            if i < start_from or i > end_at:
                continue

            if exp.name in completed_names:
                log(f"\n[{i}/{len(selected_experiments)}] {exp.name} — already completed, skipping")
                skipped += 1
                continue

            log(f"\n{'='*60}")
            log(f"[{i}/{len(selected_experiments)}] {exp.name}: {exp.desc}")
            log(f"{'='*60}")

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
                experiment_log.append(entry)
                save_log(args.log_path, experiment_log)
                skipped += 1
                continue

            prompt_paths = [PROMPTS_DIR / file for file in exp.files]
            comparison_json_path = comparison_summary_path(args.assessment_logs_dir, exp.name)
            assessment_json_path = args.assessment_logs_dir / f"{exp.name}.json"

            restore_prompts(args.backup_dir)

            try:
                incumbent_summary = run_replicated_assessment_series(
                    description=assessment_description,
                    label="incumbent",
                    run_count=max(args.comparison_runs, 1),
                    assessment_logs_dir=args.assessment_logs_dir,
                    file_stem=exp.name,
                    stream_output=args.stream_assessment_output,
                    assessment_overrides=exp.assessment_overrides,
                )
            except KeyboardInterrupt:
                log("\n  [INTERRUPTED] Restoring incumbent prompts")
                restore_prompts(args.backup_dir)
                raise SystemExit(130) from None

            if incumbent_summary.get("error"):
                entry = {
                    "name": exp.name,
                    "assessment_description": assessment_description,
                    "assessment_json": str(assessment_json_path),
                    "comparison_summary": str(comparison_json_path),
                    "file": exp.file,
                    "files": exp.files,
                    "find": exp.find,
                    "replace": exp.replace,
                    "desc": exp.desc,
                    "status": "error",
                    "best_composite": best_composite,
                    "comparison_runs": max(args.comparison_runs, 1),
                    "comparison_basis": "replicated_reset_regime",
                    "failed_side": "incumbent",
                }
                experiment_log.append(entry)
                save_log(args.log_path, experiment_log)
                skipped += 1
                restore_prompts(args.backup_dir)
                append_results_row(assessment_description, "error", incumbent_summary)
                continue

            if not apply_experiment(exp):
                log("  [ERROR] Experiment could not be re-applied after incumbent baseline run")
                restore_prompts(args.backup_dir)
                skipped += 1
                continue

            if args.git_live_commit:
                git_stage_commit_push(
                    prompt_paths,
                    assessment_description,
                    push=args.git_live_push,
                    remote=args.git_live_remote,
                    branch=args.git_live_branch or "",
                )

            try:
                result = run_replicated_assessment_series(
                    description=assessment_description,
                    label="candidate",
                    run_count=max(args.comparison_runs, 1),
                    assessment_logs_dir=args.assessment_logs_dir,
                    file_stem=exp.name,
                    stream_output=args.stream_assessment_output,
                    assessment_overrides=exp.assessment_overrides,
                )
            except KeyboardInterrupt:
                log("\n  [INTERRUPTED] Restoring best prompts and discarding partial results")
                restore_prompts(args.backup_dir)
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
                    "assessment_json": str(assessment_json_path),
                    "comparison_summary": str(comparison_json_path),
                    "file": exp.file,
                    "files": exp.files,
                    "find": exp.find,
                    "replace": exp.replace,
                    "desc": exp.desc,
                    "status": "error",
                    "best_composite": best_composite,
                    "comparison_runs": max(args.comparison_runs, 1),
                    "comparison_basis": "replicated_reset_regime",
                    "failed_side": "candidate",
                }
                experiment_log.append(entry)
                save_log(args.log_path, experiment_log)
                skipped += 1
                restore_prompts(args.backup_dir)
                append_results_row(assessment_description, "error", result)
                if args.git_live_commit:
                    git_stage_commit_push(
                        [RESULTS_TSV],
                        f"error | {assessment_description}",
                        push=args.git_live_push,
                        remote=args.git_live_remote,
                        branch=args.git_live_branch or "",
                    )
                continue

            composite = float(result["composite"])
            control_watch = summarize_control_watch(result, control_baseline_status)
            print_control_watch_summary(control_watch)
            decision = classify_experiment_result(
                result,
                incumbent_summary,
                float(incumbent_summary["composite"]),
            )
            word_delta_summary = batch_word_signal_details(result, incumbent_summary)
            comparison_summary = {
                "assessment_description": assessment_description,
                "comparison_basis": "replicated_reset_regime",
                "run_count": max(args.comparison_runs, 1),
                "incumbent": incumbent_summary,
                "candidate": result,
                "decision": {
                    "status": decision.status,
                    "composite_delta": round(decision.delta, 1),
                    "pass_rate_delta": round(float(result["pass_rate"]) - float(incumbent_summary["pass_rate"]), 3),
                    "tier_balanced_pass_rate_delta": round(
                        float(result.get("tier_balanced_pass_rate", 0.0)) - float(incumbent_summary.get("tier_balanced_pass_rate", 0.0)),
                        3,
                    ),
                    "protected_regression": decision.protected_regression,
                    "pass_regression": decision.pass_regression,
                    "tier_balanced_regression": decision.tier_balanced_regression,
                    "uncertain_reason": decision.uncertain_reason,
                    "research_signal": decision.research_signal,
                    "word_signal": decision.signal.to_dict(),
                    "word_deltas": word_delta_summary,
                },
            }
            comparison_json_path.parent.mkdir(parents=True, exist_ok=True)
            assessment_json_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            comparison_json_path.write_text(
                json.dumps(comparison_summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            status = decision.status
            symbol = {
                "keep": "✓ IMPROVED",
                "uncertain": "? Uncertain",
                "discard": "✗ No improvement",
            }[status]
            log(
                f"  {symbol}: {float(incumbent_summary['composite']):.1f} → {composite:.1f} "
                f"(pass={float(result['pass_rate']):.3f} tier={float(result.get('tier_balanced_pass_rate', 0.0)):.3f} "
                f"sem={float(result['avg_semantic']):.1f} reb={float(result['avg_rebus']):.1f})"
            )

            entry = {
                "name": exp.name,
                "assessment_description": assessment_description,
                "assessment_json": str(assessment_json_path),
                "comparison_summary": str(comparison_json_path),
                "file": exp.file,
                "files": exp.files,
                "find": exp.find,
                "replace": exp.replace,
                "desc": exp.desc,
                "status": status,
                "composite": composite,
                "pass_rate": float(result["pass_rate"]),
                "tier_balanced_pass_rate": float(result.get("tier_balanced_pass_rate", 0.0)),
                "avg_semantic": float(result["avg_semantic"]),
                "avg_rebus": float(result["avg_rebus"]),
                "prev_best": float(incumbent_summary["composite"]),
                "delta": round(decision.delta, 1),
                "protected_regression": decision.protected_regression,
                "pass_regression": decision.pass_regression,
                "tier_balanced_regression": decision.tier_balanced_regression,
                "uncertain_reason": decision.uncertain_reason,
                "research_signal": decision.research_signal,
                "family": exp.family,
                "priority": exp.priority,
                "target_words": list(exp.target_words),
                "prerequisites": list(exp.prerequisites),
                "assessment_overrides": dict(exp.assessment_overrides or {}),
                "comparison_runs": max(args.comparison_runs, 1),
                "comparison_basis": "replicated_reset_regime",
                "incumbent_summary": incumbent_summary,
                "candidate_summary": result,
                "word_signal": decision.signal.to_dict(),
                "word_deltas": word_delta_summary,
                "control_watch": control_watch,
            }
            experiment_log.append(entry)
            save_log(args.log_path, experiment_log)

            if status == "keep":
                best_composite = composite
                best_result_summary = result
                save_best_prompts(args.backup_dir)
                save_best_result_summary(args.backup_dir, result)
                append_results_row(assessment_description, "keep", result)
                kept += 1
                log(f"  New best: {best_composite:.1f}")
                if args.git_live_commit:
                    git_stage_commit_push(
                        [*prompt_paths, RESULTS_TSV, comparison_json_path],
                        f"keep | {assessment_description}",
                        push=args.git_live_push,
                        remote=args.git_live_remote,
                        branch=args.git_live_branch or "",
                    )
            else:
                restore_prompts(args.backup_dir)
                append_results_row(assessment_description, status, result)
                if status == "uncertain":
                    uncertain += 1
                else:
                    discarded += 1
                if args.git_live_commit:
                    git_stage_commit_push(
                        [RESULTS_TSV, comparison_json_path],
                        f"{status} | {assessment_description}",
                        push=args.git_live_push,
                        remote=args.git_live_remote,
                        branch=args.git_live_branch or "",
                    )

        restore_prompts(args.backup_dir)

        total_elapsed = time.monotonic() - total_start
        log(f"\n{'='*60}")
        log("EXPERIMENT RUN COMPLETE")
        log(f"{'='*60}")
        log(f"Total time: {total_elapsed/3600:.1f}h")
        log(f"Final best composite: {best_composite:.1f}")
        log(f"Kept: {kept}, Uncertain: {uncertain}, Discarded: {discarded}, Skipped: {skipped}")

        kept_entries = [e for e in experiment_log if e.get("status") == "keep"]
        if kept_entries:
            log("\nKept experiments:")
            for e in kept_entries:
                log(f"  {e['name']}: {e['desc']} — composite={e['composite']:.1f}")
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
