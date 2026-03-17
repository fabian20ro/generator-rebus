#!/usr/bin/env python3
"""Automated prompt experiment runner (autoresearch-style hill climbing).

Runs 100 single-variable prompt experiments against the multi-model assessment
pipeline, keeping improvements and reverting regressions. Each experiment builds
on the cumulative best state (compounding improvements).

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
RESULTS_TSV = PROJECT_ROOT / "generator" / "assessment" / "multistep_results.tsv"
EXPERIMENT_LOG = PROJECT_ROOT / "generator" / "assessment" / "experiment_log.json"
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
# Ordered by expected impact: pass_rate improvements first (verify + definition),
# then rating calibration, then fine-tuning.

EXPERIMENTS: list[Experiment] = []


def _exp(name: str, desc: str, file: str, find: str, replace: str) -> None:
    EXPERIMENTS.append(Experiment(name, desc, file, find, replace))


# ── BLOCK 1: Verify improvements (exp 1-25) ──────────────────────
# Pass rate is the biggest lever (weight 100). Verify prompt directly determines
# whether a good definition gets credit.

_exp("exp001", "add step-by-step solving to verify system",
     SYS_VERIFY,
     "Exemple:",
     "Proces de rezolvare:\n"
     "1. Citește definiția atent.\n"
     "2. Gândește la cuvinte românești care se potrivesc.\n"
     "3. Verifică: are exact lungimea cerută?\n"
     "4. Dacă nu, caută alt cuvânt.\n"
     "Exemple:")

_exp("exp002", "add common-word preference to verify",
     SYS_VERIFY,
     "- Răspunsul conține doar litere românești.",
     "- Răspunsul conține doar litere românești.\n"
     "- Dacă mai multe cuvinte par posibile, alege cel mai uzual și comun.")

_exp("exp003", "add expert framing to verify role",
     SYS_VERIFY,
     "Ești rezolvitor de rebusuri românești.",
     "Ești rezolvitor expert de rebusuri românești cu experiență vastă.")

_exp("exp004", "add length double-check to verify",
     SYS_VERIFY,
     "- Răspunsul conține doar litere românești.",
     "- Răspunsul conține doar litere românești.\n"
     "- Verifică de două ori: răspunsul tău are exact lungimea cerută?")

_exp("exp005", "add lateral thinking hint to verify",
     SYS_VERIFY,
     "Exemple:",
     "- Un rebus folosește definiții creative. Gândește lateral, nu literal.\n"
     "Exemple:")

_exp("exp006", "add rare/technical word hint to verify",
     SYS_VERIFY,
     "Exemple:",
     "- Dacă definiția pare tehnică sau neobișnuită, gândește-te la termeni de specialitate.\n"
     "Exemple:")

_exp("exp007", "add verb form awareness to verify",
     SYS_VERIFY,
     "Exemple:",
     "- Răspunsul poate fi un verb conjugat, o interjecție, un acronim sau o formă rară.\n"
     "Exemple:")

_exp("exp008", "add grammatical form example to verify",
     SYS_VERIFY,
     "Răspuns: AER",
     "Răspuns: AER\n"
     "Definiție: Conjuncție adversativă\n"
     "Răspuns: DAR")

_exp("exp009", "add longer word example to verify",
     SYS_VERIFY,
     "Răspuns: AER",
     "Răspuns: AER\n"
     "Definiție: Se trage un semnal de pericol\n"
     "Răspuns: ALARMA")

_exp("exp010", "add rare word example to verify",
     SYS_VERIFY,
     "Răspuns: AER",
     "Răspuns: AER\n"
     "Definiție: Bețe de sprijin pentru vița de vie\n"
     "Răspuns: ARACI")

_exp("exp011", "emphasize EXACT length in verify user template",
     USR_VERIFY,
     "Lungime răspuns: EXACT {answer_length} litere",
     "IMPORTANT: Răspunsul are EXACT {answer_length} litere. Nicio literă în plus sau minus.")

_exp("exp012", "move length before definition in verify user",
     USR_VERIFY,
     "Definiție: {definition}\nLungime răspuns: EXACT {answer_length} litere",
     "Lungime răspuns: EXACT {answer_length} litere\nDefiniție: {definition}")

_exp("exp013", "add explicit verification step to verify user",
     USR_VERIFY,
     "Răspuns:",
     "Verifică lungimea înainte de a scrie.\nRăspuns:")

_exp("exp014", "simplify verify user to bare minimum",
     USR_VERIFY,
     "Numără literele răspunsului tău înainte de a răspunde. Dacă nu are exact {answer_length} litere, gândește-te la alt cuvânt.\n",
     "")

_exp("exp015", "add rebus-style hint to verify user",
     USR_VERIFY,
     "Răspuns:",
     "Atenție: definiția este în stil rebus (creativă), nu de dicționar.\nRăspuns:")

_exp("exp016", "add think-in-romanian to verify user",
     USR_VERIFY,
     "Răspuns:",
     "Gândește în română. Un singur cuvânt.\nRăspuns:")

_exp("exp017", "add most-common-meaning instruction to verify",
     SYS_VERIFY,
     "- Nu reformulezi definiția.",
     "- Nu reformulezi definiția.\n"
     "- Alege sensul cel mai comun și direct al definiției.")

_exp("exp018", "add domain-switching awareness to verify",
     SYS_VERIFY,
     "Exemple:",
     "- Definiția poate folosi un sens figurat sau o referință din alt domeniu. Gândește flexibil.\n"
     "Exemple:")

_exp("exp019", "add short word strategy to verify",
     SYS_VERIFY,
     "Exemple:",
     "- Pentru cuvinte de 2-3 litere: gândește la prepoziții, interjecții, forme verbale scurte.\n"
     "Exemple:")

_exp("exp020", "add no-english reinforcement to verify",
     SYS_VERIFY,
     "- Răspunsul conține doar litere românești.",
     "- Răspunsul conține doar litere românești.\n"
     "- NICIODATĂ nu răspunde cu un cuvânt englezesc, chiar dacă pare să se potrivească.")

_exp("exp021", "add diacritics awareness to verify",
     SYS_VERIFY,
     "- Răspunsul conține doar litere românești.",
     "- Răspunsul conține doar litere românești (inclusiv ă, â, î, ș, ț).\n"
     "- Diacriticele nu contează la numărarea lungimii.")

_exp("exp022", "add confidence instruction to verify",
     SYS_VERIFY,
     "- Nu răspunzi cu propoziții.",
     "- Nu răspunzi cu propoziții.\n"
     "- Dacă nu ești sigur, alege totuși cel mai probabil cuvânt românesc.")

_exp("exp023", "add crossword-cell framing to verify",
     SYS_VERIFY,
     "Ești rezolvitor de rebusuri românești.",
     "Ești rezolvitor de rebusuri românești.\n"
     "Completezi celule de rebus: fiecare celulă = o literă, numărul de celule = lungimea răspunsului.")

_exp("exp024", "remove tag/markup instruction from verify (redundant)",
     SYS_VERIFY,
     "- Nu incluzi taguri, marcaje tehnice sau caractere speciale.\n",
     "")

_exp("exp025", "add multiple word examples covering all tiers to verify",
     SYS_VERIFY,
     "Răspuns: AER",
     "Răspuns: AER\n"
     "Definiție: Lumină de semnalizare pe coastă\n"
     "Răspuns: FAR\n"
     "Definiție: Moment culminant\n"
     "Răspuns: CLOU")


# ── BLOCK 2: Definition generation improvements (exp 26-55) ──────
# Better definitions → higher pass rate + semantic scores.

_exp("exp026", "add uniqueness-check rule to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Verifică mental: un rezolvitor ar scrie exact acest cuvânt din definiția ta?\n"
     "Exemple corecte:")

_exp("exp027", "add single-answer rule to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Definiția trebuie să aibă un singur răspuns posibil la lungimea dată.\n"
     "Exemple corecte:")

_exp("exp028", "add domain-surprise rule to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Preferă perifraze din domenii neașteptate pentru efect de surpriză creativă.\n"
     "Exemple corecte:")

_exp("exp029", "add anti-synonym rule to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Evită definiții care ar putea duce la un sinonim mai comun decât răspunsul.\n"
     "Exemple corecte:")

_exp("exp030", "add distinctive-sense rule to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Pentru cuvinte cu mai multe sensuri, alege sensul cel mai distinctiv și specific.\n"
     "Exemple corecte:")

_exp("exp031", "add rebus-not-dictionary framing to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Definiția de rebus ≠ definiția de dicționar. Fii concis și surprinzător.\n"
     "Exemple corecte:")

_exp("exp032", "add concrete-over-abstract rule to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Preferă indicii concrete și vizuale în loc de concepte abstracte.\n"
     "Exemple corecte:")

_exp("exp033", "add anti-generic-formula rule to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Evită formule generice: 'Acțiunea de...', 'Proces prin care...', 'Ceva care...'.\n"
     "Exemple corecte:")

_exp("exp034", "add word-count tightening to definition",
     SYS_DEFINITION,
     "- Preferi definiții precise, naturale, maxim 12 cuvinte.",
     "- Definiția ideală: 4-8 cuvinte. Maxim 12 în cazuri complexe.")

_exp("exp035", "add self-test instruction to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Testează mental: dacă citești definiția fără context, te duce la un singur cuvânt?\n"
     "Exemple corecte:")

_exp("exp036", "add quality hierarchy to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Prioritate: specificitate > concizie > creativitate.\n"
     "Exemple corecte:")

_exp("exp037", "add anti-clisee rule to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Evită clișeele: 'Se face cu...', 'Element de...', 'Parte a...', 'Un fel de...'.\n"
     "Exemple corecte:")

_exp("exp038", "add no-ambiguity reinforcement to definition",
     SYS_DEFINITION,
     "- Răspunzi cu o singură definiție scurtă.",
     "- Răspunzi cu o singură definiție scurtă, fără ambiguitate.")

_exp("exp039", "add verifiability principle to definition role",
     SYS_DEFINITION,
     "Ești autor de definiții de rebus în limba română.",
     "Ești autor de definiții de rebus în limba română.\n"
     "Principiul de bază: fiecare definiție trebuie să ducă un rezolvitor la EXACT cuvântul corect.")

_exp("exp040", "add expert framing to definition role",
     SYS_DEFINITION,
     "Ești autor de definiții de rebus în limba română.",
     "Ești autor expert de definiții de rebus în limba română, cu experiență în puzzle-uri publicate.")

_exp("exp041", "add new crossword example RIAL to definition",
     SYS_DEFINITION,
     "CLOU -> Moment culminant",
     "CLOU -> Moment culminant\n"
     "RIAL -> Se plătește la șah")

_exp("exp042", "add action-verb example to definition",
     SYS_DEFINITION,
     "CLOU -> Moment culminant",
     "CLOU -> Moment culminant\n"
     "ARDE -> Efectul focului asupra materiei")

_exp("exp043", "add longer word example to definition",
     SYS_DEFINITION,
     "CLOU -> Moment culminant",
     "CLOU -> Moment culminant\n"
     "ALARMA -> Semnal sonor de pericol")

_exp("exp044", "add verb-definition strategy to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Dacă cuvântul este un verb, descrie acțiunea sau rezultatul ei fără a folosi infinitivul.\n"
     "Exemple corecte:")

_exp("exp045", "add length-awareness to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Ține cont de lungimea cuvântului: definiția + lungimea trebuie să dea un răspuns unic.\n"
     "Exemple corecte:")

_exp("exp046", "add disambiguation strategy to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Dacă definiția ar putea avea mai multe răspunsuri, adaugă un detaliu discriminator.\n"
     "Exemple corecte:")

_exp("exp047", "strengthen no-invention rule in definition",
     SYS_DEFINITION,
     "- Nu inventezi sensuri. Dacă nu ești sigur, răspunzi exact: [NECLAR]",
     "- Nu inventezi sensuri și nu extrapolezi. Fiecare afirmație trebuie să fie factual corectă. Dacă nu ești sigur, răspunzi exact: [NECLAR]")

_exp("exp048", "add generate user rebus-style instruction",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă. Răspunde doar cu definiția.",
     "Scrie o definiție de rebus: scurtă, precisă, cu un unghi neașteptat. Un singur răspuns posibil. Răspunde doar cu definiția.")

_exp("exp049", "add generate user uniqueness instruction",
     USR_GENERATE,
     "Răspunde doar cu definiția.",
     "Scopul: un rezolvitor să scrie exact acest cuvânt.\nRăspunde doar cu definiția.")

_exp("exp050", "add generate user max-words hint",
     USR_GENERATE,
     "Răspunde doar cu definiția.",
     "Max 8 cuvinte. Răspunde doar cu definiția.")

_exp("exp051", "add generate user concreteness instruction",
     USR_GENERATE,
     "Răspunde doar cu definiția.",
     "Definește concret, nu abstract. Răspunde doar cu definiția.")

_exp("exp052", "add generate user single-sense instruction",
     USR_GENERATE,
     "Răspunde doar cu definiția.",
     "Un singur sens, un singur răspuns. Răspunde doar cu definiția.")

_exp("exp053", "add no-dictionary framing to generate user",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă.",
     "Scrie o definiție de rebus scurtă și exactă. Nu copia din dicționar.")

_exp("exp054", "add mental-test to generate user",
     USR_GENERATE,
     "Răspunde doar cu definiția.",
     "Testează mental: doar acest cuvânt se potrivește la {length} litere?\nRăspunde doar cu definiția.")

_exp("exp055", "add specificity instruction to generate user",
     USR_GENERATE,
     "Răspunde doar cu definiția.",
     "Alege unghiul cel mai specific pentru acest cuvânt. Răspunde doar cu definiția.")


# ── BLOCK 3: Rating calibration (exp 56-85) ──────────────────────
# Better rating → better rebus scores and more useful feedback.

_exp("exp056", "add anchored semantic scale to rate",
     SYS_RATE,
     "- semantic_score: cât de corectă și onestă este definiția pentru răspunsul dat",
     "- semantic_score: cât de corectă semantic este definiția — 9-10 = acoperă exact un sens real, 5-6 = parțial corectă, 1-3 = incorectă sau inventată")

_exp("exp057", "add stricter guessability framing to rate",
     SYS_RATE,
     "- guessability_score: dacă un rezolvitor ar citi definiția și ar avea {answer_length} căsuțe de completat, ar scrie exact cuvântul-răspuns?",
     "- guessability_score: dacă un rezolvitor ar citi definiția și ar avea {answer_length} căsuțe, ar scrie EXACT acest cuvânt? Fii strict: dacă există și alt cuvânt posibil, scor ≤ 6.")

_exp("exp058", "add uniqueness criterion to guessability in rate",
     SYS_RATE,
     "Criterii:",
     "- un guessability_score de 9-10 înseamnă: nu există alt cuvânt românesc de aceeași lungime care să se potrivească definiției\n"
     "Criterii:")

_exp("exp059", "add strictness instruction to rate",
     SYS_RATE,
     "Criterii:",
     "- Fii strict și obiectiv. Nu da scoruri mari din inerție.\n"
     "Criterii:")

_exp("exp060", "add multi-answer penalty to rate",
     SYS_RATE,
     "- dacă duce spre alt răspuns sau spre un sinonim mai uzual: guessability_score mic",
     "- dacă duce spre alt răspuns sau spre un sinonim mai uzual: guessability_score mic (≤ 5)\n"
     "- dacă definiția funcționează pentru 2+ cuvinte de aceeași lungime: guessability_score ≤ 6")

_exp("exp061", "add factual-accuracy emphasis to semantic in rate",
     SYS_RATE,
     "Criterii:",
     "- semantic_score reflectă corectitudinea factuală, nu stilul sau creativitatea\n"
     "Criterii:")

_exp("exp062", "add domain-switch creativity emphasis to rate",
     SYS_RATE,
     "- dacă definiția e creativă și diferită de definițiile de dicționar: creativity_score mare",
     "- dacă definiția e creativă și diferită de definițiile de dicționar: creativity_score mare\n"
     "- creativity_score 8-10: definiția face rezolvitorul să se gândească inițial la alt domeniu complet")

_exp("exp063", "add low-score JSON example to rate",
     SYS_RATE,
     'Exemplu de răspuns corect:\n{"semantic_score": 8, "guessability_score": 6, "creativity_score": 7, "feedback": "Definiția este corectă dar ușor ambiguă."}',
     'Exemple de răspunsuri corecte:\n'
     '{"semantic_score": 8, "guessability_score": 6, "creativity_score": 7, "feedback": "Definiția este corectă dar ușor ambiguă."}\n'
     '{"semantic_score": 4, "guessability_score": 2, "creativity_score": 5, "feedback": "Definiția ar putea duce la mai multe cuvinte."}')

_exp("exp064", "add high-score JSON example to rate",
     SYS_RATE,
     'Exemplu de răspuns corect:\n{"semantic_score": 8, "guessability_score": 6, "creativity_score": 7, "feedback": "Definiția este corectă dar ușor ambiguă."}',
     'Exemple de răspunsuri corecte:\n'
     '{"semantic_score": 8, "guessability_score": 6, "creativity_score": 7, "feedback": "Definiția este corectă dar ușor ambiguă."}\n'
     '{"semantic_score": 10, "guessability_score": 9, "creativity_score": 8, "feedback": "Definiție precisă și ingenioasă, un singur răspuns posibil."}')

_exp("exp065", "add no-identical-scores instruction to rate",
     SYS_RATE,
     "Criterii:",
     "- Diferențiază cele trei scoruri — e rar ca semantic, guessability și creativity să fie egale.\n"
     "Criterii:")

_exp("exp066", "add constructive feedback instruction to rate",
     SYS_RATE,
     "- feedback-ul este exclusiv în română, scurt și concret",
     "- feedback-ul este exclusiv în română, maxim 15 cuvinte, menționând: (1) ce e bun, (2) ce ar îmbunătăți")

_exp("exp067", "tighten guessability scale in rate",
     SYS_RATE,
     "9-10 = sigur da, 5-6 = posibil, 1-3 = ar scrie altceva",
     "9-10 = un singur cuvânt posibil la această lungime, 7-8 = probabil corect, 5-6 = mai multe opțiuni, 1-3 = ar scrie altceva cu certitudine")

_exp("exp068", "add length consideration to guessability in rate",
     SYS_RATE,
     "Criterii:",
     "- Consideră și lungimea: dacă definiția + lungimea dau un singur cuvânt posibil, guessability mare.\n"
     "Criterii:")

_exp("exp069", "add solver perspective framing to rate",
     SYS_RATE,
     "Evaluezi o definiție de rebus pe scara 1-10.",
     "Evaluezi o definiție de rebus pe scara 1-10.\n"
     "Pune-te în locul unui rezolvitor experimentat de rebusuri românești.")

_exp("exp070", "remove rarity-tolerance rule from rate (test stricter)",
     SYS_RATE,
     "- nu penaliza doar pentru că răspunsul este rar; penalizezi doar dacă definiția este vagă sau duce firesc la alt răspuns mai comun\n",
     "")

_exp("exp071", "add DEX reference instruction to rate",
     SYS_RATE,
     "Criterii:",
     "- Dacă sunt furnizate definiții DEX, verifică dacă definiția acoperă un sens real.\n"
     "Criterii:")

_exp("exp072", "strengthen JSON-only instruction in rate",
     SYS_RATE,
     "Răspunzi STRICT cu un singur obiect JSON, fără text înainte sau după:",
     "Răspunzi DOAR cu un singur obiect JSON valid. NIMIC altceva — niciun text, nicio explicație, niciun markup:")

_exp("exp073", "add precision-over-style to rate",
     SYS_RATE,
     "- dacă e precisă și scurtă: scoruri mari",
     "- dacă e precisă, scurtă și duce la un singur răspuns: scoruri mari")

_exp("exp074", "add creative-but-accurate to rate",
     SYS_RATE,
     "Criterii:",
     "- O definiție poate fi creativă dar incorectă — creativitatea nu compensează erori semantice.\n"
     "Criterii:")

_exp("exp075", "add rate user evaluation instruction",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează strict și obiectiv. Pune-te în locul unui rezolvitor: ar scrie exact {word}? Răspunde STRICT cu JSON.")

_exp("exp076", "add rate user guessability focus",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "Evaluează separat. Atenție specială la guessability: există alt cuvânt de {answer_length} litere care se potrivește? Răspunde STRICT cu JSON.")

_exp("exp077", "add creativity emphasis for DEX copies in rate",
     SYS_RATE,
     "- dacă definiția este aproape identică cu o definiție DEX: creativity_score mic (3-4)",
     "- dacă definiția este aproape identică cu o definiție DEX: creativity_score mic (2-3)\n"
     "- parafrazarea minimă a DEX nu e creativitate reală")

_exp("exp078", "put guessability first in rate system (most important)",
     SYS_RATE,
     "Întorci trei scoruri distincte:\n"
     "- semantic_score: cât de corectă și onestă este definiția pentru răspunsul dat\n"
     "- guessability_score:",
     "Întorci trei scoruri distincte (guessability e CEL MAI IMPORTANT):\n"
     "- guessability_score: (CEL MAI IMPORTANT) dacă un rezolvitor ar citi definiția și ar avea {answer_length} căsuțe de completat, ar scrie exact cuvântul-răspuns?\n"
     "- semantic_score: cât de corectă și onestă este definiția pentru răspunsul dat\n"
     "- guessability_score:")

_exp("exp079", "add rate user rebus-vs-dictionary note",
     USR_RATE,
     "Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.",
     "O definiție bună de rebus ≠ o definiție de dicționar. Evaluează separat corectitudinea semantică, ghicibilitatea exactă și creativitatea. Răspunde STRICT cu JSON.")

_exp("exp080", "strengthen family-word penalty in rate",
     SYS_RATE,
     "- dacă include răspunsul, o derivată clară sau aceeași familie lexicală: ambele scoruri foarte mici",
     "- dacă include răspunsul, o derivată clară sau aceeași familie lexicală: TOATE scorurile = 1")

_exp("exp081", "add vagueness penalty to rate",
     SYS_RATE,
     "- dacă e banală dar corectă: semantic mediu, guessability mediu sau mic",
     "- dacă e banală dar corectă: semantic mediu, guessability mediu sau mic\n"
     "- dacă e vagă și ar putea descrie zeci de cuvinte: guessability ≤ 3")

_exp("exp082", "add simplicity preference for high semantic in rate",
     SYS_RATE,
     "Criterii:",
     "- Definiția perfectă: simplu + precis + un singur răspuns. Nu trebuie să fie complexă.\n"
     "Criterii:")

_exp("exp083", "add short feedback instruction to rate",
     SYS_RATE,
     "- feedback-ul este exclusiv în română, scurt și concret",
     "- feedback-ul este exclusiv în română, maxim 10 cuvinte")

_exp("exp084", "add JSON enforcement to rate user",
     USR_RATE,
     "Răspunde STRICT cu JSON.",
     "Răspunde cu un singur obiect JSON valid, fără niciun text suplimentar.")

_exp("exp085", "add checker perspective to rate",
     SYS_RATE,
     "Evaluezi o definiție de rebus pe scara 1-10.",
     "Evaluezi calitatea unei definiții de rebus pe scara 1-10.\n"
     "Ești un evaluator strict și obiectiv.")


# ── BLOCK 4: Cross-prompt fine-tuning (exp 86-100) ───────────────

_exp("exp086", "reduce definition max words from 12 to 10",
     SYS_DEFINITION,
     "maxim 12 cuvinte",
     "maxim 10 cuvinte")

_exp("exp087", "add conciseness emphasis to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Fiecare cuvânt din definiție trebuie să contribuie. Elimină cuvintele de prisos.\n"
     "Exemple corecte:")

_exp("exp088", "add disambiguation for short words in definition",
     SYS_DEFINITION,
     "- Pentru cuvinte scurte, abrevieri și forme gramaticale fii literal și exact.",
     "- Pentru cuvinte scurte (2-3 litere): fii extrem de specific. Menționează categoria gramaticală sau domeniul de utilizare.\n"
     "- Pentru abrevieri și forme gramaticale fii literal și exact.")

_exp("exp089", "add negative example for vague definition",
     SYS_DEFINITION,
     "AT -> Prepoziție de loc [GREȘIT]",
     "AT -> Prepoziție de loc [GREȘIT]\n"
     "ARDE -> Ceva care se întâmplă [GREȘIT - prea vag]")

_exp("exp090", "add negative example for multi-answer definition",
     SYS_DEFINITION,
     "AT -> Prepoziție de loc [GREȘIT]",
     "AT -> Prepoziție de loc [GREȘIT]\n"
     "IDEA -> Concept mental [GREȘIT - duce la mai multe răspunsuri]")

_exp("exp091", "add verify system emphasis on exact word matching",
     SYS_VERIFY,
     "- Gândești și răspunzi exclusiv în română.",
     "- Gândești și răspunzi exclusiv în română.\n"
     "- Răspunsul tău trebuie să fie EXACT cuvântul pe care autorul l-a avut în minte.")

_exp("exp092", "add generate user word-type awareness",
     USR_GENERATE,
     "Lungime: {length}",
     "Lungime: {length}\nAcest cuvânt este în limba ROMÂNĂ.")

_exp("exp093", "add frequency hint to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- O definiție bună face cuvântul-răspuns cea mai naturală și probabilă completare.\n"
     "Exemple corecte:")

_exp("exp094", "add perspective-taking to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Scrie definiția ca și cum ar trebui publicată într-o revistă de rebus.\n"
     "Exemple corecte:")

_exp("exp095", "strengthen Romanian-only in definition role",
     SYS_DEFINITION,
     "IMPORTANT: Toate cuvintele sunt exclusiv în limba ROMÂNĂ.",
     "IMPORTANT ȘI OBLIGATORIU: Toate cuvintele sunt exclusiv în limba ROMÂNĂ. Chiar dacă un cuvânt seamănă cu unul englezesc, sensul este NUMAI cel românesc.")

_exp("exp096", "add generate user test-question framing",
     USR_GENERATE,
     "Scrie o definiție de rebus scurtă și exactă.",
     "Scrie o definiție de rebus scurtă și exactă, ca o întrebare cu un singur răspuns corect.")

_exp("exp097", "simplify definition system prompt (fewer rules)",
     SYS_DEFINITION,
     "- Dacă sensul direct ar necesita un cuvânt interzis, folosește o perifrază creativă sau o descriere indirectă.\n",
     "")

_exp("exp098", "add rewrite-awareness to definition system",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Gândește la definiție ca la un test: dacă 10 rezolvitori o citesc, toți ar scrie același cuvânt?\n"
     "Exemple corecte:")

_exp("exp099", "add wordplay emphasis to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Cel mai bun tip de definiție: o perifrază elegantă care exploatează un joc de domenii.\n"
     "Exemple corecte:")

_exp("exp100", "add precision-at-all-costs to definition",
     SYS_DEFINITION,
     "Exemple corecte:",
     "- Între o definiție creativă dar ambiguă și una simplă dar precisă, alege precizia.\n"
     "Exemple corecte:")


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
    """Read the last composite score from results TSV."""
    lines = RESULTS_TSV.read_text().strip().split("\n")
    if len(lines) < 2:
        raise ValueError("No results in TSV")
    last_line = lines[-1]
    fields = last_line.split("\t")
    return float(fields[1])


def apply_experiment(exp: Experiment) -> bool:
    """Apply an experiment's edit. Returns True if the edit was applied."""
    filepath = PROMPTS_DIR / exp.file
    if not filepath.exists():
        print(f"  [SKIP] File not found: {filepath}")
        return False

    content = filepath.read_text(encoding="utf-8")
    if exp.find not in content:
        print(f"  [SKIP] Find text not found in {exp.file}")
        return False

    new_content = content.replace(exp.find, exp.replace, 1)
    if new_content == content:
        print(f"  [SKIP] No change after replacement in {exp.file}")
        return False

    filepath.write_text(new_content, encoding="utf-8")
    return True


