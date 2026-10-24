"""Load the documented Zammad response shapes under ``tests/fixtures/zammad``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "zammad"


def load_zammad_fixture(name: str) -> Any:
    """Return one decoded JSON fixture by file name."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
