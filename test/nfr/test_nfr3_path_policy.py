"""NFR3: Validate and confine all storage paths under storage.root; reject path traversal."""
from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.adapters.storage.fs_storage import write_atomic_bytes


def test_nfr3_path_outside_root_rejected(tmp_path: Path) -> None:
    """NFR3: Target path outside storage root must be rejected."""
    root = tmp_path / "archive"
    root.mkdir()
    target_outside = tmp_path / "outside" / "file.pdf"
    target_outside.parent.mkdir(parents=True)
    with pytest.raises(ValueError, match="escapes root"):
        write_atomic_bytes(
            target_outside,
            b"data",
            storage_root=root,
            fsync=False,
        )


def test_nfr3_symlink_traversal_rejected(tmp_path: Path) -> None:
    """NFR3: Path traversing symlink under root must be rejected."""
    root = tmp_path / "archive"
    root.mkdir()
    (root / "safe").mkdir()
    escape = tmp_path / "escape"
    escape.mkdir()
    (root / "safe" / "link").symlink_to(escape)
    target_via_symlink = root / "safe" / "link" / "file.pdf"
    # Resolved path escapes root, so ensure_within_root or symlink check raises.
    with pytest.raises(ValueError, match=r"symlink|escapes root"):
        write_atomic_bytes(
            target_via_symlink,
            b"data",
            storage_root=root,
            fsync=False,
        )
