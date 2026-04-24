"""Prompt evaluation dataset and comparison helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PROMPT_EVAL_BUCKET_SIZE = 100
PROMPT_EVAL_BUCKETS = ("easy", "medium", "hard")
PROMPT_EVAL_METRICS = (
    "valid_generation_rate",
    "guard_rejection_rate",
    "verify_pass_rate",
    "semantic_score",
    "rebus_score",
    "short_word_pass_rate",
    "high_control_regression",
    "truncation_parse_failures",
)


@dataclass(frozen=True)
class WordAggregate:
    word: str
    attempts: int
    successes: int
    blockers: int
    avg_rebus: float
    avg_semantic: float
    length: int

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0


def _load_words_metadata(words_path: Path) -> dict[str, dict]:
    if not words_path.exists():
        return {}
    return {
        str(row.get("normalized") or ""): row
        for row in json.loads(words_path.read_text(encoding="utf-8"))
        if row.get("normalized")
    }


def aggregate_run_all_metrics(metrics_paths: list[Path]) -> dict[str, WordAggregate]:
    totals: dict[str, dict[str, float | int]] = {}
    for path in metrics_paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("word_metrics", []):
            word = str(row.get("word") or "")
            if not word:
                continue
            entry = totals.setdefault(
                word,
                {
                    "attempts": 0,
                    "successes": 0,
                    "blockers": 0,
                    "total_rebus": 0.0,
                    "total_semantic": 0.0,
                    "length": int(row.get("length") or len(word)),
                },
            )
            entry["attempts"] = int(entry["attempts"]) + 1
            entry["successes"] = int(entry["successes"]) + (1 if row.get("final_verified") else 0)
            entry["blockers"] = int(entry["blockers"]) + (1 if row.get("was_blocker") else 0)
            entry["total_rebus"] = float(entry["total_rebus"]) + float(row.get("rebus_score") or 0)
            entry["total_semantic"] = float(entry["total_semantic"]) + float(row.get("semantic_score") or 0)
    result: dict[str, WordAggregate] = {}
    for word, entry in totals.items():
        attempts = int(entry["attempts"])
        result[word] = WordAggregate(
            word=word,
            attempts=attempts,
            successes=int(entry["successes"]),
            blockers=int(entry["blockers"]),
            avg_rebus=round(float(entry["total_rebus"]) / attempts, 2) if attempts else 0.0,
            avg_semantic=round(float(entry["total_semantic"]) / attempts, 2) if attempts else 0.0,
            length=int(entry["length"]),
        )
    return result


def _bucket_for_word(aggregate: WordAggregate) -> str:
    if aggregate.success_rate >= 0.8 and aggregate.avg_rebus >= 8:
        return "easy"
    if aggregate.success_rate >= 0.45 and aggregate.avg_rebus >= 5:
        return "medium"
    return "hard"


def build_prompt_eval_dataset(
    metrics_paths: list[Path],
    *,
    words_path: Path = Path("build/words.json"),
    bucket_size: int = PROMPT_EVAL_BUCKET_SIZE,
) -> list[dict[str, object]]:
    metadata = _load_words_metadata(words_path)
    aggregates = aggregate_run_all_metrics(metrics_paths)
    buckets: dict[str, list[WordAggregate]] = {name: [] for name in PROMPT_EVAL_BUCKETS}
    for aggregate in aggregates.values():
        buckets[_bucket_for_word(aggregate)].append(aggregate)
    buckets["easy"].sort(key=lambda item: (-item.success_rate, -item.avg_rebus, item.word))
    buckets["medium"].sort(key=lambda item: (abs(item.success_rate - 0.6), -item.attempts, item.word))
    buckets["hard"].sort(key=lambda item: (item.success_rate, -item.blockers, item.word))

    rows: list[dict[str, object]] = []
    for tier in PROMPT_EVAL_BUCKETS:
        for aggregate in buckets[tier][:bucket_size]:
            meta = metadata.get(aggregate.word, {})
            rows.append(
                {
                    "word": aggregate.word,
                    "display_word": meta.get("original") or aggregate.word.lower(),
                    "length": int(meta.get("length") or aggregate.length),
                    "word_type": meta.get("word_type", ""),
                    "dex_definitions": "",
                    "tier": tier,
                    "avg_rebus_score": aggregate.avg_rebus,
                    "appearances": aggregate.attempts,
                    "min_rebus_score": 0,
                    "max_rebus_score": 10,
                }
            )
    return rows


def write_prompt_eval_dataset(rows: list[dict[str, object]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare_prompt_eval_results(baseline: dict, candidate: dict) -> dict[str, object]:
    def _num(payload: dict, key: str) -> float:
        return float(payload.get(key) or 0.0)

    deltas = {
        "pass_rate": round(_num(candidate, "pass_rate") - _num(baseline, "pass_rate"), 4),
        "tier_balanced_pass_rate": round(
            _num(candidate, "tier_balanced_pass_rate") - _num(baseline, "tier_balanced_pass_rate"),
            4,
        ),
        "avg_semantic": round(_num(candidate, "avg_semantic") - _num(baseline, "avg_semantic"), 3),
        "avg_rebus": round(_num(candidate, "avg_rebus") - _num(baseline, "avg_rebus"), 3),
    }
    baseline_tiers = baseline.get("tiers", {}) or {}
    candidate_tiers = candidate.get("tiers", {}) or {}
    tier_deltas = {}
    for tier in sorted(set(baseline_tiers) | set(candidate_tiers)):
        tier_deltas[tier] = {
            "pass_rate": round(
                _num(candidate_tiers.get(tier, {}), "pass_rate")
                - _num(baseline_tiers.get(tier, {}), "pass_rate"),
                4,
            ),
            "avg_rebus": round(
                _num(candidate_tiers.get(tier, {}), "avg_rebus")
                - _num(baseline_tiers.get(tier, {}), "avg_rebus"),
                3,
            ),
        }
    return {
        "deltas": deltas,
        "tier_deltas": tier_deltas,
        "metrics": list(PROMPT_EVAL_METRICS),
    }
