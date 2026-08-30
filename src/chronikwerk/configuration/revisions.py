"""Allowlisted, atomic, non-secret managed configuration revisions."""

from __future__ import annotations

import os
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from chronikwerk.configuration.errors import (
    ManagedConfigError,
    RevisionConflict,
    _PostReplaceError,
)
from chronikwerk.configuration.io import _ManagedFileIO
from chronikwerk.configuration.models import Settings
from chronikwerk.configuration.revision_chain import (
    build_revision_chain,
    is_revision_identifier,
    parse_current_payload,
    parse_revision_payload,
    retained_revision_names,
    revision_for,
)
from chronikwerk.configuration.validation import ConfigValidationError, validate_settings

# Keep the documented public exception path stable after moving implementation
# details into private modules.
log = structlog.get_logger(__name__)
_SECRET_PATHS = {
    "admin.access_token",
    "retry_bearer_token",
    "zammad.api_token",
    "zammad.webhook_hmac_secret",
    "signing.pfx_password",
    "signing.timestamp.rfc3161.password",
    "observability.metrics_bearer_token",
    "observability.history_bearer_token",
}


@dataclass(frozen=True)
class ManagedField:
    """Describe an editable setting and its validation/display metadata."""

    path: str
    group: str
    kind: str
    choices: tuple[str, ...] = ()
    security_acknowledgement: bool = False


MANAGED_FIELDS: tuple[ManagedField, ...] = (
    ManagedField("workflow.trigger_tag", "workflow", "string"),
    ManagedField("workflow.require_tag", "workflow", "boolean"),
    ManagedField("workflow.acknowledge_on_success", "workflow", "boolean"),
    ManagedField("workflow.delivery_id_ttl_seconds", "workflow", "integer"),
    ManagedField("pdf.locale", "pdf", "choice", ("de-DE", "en-GB")),
    ManagedField("pdf.timezone", "pdf", "string"),
    ManagedField("pdf.max_articles", "pdf", "integer"),
    ManagedField(
        "pdf.article_limit_mode",
        "pdf",
        "choice",
        ("fail", "cap_and_continue"),
    ),
    ManagedField("storage.fsync", "storage", "boolean"),
    ManagedField("storage.filename_pattern", "storage", "string"),
    ManagedField("zammad.timeout_seconds", "zammad", "number"),
    ManagedField("observability.log_level", "observability", "string"),
    ManagedField("observability.healthz_omit_version", "observability", "boolean"),
    ManagedField("admission.max_pending", "admission", "integer"),
    ManagedField("admission.max_running", "admission", "integer"),
    ManagedField("admission.shutdown_timeout_seconds", "admission", "number"),
    ManagedField("signing.pades.reason", "signing", "string"),
    ManagedField("signing.pades.location", "signing", "string"),
    ManagedField(
        "hardening.transport.trust_env",
        "security",
        "boolean",
        security_acknowledgement=True,
    ),
    ManagedField(
        "hardening.transport.allow_insecure_http",
        "security",
        "boolean",
        security_acknowledgement=True,
    ),
    ManagedField(
        "hardening.transport.allow_private_networks",
        "security",
        "boolean",
        security_acknowledgement=True,
    ),
)

_FIELD_BY_PATH = {field.path: field for field in MANAGED_FIELDS}


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge nested configuration mappings without discarding untouched branches."""
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def flatten_mapping(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested settings into editable dotted paths for the admin UI."""
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            flattened.update(flatten_mapping(item, path))
        else:
            flattened[path] = item
    return flattened


