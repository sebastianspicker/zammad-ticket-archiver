"""Secure, bounded, and atomic managed configuration file I/O."""

from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Any

from chronikwerk.configuration.directories import _TrustedDirectoryTraversal
from chronikwerk.configuration.errors import (
    ManagedConfigError,
    _MissingManagedFile,
    _PostReplaceCleanupError,
    _PostReplaceDurabilityError,
    _UnsafeManagedFile,
)

_MAX_MANAGED_FILE_BYTES = 256 * 1024


class _BoundedReadMixin(_TrustedDirectoryTraversal):
    """Read bounded regular files through trusted directory descriptors."""

    @staticmethod
    def _read_bounded_file(
        directory_fd: int, name: str, *, description: str, missing_ok: bool = False
    ) -> bytes | None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            file_fd = os.open(name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise _MissingManagedFile(f"{description} not found") from None
        except OSError as exc:
            raise _UnsafeManagedFile(f"{description} not found or unsafe") from exc

        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise _UnsafeManagedFile(f"{description} not found or unsafe")
            if file_stat.st_size > _MAX_MANAGED_FILE_BYTES:
                raise ManagedConfigError(f"{description} exceeds 256 KiB")
            handle = os.fdopen(file_fd, "rb")
            file_fd = -1
            with handle:
                payload = handle.read(_MAX_MANAGED_FILE_BYTES + 1)
            if len(payload) > _MAX_MANAGED_FILE_BYTES:
                raise ManagedConfigError(f"{description} exceeds 256 KiB")
            return payload
        finally:
            if file_fd >= 0:
                os.close(file_fd)

    def _read_current_bytes(self) -> bytes | None:
        if os.name != "posix":
            return self._read_current_path()

        directory_fd = self._open_state_directory()
        try:
            return self._read_bounded_file(
                directory_fd,
                self.overlay_path.name,
                description="Managed configuration file",
                missing_ok=True,
            )
        finally:
            os.close(directory_fd)

    def _read_current_path(self) -> bytes | None:
        if not self.overlay_path.exists():
            return None
        if self.overlay_path.is_symlink():
            raise ManagedConfigError("Managed configuration file must not be a symlink")
        if self.overlay_path.stat().st_size > _MAX_MANAGED_FILE_BYTES:
            raise ManagedConfigError("Managed configuration file exceeds 256 KiB")
        return self.overlay_path.read_bytes()

    def _read_revision_bytes(self, path: Path) -> bytes:
        if os.name != "posix":
            return self._read_revision_path(path)

        directory_fd = self._open_revisions_directory()
        try:
            payload = self._read_bounded_file(directory_fd, path.name, description="Revision file")
        finally:
            os.close(directory_fd)
        if payload is None:  # pragma: no cover - missing_ok is false above
            raise _UnsafeManagedFile("Revision not found or unsafe")
        return payload

    @staticmethod
    def _read_revision_path(path: Path) -> bytes:
        if not path.exists() and not path.is_symlink():
            raise _MissingManagedFile("Revision not found")
        if path.is_symlink() or not path.is_file():
            raise _UnsafeManagedFile("Revision not found or unsafe")
        if path.stat().st_size > _MAX_MANAGED_FILE_BYTES:
            raise ManagedConfigError("Revision file exceeds 256 KiB")
        return path.read_bytes()


class _RevisionPruningMixin(_TrustedDirectoryTraversal):
    """Remove obsolete revision files while retaining directory durability."""

    def _prune_revision_files(self, keep_names: set[str]) -> None:
        if os.name != "posix":
            self._prune_revision_paths(keep_names)
            return
        self._prune_revision_entries(keep_names)

    def _prune_revision_paths(self, keep_names: set[str]) -> None:
        normalized_keep = {os.path.normcase(name) for name in keep_names}
        for path in self.revisions_dir.glob("*.json"):
            if os.path.normcase(path.name) not in normalized_keep and not path.is_symlink():
                path.unlink()
        self._fsync_directory(self.revisions_dir)

    def _prune_revision_entries(self, keep_names: set[str]) -> None:
        directory_fd = self._open_revisions_directory()
        try:
            for name in os.listdir(directory_fd):
                if self._is_prunable_revision_entry(directory_fd, name, keep_names):
                    os.unlink(name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _is_prunable_revision_entry(
        directory_fd: int,
        name: str,
        keep_names: set[str],
    ) -> bool:
        if not name.endswith(".json") or name in keep_names:
            return False
        try:
            file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return stat.S_ISREG(file_stat.st_mode)

    def _unlink_revision(self, name: str) -> None:
        if os.name != "posix":
            (self.revisions_dir / name).unlink(missing_ok=True)
            self._fsync_directory(self.revisions_dir)
            return

        directory_fd = self._open_revisions_directory()
        try:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _fsync_directory(self, path: Path) -> None:
        if os.name == "posix":
            directory_fd = (
                self._open_state_directory()
                if path == self.state_dir
                else self._open_revisions_directory()
            )
        else:
            directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


class _AtomicWriteCleanupMixin(_RevisionPruningMixin):
    """Atomically replace managed files and preserve cleanup failure details."""

    @staticmethod
    def _payload_bytes(value: dict[str, Any]) -> bytes:
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        if len(payload) > _MAX_MANAGED_FILE_BYTES:
            raise ManagedConfigError("Managed configuration payload exceeds 256 KiB")
        return payload

    def _atomic_write(self, path: Path, value: dict[str, Any]) -> None:
        if os.name == "posix":
            self._atomic_write_relative(path, value)
            return

        if path.is_symlink():
            raise ManagedConfigError(f"Refusing to replace symlink: {path}")
        payload = self._payload_bytes(value)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            self._fsync_directory(path.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    def _trusted_directory_for_write(self, path: Path) -> int:
        if path.parent == self.state_dir:
            return self._open_state_directory()
        if path.parent == self.revisions_dir:
            return self._open_revisions_directory()
        raise ManagedConfigError(f"Managed write target is outside trusted state: {path}")

    @staticmethod
    def _reject_symlink_target(directory_fd: int, path: Path) -> None:
        try:
            target_stat = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(target_stat.st_mode):
            raise ManagedConfigError(f"Refusing to replace symlink: {path}")

    @staticmethod
    def _create_temp_file(directory_fd: int, target_name: str) -> tuple[int, str]:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for _attempt in range(16):
            temp_name = f".{target_name}.{secrets.token_hex(16)}"
            try:
                return os.open(temp_name, flags, 0o600, dir_fd=directory_fd), temp_name
            except FileExistsError:
                continue
        raise ManagedConfigError(  # pragma: no cover - cryptographic collision is impractical
            "Unable to allocate managed configuration temp file"
        )

    def _write_temp_payload(self, file_fd: int, payload: bytes) -> None:
        try:
            os.fchmod(file_fd, 0o600)
        except BaseException as primary_error:
            self._close_temp_after_setup_failure(file_fd, primary_error)
            raise
        with os.fdopen(file_fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _close_temp_after_setup_failure(file_fd: int, primary_error: BaseException) -> None:
        try:
            os.close(file_fd)
        except OSError as cleanup_error:
            primary_error.add_note(
                "Closing the managed-state temp file also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )

    @staticmethod
    def _replace_and_sync(directory_fd: int, temp_name: str, path: Path) -> None:
        os.replace(temp_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            raise _PostReplaceDurabilityError(
                f"Managed configuration target was replaced but directory fsync failed: {path}"
            ) from exc

    def _finish_atomic_cleanup(
        self,
        directory_fd: int,
        temp_name: str | None,
        primary_error: BaseException | None,
        *,
        replaced: bool,
    ) -> None:
        cleanup_errors = self._cleanup_atomic_resources(directory_fd, temp_name)
        if primary_error is not None:
            self._annotate_cleanup_errors(primary_error, cleanup_errors)
            return
        self._raise_cleanup_errors(cleanup_errors, replaced=replaced)

    @staticmethod
    def _cleanup_atomic_resources(directory_fd: int, temp_name: str | None) -> list[OSError]:
        cleanup_errors: list[OSError] = []
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_errors.append(exc)
        try:
            os.close(directory_fd)
        except OSError as exc:
            cleanup_errors.append(exc)
        return cleanup_errors

    @staticmethod
    def _annotate_cleanup_errors(
        primary_error: BaseException, cleanup_errors: list[OSError]
    ) -> None:
        for cleanup_error in cleanup_errors:
            primary_error.add_note(
                "Managed-state cleanup also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )

    @staticmethod
    def _raise_cleanup_errors(
        cleanup_errors: list[OSError],
        *,
        replaced: bool,
    ) -> None:
        if not cleanup_errors:
            return

        first_cleanup_error = cleanup_errors[0]
        for cleanup_error in cleanup_errors[1:]:
            first_cleanup_error.add_note(
                "Additional managed-state cleanup failure: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        if replaced:
            raise _PostReplaceCleanupError(
                "Managed configuration target was committed but descriptor cleanup failed"
            ) from first_cleanup_error
        raise first_cleanup_error

    def _atomic_write_relative(self, path: Path, value: dict[str, Any]) -> None:
        payload = self._payload_bytes(value)
        directory_fd = self._trusted_directory_for_write(path)
        temp_name: str | None = None
        primary_error: BaseException | None = None
        replaced = False
        try:
            self._reject_symlink_target(directory_fd, path)
            file_fd, temp_name = self._create_temp_file(directory_fd, path.name)
            self._write_temp_payload(file_fd, payload)
            self._replace_and_sync(directory_fd, temp_name, path)
            replaced = True
            temp_name = None
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._finish_atomic_cleanup(
                directory_fd,
                temp_name,
                primary_error,
                replaced=replaced,
            )


class _ManagedFileIO(_BoundedReadMixin, _AtomicWriteCleanupMixin):
    """Compatibility facade for managed configuration filesystem operations."""
