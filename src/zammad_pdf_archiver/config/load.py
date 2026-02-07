from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import (
    ConfigValidationError,
    ConfigValidationIssue,
    issues_from_pydantic_error,
    validate_settings,
)


def _default_config_path_if_present() -> Path | None:
    candidate = Path("config/config.yaml")
    return candidate if candidate.exists() else None


def _load_dotenv_if_present() -> None:
    dotenv_path = Path(".env")
    if dotenv_path.is_file():
        load_dotenv(dotenv_path=dotenv_path, override=False)


def _resolve_config_path(config_path: str | Path | None) -> tuple[Path | None, bool]:
    """
    Returns (path, explicit) where `explicit` is True when the user asked for this path
    (via argument or CONFIG_PATH), in which case missing files are errors.
    """
    if config_path is not None:
        return Path(config_path), True

    if (env_path := os.environ.get("CONFIG_PATH")):
        return Path(env_path), True

    return _default_config_path_if_present(), False


def _load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigValidationError(
            [ConfigValidationIssue(path=str(path), message=f"Unable to read config file: {exc}")]
        ) from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigValidationError(
            [ConfigValidationIssue(path=str(path), message="YAML root must be a mapping/object")]
        )
    return raw


def load_settings(*, config_path: str | Path | None = None) -> Settings:
    _load_dotenv_if_present()

    path, explicit = _resolve_config_path(config_path)
    yaml_data: dict[str, Any] = {}

    if path is not None:
        if not path.exists():
            if explicit:
                raise ConfigValidationError(
                    [
                        ConfigValidationIssue(
                            path="CONFIG_PATH",
                            message=f"Config file not found: {path}",
                        )
                    ]
                )
        else:
            yaml_data = _load_yaml_config(path)

    try:
        settings = Settings(**yaml_data)
    except ValidationError as exc:
        issues = issues_from_pydantic_error(exc)
        issues = _expand_required_sections(issues)
        issues = _add_hints(issues)
        raise ConfigValidationError(issues) from exc

    validate_settings(settings)
    return settings


_HINTS: dict[str, str] = {
    "zammad.base_url": "Set `ZAMMAD_BASE_URL` (or YAML `zammad.base_url`).",
    "zammad.api_token": "Set `ZAMMAD_API_TOKEN` (or YAML `zammad.api_token`).",
    "storage.root": "Set `STORAGE_ROOT` (or YAML `storage.root`).",
}


def _add_hints(issues: list[ConfigValidationIssue]) -> list[ConfigValidationIssue]:
    enriched: list[ConfigValidationIssue] = []
    for issue in issues:
        hint = _HINTS.get(issue.path)
        if hint and hint not in issue.message:
            enriched.append(ConfigValidationIssue(issue.path, f"{issue.message} {hint}"))
        else:
            enriched.append(issue)
    return enriched


def _expand_required_sections(issues: list[ConfigValidationIssue]) -> list[ConfigValidationIssue]:
    expanded: list[ConfigValidationIssue] = []
    for issue in issues:
        if issue.path == "zammad" and "Field required" in issue.message:
            expanded.append(ConfigValidationIssue("zammad.base_url", "Field required"))
            expanded.append(ConfigValidationIssue("zammad.api_token", "Field required"))
            continue
        if issue.path == "storage" and "Field required" in issue.message:
            expanded.append(ConfigValidationIssue("storage.root", "Field required"))
            continue
        expanded.append(issue)
    return expanded
