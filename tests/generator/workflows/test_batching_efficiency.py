from unittest.mock import MagicMock, patch
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.workflows.retitle.generate import generate_creative_title_result
from rebus_generator.workflows.redefine.rewrite_rounds import rewrite_session_prepare_round
from rebus_generator.domain.pipeline_state import WorkingPuzzle, WorkingClue, ClueCandidateVersion
from rebus_generator.platform.llm.lm_runtime import LmRuntime

def test_retitle_generation_is_batched():
    # Setup puzzle with words
    words = ["WORD1", "WORD2"]
    definitions = ["Def 1", "Def 2"]
    client = MagicMock()
    
    # In generate.py, run_llm_workload is imported locally in Phase 1
    # We patch it at the module where it's used
    with patch("rebus_generator.platform.llm.llm_dispatch.run_llm_workload") as mock_workload, \
         patch("rebus_generator.workflows.retitle.generate.get_active_models") as mock_models, \
         patch("rebus_generator.workflows.retitle.generate.rate_title_creativity_batch") as mock_rate:
        
        mock_models.return_value = [PRIMARY_MODEL, SECONDARY_MODEL]
        mock_workload.return_value = MagicMock()
        mock_rate.return_value = {}
        
        runtime = LmRuntime(multi_model=True)
        
        generate_creative_title_result(
            words=words,
            definitions=definitions,
            client=client,
            runtime=runtime,
            multi_model=True
        )
        
        # Verify run_llm_workload was called for generation
        assert mock_workload.called
        
        # Check if title_generate was dispatched
        # (It might be called multiple times if there are other steps, but we check task_label)
        found_gen = False
        for call in mock_workload.call_args_list:
            if call.kwargs.get("task_label") == "title_generate":
                assert len(call.kwargs["items"]) == 2
                found_gen = True
        assert found_gen

def test_redefine_generation_is_batched():
    # Fix WorkingPuzzle init
    puzzle = WorkingPuzzle(title="Test", size=5, grid=[], horizontal_clues=[], vertical_clues=[])
    clue = WorkingClue(word_normalized="TEST", word_original="TEST", word_type="GEN", row_number=1)
    # Fix ClueCandidateVersion init
    clue.current = ClueCandidateVersion(definition="Old definition", round_index=0, source="test")
    clue.current.assessment.verified = False # Force it to need rewrite
    puzzle.horizontal_clues.append(clue)
    
    session = MagicMock()
    session.puzzle = puzzle
    session.round_index = 1
    session.rounds = 5
    session.current_model = PRIMARY_MODEL
    session.theme = "Test Theme"
    session.dex.get.return_value = ""
    session.clue_canon = None
    session.runtime = LmRuntime(multi_model=True)
    session.multi_model = True
    session.final_result = None
    session.initialized = True
    session.outcomes = {"TEST": MagicMock()}
    
    with patch("rebus_generator.platform.llm.llm_dispatch.run_llm_workload") as mock_workload, \
         patch("rebus_generator.workflows.redefine.rewrite_rounds._needs_rewrite") as mock_needs:
        
        mock_needs.return_value = True
        mock_workload.return_value = MagicMock()
        
        rewrite_session_prepare_round(session)
        
        # Verify run_llm_workload was called for batch generation
        found_gen = False
        for call in mock_workload.call_args_list:
            if call.kwargs.get("task_label") == "rewrite_generate":
                assert len(call.kwargs["items"]) == 1
                assert call.kwargs["items"][0].item_id == "TEST"
                found_gen = True
        assert found_gen
