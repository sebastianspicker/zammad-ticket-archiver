"""Cover the retention policy file loader and its error messages."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.configuration.retention import RecordClassRule, load_retention_policy
from chronikwerk.configuration.validation import ConfigValidationError

_VALID = """
record_classes:
  it-authorization:
    retention: P6Y
    trigger: case_closed
    readers: [it-service-leads]
  pilot-test:
    retention: P1Y6M
    trigger: capture
    readers: [service-desk, auditors]
"""
_EXAMPLE = Path(__file__).resolve().parents[3] / "config" / "retention.example.yaml"


def _write(tmp_path: Path, text: str) -> Path:
    """Write a policy file and return its path."""
    path = tmp_path / "retention.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_policy_loads_every_class(tmp_path: Path) -> None:
    policy = load_retention_policy(_write(tmp_path, _VALID))

    assert list(policy.classes) == ["it-authorization", "pilot-test"]
    assert policy.classes["pilot-test"] == RecordClassRule(
        name="pilot-test",
        retention="P1Y6M",
        trigger="capture",
        readers=("service-desk", "auditors"),
    )


def test_shipped_example_is_a_valid_policy() -> None:
    policy = load_retention_policy(_EXAMPLE)

    assert policy.classes["pilot-test"].retention == "P30D"
    assert policy.classes["pilot-test"].trigger == "capture"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "YAML root must be a mapping"),
        ("- a\n- b\n", "YAML root must be a mapping"),
        ("a: [unclosed\n", "Invalid YAML"),
        ("record_classes: {}\n", "non-empty mapping"),
        ("record_classes: {}\nextra: 1\n", "unknown top-level keys: extra"),
        (
            "record_classes:\n  Bad_Name:\n    retention: P1Y\n    trigger: capture\n"
            "    readers: [a]\n",
            "'Bad_Name' must match",
        ),
        ("record_classes:\n  x: not-a-mapping\n", "record class 'x' must be a mapping"),
        (
            "record_classes:\n  x:\n    retention: P1Y\n    trigger: capture\n    readers: [a]\n"
            "    notes: hi\n",
            "record class 'x' has unknown keys: notes",
        ),
        (
            "record_classes:\n  x:\n    retention: P1Y\n    trigger: capture\n",
            "record class 'x' is missing keys: readers",
        ),
        (
            "record_classes:\n  x:\n    retention: 6\n    trigger: capture\n    readers: [a]\n",
            "record class 'x': retention must be an ISO 8601 duration string",
        ),
        (
            "record_classes:\n  x:\n    retention: P0D\n    trigger: capture\n    readers: [a]\n",
            "record class 'x': retention: ISO 8601 date duration 'P0D' must be positive",
        ),
        (
            "record_classes:\n  x:\n    retention: PT1H\n    trigger: capture\n    readers: [a]\n",
            "record class 'x': retention: invalid ISO 8601 date duration 'PT1H'",
        ),
        (
            "record_classes:\n  x:\n    retention: P1Y\n    trigger: closed\n    readers: [a]\n",
            "record class 'x': trigger must be one of capture, case_closed",
        ),
        (
            "record_classes:\n  x:\n    retention: P1Y\n    trigger: capture\n    readers: []\n",
            "record class 'x': readers must be a non-empty list",
        ),
    ],
)
def test_invalid_policy_names_file_and_problem(tmp_path: Path, text: str, fragment: str) -> None:
    path = _write(tmp_path, text)

    with pytest.raises(ConfigValidationError) as caught:
        load_retention_policy(path)

    assert str(path) in str(caught.value)
    assert fragment in str(caught.value)


def test_missing_file_is_a_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "absent.yaml"

    with pytest.raises(ConfigValidationError) as caught:
        load_retention_policy(path)

    assert str(path) in str(caught.value)
    assert "Unable to read retention policy" in str(caught.value)
