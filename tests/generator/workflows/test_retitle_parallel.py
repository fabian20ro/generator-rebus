import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.workflows.run_all.jobs.retitle import RetitleJobState
from rebus_generator.workflows.run_all import SupervisorWorkItem


class RetitleParallelTests(unittest.TestCase):
    def test_yields_multiple_rating_units_in_multi_model(self):
        # Setup job in rate_primary stage with a pending title
        item = SupervisorWorkItem(
            item_id="test_job",
            topic="retitle",
            task_kind="retitle",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
            payload={"puzzle_row": {"id": "123", "title": "Old Title"}}
        )
        job = RetitleJobState(item)
        job.stage = "rate_primary"
        job.pending_title = "New Title"
        job.words_list = ["word1", "word2"]
        job.status = "active"
        
        ctx = SimpleNamespace(
            multi_model=True,
            rate_client=MagicMock()
        )
        
        # Act
        units = job.plan_ready_units(ctx)
        
        # Assert: Should yield TWO units (one for PRIMARY, one for SECONDARY)
        self.assertEqual(2, len(units))
        model_ids = {u.model_id for u in units}
        self.assertIn(PRIMARY_MODEL.model_id, model_ids)
        self.assertIn(SECONDARY_MODEL.model_id, model_ids)
        
    def test_yields_only_remaining_rating_units(self):
        item = SupervisorWorkItem(
            item_id="test_job",
            topic="retitle",
            task_kind="retitle",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
            payload={"puzzle_row": {"id": "123", "title": "Old Title"}}
        )
        job = RetitleJobState(item)
        job.stage = "rate_primary"
        job.pending_title = "New Title"
        job.words_list = ["word1", "word2"]
        job.status = "active"
        
        # Primary has already voted
        job.pending_rating_votes[PRIMARY_MODEL.model_id] = (8, "good")
        
        ctx = SimpleNamespace(
            multi_model=True,
            rate_client=MagicMock()
        )
        
        # Act
        units = job.plan_ready_units(ctx)
        
        # Assert: Should yield only ONE unit for SECONDARY
        self.assertEqual(1, len(units))
        self.assertEqual(SECONDARY_MODEL.model_id, units[0].model_id)

    def test_yields_multiple_resolve_old_score_units(self):
        item = SupervisorWorkItem(
            item_id="test_job",
            topic="retitle",
            task_kind="retitle",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
            payload={"puzzle_row": {"id": "123", "title": "Old Title"}}
        )
        job = RetitleJobState(item)
        job.stage = "resolve_old_score"
        job.words_list = ["word1", "word2"]
        job.status = "active"
        
        ctx = SimpleNamespace(
            multi_model=True,
            rate_client=MagicMock()
        )
        
        # Act
        units = job.plan_ready_units(ctx)
        
        # Assert: Should yield TWO units
        self.assertEqual(2, len(units))
        model_ids = {u.model_id for u in units}
        self.assertIn(PRIMARY_MODEL.model_id, model_ids)
        self.assertIn(SECONDARY_MODEL.model_id, model_ids)

if __name__ == "__main__":
    unittest.main()
