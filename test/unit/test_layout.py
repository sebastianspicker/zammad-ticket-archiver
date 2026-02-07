from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.adapters.storage.layout import build_filename, build_target_dir


def test_build_target_dir_is_deterministic_and_safe() -> None:
    root = Path("/archive/root")
    out = build_target_dir(root, "user@example.com", ["My Team", "Tickets"])
    assert out == Path("/archive/root/user_example.com/My_Team/Tickets")


def test_build_target_dir_rejects_traversal_attempts() -> None:
    root = Path("/archive/root")
    with pytest.raises(ValueError):
        build_target_dir(root, "user", [".."])
    with pytest.raises(ValueError):
        build_target_dir(root, "user", ["a/b"])


def test_build_target_dir_sanitizes_unicode_only_segment() -> None:
    root = Path("/archive/root")
    out = build_target_dir(root, "user", ["你好"])
    assert out == Path("/archive/root/user/_")


def test_build_target_dir_enforces_allow_prefixes() -> None:
    root = Path("/archive/root")
    out = build_target_dir(
        root,
        "user@example.com",
        ["Customers", "ACME GmbH", "2026"],
        allow_prefixes=["Customers > ACME GmbH"],
    )
    assert out == Path("/archive/root/user_example.com/Customers/ACME_GmbH/2026")

    with pytest.raises(ValueError):
        build_target_dir(
            root,
            "user@example.com",
            ["Customers", "ACME GmbH", "2026"],
            allow_prefixes=["Customers > Other"],
        )


def test_build_target_dir_allow_prefixes_accepts_slash_separator() -> None:
    root = Path("/archive/root")
    out = build_target_dir(
        root,
        "user@example.com",
        ["Customers", "ACME GmbH", "2026"],
        allow_prefixes=["Customers/ACME GmbH"],
    )
    assert out == Path("/archive/root/user_example.com/Customers/ACME_GmbH/2026")


def test_build_filename_is_deterministic() -> None:
    assert build_filename(123, "2026-02-07", "Hello world") == "123-2026-02-07-Hello_world"


def test_build_filename_sanitizes_path_separators() -> None:
    assert build_filename("123", "2026-02-07", "hello/there") == "123-2026-02-07-hello_there"
