import json

from rebus_generator.workflows.generate.catalog_audit import audit_catalog, main
from rebus_generator.workflows.generate.catalog_recovery import (
    build_recovery_plan,
    main as recovery_main,
)


def test_catalog_audit_reports_policy_failures_and_size_coverage():
    report = audit_catalog(
        [
            {
                "id": "good",
                "title": "Bun",
                "grid_size": 7,
                "pass_rate": 0.75,
                "rebus_score_min": 6,
            },
            {
                "id": "weak",
                "title": "Slab",
                "grid_size": 7,
                "pass_rate": 0.25,
                "rebus_score_min": 4,
            },
        ],
        min_pass_rate=0.5,
        min_rebus_score=5,
    )

    assert report["totals"] == {"puzzles": 2, "passing": 1, "failing": 1}
    assert report["reasons"] == {"low_min_rebus_score": 1, "low_pass_rate": 1}
    assert report["by_size"] == {
        "7": {"puzzles": 2, "passing": 1, "failing": 1},
    }
    assert report["failures"] == [
        {
            "id": "weak",
            "title": "Slab",
            "grid_size": 7,
            "pass_rate": 0.25,
            "min_rebus_score": 4,
            "reasons": ["low_min_rebus_score", "low_pass_rate"],
        }
    ]


def test_catalog_audit_reads_legacy_minimum_score_from_description():
    report = audit_catalog(
        [
            {
                "id": "legacy",
                "title": "Legacy",
                "grid_size": 8,
                "pass_rate": 0.75,
                "description": "Scor rebus: 6/10 | Verificate: 15/20",
            }
        ],
        min_pass_rate=0.5,
        min_rebus_score=5,
    )

    assert report["totals"]["passing"] == 1
    assert report["failures"] == []


def test_catalog_audit_cli_writes_deterministic_json_from_snapshot(tmp_path):
    source = tmp_path / "catalog.json"
    output = tmp_path / "report.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "weak",
                    "title": "Slab",
                    "grid_size": 9,
                    "pass_rate": 0.4,
                    "rebus_score_min": 6,
                }
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["--input", str(source), "--output", str(output)])

    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["policy"] == {"min_pass_rate": 0.5, "min_rebus_score": 5}
    assert report["totals"] == {"puzzles": 1, "passing": 0, "failing": 1}


def test_catalog_audit_distinguishes_missing_metrics_from_low_metrics():
    report = audit_catalog(
        [{"id": "unknown", "title": "Necunoscut", "grid_size": 10}],
        min_pass_rate=0.5,
        min_rebus_score=5,
    )

    assert report["reasons"] == {
        "missing_min_rebus_score": 1,
        "missing_pass_rate": 1,
    }


def test_catalog_audit_reads_legacy_pass_rate_from_description():
    report = audit_catalog(
        [
            {
                "id": "legacy-rate",
                "title": "Legacy",
                "grid_size": 8,
                "rebus_score_min": 6,
                "description": "Scor rebus: 6/10 | Verificate: 15/20",
            }
        ],
        min_pass_rate=0.5,
        min_rebus_score=5,
    )

    assert report["totals"]["passing"] == 1
    assert report["failures"] == []


def test_recovery_plan_selects_best_puzzles_and_reports_size_deficits():
    plan = build_recovery_plan(
        [
            {"id": "seven-lower", "grid_size": 7, "pass_rate": 0.75, "rebus_score_min": 6},
            {"id": "seven-best", "grid_size": 7, "pass_rate": 0.9, "rebus_score_min": 7},
            {"id": "eleven-near", "grid_size": 11, "pass_rate": 0.45, "rebus_score_min": 5},
            {"id": "eleven-far", "grid_size": 11, "pass_rate": 0.1, "rebus_score_min": 2},
        ],
        sizes=(7, 11),
        target_per_size=2,
        min_pass_rate=0.5,
        min_rebus_score=5,
    )

    assert plan["rollout_ready"] is False
    assert plan["selected_ids"] == ["seven-best", "seven-lower"]
    assert plan["deficits"] == {"11": 2}
    assert plan["by_size"]["7"] == {
        "passing": 2,
        "selected_ids": ["seven-best", "seven-lower"],
        "deficit": 0,
        "repair_queue": [],
    }
    assert plan["by_size"]["11"] == {
        "passing": 0,
        "selected_ids": [],
        "deficit": 2,
        "repair_queue": ["eleven-near", "eleven-far"],
    }


def test_recovery_plan_is_ready_only_when_every_requested_size_is_covered():
    plan = build_recovery_plan(
        [
            {"id": "seven", "grid_size": 7, "pass_rate": 0.5, "rebus_score_min": 5},
            {"id": "eleven", "grid_size": 11, "pass_rate": 0.6, "rebus_score_min": 6},
        ],
        sizes=(7, 11),
        target_per_size=1,
        min_pass_rate=0.5,
        min_rebus_score=5,
    )

    assert plan["rollout_ready"] is True
    assert plan["deficits"] == {}


def test_recovery_cli_writes_plan_and_returns_two_when_rollout_is_not_ready(tmp_path):
    source = tmp_path / "catalog.json"
    output = tmp_path / "recovery.json"
    source.write_text(
        json.dumps(
            [{"id": "seven", "grid_size": 7, "pass_rate": 0.5, "rebus_score_min": 5}]
        ),
        encoding="utf-8",
    )

    exit_code = recovery_main(
        [
            "--input", str(source),
            "--output", str(output),
            "--sizes", "7,11",
            "--target-per-size", "1",
            "--require-ready",
        ]
    )

    assert exit_code == 2
    assert json.loads(output.read_text(encoding="utf-8"))["deficits"] == {"11": 1}
