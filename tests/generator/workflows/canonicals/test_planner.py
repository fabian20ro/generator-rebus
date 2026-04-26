import unittest
from unittest.mock import MagicMock
from typing import Protocol, List, Optional
from dataclasses import dataclass

from rebus_generator.workflows.canonicals.planner import (
    CanonicalPersistencePlanner,
    CanonicalInput,
    CanonicalResolverPort,
    CluePayloadBuilderPort,
)

class TestCanonicalPersistencePlanner(unittest.TestCase):
    def test_plan_delegates_to_ports(self):
        from rebus_generator.workflows.canonicals.planner import CanonicalPersistencePlanner, CanonicalInput
        
        resolver = MagicMock()
        builder = MagicMock()
        planner = CanonicalPersistencePlanner(resolver=resolver, builder=builder)
        
        inputs = [
            CanonicalInput(
                word_normalized="MUNTE",
                definition="Formă de relief",
                clue_id="clue-123",
                verified=True,
                rebus_score=5
            )
        ]
        
        resolver.resolve_definition.return_value = MagicMock(
            canonical_definition_id="canon-456",
            canonical_definition="Formă de relief înaltă",
            action="resolved"
        )
        builder.build_clue_definition_payload.return_value = {"canonical_id": "canon-456", "verified": True}
        
        plan = planner.plan(inputs)
        
        self.assertEqual(len(plan.clue_persistences), 1)
        persistence = plan.clue_persistences[0]
        self.assertEqual(persistence.clue_id, "clue-123")
        self.assertEqual(persistence.payload, {"canonical_id": "canon-456", "verified": True})
        
        resolver.resolve_definition.assert_called_once_with(
            word_normalized="MUNTE",
            word_original=None,
            definition="Formă de relief",
            word_type=None,
            verified=True,
            semantic_score=None,
            rebus_score=5,
            creativity_score=None,
        )
        builder.build_clue_definition_payload.assert_called_once_with(
            canonical_definition_id="canon-456",
            verified=True,
            verify_note=unittest.mock.ANY
        )

    def test_plan_collects_touched_canonical_ids(self):
        resolver = MagicMock()
        builder = MagicMock()
        planner = CanonicalPersistencePlanner(resolver=resolver, builder=builder)
        
        inputs = [
            CanonicalInput(word_normalized="A", definition="Def A"),
            CanonicalInput(word_normalized="B", definition="Def B"),
            CanonicalInput(word_normalized="C", definition="Def C"),
        ]
        
        resolver.resolve_definition.side_effect = [
            MagicMock(canonical_definition_id="canon-1", created_new=True),
            MagicMock(canonical_definition_id="canon-2", created_new=True),
            MagicMock(canonical_definition_id="canon-1", created_new=False), # Duplicate but not new
        ]
        
        plan = planner.plan(inputs)
        
        self.assertCountEqual(plan.touched_canonical_ids, ["canon-1", "canon-2"])

    def test_plan_repair_scenario(self):
        resolver = MagicMock()
        builder = MagicMock()
        planner = CanonicalPersistencePlanner(resolver=resolver, builder=builder)
        
        inputs = [
            CanonicalInput(
                word_normalized="MUNTE",
                definition="Formă de relief",
                clue_id="clue-1",
                verified=True,
                rebus_score=8,
                semantic_score=9,
                creativity_score=7,
                verify_note="Scor rebus: 8/10"
            )
        ]
        
        resolver.resolve_definition.return_value = MagicMock(
            canonical_definition_id="canon-1",
            canonical_definition="Formă de relief înaltă",
            action="reuse_exact"
        )
        
        planner.plan(inputs)
        
        builder.build_clue_definition_payload.assert_called_once_with(
            canonical_definition_id="canon-1",
            verified=True,
            verify_note="Scor rebus: 8/10"
        )

    def test_plan_skips_unchanged_clues(self):
        resolver = MagicMock()
        builder = MagicMock()
        planner = CanonicalPersistencePlanner(resolver=resolver, builder=builder)
        
        inputs = [
            # Changed clue
            CanonicalInput(
                word_normalized="A",
                definition="New",
                clue_id="clue-1",
                current_payload={"canonical_definition_id": "old-id"}
            ),
            # Unchanged clue
            CanonicalInput(
                word_normalized="B",
                definition="Same",
                clue_id="clue-2",
                current_payload={"canonical_definition_id": "same-id", "verified": False}
            )
        ]
        
        resolver.resolve_definition.side_effect = [
            MagicMock(canonical_definition_id="new-id", canonical_definition="New", action="promote"),
            MagicMock(canonical_definition_id="same-id", canonical_definition="Same", action="reuse")
        ]
        
        builder.build_clue_definition_payload.side_effect = [
            {"canonical_definition_id": "new-id"},
            {"canonical_definition_id": "same-id", "verified": False}
        ]
        
        plan = planner.plan(inputs)
        
        self.assertEqual(len(plan.clue_persistences), 1)
        self.assertEqual(plan.clue_persistences[0].clue_id, "clue-1")

    def test_plan_upload_scenario_inserts(self):
        resolver = MagicMock()
        builder = MagicMock()
        planner = CanonicalPersistencePlanner(resolver=resolver, builder=builder)
        
        inputs = [
            CanonicalInput(
                word_normalized="NEW",
                definition="Fresh",
                clue_id=None # No ID yet during initial generation/upload
            )
        ]
        
        resolver.resolve_definition.return_value = MagicMock(
            canonical_definition_id="canon-999",
            canonical_definition="Fresh",
            action="create_new"
        )
        
        plan = planner.plan(inputs)
        
        self.assertEqual(len(plan.clue_persistences), 1)
        self.assertIsNone(plan.clue_persistences[0].clue_id)
        self.assertEqual(plan.clue_persistences[0].canonical_definition_id, "canon-999")

    def test_plan_uses_bulk_resolver_when_available(self):
        class _BulkResolver:
            def __init__(self):
                self.inputs = None

            def resolve_definitions(self, inputs):
                self.inputs = list(inputs)
                return [
                    MagicMock(
                        canonical_definition_id=f"canon-{index}",
                        canonical_definition=inp.definition,
                        action="bulk",
                        created_new=False,
                    )
                    for index, inp in enumerate(inputs)
                ]

            def resolve_definition(self, **_kwargs):
                raise AssertionError("serial resolver should not run")

        resolver = _BulkResolver()
        builder = MagicMock()
        builder.build_clue_definition_payload.side_effect = lambda *, canonical_definition_id, **_kwargs: {
            "canonical_definition_id": canonical_definition_id,
        }
        planner = CanonicalPersistencePlanner(resolver=resolver, builder=builder)
        inputs = [
            CanonicalInput(word_normalized="A", definition="Def A"),
            CanonicalInput(word_normalized="B", definition="Def B"),
        ]

        plan = planner.plan(inputs)

        self.assertEqual(inputs, resolver.inputs)
        self.assertEqual(["canon-0", "canon-1"], [item.canonical_definition_id for item in plan.clue_persistences])

if __name__ == "__main__":
    unittest.main()
