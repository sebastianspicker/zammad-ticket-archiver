"""Verify operator action hints select the right advice and name the configured trigger tag."""

from __future__ import annotations

import errno

from pydantic import BaseModel, ValidationError

from chronikwerk.archiving.error_policy import classify
from chronikwerk.archiving.notes import action_hint

_FIELD_HINT = "Fix ticket fields / path policy validation"
_PERMISSION_HINT = "Storage permission denied"


class _Model(BaseModel):
    """Provide a pydantic model whose validation failure is a ValueError subclass."""

    archive_path: int


def _hint(exc: Exception, trigger_tag: str = "pdf:sign") -> str:
    """Return the action hint for an exception under the production classification."""
    return action_hint(exc, classified=classify(exc), trigger_tag=trigger_tag)


def test_permission_denied_os_error_gets_permission_hint() -> None:
    """EACCES and EPERM produce the storage permission hint."""
    assert _PERMISSION_HINT in _hint(PermissionError(errno.EACCES, "denied"))
    assert _PERMISSION_HINT in _hint(OSError(errno.EPERM, "not permitted"))


def test_path_policy_value_error_gets_field_hint() -> None:
    """A plain path-policy ValueError produces the ticket field hint."""
    assert _FIELD_HINT in _hint(ValueError("archive_path must not be empty"))


def test_pydantic_validation_error_does_not_get_field_hint() -> None:
    """ValueError subclasses from other layers fall back to the generic hint."""
    try:
        _Model.model_validate({"archive_path": "not-a-number"})
    except ValidationError as exc:
        hint = _hint(exc)
    assert _FIELD_HINT not in hint
    assert hint.startswith("Non-retryable failure by policy.")


def test_configured_trigger_tag_replaces_default_in_hints() -> None:
    """Permanent hints name the configured trigger tag; transient ones retry automatically."""
    transient = _hint(OSError(errno.EAGAIN, "retry later"), "archive:now")
    permanent = _hint(ValueError("archive_path must not be empty"), "archive:now")
    assert "retried automatically" in transient
    assert "archive:now" in permanent
    assert "pdf:sign" not in transient + permanent
