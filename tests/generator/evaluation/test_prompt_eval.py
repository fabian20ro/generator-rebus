import json

from rebus_generator.evaluation.prompt_eval import (
    build_prompt_eval_dataset,
    compare_prompt_eval_results,
)


def test_prompt_eval_dataset_builder_creates_easy_medium_hard_buckets(tmp_path):
    metrics = {
        "word_metrics": [
            {"word": "BUN", "length": 3, "final_verified": True, "rebus_score": 9, "semantic_score": 9},
            {"word": "MED", "length": 3, "final_verified": True, "rebus_score": 6, "semantic_score": 7},
            {"word": "GREU", "length": 4, "final_verified": False, "was_blocker": True, "rebus_score": 2, "semantic_score": 4},
        ]
    }
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

    rows = build_prompt_eval_dataset([metrics_path], words_path=tmp_path / "missing.json", bucket_size=1)

    assert {row["tier"] for row in rows} == {"easy", "medium", "hard"}


def test_prompt_eval_comparison_reports_deltas():
    report = compare_prompt_eval_results(
        {"pass_rate": 0.5, "tier_balanced_pass_rate": 0.4, "avg_semantic": 7, "avg_rebus": 6, "tiers": {"hard": {"pass_rate": 0.2, "avg_rebus": 4}}},
        {"pass_rate": 0.6, "tier_balanced_pass_rate": 0.5, "avg_semantic": 8, "avg_rebus": 6.5, "tiers": {"hard": {"pass_rate": 0.3, "avg_rebus": 5}}},
    )
    assert report["deltas"]["pass_rate"] == 0.1
    assert report["tier_deltas"]["hard"]["avg_rebus"] == 1
