from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.ocr.tesseract_engine import extract_text
from app.schemas import ExtractResponse, MergeRequest, SaveRequest
from app.services.export_service import save_jsonl
from app.services.extraction_service import build_pairs
from app.services.merge_service import merge_jsonl

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "data" / "output"

app = FastAPI(title="Golden Builder")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/extract", response_model=ExtractResponse)
async def extract(puzzle_title: str, clue_image: UploadFile = File(...), solution_image: UploadFile = File(...)) -> ExtractResponse:
    clue_bytes = await clue_image.read()
    solution_bytes = await solution_image.read()
    clue_text = extract_text(clue_bytes)
    solution_text = extract_text(solution_bytes)
    rows, warnings = build_pairs(puzzle_title, clue_text, solution_text)
    return ExtractResponse(rows=rows, warnings=warnings)


@app.post("/api/save-jsonl")
def save(req: SaveRequest) -> dict[str, str]:
    path = save_jsonl(OUTPUT_DIR, req.rows)
    return {"path": str(path)}


@app.post("/api/merge-jsonl")
def merge(req: MergeRequest) -> dict[str, int | str]:
    count, output = merge_jsonl(OUTPUT_DIR)
    return {"rows": count, "output": str(output)}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
