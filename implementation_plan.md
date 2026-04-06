# Refactoring Plan: Modularize Generator Pipeline

## Decisions (Resolved)

| Question | Decision |
|---|---|
| `ai_clues.py` split granularity | 4 new modules + slimmed original |
| Import migration | Clean — update all consumer imports directly, no re-exports |
| `clue_canon.py` split depth | Conservative — extract state serialization only (~500 lines) |
| Schema cutover | Deferred — come back after architecture refactor |

## Current State

- **546 tests passing** in ~18s
- Primary targets: `ai_clues.py` (1985 lines), `clue_canon.py` (2128 lines), `batch_publish.py` (1041 lines)

---

## Phase 1: Extract `llm_client.py` from `ai_clues.py`

The LLM transport layer has **zero domain knowledge** — it only knows OpenAI API, streaming, retry logic, and debug logging. Extracting it first removes the deepest dependency that all other extracted modules will need.

### Task 1.1: Create `generator/core/llm_client.py`

**Move these from `ai_clues.py`:**
- `create_client()` (L211–217)
- `_resolve_model_name()` (L220–223)
- `_clean_response()` (L226–227)
- `_DebugStreamChannel` class (L230–253)
- `_finish_debug_channels()` (L256–258)
- `_debug_message_text()` (L261–267)
- `_log_debug_request()` (L270–290)
- `_log_debug_response()` (L293–303)
- `_build_stream_completion_response()` (L306–333)
- `_chat_completion_create_streaming()` (L336–391)
- `_response_choice()`, `_response_message()`, `_response_finish_reason()`, `_response_content_text()`, `_response_reasoning_text()`, `_response_reasoning_tokens()` (L394–428)
- `_retry_without_thinking_max_tokens()` (L430–432)
- `_should_retry_without_thinking()` (L434–450)
- `_log_retry_without_thinking()` (L452–473)
- `_create_chat_completion_once()` (L475–538)
- `_chat_completion_create()` (L540–584)
- `_log_if_reasoning_budget_high()` (L586–622)
- `_log_if_completion_truncated()` (L624–650)
- `_extract_json_object()` (L1435–1456)
- Constants: `RETRY_WITHOUT_THINKING_MAX_TOKENS`, `RETRY_WITHOUT_THINKING_MARGIN`

**Imports needed in new file:**
```python
from openai import OpenAI
from ..config import LMSTUDIO_BASE_URL
from .llm_text import clean_llm_text_response
from .model_manager import chat_max_tokens, chat_reasoning_options
from .runtime_logging import llm_debug_enabled, log
```

**~380 lines.**

### Task 1.2: Update `ai_clues.py` imports

Replace moved code with:
```python
from .llm_client import (
    create_client, _resolve_model_name, _clean_response,
    _chat_completion_create, _response_choice, _response_message,
    _response_finish_reason, _response_content_text,
    _response_reasoning_text, _extract_json_object,
)
```

Remove the moved function bodies and constants from `ai_clues.py`.

### Task 1.3: Update all consumer imports of `create_client` and `_chat_completion_create`

Files that import from `ai_clues` and need updating:

