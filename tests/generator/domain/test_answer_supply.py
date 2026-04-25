from rebus_generator.domain.answer_supply import (
    AnswerSupplyProvider,
    all_answer_supply_entries,
    answer_supply_entries_for,
    answer_supply_prompt_context,
    augment_word_rows_for_answer_supply,
    best_grid_entries_by_answer,
    validate_answer_supply,
    valid_answer_supply_entries_for,
)

EXPECTED_ASCII_CCTLD_CODES = set(
    "AC AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ "
    "BM BN BO BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW "
    "CX CY CZ DE DJ DK DM DO DZ EC EE EG ER ES ET EU FI FJ FK FM FO FR GA GD GE GF "
    "GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO "
    "IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS "
    "LT LU LV LY MA MC MD ME MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ "
    "NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW "
    "PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SU SV "
    "SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UK US UY UZ VA "
    "VC VE VG VI VN VU WF WS YE YT ZA ZM ZW"
    .split()
)


def test_answer_supply_registry_validates_approved_entries():
    assert validate_answer_supply() == []


def test_answer_supply_contains_supplied_ro_plate_codes_with_two_variants():
    entries = all_answer_supply_entries()
    plate_entries = [entry for entry in entries if entry.source == "curated_ro_plate"]
    assert len({entry.answer for entry in plate_entries}) == 41
    for answer in ("TM", "MM", "CJ", "PH"):
        variants = [entry for entry in plate_entries if entry.answer == answer]
        assert {entry.tone for entry in variants} == {"factual", "colloquial"}


def test_answer_supply_contains_current_ascii_cctld_codes():
    entries = all_answer_supply_entries()
    cc_entries = [entry for entry in entries if entry.source == "curated_cc_tld"]
    assert {entry.answer for entry in cc_entries} == EXPECTED_ASCII_CCTLD_CODES
    assert all(entry.enabled_for_grid for entry in cc_entries)
    assert all(entry.enabled_for_prompt for entry in cc_entries)
    assert {"AN", "BQ", "EH", "GB", "TP", "UM"}.isdisjoint(
        {entry.answer for entry in cc_entries}
    )


def test_answer_supply_generic_tld_entries_are_prompt_only():
    entries = answer_supply_entries_for("AI")
    factual = [entry for entry in entries if entry.source == "curated_cc_tld"]
    generic = [entry for entry in entries if entry.source == "curated:tld"]
    assert factual and generic
    assert factual[0].priority < generic[0].priority
    assert factual[0].enabled_for_grid
    assert not generic[0].enabled_for_grid
    assert generic[0].enabled_for_prompt


def test_answer_supply_priority_prefers_factual_plate_variant():
    best = best_grid_entries_by_answer()
    assert best["TM"].definition == "Indicativ auto pentru județul Timiș."
    assert best["AI"].source == "curated_cc_tld"
    assert best["IR"].source == "curated_cc_tld"


def test_answer_supply_prompt_context_labels_non_dex_source():
    context = answer_supply_prompt_context("TM")
    assert "[curated_ro_plate/factual]" in context
    assert "Indicativ auto pentru județul Timiș." in context


def test_answer_supply_prompt_context_includes_generic_tld_alternatives():
    context = answer_supply_prompt_context("AI")
    assert "[curated_cc_tld/factual]" in context
    assert "[curated:tld/generic]" in context
    assert "inteligență artificială" in context


def test_answer_supply_augments_missing_and_existing_words_for_rust():
    rows = [{"normalized": "IR", "original": "ir", "length": 2, "rarity_level": 5}]
    augmented = augment_word_rows_for_answer_supply(rows)
    by_word = {row["normalized"]: row for row in augmented}
    assert "TM" in by_word
    assert by_word["TM"]["source"] == "curated_ro_plate"
    assert by_word["TM"]["clue_support_score"] > by_word["IR"]["clue_support_score"]


def test_valid_answer_supply_entries_for_short_code():
    entries = valid_answer_supply_entries_for("TM")
    assert entries
    assert entries[0].tone == "factual"


def test_answer_supply_provider_merges_after_dex_context():
    context = AnswerSupplyProvider().get_definition_context("TM", "- Definiție DEX.")
    assert context.startswith("- Definiție DEX.")
    assert "Definiții extra non-DEX" in context
