# Golden Builder

Local-only helper for extracting crossword solution-definition pairs from two images.

## Run

```bash
cd tools/golden-builder
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

Open http://127.0.0.1:8765
