"""Load files and environment overrides into validated configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from chronikwerk.configuration.models import Settings, canonicalize_zammad_origin
from chronikwerk.configuration.revisions import ManagedConfigError, ManagedConfigStore, deep_merge
from chronikwerk.configuration.validation import (
    ConfigValidationError,
    ConfigValidationIssue,
    issues_from_pydantic_error,
    validate_settings,
)


def _default_config_path_if_present() -> Path | None:
    candidate = Path("config/config.yaml")
    return candidate if candidate.exists() else None


def _resolve_config_path(config_path: str | Path | None) -> tuple[Path | None, bool]:
    """
    Returns (path, explicit) where `explicit` is True when the user asked for this path
    (via argument or CONFIG_PATH), in which case missing files are errors.
    """
    if config_path is not None:
        return Path(config_path), True

    if env_path := os.environ.get("CONFIG_PATH"):
        return Path(env_path), True

    return _default_config_path_if_present(), False


def _load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigValidationError(
            [ConfigValidationIssue(path=str(path), message=f"Unable to read config file: {exc}")]
        ) from exc
    except UnicodeError as exc:
        raise ConfigValidationError(
            [ConfigValidationIssue(path=str(path), message="Config file must be valid UTF-8")]
        ) from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = ""
        if mark is not None:
            location = f" at line {mark.line + 1}, column {mark.column + 1}"
        raise ConfigValidationError(
            [ConfigValidationIssue(path=str(path), message=f"Invalid YAML{location}")]
        ) from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigValidationError(
            [ConfigValidationIssue(path=str(path), message="YAML root must be a mapping/object")]
        )
    return raw


def load_settings(
    *,
    config_path: str | Path | None = None,
    include_managed: bool = True,
) -> Settings:
    """Load settings using env > YAML/init > dotenv > file secrets > defaults."""
    path, explicit = _resolve_config_path(config_path)
    yaml_data = _yaml_data(path, explicit=explicit)
    canonical_overrides = _canonical_process_env_overrides()
    settings = _settings_from_mapping(deep_merge(yaml_data, canonical_overrides), add_hints=True)
    if include_managed:
        settings = _apply_managed_overlay(settings, yaml_data, canonical_overrides)
    return settings


def _yaml_data(path: Path | None, *, explicit: bool) -> dict[str, Any]:
    if path is None:
        return {}
    if path.exists():
        return _load_yaml_config(path)
    if explicit:
        raise ConfigValidationError(
            [
                ConfigValidationIssue(
                    path="CONFIG_PATH",
                    message=f"Config file not found: {path}",
                )
            ]
        )
    return {}


def _settings_from_mapping(data: dict[str, Any], *, add_hints: bool) -> Settings:
    try:
        settings = Settings(**data)
    except ValidationError as exc:
        issues = issues_from_pydantic_error(exc)
        if add_hints:
            issues = _add_hints(_expand_required_sections(issues))
        raise ConfigValidationError(issues) from exc
    validate_settings(settings)
    return settings


def _apply_managed_overlay(
    settings: Settings,
    yaml_data: dict[str, Any],
    canonical_overrides: dict[str, Any],
) -> Settings:
    overlay_path = settings.admin.state_dir / "managed-config.json"
    if not settings.admin.enabled and not overlay_path.exists():
        return settings
    try:
        overlay = ManagedConfigStore(settings.admin.state_dir).load()
    except (ManagedConfigError, OSError, ValueError) as exc:
        raise ConfigValidationError(
            [ConfigValidationIssue(path="admin.state_dir", message=str(exc))]
        ) from exc
    if not overlay:
        return settings
    return _settings_from_mapping(
        deep_merge(deep_merge(yaml_data, overlay), canonical_overrides), add_hints=False
    )


_CANONICAL_ENV_ALIASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ZAMMAD_ORIGIN", "ZAMMAD__BASE_URL", ("zammad", "base_url")),
    ("ZAMMAD_API_TOKEN", "ZAMMAD__API_TOKEN", ("zammad", "api_token")),
    ("ZAMMAD_TIMEOUT_SECONDS", "ZAMMAD__TIMEOUT_SECONDS", ("zammad", "timeout_seconds")),
    (
        "ZAMMAD_ALLOW_PRIVATE_ORIGIN",
        "HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS",
        ("hardening", "transport", "allow_private_networks"),
    ),
    (
        "ZAMMAD_TRUST_ENV",
        "HARDENING__TRANSPORT__TRUST_ENV",
        ("hardening", "transport", "trust_env"),
    ),
)


def _canonical_process_env_overrides() -> dict[str, Any]:
    """Map canonical process aliases while rejecting ambiguous legacy values safely."""
    overrides: dict[str, Any] = {}
    for canonical_key, legacy_key, path in _CANONICAL_ENV_ALIASES:
        canonical_value = os.environ.get(canonical_key)
        if canonical_value is None:
            continue
        legacy_value = os.environ.get(legacy_key)
        if legacy_value is not None and not _aliases_match(
            canonical_key, canonical_value, legacy_value
        ):
            raise ConfigValidationError(
                [
                    ConfigValidationIssue(
                        path=canonical_key,
                        message=(
                            f"conflicts with {legacy_key}; set only one value or make them agree"
                        ),
                    )
                ]
            )
        target = overrides
        for part in path[:-1]:
            target = target.setdefault(part, {})
        target[path[-1]] = canonical_value
    return overrides


def _aliases_match(canonical_key: str, canonical_value: str, legacy_value: str) -> bool:
    """Compare aliases by their parsed value without ever including secrets in errors."""
    try:
        if canonical_key == "ZAMMAD_ORIGIN":
            return canonicalize_zammad_origin(canonical_value) == canonicalize_zammad_origin(
                legacy_value
            )
        if canonical_key == "ZAMMAD_TIMEOUT_SECONDS":
            return float(canonical_value) == float(legacy_value)
        if canonical_key in {"ZAMMAD_ALLOW_PRIVATE_ORIGIN", "ZAMMAD_TRUST_ENV"}:
            truthy = {"1", "true", "on", "yes", "y", "t"}
            falsy = {"0", "false", "off", "no", "n", "f"}
            left = canonical_value.strip().lower()
            right = legacy_value.strip().lower()
            if left in truthy | falsy and right in truthy | falsy:
                return (left in truthy) == (right in truthy)
            return False
    except ValueError:
        return False
    return canonical_value == legacy_value


_HINTS: dict[str, str] = {
    "zammad.base_url": (
        "Set `ZAMMAD_ORIGIN`, legacy `ZAMMAD__BASE_URL`, or YAML `zammad.base_url`."
    ),
    "zammad.api_token": (
        "Set `ZAMMAD_API_TOKEN`, legacy `ZAMMAD__API_TOKEN`, or YAML `zammad.api_token`."
    ),
    "storage.root": "Set `STORAGE__ROOT` (or YAML `storage.root`).",
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
