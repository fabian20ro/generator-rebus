from rebus_generator.evaluation.playful_reduction_miner import mine_playful_short_candidates


def test_playful_reduction_miner_proposes_ir_from_iesire():
    candidates = mine_playful_short_candidates(
        [{"normalized": "IESIRE", "original": "ieșire", "length": 6}],
        max_candidates_per_word=3,
    )
    ir = next(candidate for candidate in candidates if candidate.answer == "IR")
    assert ir.source_word == "IESIRE"
    assert ir.proposed_definition == "Ieșire!"
    assert ir.rejection_reasons


def test_playful_reduction_miner_candidates_are_review_only():
    candidates = mine_playful_short_candidates(
        [{"normalized": "IESIRE", "original": "ieșire", "length": 6}],
        max_candidates_per_word=1,
    )
    assert candidates
    assert all(candidate.confidence > 0 for candidate in candidates)