| File | Current Import | New Import Source |
|---|---|---|
| `generator/retitle.py` | `from .core.ai_clues import create_client as create_ai_client` | `from .core.llm_client import create_client as create_ai_client` |
| `generator/repair_puzzles.py` | `from .core.ai_clues import create_client as create_ai_client` | `from .core.llm_client import create_client as create_ai_client` |
| `generator/redefine.py` | `from .core.ai_clues import create_client as create_ai_client` | `from .core.llm_client import create_client as create_ai_client` |
| `generator/clue_canon.py` | `from .core.ai_clues import create_client` | `from .core.llm_client import create_client` |
| `generator/batch_publish.py` | `from .core.ai_clues import (... create_client ...)` | Split: `create_client` from `llm_client`, rest stays |
| `generator/phases/define.py` | `from ..core.ai_clues import create_client, generate_definition` | Split: `create_client` from `llm_client` |
| `generator/phases/theme.py` | `from ..core.ai_clues import _chat_completion_create, create_client` | Both from `llm_client` |
| `generator/phases/verify.py` | `from ..core.ai_clues import (...)` | Split: keep domain fns from `ai_clues` |
| `generator/core/clue_canon.py` | lazy `from .ai_clues import create_client` (×3) | `from .llm_client import create_client` |
| `generator/core/score_helpers.py` | `from .ai_clues import (...)` | Only needs domain types, stays |
| `generator/core/puzzle_metrics.py` | `from .ai_clues import RATE_MIN_REBUS` | Stays (domain constant) |
| `tests/test_llm_debug.py` | `from generator.core.ai_clues import _chat_completion_create` | `from generator.core.llm_client import _chat_completion_create` |

### Task 1.4: Regression test

```bash
python3 -m py_compile generator/core/llm_client.py generator/core/ai_clues.py
python3 -m pytest tests/ -q  # must pass 546 tests
```

---

## Phase 2: Extract `prompt_builders.py` and `validation_guards.py`

These two modules have a one-way dependency: prompt builders consume validation helpers. Extract `validation_guards` first.

### Task 2.1: Create `generator/core/validation_guards.py`

**Move these from `ai_clues.py`:**
- Constants: `ENGLISH_MARKERS`, `RARITY_MARKERS`, `AMBIGUITY_MARKERS`, `DANGLING_ENDING_MARKERS`, `USAGE_SUFFIX_PRECEDENCE`, `USAGE_SUFFIXES`, `_TRAILING_USAGE_SUFFIX_RE`
- `_latin_word_tokens()` (L652–657)
- `find_english_marker()` (L659–664)
- `contains_english_markers()` (L666–668)
- `_definition_mentions_answer()` (L670–676)
- `_definition_is_invalid()` (L678–682)
- `_definition_describes_english_meaning()` (L791–799)
- `_guard_english_meaning_rating()` (L801–814)
- `_guard_same_family_rating()` (L1025–1036)
- `_guard_definition_centric_rating()` (L1038–1050)
- `_strip_trailing_usage_suffixes()` (L706–708)
- `_extract_definition_usage_suffix()` (L710–719)
- `_extract_usage_suffix_from_dex()` (L721–729)
- `_normalize_definition_usage_suffix()` (L731–740)
- `_build_usage_label_line()` (L742–789)
- `_validate_definition()` (L1067–1084)
- `has_prompt_residue()` (L1458–1463)
- `_same_family_feedback()` (L684–686)
- `_tokens()` (L688–692)
- `_last_word()` (L694–697)
- `_feedback_is_rarity_only()` (L699–704)
- `_clean_verify_chunk()` (L1095–1106)
- `_extract_verify_candidates()` (L1108–1140)
- `_clamp_score()` (L1059–1065)

**Imports needed:**
```python
from .clue_family import clue_uses_same_family
from .diacritics import normalize
```

**~300 lines.**

### Task 2.2: Create `generator/core/prompt_builders.py`

**Move these from `ai_clues.py`:**
- `_build_generate_prompt()` (L829–865)
- `_append_existing_canonical_definitions()` (L867–882)
- `_build_rewrite_prompt()` (L884–930)
- `_word_type_line()` (L932–935)
- `_build_verify_prompt()` (L937–951)
- `_build_rate_prompt()` (L953–994)
- `_build_clue_tiebreak_prompt()` (L996–1005)
- `_build_clue_compare_prompt()` (L1007–1016)
- `_build_puzzle_tiebreak_prompt()` (L1018–1023)
- `_family_exclusion_note()` (L816–827)
- `_augment_definition_retry_prompt()` (L1086–1093)

