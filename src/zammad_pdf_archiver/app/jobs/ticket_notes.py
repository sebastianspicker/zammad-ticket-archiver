from html import escape

import structlog

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.config.redact import scrub_secrets_in_text
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

log = structlog.get_logger(__name__)


def success_note_html(
    *,
    storage_dir: str,
    filename: str,
    sidecar_path: str,
    size_bytes: int,
    sha256_hex: str,
    request_id: str | None,
    delivery_id: str | None,
    timestamp_utc: str,
) -> str:
    storage = escape(storage_dir)
    fname = escape(filename)
    sidecar = escape(sidecar_path)
    sha256 = escape(sha256_hex)
    rid = escape(request_id or "unknown")
    did = escape(delivery_id or "none")
    time_utc = escape(timestamp_utc)
    return (
        f"<p><strong>PDF archived ({VERSION})</strong></p>"
        "<ul>"
        f"<li>path: <code>{storage}</code></li>"
        f"<li>filename: <code>{fname}</code></li>"
        f"<li>audit_sidecar: <code>{sidecar}</code></li>"
        f"<li>size_bytes: <code>{size_bytes}</code></li>"
        f"<li>sha256: <code>{sha256}</code></li>"
        f"<li>request_id: <code>{rid}</code></li>"
        f"<li>delivery_id: <code>{did}</code></li>"
        f"<li>time_utc: <code>{time_utc}</code></li>"
        "</ul>"
    )


def error_code_and_hint(exc: BaseException) -> tuple[str, str]:
    """Return (stable_code, short_hint) for permanent failures (Bug #7)."""
    msg = str(exc).strip().lower()
    if "archive_path is missing" in msg or "archive_path" in msg and "missing" in msg:
        return ("missing_archive_path", "Set custom_fields.archive_path on the ticket.")
    if "archive_path must not be empty" in msg or "all segments were empty" in msg:
        return ("empty_archive_path", "Set archive_path to at least one non-empty segment.")
    if "archive_path must be a string" in msg or "archive_path[" in msg:
        return ("invalid_archive_path", "Use a string or list of strings for archive_path.")
    if "allow_prefixes" in msg and "not allowed" in msg:
        return ("path_not_allowed", "Check allow_prefixes; archive_path must match a prefix.")
    if "allow_prefixes is empty" in msg:
        return (
            "allow_prefixes_empty",
            "Configure at least one allow_prefixes entry or leave unset.",
        )
    if "owner.login" in msg or "updated_by.login" in msg:
        return ("missing_user_login", "Ensure ticket has owner/updated_by with login.")
    if "archive_user" in msg or "archive_user_mode" in msg:
        return ("missing_archive_user", "Set custom_fields.archive_user for fixed mode.")
    if "filename" in msg and ("pattern" in msg or "segment" in msg or "must not" in msg):
        return (
            "invalid_filename",
            "Check filename_pattern and path policy (no ., .., separators).",
        )
    if "path segment" in msg or "path separators" in msg or "dot segments" in msg:
        return ("path_validation", "Check archive_path segments (no ., .., empty, or separators).")
    return ("permanent_error", "")


def error_note_html(
    *,
    classification: str,
    message: str,
    action: str,
    request_id: str | None,
    delivery_id: str | None,
    timestamp_utc: str,
    code: str = "",
    hint: str = "",
) -> str:
    rid = escape(request_id or "unknown")
    did = escape(delivery_id or "none")
    cls = escape(classification)
    msg = escape(message)
    act = escape(action)
    code_esc = escape(code) if code else ""
    hint_esc = escape(hint) if hint else ""
    items = [
        f"<li>classification: <code>{cls}</code></li>",
        f"<li>error: <code>{msg}</code></li>",
        f"<li>action: <code>{act}</code></li>",
    ]
    if code_esc:
        items.append(f"<li>code: <code>{code_esc}</code></li>")
    if hint_esc:
        items.append(f"<li>hint: <code>{hint_esc}</code></li>")
    items.extend(
        [
            f"<li>request_id: <code>{rid}</code></li>",
            f"<li>delivery_id: <code>{did}</code></li>",
            f"<li>time_utc: <code>{timestamp_utc}</code></li>",
        ]
    )
    return (
        f"<p><strong>PDF archiver error ({VERSION})</strong></p>"
        "<ul>"
        + "".join(items)
        + "</ul>"
    )


def concise_exc_message(exc: BaseException) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    text = text.strip()
    text = scrub_secrets_in_text(text)
    return text[:500] if len(text) > 500 else text


def action_hint(exc: BaseException, *, classified: TransientError | PermanentError | None) -> str:
    if classified is not None and isinstance(classified, TransientError):
        return (
            "Transient failure. Verify Zammad/TSA reachability and storage availability; "
            "the ticket keeps pdf:sign so a retry can be triggered by saving the ticket "
            "or reapplying the macro."
        )

    # PermanentError: aim for a concrete operator action.
    if isinstance(exc, AuthError):
        return "Fix Zammad API token/permissions (HTTP 401/403), then reapply the pdf:sign macro."
    if isinstance(exc, NotFoundError):
        return (
            "Ticket/resource not found in Zammad. Verify the ticket still exists, then reapply "
            "pdf:sign."
        )
    if isinstance(exc, (ServerError, RateLimitError)):
        return (
            "Upstream Zammad error was treated as permanent by policy. "
            "If the issue is resolved, reapply the pdf:sign macro to reprocess."
        )
    if isinstance(exc, PermissionError):
        return (
            "Storage permission denied. Check network share mount options, ownership, and ACLs, "
            "then reapply the pdf:sign macro."
        )
    if isinstance(exc, (ValueError, TypeError)):
        return (
            "Fix ticket fields / path policy validation, then reapply the pdf:sign macro "
            "(and optionally remove pdf:error for clarity)."
        )
    return (
        "Non-retryable failure by policy. Fix the underlying issue and reapply the pdf:sign macro "
        "(and optionally remove pdf:error)."
    )
