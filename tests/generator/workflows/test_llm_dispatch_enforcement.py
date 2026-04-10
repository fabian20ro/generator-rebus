from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_DIR = ROOT / "packages" / "rebus-generator" / "src" / "rebus_generator"


def _python_files() -> list[Path]:
    return sorted(
        path for path in GENERATOR_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_no_direct_chat_completions_create_outside_llm_client() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if path.name == "llm_client.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "chat.completions.create(" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_no_direct_runtime_activation_in_production_pipelines() -> None:
    allowed = {
        "packages/rebus-generator/src/rebus_generator/platform/llm/lm_runtime.py",
        "packages/rebus-generator/src/rebus_generator/platform/llm/model_aware_scheduler.py",
        "packages/rebus-generator/src/rebus_generator/platform/llm/llm_dispatch.py",
        "packages/rebus-generator/src/rebus_generator/cli/run_all.py",
        "packages/rebus-generator/src/rebus_generator/evaluation/assessment/service.py",
        "packages/rebus-generator/src/rebus_generator/workflows/canonicals/simplify.py",
        "packages/rebus-generator/src/rebus_generator/platform/llm/definition_referee.py",
        "packages/rebus-generator/src/rebus_generator/workflows/run_all/scheduler.py",
    }
    banned_markers = (
        "activate_primary(",
        "activate_secondary(",
        "alternate(",
        "activate(",
    )
    offenders: list[str] = []
    for path in _python_files():
        relative = str(path.relative_to(ROOT))
        if relative in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in banned_markers):
            offenders.append(relative)
    assert offenders == []