**Imports needed:**
```python
from ..prompts.loader import load_system_prompt, load_user_template
from .clue_family import forbidden_definition_stems
from .diacritics import normalize
from .quality import ENGLISH_HOMOGRAPH_HINTS
from .validation_guards import (
    WORD_TYPE_LABELS, _build_usage_label_line, _same_family_feedback, _family_exclusion_note,
)
```

> [!NOTE]
> `WORD_TYPE_LABELS` stays in `ai_clues.py` (used by `generate_definition()`) but is also needed by prompt builders. We'll define it in `validation_guards.py` (it's a linguistic constant) and import it where needed.

**~250 lines.**

### Task 2.3: Update `ai_clues.py` — remove moved code, add imports

```python
from .llm_client import (create_client, _resolve_model_name, ...)
from .validation_guards import (
    contains_english_markers, find_english_marker, has_prompt_residue,
    _validate_definition, _guard_same_family_rating, _guard_english_meaning_rating,
    _guard_definition_centric_rating, _normalize_definition_usage_suffix,
    _strip_trailing_usage_suffixes, _extract_usage_suffix_from_dex,
    _extract_definition_usage_suffix, _clamp_score, _clean_verify_chunk,
    _extract_verify_candidates, _feedback_is_rarity_only,
    USAGE_SUFFIXES, WORD_TYPE_LABELS, RATE_MIN_SEMANTIC, RATE_MIN_REBUS,
)
from .prompt_builders import (
    _build_generate_prompt, _build_rewrite_prompt, _build_verify_prompt,
    _build_rate_prompt, _build_clue_tiebreak_prompt, _build_clue_compare_prompt,
    _build_puzzle_tiebreak_prompt, _append_existing_canonical_definitions,
    _augment_definition_retry_prompt,
)
```

### Task 2.4: Update consumer imports

| File | Symbol | New Source |
|---|---|---|
| `tests/test_verify.py` | `contains_english_markers` | `validation_guards` |
| `tests/test_ai_clues.py` | (audit all — many symbols will move) | Split across new modules |
| `generator/core/score_helpers.py` | `contains_english_markers, find_english_marker, ...` | `validation_guards` |
| `generator/phases/verify.py` | `contains_english_markers, VerifyResult` | `validation_guards` for markers, `ai_clues` for `VerifyResult` |
| `generator/core/rewrite_engine.py` | `RewriteAttemptResult, has_prompt_residue` | `ai_clues` for dataclass, `validation_guards` for `has_prompt_residue` |

### Task 2.5: Regression test

```bash
python3 -m py_compile generator/core/validation_guards.py generator/core/prompt_builders.py generator/core/ai_clues.py
python3 -m pytest tests/ -q  # must pass 546 tests
```

---

## Phase 3: Extract `definition_referee.py`

### Task 3.1: Create `generator/core/definition_referee.py`

**Move these from `ai_clues.py`:**
- `AdaptiveRefereeBatchResult` dataclass (L196–204)
- `compare_definition_variants()` (L1597–1622)
- `compare_definition_variants_attempt()` (L1625–1641)
- `_compare_definition_variant_attempt()` (L1644–1737)
- `_remap_swapped_vote()` (L1740–1751)
- `_with_diagnostics()` (L1754–1765)
- `_build_referee_diagnostics()` (L1768–1786)
- `run_definition_referee_batch()` (L1789–1794)
- `run_definition_referee_adaptive_batch()` (L1797–1906)
- `run_definition_referee()` (L1909–1929)
- `choose_better_clue_variant()` (L1932–1959)
- `choose_better_puzzle_variant()` (L1962–1985)
- `_pick_tiebreak_winner()` (L1052–1057)

**Imports needed:**
```python
from openai import OpenAI
from ..prompts.loader import load_system_prompt
from .clue_canon import aggregate_referee_votes
from .clue_canon_types import (
    DefinitionComparisonAttempt, DefinitionComparisonVote,
    DefinitionRefereeDiagnostics, DefinitionRefereeInput, DefinitionRefereeResult,
)
from .llm_client import _chat_completion_create, _resolve_model_name, _extract_json_object
from .model_manager import PRIMARY_MODEL, SECONDARY_MODEL, chat_max_tokens
from .prompt_builders import _build_clue_tiebreak_prompt, _build_clue_compare_prompt, _build_puzzle_tiebreak_prompt
from .runtime_logging import log
```

