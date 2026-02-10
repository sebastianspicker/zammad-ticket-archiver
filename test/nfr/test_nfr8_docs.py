"""NFR8: Document Zammad setup, path policy, signing, storage, operations, security."""
from __future__ import annotations

from pathlib import Path


def test_nfr8_key_docs_exist() -> None:
    """NFR8: Key documentation files must exist."""
    repo_root = Path(__file__).resolve().parents[2]
    docs = repo_root / "docs"
    required = [
        "00-overview.md",
        "01-architecture.md",
        "02-zammad-setup.md",
        "04-path-policy.md",
        "06-signing-and-timestamp.md",
        "07-storage.md",
        "08-operations.md",
        "09-security.md",
        "api.md",
        "config-reference.md",
    ]
    missing = [f for f in required if not (docs / f).is_file()]
    assert not missing, f"Missing docs: {missing}"
