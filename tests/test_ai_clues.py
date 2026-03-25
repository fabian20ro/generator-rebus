import json
import unittest
from types import SimpleNamespace

from generator.core.ai_clues import (
    DefinitionRating,
    _clean_response,
    _extract_usage_suffix_from_dex,
    _normalize_definition_usage_suffix,
    _build_verify_prompt,
    _definition_describes_english_meaning,
    _build_generate_prompt,
    _build_rate_prompt,
    _build_rewrite_prompt,
    compute_rebus_score,
    generate_definition,
    rate_definition,
    rewrite_definition,
    verify_definition_candidates,
)
from generator.core.model_manager import PRIMARY_MODEL
from generator.prompts.loader import load_system_prompt


class _RecordingClient:
    def __init__(self, responses):
        self.prompts = []
        self.calls = []
        queue = list(responses)

        def _create(**kwargs):
            self.calls.append(kwargs)
            self.prompts.append(kwargs["messages"][-1]["content"])
            content = queue.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=_create
            )
        )


class AiCluesTests(unittest.TestCase):
    def test_rewrite_prompt_omits_bad_example_by_default(self):
        client = _RecordingClient(["Definiție mai bună"])

        rewrite_definition(
            client,
            word="ARACI",
            original="araci",
            theme="",
            previous_definition="Prezintă un fapt în mod clar și convingător.",
            wrong_guess="",
            rating_feedback="Prea vagă pentru răspunsul exact.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertNotIn("Exemplu de definiție rea de evitat", client.prompts[0])

    def test_rewrite_prompt_includes_bad_example_when_provided(self):
        client = _RecordingClient(["Definiție mai bună"])

        rewrite_definition(
            client,
            word="ARACI",
            original="araci",
            theme="",
            previous_definition="Prezintă un fapt în mod clar și convingător.",
            wrong_guess="ALTUL",
            rating_feedback="Duce la alt răspuns.",
            bad_example_definition="Prezintă un fapt în mod clar și convingător.",
            bad_example_reason="Duce la alt răspuns: ALTUL.",
            model=PRIMARY_MODEL.model_id,
        )

        prompt = client.prompts[0]
        self.assertIn("Exemplu de definiție rea de evitat", prompt)
        self.assertIn("Duce la alt răspuns: ALTUL.", prompt)

    def test_rate_definition_does_not_penalize_rarity_only_feedback(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 9,
                "guessability_score": 4,
                "creativity_score": 6,
                "feedback": "Răspunsul este rar și mai puțin comun.",
            })
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(9, rating.semantic_score)
        self.assertEqual(4, rating.guessability_score)
        self.assertTrue(rating.rarity_only_override)

    def test_rate_definition_keeps_legitimate_guessability_penalty(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 9,
                "guessability_score": 4,
                "creativity_score": 5,
                "feedback": "Definiția duce la un sinonim mai uzual.",
            })
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(4, rating.guessability_score)
        self.assertIn("sinonim", rating.feedback)

    def test_rate_definition_retries_with_stricter_json_instruction(self):
        client = _RecordingClient([
            "semantic_score: 9, guessability_score: 4",
            json.dumps({
                "semantic_score": 9,
                "guessability_score": 6,
                "creativity_score": 5,
                "feedback": "Definiția este corectă.",
            }),
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertIsNotNone(rating)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("strict cu un singur obiect JSON valid", client.prompts[1])


    def test_clean_response_strips_model_tokens(self):
        self.assertEqual("ZI", _clean_response("<|channel|>ZI"))
        self.assertEqual("", _clean_response("<|channel|>"))
        self.assertEqual("AER", _clean_response("<|endoftext|>AER"))
        self.assertEqual("CASA", _clean_response("CASA<|im_end|>"))

    def test_clean_response_takes_first_line(self):
        self.assertEqual("CASA", _clean_response("CASA\naltceva pe linia doi"))

    def test_clean_response_strips_markdown_emphasis(self):
        self.assertEqual("CASA", _clean_response("**CASA**"))
        self.assertEqual("AER", _clean_response("`AER`"))

    def test_clean_response_strips_definitia_noua_prefix(self):
        self.assertEqual(
            "Material de bază în grădinărit, rezultat din descompunerea vegetală.",
            _clean_response("Definiția nouă:** Material de bază în grădinărit, rezultat din descompunerea vegetală."),
        )

    def test_definition_describes_english_meaning_detects_engleza(self):
        self.assertTrue(_definition_describes_english_meaning("AN", "Articol nehotărât în limba engleză"))

    def test_definition_describes_english_meaning_detects_patterns(self):
        self.assertTrue(_definition_describes_english_meaning("AN", "Articol nehotărât"))
        self.assertTrue(_definition_describes_english_meaning("OF", "Prepoziție de posesie"))
        self.assertTrue(_definition_describes_english_meaning("IN", "Prepoziție care indică poziția"))
        self.assertTrue(_definition_describes_english_meaning("HAT", "O pălărie mare"))
        self.assertTrue(_definition_describes_english_meaning("NAT", "Traducere a adreselor IP"))

    def test_definition_describes_english_meaning_passes_romanian(self):
        self.assertFalse(_definition_describes_english_meaning("AN", "Unitate de timp egală cu 12 luni"))
        self.assertFalse(_definition_describes_english_meaning("OF", "Interjecție de durere"))
        self.assertFalse(_definition_describes_english_meaning("IN", "Plantă textilă cu flori albastre"))
        self.assertFalse(_definition_describes_english_meaning("CASA", "Locuință"))

    def test_generate_prompt_includes_homograph_hint(self):
        prompt = _build_generate_prompt("an", "AN", 2)
        self.assertIn("ATENȚIE", prompt)
        self.assertIn("unitate de timp", prompt)
        self.assertIn("NU defini ca și cum ar fi un cuvânt englezesc", prompt)

    def test_generate_prompt_no_hint_for_normal_word(self):
        prompt = _build_generate_prompt("aer", "AER", 3)
        self.assertNotIn("ATENȚIE", prompt)
        self.assertIn("nu un singur cuvânt izolat", prompt)

    def test_extract_usage_suffix_from_dex_prefers_highest_precedence(self):
        dex = "- Pronume personal, în limbaj arhaic.\n- Formă învechită."
        self.assertEqual("(arh.)", _extract_usage_suffix_from_dex(dex))

    def test_extract_usage_suffix_from_dex_returns_none_when_no_explicit_label(self):
        dex = "- Țesut dur al scheletului."
        self.assertIsNone(_extract_usage_suffix_from_dex(dex))

    def test_normalize_definition_usage_suffix_appends_required_suffix(self):
        self.assertEqual(
            "Pronume personal de persoana I singular (arh.)",
            _normalize_definition_usage_suffix(
                "Pronume personal de persoana I singular",
                "(arh.)",
            ),
        )

    def test_normalize_definition_usage_suffix_replaces_existing_suffix(self):
        self.assertEqual(
            "Pronume personal de persoana I singular (arh.)",
            _normalize_definition_usage_suffix(
                "Pronume personal de persoana I singular (reg.)",
                "(arh.)",
            ),
        )

    def test_normalize_definition_usage_suffix_removes_gratuitous_suffix(self):
        self.assertEqual(
            "Locuință obișnuită",
            _normalize_definition_usage_suffix("Locuință obișnuită (reg.)", None),
        )

    def test_rate_english_meaning_forces_low_scores(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 10,
                "guessability_score": 10,
                "creativity_score": 10,
                "feedback": "Definiția este perfectă.",
            })
        ])

        rating = rate_definition(
            client,
            word="AN",
            original="an",
            definition="Articol nehotărât în limba engleză",
            answer_length=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(1, rating.semantic_score)
        self.assertEqual(1, rating.guessability_score)


    def test_generate_prompt_includes_forbidden_stems(self):
        prompt = _build_generate_prompt("tibetan", "TIBETAN", 7)
        self.assertIn("TIBET", prompt)
        self.assertIn("interzise", prompt)

    def test_generate_prompt_forbidden_for_short_word(self):
        prompt = _build_generate_prompt("at", "AT", 2)
        self.assertNotIn("interzise", prompt)

    def test_compute_rebus_score_formula(self):
        self.assertEqual(7, compute_rebus_score(6, 10))
        self.assertEqual(5, compute_rebus_score(5, 5))
        self.assertEqual(8, compute_rebus_score(8, 8))

    def test_rate_definition_returns_creativity_score(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 8,
                "guessability_score": 7,
                "creativity_score": 9,
                "feedback": "surpriză de domeniu",
            })
        ])

        rating = rate_definition(
            client,
            word="RIAL",
            original="rial",
            definition="Se plătește la șah",
            answer_length=4,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(9, rating.creativity_score)
        self.assertEqual(8, rating.semantic_score)
        self.assertEqual(7, rating.guessability_score)


    def test_generate_prompt_includes_word_type(self):
        prompt = _build_generate_prompt("lovi", "LOVI", 4, word_type="V")
        self.assertIn("Categorie gramaticală: verb", prompt)

    def test_generate_prompt_includes_usage_label_instruction(self):
        prompt = _build_generate_prompt(
            "az",
            "AZ",
            2,
            dex_definitions="- Pronume personal, în limbaj arhaic.",
        )
        self.assertIn("Marcaj DEX explicit: (arh.)", prompt)
        self.assertIn("încheie definiția exact cu (arh.)", prompt)

    def test_generate_prompt_no_word_type_for_empty(self):
        prompt = _build_generate_prompt("casă", "CASA", 4, word_type="")
        self.assertNotIn("Categorie gramaticală", prompt)

    def test_generate_prompt_no_word_type_for_unknown(self):
        prompt = _build_generate_prompt("casă", "CASA", 4, word_type="X")
        self.assertNotIn("Categorie gramaticală", prompt)

    def test_rewrite_prompt_includes_word_type(self):
        prompt = _build_rewrite_prompt(
            "lovi", "LOVI", "A atinge cu forță", "[niciun feedback]", "", word_type="V",
        )
        self.assertIn("Categorie gramaticală: verb", prompt)

    def test_rewrite_prompt_includes_usage_label_instruction(self):
        prompt = _build_rewrite_prompt(
            "az",
            "AZ",
            "Pronume personal",
            "[niciun feedback]",
            "",
            dex_definitions="- Pronume personal, în limbaj arhaic.",
        )
        self.assertIn("Marcaj DEX explicit: (arh.)", prompt)
        self.assertIn("Păstrează sau restaurează", prompt)

    def test_build_verify_prompt_includes_candidate_count(self):
        prompt = _build_verify_prompt("Țesut dur al scheletului", 2, max_guesses=3)
        self.assertIn("maximum 3", prompt)
        self.assertIn("Răspunsuri", prompt)

    def test_build_verify_prompt_includes_usage_label_context(self):
        prompt = _build_verify_prompt("Pronume personal de persoana I singular (arh.)", 2, max_guesses=3)
        self.assertIn("Marcaj de uz explicit în definiție: (arh.)", prompt)

    def test_verify_definition_candidates_parses_numbered_lines(self):
        client = _RecordingClient(["1. BAR\n2. TUN\n3. ARC"])

        result = verify_definition_candidates(
            client,
            "Recipient mare pentru vin",
            answer_length=3,
            max_guesses=3,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(["BAR", "TUN", "ARC"], result.candidates)

    def test_generate_definition_passes_explicit_model(self):
        client = _RecordingClient(["Locuință obișnuită"])

        generate_definition(
            client,
            word="CASA",
            original="casă",
            theme="",
            model="openai/gpt-oss-20b",
        )

        self.assertEqual("openai/gpt-oss-20b", client.calls[0]["model"])

    def test_verify_definition_candidates_passes_explicit_model(self):
        client = _RecordingClient(["1. BAR\n2. TUN"])

        verify_definition_candidates(
            client,
            "Recipient mare pentru vin",
            answer_length=3,
            model="openai/gpt-oss-20b",
        )

        self.assertEqual("openai/gpt-oss-20b", client.calls[0]["model"])

    def test_rate_definition_passes_explicit_model(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 8,
                "guessability_score": 7,
                "creativity_score": 6,
                "feedback": "Corect.",
            })
        ])

        rate_definition(
            client,
            word="CASA",
            original="casă",
            definition="Locuință",
            answer_length=4,
            model="openai/gpt-oss-20b",
        )

        self.assertEqual("openai/gpt-oss-20b", client.calls[0]["model"])

    def test_verify_definition_candidates_filters_wrong_lengths(self):
        client = _RecordingClient(["1. BARIL\n2. TUN\n3. ARC"])

        result = verify_definition_candidates(
            client,
            "Recipient mare pentru vin",
            answer_length=3,
            max_guesses=3,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(["TUN", "ARC"], result.candidates)

    def test_rate_prompt_includes_word_type(self):
        prompt = _build_rate_prompt("casă", "CASA", "Locuință", 4, word_type="N")
        self.assertIn("Categorie gramaticală: substantiv", prompt)

    def test_rate_prompt_includes_usage_label_and_bias_context(self):
        prompt = _build_rate_prompt(
            "az",
            "AZ",
            "Pronume personal de persoana I singular (arh.)",
            2,
            dex_definitions="- Pronume personal, în limbaj arhaic.",
        )
        self.assertIn("Marcaj DEX permis: (arh.)", prompt)
        self.assertIn("Eticheta corespunde explicit unui sens DEX marcat", prompt)

    def test_verify_prompt_includes_word_type(self):
        prompt = _build_verify_prompt("Locuință", 4, word_type="N")
        self.assertIn("Categorie gramaticală: substantiv", prompt)
        self.assertIn("Definiție: Locuință", prompt)

    def test_generate_definition_appends_required_suffix(self):
        client = _RecordingClient(["Pronume personal de persoana I singular"])

        definition = generate_definition(
            client,
            word="AZ",
            original="az",
            theme="",
            dex_definitions="- Pronume personal, în limbaj arhaic.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Pronume personal de persoana I singular (arh.)", definition)

    def test_generate_definition_removes_gratuitous_suffix_when_dex_has_no_label(self):
        client = _RecordingClient(["Locuință obișnuită (reg.)"])

        definition = generate_definition(
            client,
            word="CASA",
            original="casă",
            theme="",
            dex_definitions="- Locuință.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Locuință obișnuită", definition)

    def test_rewrite_definition_restores_required_suffix(self):
        client = _RecordingClient(["Pronume personal de persoana I singular"])

        definition = rewrite_definition(
            client,
            word="AZ",
            original="az",
            theme="",
            previous_definition="Pronume personal",
            wrong_guess="EU",
            dex_definitions="- Pronume personal, în limbaj arhaic.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Pronume personal de persoana I singular (arh.)", definition)

    def test_system_prompts_include_usage_label_examples(self):
        definition_prompt = load_system_prompt("definition")
        verify_prompt = load_system_prompt("verify")
        rate_prompt = load_system_prompt("rate")

        self.assertIn("AZ -> Pronume personal de persoana I singular (arh.)", definition_prompt)
        self.assertIn("Definiție: Pronume personal de persoana I singular (arh.)", verify_prompt)
        self.assertIn(
            "`Pronume personal de persoana I singular (arh.)` pentru un răspuns rar ca `AZ`",
            rate_prompt,
        )
        self.assertIn(
            "`Locuință (reg.)` pentru un răspuns comun ca `CASĂ`",
            rate_prompt,
        )

    def test_rate_definition_returns_none_on_unparseable_response(self):
        """When JSON parsing fails, rate_definition returns None (not 5/5/5)."""
        client = _RecordingClient([
            "This is not JSON at all",
            "Still not JSON",
        ])

        rating = rate_definition(
            client,
            word="CASA",
            original="casă",
            definition="Locuință",
            answer_length=4,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertIsNone(rating)

    def test_rate_definition_extracts_json_from_markdown_fence(self):
        """eurollm-22b wraps JSON in ```json ... ``` — must still parse."""
        fenced_json = (
            '```json\n'
            '{"semantic_score": 9, "guessability_score": 8, '
            '"creativity_score": 7, "feedback": "Corect"}\n'
            '```'
        )
        client = _RecordingClient([fenced_json])

        rating = rate_definition(
            client,
            word="CASA",
            original="casă",
            definition="Locuință",
            answer_length=4,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertIsNotNone(rating)
        self.assertEqual(9, rating.semantic_score)
        self.assertEqual(8, rating.guessability_score)
        self.assertEqual(7, rating.creativity_score)

    def test_rewrite_prompt_includes_failure_history(self):
        """Failure history should appear as numbered list in the prompt."""
        client = _RecordingClient(["Parte a conjugării verbale"])

        rewrite_definition(
            client,
            word="AR",
            original="ar",
            theme="",
            previous_definition="Verb auxiliar",
            wrong_guess="DE",
            failure_history=[
                ("Condiționare", ["DE", "LA"]),
                ("Auxiliar verbal", ["AL"]),
                ("Verb auxiliar", ["DE", "A"]),
            ],
            model=PRIMARY_MODEL.model_id,
        )

        prompt = client.prompts[0]
        self.assertIn("Încercări anterioare eșuate", prompt)
        self.assertIn("'Condiționare' → propus: DE, LA", prompt)
        self.assertIn("'Auxiliar verbal' → propus: AL", prompt)

    def test_rewrite_prompt_prefers_all_wrong_guesses(self):
        client = _RecordingClient(["Parte a conjugării verbale"])

        rewrite_definition(
            client,
            word="AR",
            original="ar",
            theme="",
            previous_definition="Verb auxiliar",
            wrong_guess="DE",
            wrong_guesses=["DE", "LA", "PE"],
            model=PRIMARY_MODEL.model_id,
        )

        prompt = client.prompts[0]
        self.assertIn("Rezolvitorul a propus: DE, LA, PE", prompt)

    def test_rewrite_prompt_omits_history_when_none(self):
        """No history section when failure_history is None."""
        client = _RecordingClient(["Definiție mai bună"])

        rewrite_definition(
            client,
            word="CASA",
            original="casă",
            theme="",
            previous_definition="Locuință",
            wrong_guess="",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertNotIn("Încercări anterioare eșuate", client.prompts[0])

    def test_generate_definition_retries_after_single_word_gloss(self):
        client = _RecordingClient([
            "Pământ",
            "Pământ fertil, brun-închis și afânat.",
        ])

        definition = generate_definition(
            client,
            word="MUL",
            original="mul",
            theme="",
            retries=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Pământ fertil, brun-închis și afânat.", definition)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("single-word gloss", client.prompts[1])
        self.assertIn("minimum 2 cuvinte", client.prompts[1])

    def test_rewrite_definition_retries_after_single_word_gloss(self):
        client = _RecordingClient([
            "Pământ",
            "Pământ fertil, brun-închis și afânat.",
        ])

        definition = rewrite_definition(
            client,
            word="MUL",
            original="mul",
            theme="",
            previous_definition="Pământ",
            wrong_guess="ARG",
            retries=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Pământ fertil, brun-închis și afânat.", definition)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("single-word gloss", client.prompts[1])
        self.assertIn("minimum 2 cuvinte", client.prompts[1])

    def test_generate_definition_retries_after_dangling_ending(self):
        client = _RecordingClient([
            "Împușcarea unui câine asupra unei",
            "Îndemnarea unui câine să atace o persoană.",
        ])

        definition = generate_definition(
            client,
            word="AMUTARE",
            original="amuțare",
            theme="",
            retries=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Îndemnarea unui câine să atace o persoană.", definition)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("dangling ending", client.prompts[1])

    def test_rewrite_definition_retries_after_dangling_ending(self):
        client = _RecordingClient([
            "Împușcarea unui câine asupra unei",
            "Îndemnarea unui câine să atace o persoană.",
        ])

        definition = rewrite_definition(
            client,
            word="AMUTARE",
            original="amuțare",
            theme="",
            previous_definition="Împușcarea unui câine.",
            wrong_guess="ÎMPUSCARE",
            retries=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Îndemnarea unui câine să atace o persoană.", definition)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("dangling ending", client.prompts[1])


if __name__ == "__main__":
    unittest.main()