def overlay_from_flat(values: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a nested configuration overlay from editable dotted paths."""
    overlay: dict[str, Any] = {}
    for path, value in values.items():
        if path not in _FIELD_BY_PATH:
            if path in _SECRET_PATHS:
                raise ManagedConfigError(f"Secret field is not manageable: {path}")
            raise ManagedConfigError(f"Unknown or external-only field: {path}")
        cursor = overlay
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return overlay


def validate_overlay_paths(overlay: dict[str, Any]) -> None:
    """Reject editable paths that do not map to managed configuration fields."""
    overlay_from_flat(flatten_mapping(overlay))


def environment_owns(path: str) -> bool:
    """Report whether a setting is explicitly controlled by environment variables."""
    return path.upper().replace(".", "__") in os.environ


def get_path(mapping: dict[str, Any], path: str) -> Any:
    """Read a dotted path from nested configuration without mutating it."""
    current: Any = mapping
    for part in path.split("."):
        current = current[part]
    return current


def config_read_model(settings: Settings, overlay: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a safe, typed configuration view for operator interfaces."""
    effective = settings.model_dump(mode="json")
    managed = flatten_mapping(overlay)
    return [
        {
            **asdict(field),
            "value": (
                get_path(effective, field.path)
                if environment_owns(field.path) or field.path not in managed
                else managed[field.path]
            ),
            "source": (
                "environment"
                if environment_owns(field.path)
                else "managed"
                if field.path in managed
                else "base_or_default"
            ),
            "editable": not environment_owns(field.path),
        }
        for field in MANAGED_FIELDS
    ]


def secret_presence(settings: Settings) -> dict[str, bool]:
    """Report whether required secrets are configured without exposing values."""
    values = settings.model_dump()
    presence: dict[str, bool] = {}
    for path in sorted(_SECRET_PATHS):
        current: Any = values
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if hasattr(current, "get_secret_value"):
            current = current.get_secret_value()
        presence[path] = bool(current)
    return presence


def validate_candidate(
    settings: Settings, overlay: dict[str, Any]
) -> tuple[Settings, dict[str, Any]]:
    """Validate a proposed configuration while retaining safe error details."""
    validate_overlay_paths(overlay)
    base = settings.model_dump()
    candidate = Settings.from_mapping(deep_merge(base, overlay))
    validate_settings(candidate)
    normalized = candidate.model_dump(mode="json")
    normalized_flat = {path: get_path(normalized, path) for path in flatten_mapping(overlay)}
    return candidate, overlay_from_flat(normalized_flat)


class ManagedConfigStore(_ManagedFileIO):
    """Atomic current overlay and bounded immutable revision files."""

    def __init__(self, state_dir: Path, *, keep_revisions: int = 20) -> None:
        self.state_dir = Path(os.path.abspath(state_dir))
        self.keep_revisions = keep_revisions
        self.overlay_path = self.state_dir / "managed-config.json"
        self.revisions_dir = self.state_dir / "revisions"
        self._lock = threading.Lock()
        self._state_identity: tuple[int, int] | None = None
        self._revisions_identity: tuple[int, int] | None = None
        self._initialize_managed_directories()

    def load(self) -> dict[str, Any]:
        """Load the active managed configuration as a validated settings object."""
        overlay, _revision = self._read_current()
        return overlay

    def _read_current(self) -> tuple[dict[str, Any], str]:
        return parse_current_payload(
            self._read_current_bytes(),
            validate_overlay=validate_overlay_paths,
        )

    def current_revision(self) -> str:
        """Return the revision identifier associated with the active configuration."""
        _overlay, revision = self._read_current()
        return revision

    def stage(
        self,
        overlay: dict[str, Any],
        *,
        expected_revision: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Persist a revision-checked candidate as the next managed configuration."""
        validate_overlay_paths(overlay)
        with self._lock:
            current_overlay, current_revision = self._read_current()
            if expected_revision != current_revision:
                raise RevisionConflict("Managed configuration revision changed")
            metadata, revision_path, revision_value, current_value = self._stage_values(
                overlay, current_overlay, current_revision, request_id
            )
            self._write_staged_values(revision_path, revision_value, current_value)
            self._prune_staged_revision(metadata["revision"])
            return metadata

    def _stage_values(
        self,
        overlay: dict[str, Any],
        current_overlay: dict[str, Any],
        current_revision: str,
        request_id: str,
    ) -> tuple[dict[str, Any], Path, dict[str, Any], dict[str, Any]]:
        timestamp = datetime.now(UTC).isoformat()
        new_revision = revision_for(
            {
                "overlay": overlay,
                "previous_revision": current_revision,
                "request_id": request_id,
                "created_at": timestamp,
            }
        )
        current_flat, new_flat = flatten_mapping(current_overlay), flatten_mapping(overlay)
        metadata = {
            "revision": new_revision,
            "previous_revision": current_revision,
            "created_at": timestamp,
            "request_id": request_id,
            "changed_paths": sorted(
                path
                for path in set(current_flat) | set(new_flat)
                if current_flat.get(path) != new_flat.get(path)
            ),
        }
        revision_value = {"metadata": metadata, "overlay": overlay}
        current_value = {"revision": new_revision, "overlay": overlay}
        self._payload_bytes(revision_value)
        self._payload_bytes(current_value)
        return metadata, self.revisions_dir / f"{new_revision}.json", revision_value, current_value

    def _write_staged_values(
        self,
        revision_path: Path,
        revision_value: dict[str, Any],
        current_value: dict[str, Any],
    ) -> None:
        self._atomic_write(revision_path, revision_value)
        try:
            self._atomic_write(self.overlay_path, current_value)
        except _PostReplaceError:
            raise
        except Exception as primary_error:
            self._rollback_staged_revision(revision_path, primary_error)
            raise

    def _rollback_staged_revision(self, revision_path: Path, primary_error: Exception) -> None:
        try:
            self._unlink_revision(revision_path.name)
        except (ManagedConfigError, OSError) as cleanup_error:
            primary_error.add_note(
                "Failed to remove the staged revision after the current-pointer "
                f"write failed: {type(cleanup_error).__name__}"
            )

    def _prune_staged_revision(self, revision: str) -> None:
        try:
            self._prune_revisions()
        except Exception as cleanup_error:  # pylint: disable=broad-exception-caught
            log.warning(
                "managed_config.revision_prune_failed",
                revision=revision,
                error_type=type(cleanup_error).__name__,
            )

    def list_revisions(self) -> list[dict[str, Any]]:
        """List available managed revisions without reading their secret values."""
        return [data["metadata"] for _path, data in self._revision_chain()[: self.keep_revisions]]

    def restore(
        self,
        revision: str,
        *,
        expected_revision: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Activate a retained revision through the same validation path as staging."""
        data = self._read_revision(revision)
        return self.stage(
            data["overlay"],
            expected_revision=expected_revision,
            request_id=request_id,
        )

    def revision_overlay(self, revision: str) -> dict[str, Any]:
        """Return a validated non-secret overlay for route-level review."""
        return deepcopy(self._read_revision(revision)["overlay"])

    def _read_revision(self, revision: str) -> dict[str, Any]:
        if not is_revision_identifier(revision):
            raise ManagedConfigError("Invalid revision identifier")
        return self._read_revision_file(self.revisions_dir / f"{revision}.json")

    def _read_revision_file(self, path: Path) -> dict[str, Any]:
        return parse_revision_payload(
            self._read_revision_bytes(path),
            expected_revision=path.stem,
            validate_overlay=validate_overlay_paths,
        )

    def _revision_chain(self) -> list[tuple[Path, dict[str, Any]]]:
        max_entries = self.keep_revisions if self.keep_revisions > 0 else None
        return build_revision_chain(
            current_revision=self.current_revision(),
            revisions_dir=self.revisions_dir,
            read_revision=self._read_revision_file,
            max_entries=max_entries,
        )

    def _prune_revisions(self) -> None:
        keep_names = retained_revision_names(self._revision_chain(), self.keep_revisions)
        self._prune_revision_files(keep_names)


def validation_errors(exc: Exception) -> list[dict[str, str]]:
    """Return the safe validation errors produced by the latest candidate check."""
    if isinstance(exc, ConfigValidationError):
        return [{"path": issue.path, "message": issue.message} for issue in exc.issues]
    return [{"path": "<root>", "message": str(exc)}]