**~250 lines.**

### Task 3.2: Update `ai_clues.py` — remove moved code, add imports

The remaining `ai_clues.py` should now contain only:
- Dataclasses: `DefinitionRating`, `VerifyResult`, `RewriteAttemptResult`, `MergeRewriteAttemptResult`, `MergeRewriteValidationResult`
- Constants: `RATE_MIN_SEMANTIC`, `RATE_MIN_REBUS`
- Core pipeline ops: `generate_definition()`, `rewrite_definition()`, `verify_definition_candidates()`, `rate_definition()`
- Canonical merge ops: `rewrite_merged_canonical_definition()`, `validate_rewritten_canonical_definition_locally()`, `validate_merged_canonical_definition()`
- `compute_rebus_score()`

**Target: ~750 lines.**

### Task 3.3: Update consumer imports

| File | Symbol | New Source |
|---|---|---|
| `generator/core/clue_canon.py` (lazy) | `run_definition_referee`, `run_definition_referee_batch`, `run_definition_referee_adaptive_batch` | `definition_referee` |
| `generator/core/clue_canon_simplify.py` | `validate_rewritten_canonical_definition_locally` | stays in `ai_clues` |
| `generator/batch_publish.py` | `choose_better_clue_variant, choose_better_puzzle_variant` | `definition_referee` |
| `generator/assessment/run_assessment.py` | `generate_definition, rate_definition, ...` | stays in `ai_clues` |
| `tests/test_ai_clues.py` | (audit all referee-related tests) | Split imports |

### Task 3.4: Regression test

```bash
python3 -m py_compile generator/core/definition_referee.py generator/core/ai_clues.py
python3 -m pytest tests/ -q  # must pass 546 tests
# Verify no circular imports:
python3 -c "from generator.core.llm_client import create_client"
python3 -c "from generator.core.validation_guards import contains_english_markers"
python3 -c "from generator.core.prompt_builders import _build_generate_prompt"
python3 -c "from generator.core.definition_referee import run_definition_referee_adaptive_batch"
python3 -c "from generator.core.ai_clues import generate_definition, rate_definition"
```

---

## Phase 4: Split `clue_canon.py` (conservative) and `batch_publish.py`

### Task 4.1: Create `generator/clue_canon_state.py`

**Move these from `generator/clue_canon.py`:**
- All `_record_to_state()` / `_record_from_state()` (L718–752)
- `_cluster_to_state()` / `_cluster_from_state()` (L754–776)
- `_merge_state_to_state()` / `_merge_state_from_state()` (L779–832)
- `_queued_word_to_state()` / `_queued_word_from_state()` (L834–880)
- `_stats_to_state()` / `_stats_from_state()` (L882–987)
- `_default_state_path()` (L989–992)
- `_config_matches_state()` (L994–1028)
- `_write_state()` (L1030–1071)
- `_load_state()` (L1073–1112)
- `_build_summary()` (L1114–1219)
- `_resume_item_is_valid()` (L1221–1223)
- `_remaining_clusters()` (L1225–1227)
- `_estimate_remaining_pair_checks()` (L1229–1239)
- `_should_defer_word()` (L1241–1253)
- `_defer_word()` (L1255–1272)
- `_reconcile_resume_state()` (L1274–1337)
- `_is_stale_waiting_resume()` (L1339–1349)
- `_normalize_stale_waiting_items()` (L1351–1374)
- Constants: `STATE_VERSION`, `STATE_FLUSH_INTERVAL_SECONDS`, `DEFAULT_STATE_PATH`

**~600 lines.**

