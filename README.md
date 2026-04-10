# generator-rebus

Romanian rebus (crossword) generator. Pipeline CLI that creates puzzles from a Scrabble dictionary, generates definitions with a local LLM, and publishes them to a web frontend.

## Current map

- `packages/rebus-generator/src/rebus_generator/workflows/generate/service.py`
  Main generation workflow. prepares candidate grids, runs definition/rewrite/title passes, publishes.
- `packages/rebus-generator/src/rebus_generator/domain/`
  Puzzle/clue state, scoring, selection, text rules, shared business logic.
- `packages/rebus-generator/src/rebus_generator/platform/llm/`
  LM Studio client/runtime/model registry and prompt-facing helpers.
- `packages/rebus-generator/src/rebus_generator/evaluation/`
  Assessment runs, datasets, campaign policy, reports, prompt-lab tooling.
- `engines/crossword-engine/`
  Rust crossword fill engine, split by generation/solver/template/model/quality capability.
- `apps/frontend/`
  Static client app that reads published puzzles from the worker API.
- `apps/worker/`
  Cloudflare Worker that exposes puzzle endpoints to the frontend.
- `tests/`
  Unit coverage for clue prompts, selection behavior, quality filters, title generation, and verification.
- `run_all.sh`
  Only unattended production entrypoint. One active slot each for `generate`, `redefine`, `retitle`, `simplify`.

```
run_all / cli
   ↓
workflows/
  generate    redefine    retitle    canonicals    run_all
   ↓
platform/llm + platform/persistence + platform/io
   ↓
engines/crossword-engine + Supabase + LM Studio + apps/worker + apps/frontend
```

## Prerequisites

- **Python 3.10+** (uses `str | None` union syntax)
- **Node.js 22+** (for frontend dev)
- **Supabase** account with the `words` table populated (same instance as propozitii-nostime)
- **LM Studio** running locally (for `theme`, `define`, `verify` phases)

## Setup

All generator commands run from the repo root.

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install Python dependencies
pip install -r packages/rebus-generator/requirements.txt

# Copy and fill in your credentials
cp .env.example .env
```

The `.env` file needs:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
LMSTUDIO_BASE_URL=http://127.0.0.1:1234
```

### Database setup

Fresh install: run [schema.sql](/Users/fabian/git/generator-rebus/db/schema.sql) in Supabase SQL Editor. It creates:
- `crossword_puzzles`
- `crossword_clues`
- canonical clue library tables
- `crossword_clue_effective` view

Existing install: apply migrations in `db/migrations/` first.

## Generator CLI

```bash
python -m rebus_generator <phase> <input_file> <output_file> [options]
```

Supported CLI phases:
- `download`
- `theme`
- `define`
- `verify`
- `upload`
- `activate`
- `deactivate`

Typical manual flow:

```bash
python -m rebus_generator download - build/words.json
python -m rebus_generator theme build/filled.md build/themed.md
python -m rebus_generator define build/themed.md build/defs.md
python -m rebus_generator verify build/defs.md build/verified.md
python -m rebus_generator upload build/verified.md -
python -m rebus_generator activate <puzzle-id>
```

For unattended generation + improvement, use:

```bash
./run_all.sh --debug
```

## Things that aren't obvious

**`-` as input/output.** `download` uses `-` for no input. `upload` uses `-` for no output because it prints the puzzle id.

**Artifacts go under `build/`.** Word caches, batch runs, assessments, and logs should not live inside source packages.

**LM Studio models.** The pipeline uses a two-model workflow via LM Studio's OpenAI-compatible API. The active pair defaults to `gemma-4` + `eurollm-22b`; registry/policy live in [models.py](/Users/fabian/git/generator-rebus/packages/rebus-generator/src/rebus_generator/platform/llm/models.py) and LM Studio REST helpers in [lm_studio_api.py](/Users/fabian/git/generator-rebus/packages/rebus-generator/src/rebus_generator/platform/llm/lm_studio_api.py).

**You can skip phases.** The markdown format is human-readable and editable. You can:
- Write definitions manually instead of using `define`
- Skip `verify` and use `--force` on upload
- Edit any intermediate `.md` file in a text editor between phases

## Canonical clue maintenance

Canonical clue cleanup now has two surfaces:

