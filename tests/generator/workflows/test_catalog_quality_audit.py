import json

from rebus_generator.workflows.generate.catalog_audit import audit_catalog, main


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
