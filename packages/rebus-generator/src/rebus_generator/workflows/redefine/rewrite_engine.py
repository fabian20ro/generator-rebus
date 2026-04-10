from rebus_generator.platform.io.runtime_logging import audit
from rebus_generator.platform.llm.ai_clues import generate_definition, rewrite_definition
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.domain.score_helpers import _update_best_clue_version
from rebus_generator.workflows.generate.verify import rate_working_puzzle, verify_working_puzzle

from .rewrite_rounds import (
    HYBRID_REBUS_THRESHOLD,
    _build_pending_candidates,
    _definition_key,
    _evaluate_single_candidate,
    _select_hybrid_candidate,
    _should_try_hybrid,
    rewrite_session_finalize_round,
    rewrite_session_prepare_round,
    rewrite_session_score_round,
    run_rewrite_loop,
)
from .rewrite_session import (
    PendingCandidate,
    RewriteLoopResult,
    RewriteRoundState,
    RewriteSession,
    RewriteWordOutcome,
    finish_rewrite_session,
    rewrite_session_initial_rate,
    rewrite_session_initial_verify,
    start_rewrite_session,
)
