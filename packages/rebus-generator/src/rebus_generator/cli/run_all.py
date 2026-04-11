"""Thin CLI/assembly entrypoint for the run_all supervisor."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import time
from pathlib import Path

from rebus_generator.workflows.canonicals.runtime import DEFAULT_SIMPLIFY_BATCH_SIZE
from rebus_generator.platform.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.platform.llm.llm_client import (
    _clean_response,
    _chat_completion_create,
    _response_completion_tokens,
    _response_content_text,
    _response_finish_reason,
    _response_reasoning_tokens,
    configure_run_llm_policy,
    create_client as create_ai_client,
    reset_run_llm_state,
)
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.platform.llm.lm_studio_api import get_loaded_model_instances, unload_instance
from rebus_generator.platform.io.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from rebus_generator.platform.persistence.supabase_ops import create_service_role_client
from rebus_generator.platform.io.rust_bridge import _rust_binary_path
from rebus_generator.workflows.run_all.scheduler import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_IDLE_SLEEP_SECONDS,
    RunAllSupervisor,
)
from rebus_generator.workflows.run_all.types import RunAllContext

SUPPORTED_TOPICS = ("generate", "redefine", "retitle", "simplify")
LOCK_PATH = Path("/tmp/generator_rebus_run_all.lock")


def _parse_topics(value: str | None) -> list[str]:
    if not value:
        return list(SUPPORTED_TOPICS)
    topics = [topic.strip().lower() for topic in value.split(",") if topic.strip()]
    invalid = [topic for topic in topics if topic not in SUPPORTED_TOPICS]
    if invalid:
        raise SystemExit(f"Unsupported topics: {', '.join(invalid)}")
    deduped: list[str] = []
    for topic in topics:
        if topic not in deduped:
            deduped.append(topic)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified long-running supervisor for generation and improvement.")
    parser.add_argument(
        "--topics",
        help="Comma-separated topics: generate,redefine,retitle,simplify (default: all).",
    )
    parser.add_argument("--words", default="build/words.json", help="Path to words.json cache.")
    parser.add_argument("--output-root", default="build/run_all_runs", help="Supervisor artifact root.")
    parser.add_argument("--generate-cap", type=int, default=1)
    parser.add_argument("--redefine-cap", type=int, default=1)
    parser.add_argument("--retitle-cap", type=int, default=1)
    parser.add_argument("--simplify-cap", type=int, default=1)
    parser.add_argument("--idle-sleep-seconds", type=int, default=DEFAULT_IDLE_SLEEP_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--rewrite-rounds", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--verify-candidates", type=int, default=VERIFY_CANDIDATE_COUNT)
    parser.add_argument("--simplify-batch-size", type=int, default=DEFAULT_SIMPLIFY_BATCH_SIZE)
    parser.add_argument("--gemma-verify-reasoning", default="none")
    parser.add_argument("--gemma-rate-reasoning", default="minimal")
    parser.add_argument("--gemma-title-generate-reasoning", default="none")
    parser.add_argument("--gemma-title-rate-reasoning", default="none")
    parser.add_argument("--llm-preflight", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--llm-stall-seconds", type=int, default=1800)
    parser.add_argument("--llm-truncation-threshold", type=int, default=3)
    parser.add_argument(
        "--multi-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the configured two-model workflow (default: True).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not persist DB changes for non-generation topics.")
    add_llm_debug_argument(parser)
    return parser


@contextlib.contextmanager
def _singleton_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SystemExit(f"Another run_all supervisor already holds {path}") from exc
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    try:
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _smoke_messages(*, model_id: str, purpose: str) -> list[dict[str, str]]:
    if purpose == "definition_verify":
        content = "Răspunde exact cu: GUAS"
    elif purpose == "title_generate":
        content = "Propune exact un titlu scurt în română: Poduri"
    else:
        content = "Răspunde exact cu: ok"
    return [
        {"role": "system", "content": "Răspunde scurt, exclusiv în română, fără explicații."},
        {"role": "user", "content": content},
    ]


def _preflight_signature(exc: Exception) -> str:
    raw = str(exc).strip() or exc.__class__.__name__
    lowered = raw.lower()
    if "failed to load model" in lowered and "insufficient system resources" in lowered:
        return "lmstudio_resource_guard"
    if "did not load within" in lowered:
        return "lmstudio_load_timeout"
    if "did not activate expected model" in lowered or "left extra models active" in lowered:
        return "lmstudio_malformed_state"
    if isinstance(exc, KeyError):
        return f"KeyError:{raw}"
    return f"{exc.__class__.__name__}:{raw}"


def _preflight_unload_all() -> None:
    for model_id, instance_id in get_loaded_model_instances().items():
        try:
            unload_instance(instance_id, model_id=model_id)
        except Exception as exc:
            log(f"[run_all preflight] unload_skip model={model_id} error={exc}", level="WARN")


def _reasoning_overrides(args: argparse.Namespace) -> dict[tuple[str, str], str | None]:
    return {
        (PRIMARY_MODEL.model_id, "definition_verify"): args.gemma_verify_reasoning,
        (PRIMARY_MODEL.model_id, "definition_rate"): args.gemma_rate_reasoning,
        (PRIMARY_MODEL.model_id, "title_generate"): args.gemma_title_generate_reasoning,
        (PRIMARY_MODEL.model_id, "title_rate"): args.gemma_title_rate_reasoning,
        (PRIMARY_MODEL.model_id, "clue_compare"): "none",
        (PRIMARY_MODEL.model_id, "clue_tiebreaker"): "none",
    }


def _preflight(*, topics: list[str], artifact_path: Path, multi_model: bool) -> None:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "created_at": time.time(),
        "multi_model": multi_model,
        "topics": list(topics),
        "models": [],
        "blocking_error": None,
    }
    runtime = None
    smoke_client = None
    models: list[object] = []
    try:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            message = "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env"
            report["blocking_error"] = {
                "model_id": None,
                "purpose": "bootstrap",
                "signature": _preflight_signature(RuntimeError(message)),
                "error": message,
            }
            raise SystemExit(message)
        create_service_role_client()
        runtime = LmRuntime(multi_model=multi_model)
        runtime.sync()
        if "generate" in topics:
            _rust_binary_path()
        smoke_client = create_ai_client()
        models = [PRIMARY_MODEL] + ([SECONDARY_MODEL] if multi_model else [])
        for model in models:
            purpose = "definition_verify" if model.model_id == PRIMARY_MODEL.model_id else "title_generate"
            started_at = time.monotonic()
            result = {
                "model_id": model.model_id,
                "purpose": purpose,
                "status": "ok",
                "elapsed_seconds": 0.0,
                "signature": "",
            }
            try:
                runtime.activate(model)
                response = _chat_completion_create(
                    smoke_client,
                    model=model.model_id,
                    messages=_smoke_messages(model_id=model.model_id, purpose=purpose),
                    temperature=0.0,
                    max_tokens=96,
                    purpose=purpose,
                )
                raw_content = _response_content_text(response)
                cleaned_content = _clean_response(raw_content)
                result["finish_reason"] = _response_finish_reason(response)
                result["completion_tokens"] = _response_completion_tokens(response)
                result["reasoning_tokens"] = _response_reasoning_tokens(response)
                result["response_source"] = getattr(response, "_response_source", "unknown")
                result["raw_content_preview"] = raw_content[:160]
                result["cleaned_content_preview"] = cleaned_content[:160]
                if not cleaned_content:
                    raise RuntimeError("preflight_no_visible_output")
            except Exception as exc:
                result["status"] = "failed"
                result["signature"] = _preflight_signature(exc)
                result["error"] = str(exc)
                report["blocking_error"] = {
                    "model_id": model.model_id,
                    "purpose": purpose,
                    "signature": result["signature"],
                    "error": str(exc),
                }
                report["models"].append(result)
                raise SystemExit(f"run_all preflight failed: {result['signature']}") from exc
            finally:
                result["elapsed_seconds"] = round(time.monotonic() - started_at, 3)
                if result not in report["models"]:
                    report["models"].append(result)
    finally:
        if runtime is not None:
            _preflight_unload_all()
        artifact_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    topics = _parse_topics(args.topics)
    if args.dry_run and "generate" in topics:
        parser.error("--dry-run is not supported when generate topic is enabled")

    run_root = Path(args.output_root)
    run_dir = run_root / path_timestamp()
    log_path = run_dir / "run.log"
    audit_path = run_dir / "audit.jsonl"
    handle = install_process_logging(
        run_id=run_dir.name,
        component="run_all",
        log_path=log_path,
        audit_path=audit_path,
        tee_console=True,
    )
    try:
        set_llm_debug_enabled(bool(args.debug))
        log(f"Run log: {log_path}")
        log(f"Audit log: {audit_path}")
        log(f"Topics: {','.join(topics)}")
        with _singleton_lock(LOCK_PATH):
            reset_run_llm_state()
            if args.llm_preflight:
                _preflight(
                    topics=topics,
                    artifact_path=run_dir / "preflight.json",
                    multi_model=bool(args.multi_model),
                )
            reset_run_llm_state()
            configure_run_llm_policy(
                reasoning_overrides=_reasoning_overrides(args),
                truncation_threshold=max(1, int(args.llm_truncation_threshold)),
            )
            supabase = create_service_role_client()
            runtime = LmRuntime(multi_model=args.multi_model)
            context = RunAllContext(
                supabase=supabase,
                ai_client=create_ai_client(),
                rate_client=create_ai_client(),
                runtime=runtime,
                store=ClueCanonStore(client=supabase),
                run_dir=run_dir,
                batch_output_root=run_dir / "batch",
                words_path=Path(args.words),
                multi_model=args.multi_model,
                dry_run=bool(args.dry_run),
                generate_rewrite_rounds=max(1, args.rewrite_rounds),
                redefine_rounds=max(1, args.rounds),
                verify_candidates=max(1, args.verify_candidates),
                simplify_batch_size=max(1, args.simplify_batch_size),
                preflight_enabled=bool(args.llm_preflight),
                llm_stall_seconds=max(60, int(args.llm_stall_seconds)),
                llm_truncation_threshold=max(1, int(args.llm_truncation_threshold)),
                gemma_verify_reasoning=args.gemma_verify_reasoning,
                gemma_rate_reasoning=args.gemma_rate_reasoning,
                gemma_title_generate_reasoning=args.gemma_title_generate_reasoning,
                gemma_title_rate_reasoning=args.gemma_title_rate_reasoning,
            )
            supervisor = RunAllSupervisor(
                context=context,
                topics=topics,
                topic_caps={
                    "generate": args.generate_cap,
                    "redefine": args.redefine_cap,
                    "retitle": args.retitle_cap,
                    "simplify": args.simplify_cap,
                },
                idle_sleep_seconds=max(1, args.idle_sleep_seconds),
                heartbeat_seconds=max(1, args.heartbeat_seconds),
                debug=bool(args.debug),
            )
            try:
                supervisor.run()
            finally:
                supervisor.close()
        return 0
    finally:
        handle.restore()


if __name__ == "__main__":
    raise SystemExit(main())
