from __future__ import annotations

from PIL import Image


def extract_text(image_bytes: bytes) -> str:
    try:
        import pytesseract  # type: ignore
    except Exception:
        return ""
    image = Image.open(__import__("io").BytesIO(image_bytes))
    return pytesseract.image_to_string(image, lang="ron")
