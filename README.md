# generator-rebus

Romanian rebus (crossword) generator. Pipeline CLI that creates puzzles from a Scrabble dictionary, generates definitions with a local LLM, and publishes them to a web frontend.

## Current map

- `generator/batch_publish.py`
  Main batch runner. Generates, evaluates, rewrites, uploads, and activates puzzles.
- `generator/core/size_tuning.py`
  Single source of truth for size-specific generation/search settings (`7` through `12`, plus `15`).
- `generator/core/pipeline_state.py`
  Internal typed working state for clues and puzzles. This is the main in-memory model used by the modern batch pipeline.
- `generator/core/selection_engine.py`
  Centralized clue and puzzle comparison logic, including deterministic ranking and tie-break routing.
- `generator/phases/define.py` and `generator/phases/verify.py`
  LLM-facing phases for definition generation, verification, and rating.
- `generator/core/quality.py`, `generator/core/constraint_solver.py`, `generator/core/grid_template.py`
  Word filtering, candidate scoring, CSP fill logic, and template generation.
- `frontend/`
  Static client app that reads published puzzles from the worker API.
- `worker/`
  Cloudflare Worker that exposes puzzle endpoints to the frontend.
- `tests/`
  Unit coverage for clue prompts, selection behavior, quality filters, title generation, and verification.
- `run_batch_loop.sh`
  Thin wrapper over the Python loop controller for repeated overnight batches (`7x7` through `12x12`).
- `generator/loop_controller.py`
  Size-resilient overnight controller that runs one size at a time and keeps going after per-size failures.

```
download → generate-grid → fill → theme → define → verify → upload → activate
   ↓           ↓            ↓       ↓        ↓         ↓        ↓         ↓
words.json  grid.md     filled.md themed.md defs.md  verified.md  (id)   (toggle)
```

## Prerequisites

- **Python 3.10+** (uses `str | None` union syntax)
- **Node.js 22+** (for frontend dev)
- **Supabase** account with the `words` table populated (same instance as propozitii-nostime)
- **LM Studio** running locally (for `theme`, `define`, `verify` phases)

## Setup

All generator commands run from the **repo root** (not from inside `generator/`).

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install Python dependencies
pip install -r generator/requirements.txt

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

Run `schema.sql` in your Supabase SQL Editor. It creates two tables (`crossword_puzzles`, `crossword_clues`) with RLS policies that allow public read access only to published puzzles. The local DDL also documents puzzle metadata such as `title_score`, `updated_at`, and repair metrics.

## Generator CLI

```bash
python -m generator <phase> <input_file> <output_file> [options]
```

### Full pipeline example

```bash
# 1. Download words from Supabase (one-time, ~70K words)
python -m generator download - generator/output/words.json

# 2. Generate a grid template
python -m generator generate-grid - generator/output/grid.md --size 10

# 3. Fill grid with dictionary words
python -m generator fill generator/output/grid.md generator/output/filled.md \
  --words generator/output/words.json

# 4. Generate a theme (requires LM Studio)
python -m generator theme generator/output/filled.md generator/output/themed.md

# 5. Generate definitions (requires LM Studio)
python -m generator define generator/output/themed.md generator/output/defs.md

# --- Edit generator/output/defs.md manually if needed ---

# 6. Verify definitions (AI tries to guess each word)
python -m generator verify generator/output/defs.md generator/output/verified.md

# --- Fix any definitions marked ✗, then re-verify or continue ---

# 7. Upload to Supabase (starts unpublished)
python -m generator upload generator/output/verified.md -

# 8. Publish it
python -m generator activate <puzzle-id-from-step-7>
```

### Phases

| Phase | Input | Output | Needs Supabase | Needs LM Studio |
|-------|-------|--------|:-:|:-:|
| `download` | `-` | `words.json` | yes | no |
| `generate-grid` | `-` | `grid.md` | no | no |
| `fill` | `grid.md` | `filled.md` | no | no |
| `theme` | `filled.md` | `themed.md` | no | yes |
| `define` | `themed.md` | `defs.md` | no | yes |
| `verify` | `defs.md` | `verified.md` | no | yes |
| `upload` | `verified.md` | `-` (prints ID) | yes | no |
| `activate` | `<puzzle-id>` | (none) | yes | no |
| `deactivate` | `<puzzle-id>` | (none) | yes | no |

