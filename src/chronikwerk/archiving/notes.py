"""Create safe, concise Zammad notes describing archival outcomes."""

import errno
from dataclasses import dataclass
from html import escape

import structlog

from chronikwerk._version import VERSION
from chronikwerk.archiving.tags import ERROR_TAG
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.redaction import scrub_secrets_in_text

log = structlog.get_logger(__name__)

_ErrorHintRule = tuple[tuple[str, ...], tuple[str, ...], str, str]

_ERROR_HINT_RULES: tuple[_ErrorHintRule, ...] = (
    (
        ("archive_path", "missing"),
        (),
        "missing_archive_path",
        "Set the archive_path ticket field.",
    ),
    (
        (),
        ("archive_path must not be empty", "all segments were empty"),
        "empty_archive_path",
        "Set archive_path to at least one non-empty segment.",
    ),
    (
        (),
        ("archive_path must be a string", "archive_path["),
        "invalid_archive_path",
        "Use a string or list of strings for archive_path.",
    ),
    (
        ("chronikwerk api user",),
        (),
        "service_account_as_agent",
        "Apply the archive macro in Zammad instead of retrying, or use fixed mode.",
    ),
    (
        (),
        ("owner.login", "updated_by.login"),
        "missing_user_login",
        "Assign an owner (owner mode) or check that a last editor exists (current_agent).",
    ),
    (
        (),
        ("archive_user", "archive_user_mode"),
        "missing_archive_user",
        "Set the archive_user ticket field for fixed mode.",
    ),
    (
        ("filename",),
        ("pattern", "segment", "must not"),
        "invalid_filename",
        "Check filename_pattern and path policy (no ., .., separators).",
    ),
    (
        (),
        ("path segment", "path separators", "dot segments"),
        "path_validation",
        "Check archive_path segments (no ., .., empty, or separators).",
    ),
)


@dataclass(frozen=True)
class SuccessNotePayload:
    """Values rendered into one successful archival acknowledgement."""

    storage_dir: str
    filename: str
    sidecar_path: str
    size_bytes: int
    sha256_hex: str
    request_id: str | None
    delivery_id: str | None
    timestamp_utc: str


@dataclass(frozen=True)
class ErrorNotePayload:
    """Values rendered into one archival failure acknowledgement."""

    classification: str
    message: str
    action: str
    request_id: str | None
    delivery_id: str | None
    timestamp_utc: str
    code: str = ""
    hint: str = ""


def _html_field_list(heading: str, fields: list[tuple[str, str]]) -> str:
    """Build an HTML note with a heading and escaped key/value list."""
    items = "".join(
        f"<li>{label}: <code>{escape(str(value))}</code></li>" for label, value in fields
    )
    return f"<p><strong>{escape(heading)}</strong></p><ul>{items}</ul>"


def success_note_html(payload: SuccessNotePayload) -> str:
    """Return an HTML note body summarising a successful PDF archival operation."""
    return _html_field_list(
        f"PDF archived ({VERSION})",
        [
            ("path", payload.storage_dir),
            ("filename", payload.filename),
            ("audit_sidecar", payload.sidecar_path),
            ("size_bytes", str(payload.size_bytes)),
            ("sha256", payload.sha256_hex),
            ("request_id", payload.request_id or "unknown"),
            ("delivery_id", payload.delivery_id or "none"),
            ("time_utc", payload.timestamp_utc),
        ],
    )


def error_code_and_hint(exc: BaseException) -> tuple[str, str]:
    """Return (stable_code, short_hint) for permanent failures (Bug #7)."""
    msg = str(exc).strip().lower()
    for required_terms, optional_terms, code, hint in _ERROR_HINT_RULES:
        if all(term in msg for term in required_terms) and (
            not optional_terms or any(term in msg for term in optional_terms)
        ):
            return (code, hint)
    return ("permanent_error", "")


def error_note_html(payload: ErrorNotePayload) -> str:
    """Return an HTML note body describing an archival failure with classification and hints."""
    fields: list[tuple[str, str]] = [
        ("classification", payload.classification),
        ("error", payload.message),
        ("action", payload.action),
    ]
    if payload.code:
        fields.append(("code", payload.code))
    if payload.hint:
        fields.append(("hint", payload.hint))
    fields.extend(
        [
            ("request_id", payload.request_id or "unknown"),
            ("delivery_id", payload.delivery_id or "none"),
            ("time_utc", payload.timestamp_utc),
        ]
    )
    return _html_field_list(f"PDF archiver error ({VERSION})", fields)


def concise_exc_message(exc: BaseException) -> str:
    """Return a bounded, non-secret failure summary suitable for ticket notes."""
    text = f"{exc.__class__.__name__}: {exc}"
    text = text.strip()
    text = scrub_secrets_in_text(text)
    return text[:500] if len(text) > 500 else text


def _is_permission_denied(exc: BaseException) -> bool:
    """Report whether the exception is an OS error caused by missing storage permission."""
    return isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EPERM)


def _is_ticket_field_error(exc: BaseException) -> bool:
    """Report whether the exception is a plain ticket-field or path-policy error."""
    return type(exc) in (ValueError, TypeError) and error_code_and_hint(exc)[1] != ""


def action_hint(
    exc: BaseException, *, classified: TransientError | PermanentError, trigger_tag: str
) -> str:
    """Return a human-readable operator action hint for the given exception and classification."""
    if isinstance(classified, TransientError):
        return (
            "Transient failure. Verify Zammad/TSA reachability and storage availability; "
            f"the ticket keeps {trigger_tag} so a retry can be triggered by saving the ticket "
            "or reapplying the macro."
        )

    # PermanentError: aim for a concrete operator action.
    zammad_hints = {
        "AuthError": (
            "Fix Zammad API token/permissions (HTTP 401/403), "
            f"then reapply the {trigger_tag} macro."
        ),
        "NotFoundError": (
            "Ticket/resource not found in Zammad. Verify the ticket still exists, then reapply "
            f"{trigger_tag}."
        ),
    }
    if hint := zammad_hints.get(exc.__class__.__name__):
        return hint
    if _is_permission_denied(exc):
        return (
            "Storage permission denied. Check network share mount options, ownership, and ACLs, "
            f"then reapply the {trigger_tag} macro."
        )
    if _is_ticket_field_error(exc):
        return (
            f"Fix ticket fields / path policy validation, then reapply the {trigger_tag} macro "
            f"(and optionally remove {ERROR_TAG} for clarity)."
        )
    return (
        f"Non-retryable failure by policy. Fix the underlying issue and reapply the {trigger_tag} "
        f"macro (and optionally remove {ERROR_TAG})."
    )
