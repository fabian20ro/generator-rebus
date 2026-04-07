from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_DIR = ROOT / "generator"


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
        "generator/core/lm_runtime.py",
        "generator/core/model_aware_scheduler.py",
        "generator/core/llm_dispatch.py",
        "generator/run_all.py",
        "generator/assessment/run_assessment.py",
        "generator/core/clue_canon_simplify.py",
        "generator/core/definition_referee.py",
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