### Options

| Flag | Default | Used by | Description |
|------|---------|---------|-------------|
| `--size` | `10` | `generate-grid` | Grid size: `7`, `8`, `9`, `10`, `11`, `12`, or `15` |
| `--words` | (required) | `fill` | Path to `words.json` |
| `--max-backtracks` | `50000` | `fill` | Solver gives up after this many backtracks |
| `--max-rarity` | `5` | `fill` | Filter out words with rarity > N (1-5 scale) |
| `--force` | off | `upload` | Upload even if some definitions failed verification |

## Things that aren't obvious

**`-` as input/output.** Phases that don't read a file (`download`, `generate-grid`) take `-` as their input_file. `upload` takes `-` as output because it prints the puzzle ID to stdout instead.

**`fill` requires `--words`.** The solver needs the word list downloaded in step 1. Pass it explicitly:
```bash
python -m generator fill grid.md filled.md --words generator/output/words.json
```

**`activate`/`deactivate` use the puzzle ID as the input argument.** There's no file involved:
```bash
python -m generator activate a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**The solver can fail.** If `fill` says "Failed to find a solution", either:
- Re-run it (grids are random, another template may work)
- Raise the limit: `--max-backtracks 200000`
- Try a smaller grid: `--size 7`

**`generator/output/` is gitignored.** Use it freely as your workspace for intermediate files.

**LM Studio model.** The `theme`/`define`/`verify` phases connect to LM Studio's OpenAI-compatible API and use model name `"default"`. Make sure LM Studio is running with a model loaded before running these phases.

**You can skip phases.** The markdown format is human-readable and editable. You can:
- Write definitions manually instead of using `define`
- Skip `verify` and use `--force` on upload
- Edit any intermediate `.md` file in a text editor between phases

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
cd frontend
npm install
npm run dev     # local dev server
npm run build   # production build → dist/
```

The frontend needs `VITE_API_BASE` set to the Cloudflare Worker URL. For local dev, you can either:
- Point it to a local worker (`wrangler dev` in the `worker/` folder)
- Hard-code a deployed worker URL in `src/db/puzzle-repository.ts`

### GitHub Pages deploy

The `.github/workflows/deploy-frontend.yml` action builds and deploys to Pages on push to `main`. It needs these GitHub Secrets:

| Secret | Description |
|--------|-------------|
| `VITE_API_BASE` | Cloudflare Worker URL (e.g. `https://rebus-api.your-account.workers.dev`) |

## Cloudflare Worker

The `worker/` folder contains a Cloudflare Worker that proxies frontend requests to Supabase, adding auth headers so the Supabase key is never exposed in the browser.

It auto-deploys from the `worker/` folder. To set its secrets:

```bash
cd worker
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
├── generator/                     # Python CLI (run from repo root)
│   ├── rebus.py                   # CLI entry point
│   ├── requirements.txt           # Python deps: supabase, openai, python-dotenv
│   ├── config.py                  # Reads .env
│   ├── phases/                    # One module per pipeline phase
│   │   ├── download.py
│   │   ├── generate_grid.py
│   │   ├── fill.py
│   │   ├── theme.py
│   │   ├── define.py
│   │   ├── verify.py
│   │   ├── upload.py
│   │   └── activate.py
│   ├── core/                      # Shared logic
│   │   ├── diacritics.py          # ă→A, â→A, î→I, ș→S, ț→T
│   │   ├── word_index.py          # Length-bucketed positional index
│   │   ├── grid_template.py       # Procedural template generation
│   │   ├── slot_extractor.py      # Find H/V word slots + intersections
│   │   ├── constraint_solver.py   # CSP backtracking with MRV
│   │   └── markdown_io.py         # Read/write the markdown format
│   └── output/                    # Gitignored workspace
├── frontend/                      # Vanilla TS + Vite
├── worker/                        # Cloudflare Worker (Supabase proxy)
├── schema.sql                     # Database DDL
├── .env.example                   # Template for credentials
└── .github/workflows/
    └── deploy-frontend.yml        # GitHub Pages deploy
```