def run_assessment(description: str) -> dict:
    """Run the multi-model assessment and return parsed results."""
    cmd = [
        sys.executable, "-m", "generator.assessment.run_assessment",
        "--description", description,
    ]
    print(f"  Running assessment: {description}")
    start = time.monotonic()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=2400,  # 40 min timeout
        cwd=str(PROJECT_ROOT),
    )

    elapsed = time.monotonic() - start
    print(f"  Assessment completed in {elapsed:.0f}s")

    if result.returncode != 0:
        print(f"  [ERROR] Assessment failed:\n{result.stderr[-500:]}")
        return {"composite": 0.0, "pass_rate": 0.0, "avg_semantic": 0.0, "avg_rebus": 0.0, "error": True}

    # Parse from TSV (last line)
    lines = RESULTS_TSV.read_text().strip().split("\n")
    last = lines[-1].split("\t")
    return {
        "composite": float(last[1]),
        "pass_rate": float(last[2]),
        "avg_semantic": float(last[3]),
        "avg_rebus": float(last[4]),
        "error": False,
    }


def load_log() -> list[dict]:
    """Load experiment log from JSON."""
    if EXPERIMENT_LOG.exists():
        return json.loads(EXPERIMENT_LOG.read_text())
    return []


def save_log(log: list[dict]) -> None:
    """Save experiment log to JSON."""
    EXPERIMENT_LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 100 prompt experiments")
    parser.add_argument("--start-from", type=int, default=1,
                        help="Resume from experiment N (1-indexed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show experiments without running")
    args = parser.parse_args()

    if args.dry_run:
        for i, exp in enumerate(EXPERIMENTS, 1):
            print(f"{i:3d}. [{exp.name}] {exp.desc}")
            print(f"     File: {exp.file}")
        print(f"\nTotal: {len(EXPERIMENTS)} experiments")
        return

    # Initialize
    log = load_log()
    completed_names = {entry["name"] for entry in log}

    # Get current best composite
    best_composite = get_last_composite()
    print(f"Starting composite: {best_composite:.1f}")
    print(f"Total experiments: {len(EXPERIMENTS)}")
    print(f"Starting from: experiment {args.start_from}")

    # Back up current prompts as "best"
    backup_prompts()
    print(f"Best prompts backed up to {BEST_BACKUP_DIR}")

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
        restore_prompts()

        # Apply the experiment edit
        applied = apply_experiment(exp)
        if not applied:
            entry = {
                "name": exp.name,
                "desc": exp.desc,
                "status": "skipped",
                "reason": "find text not found",
                "best_composite": best_composite,
            }
            log.append(entry)
            save_log(log)
            skipped += 1
            continue

        # Run assessment
        result = run_assessment(exp.name)

        if result.get("error"):
            entry = {
                "name": exp.name,
                "desc": exp.desc,
                "status": "error",
                "best_composite": best_composite,
            }
            log.append(entry)
            save_log(log)
            skipped += 1
            # Restore best prompts
            restore_prompts()
            continue

        composite = result["composite"]
        improved = composite > best_composite

        status = "keep" if improved else "discard"
        symbol = "✓ IMPROVED" if improved else "✗ No improvement"
        print(f"  {symbol}: {best_composite:.1f} → {composite:.1f} "
              f"(pass={result['pass_rate']:.3f} sem={result['avg_semantic']:.1f} reb={result['avg_rebus']:.1f})")

        entry = {
            "name": exp.name,
            "desc": exp.desc,
            "status": status,
            "composite": composite,
            "pass_rate": result["pass_rate"],
            "avg_semantic": result["avg_semantic"],
            "avg_rebus": result["avg_rebus"],
            "prev_best": best_composite,
        }
        log.append(entry)
        save_log(log)

        if improved:
            best_composite = composite
            save_best_prompts()
            kept += 1
            print(f"  New best: {best_composite:.1f}")
        else:
            restore_prompts()
            discarded += 1

    # Final restore of best prompts
    restore_prompts()

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
