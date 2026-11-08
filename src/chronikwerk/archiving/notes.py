"""Create safe, concise Zammad notes describing archival outcomes."""

import errno
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape

import structlog

from chronikwerk._version import VERSION
from chronikwerk.archiving.tags import ERROR_TAG
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.redaction import scrub_secrets_in_text

log = structlog.get_logger(__name__)

_MAX_NOTE_TEXT_CHARS = 500
# Absolute POSIX paths (not URL or relative-key segments) and Windows drive paths.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.:/~-])/[^\s'\"<>()]+|\b[A-Za-z]:\\[^\s'\"<>()]*")
_PATH_PLACEHOLDER = "<path>"

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


_AUTOMATIC_RETRY = "The request is retried automatically with back-off"

# Note hint and operator action per preservation error code (brief section 5.10). Actions may
# name the trigger tag as ``{trigger_tag}``.
PRESERVATION_NOTE_HINTS: Mapping[str, tuple[str, str]] = {
    "record_class_missing": (
        "The record_class ticket field is empty or names a class the retention policy lacks.",
        "Set a record class from the retention policy file, then reapply {trigger_tag}.",
    ),
    "inconsistent_source": (
        "The ticket changed during every capture attempt.",
        "Wait until the ticket is no longer being edited, then reapply {trigger_tag}.",
    ),
    "attachment_too_large": (
        "An attachment exceeds capture.max_attachment_bytes.",
        "Raise capture.max_attachment_bytes or ask the custodian, then reapply {trigger_tag}.",
    ),
    "package_too_large": (
        "The package exceeds capture.max_package_bytes.",
        "Raise capture.max_package_bytes or ask the custodian, then reapply {trigger_tag}.",
    ),
    "temp_space_exceeded": (
        "The capture build area exceeded its byte limit.",
        f"Check free temporary space and the build-area limit. {_AUTOMATIC_RETRY}.",
    ),
    "seal_failed": (
        "The manifest could not be sealed, or the seal failed its check.",
        "Check the seal certificate, its password and the trust anchors, then reapply "
        "{trigger_tag}.",
    ),
    "timestamp_failed": (
        "The TSA could not be reached, or its token failed the check.",
        f"Check TSA reachability, credentials and trust anchors. {_AUTOMATIC_RETRY} when "
        "the TSA was unreachable; otherwise reapply {trigger_tag}.",
    ),
    "validation_unavailable": (
        "Revocation data for the seal or TSA certificates could not be fetched.",
        f"Check access to the validation hosts. {_AUTOMATIC_RETRY}.",
    ),
    "store_write_failed": (
        "The archive store refused or failed a write.",
        f"Check archive store availability, permissions and capacity. {_AUTOMATIC_RETRY}.",
    ),
    "store_conflict": (
        "The archive store already holds different content under this revision key.",
        "Ask the custodian to investigate the key prefix; do not overwrite it. Then reapply "
        "{trigger_tag}.",
    ),
    "verification_failed": (
        "What the archive store returned differs from the sealed manifest.",
        "Check the archive store's integrity with the custodian, then reapply {trigger_tag}.",
    ),
    "retention_not_confirmed": (
        "The archive store did not confirm the final retention date.",
        "Check the store's Object Lock or WORM configuration, then reapply {trigger_tag}.",
    ),
    "commit_interrupted": (
        "A restart interrupted the commit before the sealed revision was stored.",
        f"No action needed. {_AUTOMATIC_RETRY} and gets a new revision ID.",
    ),
    "not_triggered": (
        "The ticket did not carry the trigger tag when the request ran.",
        "Apply {trigger_tag} to archive the ticket.",
    ),
    "internal_error": (
        "An unexpected internal error stopped the capture.",
        f"Check the service log for this request ID. {_AUTOMATIC_RETRY}.",
    ),
}


def preservation_hint(code: str, *, trigger_tag: str) -> tuple[str, str] | None:
    """Return ``(hint, operator action)`` for a preservation error code, or ``None``."""
    entry = PRESERVATION_NOTE_HINTS.get(code)
    if entry is None:
        return None
    hint, action = entry
    return hint, action.replace("{trigger_tag}", trigger_tag)


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


@dataclass(frozen=True)
class RevisionNotePayload:
    """Values rendered into the acknowledgement of a complete, sealed revision."""

    revision_number: int
    revision_id: str
    manifest_sha256: str
    key_prefix: str
    coverage: str
    retain_until: str
    retention_enforced: bool
    request_id: str | None
    delivery_id: str | None
    timestamp_utc: str


def revision_note_subject() -> str:
    """Return the subject of the revision acknowledgement note."""
    return f"Archive revision complete ({VERSION})"


def revision_note_html(payload: RevisionNotePayload) -> str:
    """Return an HTML note body naming the complete revision and where it is stored."""
    return _html_field_list(
        revision_note_subject(),
        [
            ("revision_number", str(payload.revision_number)),
            ("revision_id", payload.revision_id),
            ("manifest_sha256", payload.manifest_sha256),
            ("key_prefix", payload.key_prefix),
            ("coverage", payload.coverage),
            ("retain_until", payload.retain_until),
            ("retention_enforced", "yes" if payload.retention_enforced else "no"),
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


def ticket_field_hint(code: str) -> str:
    """Return the hint of a ticket-field or path-policy code, or ``""`` when it has none."""
    return next((hint for *_terms, known, hint in _ERROR_HINT_RULES if known == code), "")


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


def redact_paths(text: str) -> str:
    """Replace absolute filesystem paths, which name host internals, with ``<path>``."""
    return _ABSOLUTE_PATH_RE.sub(_PATH_PLACEHOLDER, text)


def bounded_note_text(text: str) -> str:
    """Return ``text`` without absolute paths or secrets, at most 500 characters long."""
    text = scrub_secrets_in_text(redact_paths(text.strip()))
    return text[:_MAX_NOTE_TEXT_CHARS]


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
            "the request is retried automatically with back-off."
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
