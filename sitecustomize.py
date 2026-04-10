from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
PACKAGE_SRC = PROJECT_ROOT / "packages" / "rebus-generator" / "src"

if PACKAGE_SRC.is_dir():
    src_text = str(PACKAGE_SRC)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)
