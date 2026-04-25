import re

with open("tests/generator/cli/test_run_all.py", "r") as f:
    content = f.read()

# Add a mock for apply_scored_canonical_fallbacks because test_generate_define_initial_injects_metadata_into_working_state
# goes into _rescue_unresolved_generated_definitions which calls apply_scored_canonical_fallbacks.

content = content.replace(
    "@patch(\"rebus_generator.workflows.run_all.jobs.generate.generate_definition_for_working_clue\", return_value=\"Gaz din atmosferă\")\n    def test_generate_define_initial_injects_metadata_into_working_state",
    "@patch(\"rebus_generator.workflows.run_all.jobs.generate.apply_scored_canonical_fallbacks\")\n    @patch(\"rebus_generator.workflows.run_all.jobs.generate.generate_definition_for_working_clue\", return_value=\"Gaz din atmosferă\")\n    def test_generate_define_initial_injects_metadata_into_working_state"
)

content = content.replace(
    "def test_generate_define_initial_injects_metadata_into_working_state(self, _mock_define, _mock_dex, _mock_session):",
    "def test_generate_define_initial_injects_metadata_into_working_state(self, _mock_define, _mock_apply, _mock_dex, _mock_session):"
)


# There's another test failing in test_run_all.py:
# test_generate_define_finalize_rescues_placeholder_from_short_word_overlay
content = content.replace(
    "@patch(\"rebus_generator.workflows.run_all.jobs.generate.generate_definition_for_working_clue\", return_value=\"[Definiție negenerată]\")\n    def test_generate_define_finalize_rescues_placeholder_from_short_word_overlay",
    "@patch(\"rebus_generator.workflows.run_all.jobs.generate.apply_scored_canonical_fallbacks\")\n    @patch(\"rebus_generator.workflows.run_all.jobs.generate.generate_definition_for_working_clue\", return_value=\"[Definiție negenerată]\")\n    def test_generate_define_finalize_rescues_placeholder_from_short_word_overlay"
)

content = content.replace(
    "def test_generate_define_finalize_rescues_placeholder_from_short_word_overlay(self, _mock_define, _mock_dex, _mock_session):",
    "def test_generate_define_finalize_rescues_placeholder_from_short_word_overlay(self, _mock_define, _mock_apply, _mock_dex, _mock_session):"
)


with open("tests/generator/cli/test_run_all.py", "w") as f:
    f.write(content)
