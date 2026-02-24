from __future__ import annotations

from pathlib import Path


def _parse_env_example(repo_root: Path) -> dict[str, str]:
    """
    Parse `.env.example` as a simple KEY=VALUE file:
    - ignores blank lines and comments
    - keeps the last occurrence of a key
    """
    values: dict[str, str] = {}
    try:
        lines = (repo_root / ".env.example").read_text("utf-8").splitlines()
    except PermissionError:
        import pytest
        pytest.skip("PermissionError reading .env.example (system locked)")
        
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_env_example_does_not_force_missing_config_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = _parse_env_example(repo_root)

    # `CONFIG_PATH` is optional; setting it to a missing file causes startup to fail.
    assert env.get("CONFIG_PATH", "") == ""


def test_env_example_uses_canonical_zammad_base_url_var() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = _parse_env_example(repo_root)

    # The service supports legacy aliases, but the example should be canonical.
    assert "ZAMMAD_BASE_URL" in env
    assert "ZAMMAD_URL" not in env

