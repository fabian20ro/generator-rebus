from rebus_generator.domain.guards.definition_guards import validate_definition_text
from rebus_generator.domain.short_word_clues import (
    forbidden_short_word_terms,
    short_word_prompt_context,
    valid_short_word_clues_for,
)


def test_seed_short_word_clues_pass_definition_guard():
    for word in ("IT", "IJE", "SEM"):
        clues = valid_short_word_clues_for(word)
        assert clues
        for clue in clues:
            assert validate_definition_text(word, clue.definition) is None


def test_short_word_prompt_context_contains_overlay_definition():
    context = short_word_prompt_context("SEM")
    assert "Trăsătură distinctivă" in context


def test_sem_forbidden_terms_include_semantic_family():
    terms = forbidden_short_word_terms("SEM")
    assert "sem" in terms
    assert "semantic" in terms
    assert "semem" in terms
    assert "semnificație" in terms