### Task 4.2: Update `generator/clue_canon.py` imports

Replace moved code with imports from `clue_canon_state`. The remaining `clue_canon.py` should contain:
- `_PendingReferee`, `_ResolvedOutcome` dataclasses
- Data preparation: `_fetch_*`, `_enrich_rows`, `_build_boilerplate_tokens`, etc.
- Merge orchestration: `_merge_word_batch()`, `_collect_pending_referees()`, `_apply_terminal_outcome()`, etc.
- Cluster application: `_apply_clusters()`
- Audit: `_direct_legacy_code_refs()`, `run_audit()`
- CLI: `build_parser()`, `run_backfill()`, `main()`

**Target: ~1500 lines** (still large, but the state machine logic is cohesive and recently stabilized).

### Task 4.3: Regression test for `clue_canon.py` split

```bash
python3 -m py_compile generator/clue_canon.py generator/clue_canon_state.py
python3 -m pytest tests/test_clue_canon.py tests/test_clue_canon_store.py tests/test_clue_canon_simplify.py -q
python3 -m pytest tests/ -q  # full suite
```

### Task 4.4: Create `generator/rust_bridge.py`

**Move these from `generator/batch_publish.py`:**
- `_load_words()` (L142–148)
- `_metadata_by_word()` (L150–158)
- `_normalize_metadata_pool()` (L160–172)
- `_rust_binary_path()` (L174–183)
- `_quality_report_from_payload()` (L185–199)
- `_render_markdown_from_rust_payload()` (L201–238)
- `_best_candidate_rust()` (L240–305)
- `_best_candidate()` (L307–332)
- `_template_fingerprint()` (L343–345)
- `Candidate` dataclass (L107–115)

**~250 lines.**

### Task 4.5: Update `generator/batch_publish.py` imports

```python
from .rust_bridge import (
    Candidate, _best_candidate, _load_words, _metadata_by_word,
    _normalize_metadata_pool, _template_fingerprint,
)
```

### Task 4.6: Regression test for `batch_publish.py` split

```bash
python3 -m py_compile generator/rust_bridge.py generator/batch_publish.py
python3 -m pytest tests/test_batch_publish.py -q
python3 -m pytest tests/ -q  # full suite
```

---

## Final Verification

After all phases complete:

```bash
# Full test suite
python3 -m pytest tests/ -q

# Circular import check
python3 -c "
from generator.core.llm_client import create_client
from generator.core.validation_guards import contains_english_markers
from generator.core.prompt_builders import _build_generate_prompt
from generator.core.definition_referee import run_definition_referee_adaptive_batch
from generator.core.ai_clues import generate_definition, rate_definition
from generator.clue_canon_state import _write_state, _load_state
from generator.rust_bridge import _best_candidate
print('All imports OK')
"

# Line count audit
find generator -name "*.py" -exec wc -l {} + | sort -rn | head -15
```

**Expected result:** No file over ~1600 lines. `ai_clues.py` drops from 1985 to ~750. `clue_canon.py` drops from 2128 to ~1500. `batch_publish.py` drops from 1041 to ~800.

---

## File Change Summary

| File | Action | Line Delta |
|---|---|---|
| `generator/core/llm_client.py` | **NEW** | +380 |
| `generator/core/validation_guards.py` | **NEW** | +300 |
| `generator/core/prompt_builders.py` | **NEW** | +250 |
| `generator/core/definition_referee.py` | **NEW** | +250 |
| `generator/clue_canon_state.py` | **NEW** | +600 |
| `generator/rust_bridge.py` | **NEW** | +250 |
| `generator/core/ai_clues.py` | **MODIFY** | −1235 (1985→750) |
| `generator/clue_canon.py` | **MODIFY** | −600 (2128→1500) |
| `generator/batch_publish.py` | **MODIFY** | −240 (1041→800) |
| 15+ consumer files | **MODIFY** | import path changes only |
| 5+ test files | **MODIFY** | import path changes only |
