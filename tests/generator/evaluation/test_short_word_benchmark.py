from rebus_generator.evaluation.short_word_benchmark import (
    SHORT_WORD_PROMPT_BENCHMARK_METRICS,
    SHORT_WORD_PROMPT_BENCHMARK_WORDS,
    build_short_word_prompt_benchmark_dataset,
)


def test_short_word_prompt_benchmark_dataset_shape():
    rows = build_short_word_prompt_benchmark_dataset(runs=5)
    assert len(rows) == 5 * len(SHORT_WORD_PROMPT_BENCHMARK_WORDS)
    assert {row["word"] for row in rows} == set(SHORT_WORD_PROMPT_BENCHMARK_WORDS)
    assert "verify_pass_rate" in SHORT_WORD_PROMPT_BENCHMARK_METRICS
    assert "guard_rejection_rate" in SHORT_WORD_PROMPT_BENCHMARK_METRICS


def test_short_word_prompt_benchmark_seeds_overlay_context():
    rows = build_short_word_prompt_benchmark_dataset(runs=1)
    sem = next(row for row in rows if row["word"] == "SEM")
    assert "Trăsătură distinctivă" in sem["dex_definitions"]
