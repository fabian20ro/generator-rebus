import unittest
from unittest.mock import MagicMock
from typing import Protocol, List, Optional
from dataclasses import dataclass

from rebus_generator.workflows.canonicals.planner import (
    CanonicalPersistencePlanner,
    CanonicalInput,
    CanonicalResolverPort,
    CluePayloadBuilderPort,
    ExistingPuzzleClueInput,
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

    def test_plan_new_puzzle_clues_uses_bulk_without_temp_ids(self):
        class _BulkResolver:
            def __init__(self):
                self.calls = []

            def resolve_definitions(self, inputs):
                self.calls.append(list(inputs))
                return [
                    MagicMock(
                        canonical_definition_id="canon-1",
                        canonical_definition="Def canon",
                        action="create_new",
                        created_new=True,
                    )
                ]

            def resolve_definition(self, **_kwargs):
                raise AssertionError("serial resolver should not run")

        resolver = _BulkResolver()
        builder = MagicMock()
        builder.build_clue_definition_payload.return_value = {
            "canonical_definition_id": "canon-1",
            "definition": "Def canon",
            "verified": False,
        }
        planner = CanonicalPersistencePlanner(resolver=resolver, builder=builder)

        plan = planner.plan_new_puzzle_clues([
            {
                "word_normalized": "NOU",
                "word_original": "nou",
                "word_type": "adj",
                "clue_number": 1,
                "_candidate_definition": "Def candidat",
            }
        ])

        self.assertEqual(1, len(resolver.calls))
        self.assertIsNone(resolver.calls[0][0].clue_id)
        self.assertEqual(["canon-1"], plan.touched_canonical_ids)
        self.assertEqual("canon-1", plan.clues[0].record["canonical_definition_id"])
        self.assertNotIn("_candidate_definition", plan.clues[0].record)
        self.assertEqual("create_new", plan.clues[0].canonical_event.action)
        self.assertEqual("Def candidat", plan.clues[0].canonical_event.candidate_definition)

    def test_plan_existing_puzzle_clues_builds_current_diff_and_merges_touched_ids(self):
        class _BulkResolver:
            def __init__(self):
                self.calls = []

            def resolve_definitions(self, inputs):
                self.calls.append(list(inputs))
                return [
                    MagicMock(
                        canonical_definition_id="new-id",
                        canonical_definition="Def nou",
                        action="promote_new",
                        created_new=True,
                    ),
                    MagicMock(
                        canonical_definition_id="same-id",
                        canonical_definition="Def vechi",
                        action="reuse_exact",
                        created_new=False,
                    ),
                ]

            def resolve_definition(self, **_kwargs):
                raise AssertionError("serial resolver should not run")

        resolver = _BulkResolver()
        builder = MagicMock()
        builder.build_clue_definition_payload.side_effect = [
            {"canonical_definition_id": "new-id", "verified": True, "verify_note": "ok"},
            {"canonical_definition_id": "same-id", "verified": False, "verify_note": ""},
        ]
        planner = CanonicalPersistencePlanner(resolver=resolver, builder=builder)

        changed_row = {
            "id": "row-1",
            "definition": "Def veche",
            "canonical_definition_id": "old-id",
            "verified": False,
            "verify_note": "",
        }
        unchanged_row = {
            "id": "row-2",
            "definition": "Def vechi",
            "canonical_definition_id": "same-id",
            "verified": False,
            "verify_note": "",
        }
        plan = planner.plan_existing_puzzle_clues(
            [
                ExistingPuzzleClueInput(
                    row=changed_row,
                    word_normalized="A",
                    definition="Def nou",
                    verified=True,
                    verify_note="ok",
                    context={"key": ("H", 0, 0)},
                ),
                ExistingPuzzleClueInput(
                    row=unchanged_row,
                    word_normalized="B",
                    definition="Def vechi",
                    verified=False,
                    verify_note="",
                    context={"key": ("V", 0, 0)},
                ),
            ],
            touched_canonical_ids=["prior-id"],
        )

        self.assertEqual(1, len(resolver.calls))
        self.assertEqual(1, len(plan.clues))
        self.assertEqual("row-1", plan.clues[0].persistence.clue_id)
        self.assertEqual({"key": ("H", 0, 0)}, plan.clues[0].context)
        self.assertEqual("promote_new", plan.clues[0].canonical_event.action)
        self.assertEqual(["new-id", "prior-id"], plan.touched_canonical_ids)

if __name__ == "__main__":
    unittest.main()