```bash
# Audit canonical library health
python -m rebus_generator.workflows.canonicals.service audit

# One-off simplify maintenance
python -m rebus_generator.workflows.canonicals.service simplify-fanout --dry-run
python -m rebus_generator.workflows.canonicals.service simplify-fanout --apply
```

Notes:
- `audit` checks pointer integrity, superseded links, duplicate active canonicals, oversized fanout, and effective-view coverage
- `simplify-fanout` prefers the best existing canonical survivor; it only rewrites a new survivor when same-sense inputs are all weak
- unattended simplify now runs only through `./run_all.sh --topics simplify`

## `run_all` supervisor

For unattended mixed work, use:

```bash
./run_all.sh --debug
```

Current shape:
- supported topics: `generate`, `redefine`, `retitle`, `simplify`
- single-process supervisor/orchestrator with one active job slot per topic and local claims
- one shared LM Studio runtime with queue telemetry for admissions, step batches, switches, and heartbeats
- jobs keep in-memory stage/state and advance in small steps across topics
- puzzle topics claim work by `puzzle_id`
- `simplify` is excluded from words currently owned by active puzzle jobs

Current limitation:
- protection is local to one supervisor process
- manual legacy entrypoints or a second process can still race because claims are in-memory, not DB-backed

Architecture note:
- current system is not a durable event bus
- no replay, pub/sub subscriber graph, or multi-consumer idempotent event handling

## Markdown format

Each phase progressively adds to the same markdown structure:

| Phase | What it adds |
|-------|-------------|
| `generate-grid` | Grid template (`.` for letters, `#` for black squares) |
| `fill` | Letters in grid + word lists under Orizontal/Vertical |
| `theme` | Updates the title (`# Rebus: Your Theme Here`) |
| `define` | Adds `[original_word] - Definition text` to each word |
| `verify` | Prepends checkmarks to each definition |

A fully processed file looks like:

```markdown
# Rebus: Natură și Anotimpuri

Dimensiune: 10x10

## Grid

C A S A # M A R E #
O . . . # . . . . #
...

## Orizontal

1. ✓ CASA [casă] - Locul unde te simți acasă
1. ✓ MARE [mare] - Întindere de apă sărată
2. ✗ OI [oi] - Animale de la stână → AI a ghicit: CAPRE

## Vertical

1. ✓ COPAC [copac] - Îl urci când ești copil
...
```

## Frontend

Vanilla TypeScript + Vite. No framework. Reads puzzle data from a Cloudflare Worker proxy.

```bash
cd apps/frontend
npm install
npm run dev     # local dev server
npm run build   # production build → dist/
```

The frontend needs `VITE_API_BASE` set to the Cloudflare Worker URL. For local dev, either point it at `wrangler dev` in `apps/worker` or use the deployed worker URL.

### GitHub Pages deploy

The `.github/workflows/deploy-frontend.yml` action builds and deploys to Pages on push to `main`. It needs these GitHub Secrets:

| Secret | Description |
|--------|-------------|
| `VITE_API_BASE` | Cloudflare Worker URL (e.g. `https://rebus-api.your-account.workers.dev`) |

## Cloudflare Worker

The `apps/worker/` folder contains a Cloudflare Worker that proxies frontend requests to Supabase, adding auth headers so the Supabase key is never exposed in the browser.

To set its secrets:

```bash
cd apps/worker
npm install
npx wrangler secret put SUPABASE_URL
npx wrangler secret put SUPABASE_ANON_KEY
```

Routes:
- `GET /puzzles` — list published puzzles
- `GET /puzzles/:id` — puzzle with template + clues (no solution)
- `GET /puzzles/:id/solution` — just the solution grid (for checking answers)
- `GET /health` — health check

## Project structure

```
generator-rebus/
├── apps/
│   ├── frontend/                  # Vanilla TS + Vite
│   └── worker/                    # Cloudflare Worker (Supabase proxy)
├── engines/
│   └── crossword-engine/          # Rust generator/fill engine
├── packages/
│   └── rebus-generator/
│       ├── requirements.txt
│       └── src/rebus_generator/   # Python generator package
├── db/
│   ├── migrations/
│   └── schema.sql                 # Database DDL
├── docs/
│   └── architecture/
├── .env.example                   # Template for credentials
└── .github/workflows/
    └── deploy-frontend.yml        # GitHub Pages deploy
```
