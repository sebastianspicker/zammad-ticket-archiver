from __future__ import annotations

from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_systemd_env_template_does_not_force_missing_config_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "zammad-archiver.env"
    env = _parse_env_file(env_path)

    # The YAML config is optional; default template should not force a missing file.
    assert env.get("CONFIG_PATH", "") == ""

