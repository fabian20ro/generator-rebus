#!/usr/bin/env python3
"""Safe-stop, recoverable, policy-driven prompt autoresearch supervisor."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from generator.assessment.benchmark_policy import (
    CAMPAIGN_STOP_STALE_FAMILIES,
    CONTROL_WORD_WATCH,
    WORKING_BASELINE_DESCRIPTION,
)
from generator.core.runtime_logging import audit, install_process_logging, path_timestamp
from scripts import run_experiments as runner


DEFAULT_STATE_DIR = PROJECT_ROOT / "build" / "prompt_research"
DEFAULT_DESCRIPTION_PREFIX = "autoresearch/"
DEFAULT_ASSESSMENT_LOGS_DIRNAME = "assessment_logs"
STATE_FILENAME = "state.json"
FAMILIES_FILENAME = "families.json"
INCUMBENT_FILENAME = "incumbent.json"
EVENTS_FILENAME = "events.jsonl"
TRIALS_DIRNAME = "trials"
SNAPSHOTS_DIRNAME = "snapshots"
INCUMBENT_PROMPTS_DIRNAME = "incumbent_prompts"
TRIAL_PROMPTS_DIRNAME = "trial_prompts"
RUN_LOG_FILENAME = "current_run.log"
VALID_STATUSES = {"idle", "running", "stopped", "interrupted"}
DEFAULT_EXPERIMENT_SET = "v1"


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json_atomic(path: Path, payload: dict | list) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def copy_prompt_tree(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(runner.PROMPTS_DIR, destination)


def restore_prompt_tree(source: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if runner.PROMPTS_DIR.exists():
        shutil.rmtree(runner.PROMPTS_DIR)
    shutil.copytree(source, runner.PROMPTS_DIR)


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def prompt_tree_matches_snapshot(snapshot_dir: Path) -> bool:
    if not snapshot_dir.exists():
        return False
    for prompt in runner.PROMPTS_DIR.rglob("*.md"):
        rel = prompt.relative_to(runner.PROMPTS_DIR)
        snap = snapshot_dir / rel
        if not snap.exists():
            return False
        if prompt.read_text(encoding="utf-8") != snap.read_text(encoding="utf-8"):
            return False
    return True


def family_paths(state_dir: Path) -> dict[str, Path]:
    return {
        "state": state_dir / STATE_FILENAME,
        "families": state_dir / FAMILIES_FILENAME,
        "incumbent": state_dir / INCUMBENT_FILENAME,
        "events": state_dir / EVENTS_FILENAME,
        "trials": state_dir / TRIALS_DIRNAME,
        "snapshots": state_dir / SNAPSHOTS_DIRNAME,
        "incumbent_prompts": state_dir / SNAPSHOTS_DIRNAME / INCUMBENT_PROMPTS_DIRNAME,
        "trial_prompts": state_dir / SNAPSHOTS_DIRNAME / TRIAL_PROMPTS_DIRNAME,
        "run_log": state_dir / RUN_LOG_FILENAME,
        "assessment_logs": state_dir / DEFAULT_ASSESSMENT_LOGS_DIRNAME,
    }


def default_family_state(name: str) -> dict[str, object]:
    return {
        "name": name,
        "attempts": 0,
        "keeps": 0,
        "uncertains": 0,
        "discards": 0,
        "consecutive_non_keeps": 0,
        "total_non_keeps_since_last_keep": 0,
        "best_delta": 0.0,
        "repeated_collateral_losers": [],
        "collateral_loss_counts": {},
        "stale": False,
        "stale_reason": None,
        "has_signal": False,
    }


def resolve_campaign_log_path(state: dict, campaign_log: Path | None) -> Path | None:
    if campaign_log is not None:
        return campaign_log
    value = state.get("campaign_log")
    return Path(value) if value else None


def resolve_baseline_json_path(state: dict, baseline_json: Path | None) -> Path | None:
    if baseline_json is not None:
        return baseline_json
    value = state.get("baseline_json")
    return Path(value) if value else None


def default_families(experiment_set: str = DEFAULT_EXPERIMENT_SET) -> dict[str, dict[str, object]]:
    priority = runner.V2_EXPERIMENT_FAMILY_PRIORITY if experiment_set == "v2" else runner.EXPERIMENT_FAMILY_PRIORITY
    families = {name: default_family_state(name) for name in priority}
    for exp in runner.experiments_for_set(experiment_set):
        families.setdefault(exp.family, default_family_state(exp.family))
    return families


def latest_keep_description(results_path: Path) -> str | None:
    if not results_path.exists():
        return None
    lines = results_path.read_text(encoding="utf-8").strip().splitlines()
    for line in reversed(lines[1:]):
        fields = line.split("\t")
        if len(fields) >= 7 and fields[5] == "keep":
            return fields[6]
    return None


def discover_baseline_json(description: str | None) -> Path | None:
    if not description:
        return None
    roots = [
        PROJECT_ROOT / "build" / "assessment_runs",
        PROJECT_ROOT / "logs",
    ]
    patterns = [f"{description}_*.json", f"**/{description}_*.json"]
    matches: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            matches.extend(root.glob(pattern))
    if not matches:
        return None
    matches = sorted({path.resolve() for path in matches})
    return matches[-1]


def current_incumbent_payload(campaign_log: Path | None, baseline_json: Path | None) -> dict:
    if campaign_log and campaign_log.exists():
        if baseline_json and baseline_json.exists():
            return load_json(baseline_json)
        discovered = discover_baseline_json(WORKING_BASELINE_DESCRIPTION)
        if discovered is not None:
            return load_json(discovered)
        raise FileNotFoundError("Campaign replay needs baseline assessment JSON")

    if baseline_json and baseline_json.exists():
        return load_json(baseline_json)

    discovered = discover_baseline_json(latest_keep_description(runner.RESULTS_TSV))
    if discovered is not None:
        return load_json(discovered)

    raise FileNotFoundError("Could not determine incumbent assessment JSON")


def update_family_state(family_state: dict[str, object], entry: dict[str, object]) -> None:
    experiment_set = str(entry.get("experiment_set") or DEFAULT_EXPERIMENT_SET)
    status = str(entry.get("status"))
    family_state["attempts"] = int(family_state["attempts"]) + 1
    if status == "keep":
        family_state["keeps"] = int(family_state["keeps"]) + 1
        family_state["consecutive_non_keeps"] = 0
        family_state["total_non_keeps_since_last_keep"] = 0
        family_state["best_delta"] = max(float(family_state["best_delta"]), float(entry.get("delta", 0.0)))
        family_state["has_signal"] = True
    else:
        if status == "uncertain":
            family_state["uncertains"] = int(family_state["uncertains"]) + 1
            if entry.get("research_signal"):
                family_state["has_signal"] = True
        elif status == "discard":
            family_state["discards"] = int(family_state["discards"]) + 1
        family_state["consecutive_non_keeps"] = int(family_state["consecutive_non_keeps"]) + 1
        family_state["total_non_keeps_since_last_keep"] = int(family_state["total_non_keeps_since_last_keep"]) + 1

    loss_counts = dict(family_state.get("collateral_loss_counts", {}))
    primary_loss_counts = dict(family_state.get("primary_fragile_loss_counts", {}))
    word_signal = entry.get("word_signal", {})
    for key in ("lost_low_medium", "lost_high"):
        for word in word_signal.get(key, []):
            loss_counts[word] = int(loss_counts.get(word, 0)) + 1
    for word in word_signal.get("lost_primary_fragile", []):
        primary_loss_counts[word] = int(primary_loss_counts.get(word, 0)) + 1
    family_state["collateral_loss_counts"] = loss_counts
    family_state["primary_fragile_loss_counts"] = primary_loss_counts
    family_state["repeated_collateral_losers"] = sorted(
        word for word, count in loss_counts.items() if count >= runner.FAMILY_STOP_REPEAT_COLLATERAL
    )
    family_state["repeated_primary_fragile_losers"] = sorted(
        word for word, count in primary_loss_counts.items() if count >= runner.V2_FAMILY_STOP_REPEAT_PRIMARY
    )

    if int(family_state["consecutive_non_keeps"]) >= runner.family_stop_consecutive_non_keeps(experiment_set):
        family_state["stale"] = True
        family_state["stale_reason"] = "consecutive_non_keeps"
    elif int(family_state["total_non_keeps_since_last_keep"]) >= runner.family_stop_total_non_keeps(experiment_set):
        family_state["stale"] = True
        family_state["stale_reason"] = "total_non_keeps"
    elif experiment_set == "v2" and family_state["repeated_primary_fragile_losers"]:
        family_state["stale"] = True
        family_state["stale_reason"] = "repeated_primary_fragile_losers"
    elif family_state["repeated_collateral_losers"]:
        family_state["stale"] = True
        family_state["stale_reason"] = "repeated_collateral_losers"
    else:
        family_state["stale"] = False
        family_state["stale_reason"] = None


def build_trial_record(
    exp: runner.Experiment,
    description: str,
    assessment_json_path: Path,
    decision: runner.ClassificationDecision | None,
    result: dict | None,
) -> dict[str, object]:
    record = {
        "id": exp.name,
        "family": exp.family,
        "priority": exp.priority,
        "description": description,
        "files": exp.files,
        "tags": list(exp.tags),
        "target_words": list(exp.target_words),
        "risk_words": list(exp.risk_words),
        "prerequisites": list(exp.prerequisites),
        "patch": [
            {"file": edit.file, "find": edit.find, "replace": edit.replace}
            for edit in exp.edits
        ],
        "assessment_json": str(assessment_json_path),
    }
    if decision is not None:
        record.update(
            {
                "status": decision.status,
                "delta": round(decision.delta, 1),
                "uncertain_reason": decision.uncertain_reason,
                "research_signal": decision.research_signal,
                "word_signal": decision.signal.to_dict(),
            }
        )
    if result is not None:
        record["result"] = {
            "composite": float(result["composite"]),
            "pass_rate": float(result["pass_rate"]),
            "avg_semantic": float(result["avg_semantic"]),
            "avg_rebus": float(result["avg_rebus"]),
        }
    return record


def rewrite_results_tsv_from_log(log_entries: list[dict], *, results_path: Path) -> None:
    header = "commit\tcomposite\tpass_rate\tavg_semantic\tavg_rebus\tstatus\tdescription\n"
    existing = []
    if results_path.exists():
        existing = results_path.read_text(encoding="utf-8").splitlines()
    preserved = [header.rstrip("\n")]
    descriptions = {entry["assessment_description"] for entry in log_entries if entry.get("assessment_description")}
    for line in existing[1:]:
        fields = line.split("\t")
        if len(fields) >= 7 and fields[6] not in descriptions:
            preserved.append(line)
    for entry in log_entries:
        if entry.get("status") not in {"keep", "uncertain", "discard", "error"}:
            continue
        preserved.append(
            "\t".join(
                [
                    runner.git_short_hash(),
                    f"{float(entry['composite']):.1f}",
                    f"{float(entry['pass_rate']):.3f}",
                    f"{float(entry['avg_semantic']):.1f}",
                    f"{float(entry['avg_rebus']):.1f}",
                    str(entry["status"]),
                    str(entry["assessment_description"]),
                ]
            )
        )
    write_text_atomic(results_path, "\n".join(preserved) + "\n")


def replay_campaign_log(
    campaign_log: Path,
    *,
    incumbent_payload: dict,
    experiment_set: str = DEFAULT_EXPERIMENT_SET,
) -> tuple[list[dict], dict, dict[str, dict[str, object]]]:
    entries = load_json(campaign_log, [])
    reclassified: list[dict] = []
    families = default_families(experiment_set)
    current_incumbent = incumbent_payload
    best_composite = float(current_incumbent["composite"])

    for entry in entries:
        exp = runner.get_experiment(entry["name"], experiment_set)
        entry["family"] = exp.family
        entry["priority"] = exp.priority
        entry["target_words"] = list(exp.target_words)
        entry["prerequisites"] = list(exp.prerequisites)
        entry["experiment_set"] = experiment_set
        assessment_json = entry.get("assessment_json")
        if not assessment_json or not Path(assessment_json).exists():
            reclassified.append(entry)
            continue
        result = load_json(Path(assessment_json))
        decision = runner.classify_experiment_result(result, current_incumbent, best_composite)
        entry["status"] = decision.status
        entry["composite"] = float(result["composite"])
        entry["pass_rate"] = float(result["pass_rate"])
        entry["avg_semantic"] = float(result["avg_semantic"])
        entry["avg_rebus"] = float(result["avg_rebus"])
        entry["prev_best"] = best_composite
        entry["delta"] = round(decision.delta, 1)
        entry["protected_regression"] = decision.protected_regression
        entry["pass_regression"] = decision.pass_regression
        entry["uncertain_reason"] = decision.uncertain_reason
        entry["research_signal"] = decision.research_signal
        entry["word_signal"] = decision.signal.to_dict()
        reclassified.append(entry)
        update_family_state(families[exp.family], entry)
        if decision.status == "keep":
            current_incumbent = result
            best_composite = float(result["composite"])

    return reclassified, current_incumbent, families


def initialize_state(
    *,
    state_dir: Path,
    incumbent_payload: dict,
    campaign_log: Path | None,
    baseline_json: Path | None,
    experiment_set: str = DEFAULT_EXPERIMENT_SET,
) -> tuple[dict, dict[str, dict[str, object]], dict]:
    paths = family_paths(state_dir)
    copy_prompt_tree(paths["incumbent_prompts"])
    attempted_experiments: list[str] = []
    if campaign_log and campaign_log.exists():
        reclassified, incumbent_payload, families = replay_campaign_log(
            campaign_log,
            incumbent_payload=incumbent_payload,
            experiment_set=experiment_set,
        )
        write_json_atomic(campaign_log, reclassified)
        rewrite_results_tsv_from_log(reclassified, results_path=runner.RESULTS_TSV)
        attempted_experiments = [
            entry["name"]
            for entry in reclassified
            if entry.get("status") in {"keep", "uncertain", "discard", "error", "skipped"}
        ]
    else:
        families = default_families(experiment_set)

    state = {
        "campaign_id": f"prompt_research_{path_timestamp()}",
        "status": "idle",
        "current_family": None,
        "current_experiment": None,
        "incumbent_composite": float(incumbent_payload["composite"]),
        "incumbent_pass_rate": float(incumbent_payload["pass_rate"]),
        "incumbent_prompt_snapshot": str(paths["incumbent_prompts"]),
        "active_trial": None,
        "stop_reason": None,
        "campaign_log": str(campaign_log) if campaign_log is not None else None,
        "baseline_json": str(baseline_json) if baseline_json is not None else None,
        "attempted_experiments": attempted_experiments,
        "stale_family_streak": 0,
        "heartbeat_ts": None,
        "experiment_set": experiment_set,
    }
    write_json_atomic(paths["state"], state)
    write_json_atomic(paths["families"], families)
    write_json_atomic(paths["incumbent"], incumbent_payload)
    return state, families, incumbent_payload


def load_or_initialize_state(
    *,
    state_dir: Path,
    campaign_log: Path | None,
    baseline_json: Path | None,
    experiment_set: str = DEFAULT_EXPERIMENT_SET,
) -> tuple[dict, dict[str, dict[str, object]], dict]:
    paths = family_paths(state_dir)
    if paths["state"].exists():
        return resume_existing_state(
            state_dir=state_dir,
            campaign_log=campaign_log,
            baseline_json=baseline_json,
            experiment_set=experiment_set,
        )
    return bootstrap_from_campaign(
        state_dir=state_dir,
        campaign_log=campaign_log,
        baseline_json=baseline_json,
        experiment_set=experiment_set,
    )


def persist_campaign_state(
    state_dir: Path,
    state: dict,
    families: dict[str, dict[str, object]],
    incumbent: dict,
) -> None:
    paths = family_paths(state_dir)
    write_json_atomic(paths["state"], state)
    write_json_atomic(paths["families"], families)
    write_json_atomic(paths["incumbent"], incumbent)


def recover_if_interrupted(state_dir: Path, state: dict) -> None:
    paths = family_paths(state_dir)
    active_trial = state.get("active_trial")
    if state.get("status") != "running" or not active_trial:
        return
    restore_prompt_tree(paths["incumbent_prompts"])
    if paths["trial_prompts"].exists():
        shutil.rmtree(paths["trial_prompts"])
    state["status"] = "interrupted"
    state["stop_reason"] = f"recovered interrupted trial {active_trial['id']}"
    state["active_trial"] = None
    state["current_experiment"] = None
    audit("prompt_autoresearch_recovered", payload={"trial": active_trial["id"]})


def validate_state(
    *,
    state_dir: Path,
    state: dict,
    families: dict[str, dict[str, object]],
    incumbent: dict,
    campaign_log: Path | None,
) -> tuple[bool, str | None]:
    paths = family_paths(state_dir)
    campaign_log = resolve_campaign_log_path(state, campaign_log)

    required = [paths["state"], paths["families"], paths["incumbent"], paths["incumbent_prompts"]]
    for path in required:
        if not path.exists():
            return False, f"missing durable artifact: {path.name}"

    if state.get("status") not in VALID_STATUSES:
        return False, f"invalid status: {state.get('status')}"

    state_comp = float(state.get("incumbent_composite", -1))
    incumbent_comp = float(incumbent.get("composite", -2))
    state_pass = float(state.get("incumbent_pass_rate", -1))
    incumbent_pass = float(incumbent.get("pass_rate", -2))
    if state_comp != incumbent_comp or state_pass != incumbent_pass:
        return False, "incumbent metrics mismatch"

    if state.get("status") in {"idle", "stopped"} and not prompt_tree_matches_snapshot(paths["incumbent_prompts"]):
        return False, "prompt tree does not match incumbent snapshot"

    attempted = set(state.get("attempted_experiments", []))
    logged = set()
    if campaign_log and campaign_log.exists():
        logged = {entry["name"] for entry in load_json(campaign_log, []) if entry.get("name")}

    for exp_name in attempted:
        trial_path = paths["trials"] / f"{exp_name}.json"
        if not trial_path.exists() and exp_name not in logged:
            return False, f"missing trial or log record for {exp_name}"

    current_experiment = state.get("current_experiment")
    active_trial = state.get("active_trial")
    if state.get("status") == "running":
        if not current_experiment or not active_trial:
            return False, "running state missing active trial"
    else:
        if active_trial is not None:
            return False, "non-running state has active trial"

    return True, None


def bootstrap_from_campaign(
    *,
    state_dir: Path,
    campaign_log: Path | None,
    baseline_json: Path | None,
    experiment_set: str = DEFAULT_EXPERIMENT_SET,
) -> tuple[dict, dict[str, dict[str, object]], dict]:
    incumbent_payload = current_incumbent_payload(campaign_log, baseline_json)
    return initialize_state(
        state_dir=state_dir,
        incumbent_payload=incumbent_payload,
        campaign_log=campaign_log,
        baseline_json=baseline_json,
        experiment_set=experiment_set,
    )


def rebuild_state_from_campaign(
    *,
    state_dir: Path,
    campaign_log: Path | None,
    baseline_json: Path | None,
    experiment_set: str = DEFAULT_EXPERIMENT_SET,
) -> tuple[dict, dict[str, dict[str, object]], dict]:
    tmp_dir = state_dir.with_name(f"{state_dir.name}.rebuild_tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    bootstrap_from_campaign(
        state_dir=tmp_dir,
        campaign_log=campaign_log,
        baseline_json=baseline_json,
        experiment_set=experiment_set,
    )
    if state_dir.exists():
        shutil.rmtree(state_dir)
    shutil.move(str(tmp_dir), str(state_dir))
    paths = family_paths(state_dir)
    state = load_json(paths["state"], {})
    families = load_json(paths["families"], default_families(DEFAULT_EXPERIMENT_SET))
    incumbent = load_json(paths["incumbent"], {})
    state["incumbent_prompt_snapshot"] = str(paths["incumbent_prompts"])
    persist_campaign_state(state_dir, state, families, incumbent)
    return state, families, incumbent


def resume_existing_state(
    *,
    state_dir: Path,
    campaign_log: Path | None,
    baseline_json: Path | None,
    experiment_set: str = DEFAULT_EXPERIMENT_SET,
) -> tuple[dict, dict[str, dict[str, object]], dict]:
    paths = family_paths(state_dir)
    state = load_json(paths["state"], {})
    campaign_log = resolve_campaign_log_path(state, campaign_log)
    baseline_json = resolve_baseline_json_path(state, baseline_json)
    experiment_set = str(state.get("experiment_set") or experiment_set)
    families = load_json(paths["families"], default_families(experiment_set))
    incumbent = load_json(paths["incumbent"], {})
    valid, reason = validate_state(
        state_dir=state_dir,
        state=state,
        families=families,
        incumbent=incumbent,
        campaign_log=campaign_log,
    )
    if valid:
        return state, families, incumbent

    audit("state_rebuild_required", payload={"reason": reason or "invalid_state"})
    try:
        rebuilt = rebuild_state_from_campaign(
            state_dir=state_dir,
            campaign_log=campaign_log,
            baseline_json=baseline_json,
            experiment_set=experiment_set,
        )
    except Exception as exc:
        state["status"] = "stopped"
        state["stop_reason"] = f"state validation failed and rebuild failed: {reason}; {exc}"
        state["active_trial"] = None
        persist_campaign_state(state_dir, state, families, incumbent)
        raise RuntimeError(state["stop_reason"]) from exc
    rebuilt_state, rebuilt_families, rebuilt_incumbent = rebuilt
    rebuilt_state["stop_reason"] = f"rebuilt state after validation failure: {reason}"
    persist_campaign_state(state_dir, rebuilt_state, rebuilt_families, rebuilt_incumbent)
    return rebuilt


def family_has_signal(families: dict[str, dict[str, object]], family: str) -> bool:
    return bool(families.get(family, {}).get("has_signal"))


def family_unlocked(exp: runner.Experiment, families: dict[str, dict[str, object]]) -> bool:
    if not exp.prerequisites:
        return True
    return any(family_has_signal(families, family) for family in exp.prerequisites)


def available_experiments_for_family(
    family: str,
    attempted: set[str],
    families: dict[str, dict[str, object]],
    experiment_set: str,
) -> list[runner.Experiment]:
    return [
        exp
        for exp in runner.experiments_for_family(family, experiment_set)
        if exp.name not in attempted and family_unlocked(exp, families)
    ]


def select_next_experiment(
    state: dict,
    families: dict[str, dict[str, object]],
) -> runner.Experiment | None:
    experiment_set = str(state.get("experiment_set") or DEFAULT_EXPERIMENT_SET)
    attempted = set(state.get("attempted_experiments", []))
    current_family = state.get("current_family")
    if current_family:
        current_state = families.get(current_family, {})
        if not current_state.get("stale"):
            available = available_experiments_for_family(current_family, attempted, families, experiment_set)
            if available:
                return sorted(available, key=lambda exp: (exp.priority, exp.name))[0]

    family_priority = runner.V2_EXPERIMENT_FAMILY_PRIORITY if experiment_set == "v2" else runner.EXPERIMENT_FAMILY_PRIORITY
    for family in family_priority:
        family_state = families.get(family, {})
        if family_state.get("stale"):
            continue
        available = available_experiments_for_family(family, attempted, families, experiment_set)
        if available:
            return sorted(available, key=lambda exp: (exp.priority, exp.name))[0]
    return None


def safe_stop(state: dict, *, reason: str) -> None:
    state["status"] = "stopped"
    state["stop_reason"] = reason
    state["active_trial"] = None
    state["current_experiment"] = None


def run_supervisor(
    *,
    state_dir: Path,
    campaign_log: Path | None,
    baseline_json: Path | None,
    max_trials: int | None,
    description_prefix: str,
    dry_run: bool,
    experiment_set: str = DEFAULT_EXPERIMENT_SET,
) -> int:
    paths = family_paths(state_dir)
    state, families, incumbent = load_or_initialize_state(
        state_dir=state_dir,
        campaign_log=campaign_log,
        baseline_json=baseline_json,
        experiment_set=experiment_set,
    )
    recover_if_interrupted(state_dir, state)
    persist_campaign_state(state_dir, state, families, incumbent)

    exp = select_next_experiment(state, families)
    if dry_run:
        valid, reason = validate_state(
            state_dir=state_dir,
            state=state,
            families=families,
            incumbent=incumbent,
            campaign_log=campaign_log,
        )
        if exp is None:
            print("No viable experiment available.")
            return 0
        print(
            f"State valid: {'yes' if valid else 'no'}"
            + (f" ({reason})" if reason else "")
        )
        print(f"Incumbent: {state['incumbent_composite']:.1f} / {state['incumbent_pass_rate']:.3f}")
        print(f"Next experiment: {exp.name} ({exp.family}) — {exp.desc}")
        return 0

    trials_run = 0
    while exp is not None:
        if max_trials is not None and trials_run >= max_trials:
            break

        state["status"] = "running"
        state["current_family"] = exp.family
        state["current_experiment"] = exp.name
        state["heartbeat_ts"] = runner.path_timestamp()
        restore_prompt_tree(paths["incumbent_prompts"])
        description = runner.build_assessment_description(description_prefix, exp)
        assessment_log_path = paths["assessment_logs"] / f"{exp.name}.log"
        assessment_json_path = paths["assessment_logs"] / f"{exp.name}.json"
        state["active_trial"] = {
            "id": exp.name,
            "family": exp.family,
            "description": description,
            "assessment_log": str(assessment_log_path),
            "assessment_json": str(assessment_json_path),
        }
        persist_campaign_state(state_dir, state, families, incumbent)
        audit("trial_started", payload={"experiment": exp.name, "family": exp.family})

        applied = runner.apply_experiment(exp)
        if not applied:
            trial = build_trial_record(exp, description, assessment_json_path, None, None)
            trial["status"] = "skipped"
            write_json_atomic(paths["trials"] / f"{exp.name}.json", trial)
            state["attempted_experiments"].append(exp.name)
            state["active_trial"] = None
            state["heartbeat_ts"] = runner.path_timestamp()
            persist_campaign_state(state_dir, state, families, incumbent)
            audit("trial_skipped", payload={"experiment": exp.name})
            exp = select_next_experiment(state, families)
            continue

        copy_prompt_tree(paths["trial_prompts"])
        result_snapshot = runner.snapshot_results_tsv()
        try:
            result = runner.run_assessment(
                description,
                assessment_log_path=assessment_log_path,
                assessment_json_path=assessment_json_path,
                stream_output=False,
            )
        except KeyboardInterrupt:
            restore_prompt_tree(paths["incumbent_prompts"])
            runner.restore_results_tsv(result_snapshot)
            state["status"] = "interrupted"
            state["stop_reason"] = f"keyboard interrupt during {exp.name}"
            state["active_trial"] = None
            persist_campaign_state(state_dir, state, families, incumbent)
            audit("trial_interrupted", payload={"experiment": exp.name})
            return 130

        if result.get("error"):
            restore_prompt_tree(paths["incumbent_prompts"])
            runner.restore_results_tsv(result_snapshot)
            safe_stop(state, reason=f"assessment error in {exp.name}")
            persist_campaign_state(state_dir, state, families, incumbent)
            audit("trial_error", payload={"experiment": exp.name})
            return 1

        decision = runner.classify_experiment_result(
            result,
            incumbent,
            float(incumbent["composite"]),
        )
        trial = build_trial_record(exp, description, assessment_json_path, decision, result)
        write_json_atomic(paths["trials"] / f"{exp.name}.json", trial)

        entry = {
            "name": exp.name,
            "assessment_description": description,
            "assessment_log": str(assessment_log_path),
            "assessment_json": str(assessment_json_path),
            "file": exp.file,
            "files": exp.files,
            "find": exp.find,
            "replace": exp.replace,
            "desc": exp.desc,
            "status": decision.status,
            "composite": float(result["composite"]),
            "pass_rate": float(result["pass_rate"]),
            "avg_semantic": float(result["avg_semantic"]),
            "avg_rebus": float(result["avg_rebus"]),
            "prev_best": float(incumbent["composite"]),
            "delta": round(decision.delta, 1),
            "protected_regression": decision.protected_regression,
            "pass_regression": decision.pass_regression,
            "uncertain_reason": decision.uncertain_reason,
            "research_signal": decision.research_signal,
            "family": exp.family,
            "priority": exp.priority,
            "target_words": list(exp.target_words),
            "prerequisites": list(exp.prerequisites),
            "word_signal": decision.signal.to_dict(),
            "control_watch": runner.summarize_control_watch(result),
            "experiment_set": experiment_set,
        }
        update_family_state(families[exp.family], entry)
        state["attempted_experiments"].append(exp.name)
        state["active_trial"] = None
        state["heartbeat_ts"] = runner.path_timestamp()

        if decision.status == "keep":
            runner.append_results_row(description, decision.status, result)
            incumbent = result
            copy_prompt_tree(paths["incumbent_prompts"])
            state["incumbent_composite"] = float(result["composite"])
            state["incumbent_pass_rate"] = float(result["pass_rate"])
            state["incumbent_prompt_snapshot"] = str(paths["incumbent_prompts"])
            state["stale_family_streak"] = 0
        else:
            restore_prompt_tree(paths["incumbent_prompts"])
            runner.restore_results_tsv(result_snapshot)
            runner.append_results_row(description, decision.status, result)

        if families[exp.family]["stale"]:
            state["stale_family_streak"] = int(state.get("stale_family_streak", 0)) + 1
            audit(
                "family_stale",
                payload={
                    "family": exp.family,
                    "reason": families[exp.family]["stale_reason"],
                    "experiment": exp.name,
                },
            )
        if int(state.get("stale_family_streak", 0)) >= CAMPAIGN_STOP_STALE_FAMILIES:
            safe_stop(state, reason="three consecutive stale families")
            persist_campaign_state(state_dir, state, families, incumbent)
            audit("campaign_stopped", payload={"reason": state["stop_reason"]})
            return 0

        persist_campaign_state(state_dir, state, families, incumbent)
        audit(
            "trial_finished",
            payload={
                "experiment": exp.name,
                "family": exp.family,
                "status": decision.status,
                "delta": round(decision.delta, 1),
                "uncertain_reason": decision.uncertain_reason,
            },
        )
        trials_run += 1
        exp = select_next_experiment(state, families)

    if exp is None:
        safe_stop(state, reason="no viable families remaining")
        state["current_family"] = None
    else:
        state["status"] = "idle"
        state["stop_reason"] = "max trials reached"
        state["current_experiment"] = exp.name
        state["current_family"] = exp.family
    persist_campaign_state(state_dir, state, families, incumbent)
    audit("campaign_stopped", payload={"reason": state["stop_reason"]})
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Prompt autoresearch supervisor")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--campaign-log", type=Path, help="Existing experiment log to import/reclassify")
    parser.add_argument("--baseline-json", type=Path, help="Baseline/incumbent assessment JSON")
    parser.add_argument("--experiment-set", choices=sorted(runner.EXPERIMENT_SETS), default=DEFAULT_EXPERIMENT_SET)
    parser.add_argument("--max-trials", type=int, help="Run at most N new trials")
    parser.add_argument("--continuous", action="store_true", help="Run until safe-stop instead of stopping after N trials")
    parser.add_argument("--rebuild-state", action="store_true", help="Rebuild durable state from campaign log and baseline JSON")
    parser.add_argument("--description-prefix", default=DEFAULT_DESCRIPTION_PREFIX)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true", help="Print current durable state and exit")
    args = parser.parse_args()
    if args.continuous and args.max_trials is not None:
        parser.error("--continuous and --max-trials are mutually exclusive")

    paths = family_paths(args.state_dir)
    handle = install_process_logging(
        run_id=f"prompt_autoresearch_{path_timestamp()}",
        component="prompt_autoresearch",
        log_path=paths["run_log"],
        audit_path=paths["events"],
        tee_console=True,
    )
    try:
        try:
            if args.rebuild_state:
                state, families, incumbent = rebuild_state_from_campaign(
                    state_dir=args.state_dir,
                    campaign_log=args.campaign_log,
                    baseline_json=args.baseline_json,
                    experiment_set=args.experiment_set,
                )
                valid, reason = validate_state(
                    state_dir=args.state_dir,
                    state=state,
                    families=families,
                    incumbent=incumbent,
                    campaign_log=args.campaign_log,
                )
                print(
                    f"Rebuilt state: incumbent={state['incumbent_composite']:.1f}/{state['incumbent_pass_rate']:.3f} "
                    f"valid={'yes' if valid else 'no'}"
                    + (f" ({reason})" if reason else "")
                )
                if args.status or args.dry_run:
                    if args.status:
                        print(json.dumps(state, ensure_ascii=False, indent=2))
                    if args.dry_run:
                        next_exp = select_next_experiment(state, families)
                        if next_exp is None:
                            print("No viable experiment available.")
                        else:
                            print(f"Next experiment: {next_exp.name} ({next_exp.family}) — {next_exp.desc}")
                    return
            if args.status and paths["state"].exists():
                state = load_json(paths["state"], {})
                families = load_json(paths["families"], default_families(str(state.get("experiment_set") or DEFAULT_EXPERIMENT_SET)))
                incumbent = load_json(paths["incumbent"], {})
                valid, reason = validate_state(
                    state_dir=args.state_dir,
                    state=state,
                    families=families,
                    incumbent=incumbent,
                    campaign_log=args.campaign_log,
                )
                payload = dict(state)
                payload["state_valid"] = valid
                payload["state_validation_reason"] = reason
                next_exp = select_next_experiment(state, families)
                payload["next_experiment"] = next_exp.name if next_exp else None
                payload["next_family"] = next_exp.family if next_exp else None
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return
            exit_code = run_supervisor(
                state_dir=args.state_dir,
                campaign_log=args.campaign_log,
                baseline_json=args.baseline_json,
                max_trials=None if args.continuous else args.max_trials,
                description_prefix=args.description_prefix,
                dry_run=args.dry_run,
                experiment_set=args.experiment_set,
            )
            raise SystemExit(exit_code)
        except RuntimeError as exc:
            print(str(exc))
            raise SystemExit(1) from exc
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
