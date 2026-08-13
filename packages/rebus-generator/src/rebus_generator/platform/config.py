from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return value


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return value


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234")
VERIFY_CANDIDATE_COUNT = max(1, min(5, _int_env("VERIFY_CANDIDATE_COUNT", 3)))
PUBLICATION_MIN_PASS_RATE = max(
    0.0,
    min(1.0, _float_env("PUBLICATION_MIN_PASS_RATE", 0.5)),
)
PUBLICATION_MIN_REBUS_SCORE = max(
    1,
    min(10, _int_env("PUBLICATION_MIN_REBUS_SCORE", 5)),
)
