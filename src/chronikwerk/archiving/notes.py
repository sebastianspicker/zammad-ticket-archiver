"""Create safe, concise Zammad notes describing archival outcomes."""

from dataclasses import dataclass
from html import escape

import structlog

from chronikwerk._version import VERSION
from chronikwerk.archiving.redaction import scrub_secrets_in_text
from chronikwerk.failures import PermanentError, TransientError

log = structlog.get_logger(__name__)

_ErrorHintRule = tuple[tuple[str, ...], tuple[str, ...], str, str]

_ERROR_HINT_RULES: tuple[_ErrorHintRule, ...] = (
    (
        ("archive_path", "missing"),
        (),
        "missing_archive_path",
        "Set custom_fields.archive_path on the ticket.",
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
        (),
        ("owner.login", "updated_by.login"),
        "missing_user_login",
        "Ensure ticket has owner/updated_by with login.",
    ),
    (
        (),
        ("archive_user", "archive_user_mode"),
        "missing_archive_user",
        "Set custom_fields.archive_user for fixed mode.",
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


def action_hint(exc: BaseException, *, classified: TransientError | PermanentError | None) -> str:
    """Return a human-readable operator action hint for the given exception and classification."""
    if classified is not None and isinstance(classified, TransientError):
        return (
            "Transient failure. Verify Zammad/TSA reachability and storage availability; "
            "the ticket keeps pdf:sign so a retry can be triggered by saving the ticket "
            "or reapplying the macro."
        )

    # PermanentError: aim for a concrete operator action.
    for error_type, hint in (
        (
            "AuthError",
            "Fix Zammad API token/permissions (HTTP 401/403), then reapply the pdf:sign macro.",
        ),
        (
            "NotFoundError",
            "Ticket/resource not found in Zammad. Verify the ticket still exists, then reapply "
            "pdf:sign.",
        ),
        (
            ("ServerError", "RateLimitError"),
            "Upstream Zammad error was treated as permanent by policy. "
            "If the issue is resolved, reapply the pdf:sign macro to reprocess.",
        ),
        (
            PermissionError,
            "Storage permission denied. Check network share mount options, ownership, and ACLs, "
            "then reapply the pdf:sign macro.",
        ),
        (
            (ValueError, TypeError),
            "Fix ticket fields / path policy validation, then reapply the pdf:sign macro "
            "(and optionally remove pdf:error for clarity).",
        ),
    ):
        if exc.__class__.__name__ in (
            error_type if isinstance(error_type, tuple) else (error_type,)
        ):
            return hint
    return (
        "Non-retryable failure by policy. Fix the underlying issue and reapply the pdf:sign macro "
        "(and optionally remove pdf:error)."
    )
