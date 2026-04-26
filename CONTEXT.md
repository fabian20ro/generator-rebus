# Context

## Terms

**Topic Slot**
A resumable run_all lane for one topic. Topic slots must keep progressing at their own speed; a slow topic must not block other ready topic slots.

**Model Drain**
The scheduler phase that runs ready LLM units against the currently loaded model set. The model drain owns model ordering and loaded-model efficiency.

**Generate Attempt**
The run_all lifecycle for one generated puzzle candidate, from unresolved-definition rescue through rewrite, title, and publish decision.

**Definition Evaluation**
The clue verification and rating lifecycle: single-model or pair-model vote capture, finalization, and scoring projection.

**Puzzle Session**
The frontend interaction state for one puzzle: load, input, hints, checks, progress persistence, solved-view hydration, and completion.

**Puzzle Session Adapter**
A concrete adapter around browser storage, puzzle API, clock, scoring side effects, or DOM rendering for a puzzle session.
