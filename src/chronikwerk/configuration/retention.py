"""Load and validate the record-class retention policy file."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from chronikwerk.configuration.validation import ConfigValidationError, ConfigValidationIssue
from chronikwerk.timestamps import parse_iso8601_date_duration

_CLASS_NAME = re.compile(r"^[a-z0-9-]{1,64}$")
_TRIGGERS = ("capture", "case_closed")
_RULE_KEYS = frozenset({"retention", "trigger", "readers"})


@dataclass(frozen=True)
class RecordClassRule:
    """Describe the retention rule of one record class."""

    name: str
    retention: str
    trigger: Literal["capture", "case_closed"]
    readers: tuple[str, ...]


@dataclass(frozen=True)
class RetentionPolicy:
    """Hold every record class rule keyed by class name."""

    classes: Mapping[str, RecordClassRule]


def _fail(path: Path, message: str) -> ConfigValidationError:
    return ConfigValidationError([ConfigValidationIssue(path=str(path), message=message)])


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _fail(path, f"Unable to read retention policy: {exc}") from exc
    except UnicodeError as exc:
        raise _fail(path, "Retention policy must be valid UTF-8") from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise _fail(path, f"Invalid YAML{location}") from exc


def _parse_retention(path: Path, where: str, retention: object) -> str:
    if not isinstance(retention, str):
        raise _fail(path, f"{where}: retention must be an ISO 8601 duration string such as P6Y")
    try:
        parse_iso8601_date_duration(retention)
    except ValueError as exc:
        raise _fail(path, f"{where}: retention: {exc}") from exc
    return retention


def _parse_readers(path: Path, where: str, readers: object) -> tuple[str, ...]:
    if (
        not isinstance(readers, list)
        or not readers
        or not all(isinstance(item, str) and item.strip() for item in readers)
    ):
        raise _fail(path, f"{where}: readers must be a non-empty list of group names")
    return tuple(item.strip() for item in readers)


def _parse_rule(path: Path, name: str, raw: object) -> RecordClassRule:
    where = f"record class {name!r}"
    if not isinstance(raw, dict):
        raise _fail(path, f"{where} must be a mapping with retention, trigger and readers")
    unknown = sorted(str(key) for key in raw if key not in _RULE_KEYS)
    if unknown:
        raise _fail(path, f"{where} has unknown keys: {', '.join(unknown)}")
    missing = sorted(_RULE_KEYS - raw.keys())
    if missing:
        raise _fail(path, f"{where} is missing keys: {', '.join(missing)}")
    trigger = raw["trigger"]
    if trigger not in _TRIGGERS:
        raise _fail(path, f"{where}: trigger must be one of {', '.join(_TRIGGERS)}")
    return RecordClassRule(
        name=name,
        retention=_parse_retention(path, where, raw["retention"]),
        trigger=cast(Literal["capture", "case_closed"], trigger),
        readers=_parse_readers(path, where, raw["readers"]),
    )


def load_retention_policy(path: Path) -> RetentionPolicy:
    """Read and validate a retention policy file; raise ConfigValidationError naming the file."""
    raw: Any = _read_yaml(path)
    if not isinstance(raw, dict):
        raise _fail(path, "YAML root must be a mapping with a record_classes key")
    unknown = sorted(str(key) for key in raw if key != "record_classes")
    if unknown:
        raise _fail(path, f"unknown top-level keys: {', '.join(unknown)}")
    classes = raw.get("record_classes")
    if not isinstance(classes, dict) or not classes:
        raise _fail(path, "record_classes must be a non-empty mapping of class name to rule")
    rules: dict[str, RecordClassRule] = {}
    for name, rule in classes.items():
        if not isinstance(name, str) or _CLASS_NAME.fullmatch(name) is None:
            raise _fail(path, f"record class name {name!r} must match [a-z0-9-]{{1,64}}")
        rules[name] = _parse_rule(path, name, rule)
    return RetentionPolicy(classes=rules)
