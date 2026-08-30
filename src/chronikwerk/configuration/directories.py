"""Trusted traversal for managed configuration state directories."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from chronikwerk.configuration.errors import ManagedConfigError


class _TrustedDirectoryTraversal:
    """Open and validate managed-state directories without following symlinks."""

    state_dir: Path
    overlay_path: Path
    revisions_dir: Path
    _state_identity: tuple[int, int] | None
    _revisions_identity: tuple[int, int] | None

    def _initialize_managed_directories(self) -> None:
        if os.name != "posix":
            self._ensure_directory(self.state_dir)
            self._ensure_directory(self.revisions_dir)
            return

        state_fd = self._open_directory_chain(self.state_dir, create=True)
        try:
            self._state_identity = self._identity(os.fstat(state_fd))
            revisions_fd = self._open_child_directory(
                state_fd, "revisions", create=True, display_path=self.revisions_dir
            )
            try:
                self._revisions_identity = self._identity(os.fstat(revisions_fd))
            finally:
                os.close(revisions_fd)
        finally:
            os.close(state_fd)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        if path.is_symlink():
            raise ManagedConfigError(f"Managed configuration path must not be a symlink: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path_stat = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(path_stat.st_mode):
            raise ManagedConfigError(f"Managed configuration path is not a directory: {path}")
        if os.name == "posix":
            if path_stat.st_uid != os.geteuid():
                raise ManagedConfigError(
                    f"Managed configuration path is not owned by the service user: {path}"
                )
            if path_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise ManagedConfigError(
                    f"Managed configuration path must not be group or world writable: {path}"
                )

    @staticmethod
    def _identity(path_stat: os.stat_result) -> tuple[int, int]:
        return (path_stat.st_dev, path_stat.st_ino)

    @staticmethod
    def _directory_open_flags() -> int:
        return (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )

    @staticmethod
    def _validate_directory_stat(
        path_stat: os.stat_result,
        *,
        path: Path,
        final: bool,
    ) -> None:
        if not stat.S_ISDIR(path_stat.st_mode):
            raise ManagedConfigError(f"Managed configuration path is not a directory: {path}")
        if os.name != "posix":
            return

        expected_owners = {os.geteuid()} if final else {0, os.geteuid()}
        if path_stat.st_uid not in expected_owners:
            raise ManagedConfigError(f"Managed configuration path has an untrusted owner: {path}")
        writable_by_others = path_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        sticky_ancestor = not final and bool(path_stat.st_mode & stat.S_ISVTX)
        if writable_by_others and not sticky_ancestor:
            raise ManagedConfigError(
                f"Managed configuration path must not be group or world writable: {path}"
            )

    @classmethod
    def _open_directory_chain(
        cls, path: Path, *, create: bool, expected_identity: tuple[int, int] | None = None
    ) -> int:
        """Open a directory without following any path-component symlink."""
        absolute = Path(os.path.abspath(path))
        components = absolute.parts[1:]
        current = Path(absolute.anchor)
        try:
            directory_fd = os.open(absolute.anchor, cls._directory_open_flags())
        except OSError as exc:
            raise ManagedConfigError(
                f"Unable to open managed configuration path: {current}"
            ) from exc

        try:
            cls._validate_directory_stat(os.fstat(directory_fd), path=current, final=not components)
            for index, component in enumerate(components):
                current /= component
                directory_fd = cls._open_chain_directory(
                    directory_fd,
                    component,
                    create=create,
                    display_path=current,
                    final=index == len(components) - 1,
                )
            cls._validate_directory_identity(directory_fd, expected_identity, absolute)
            return directory_fd
        except Exception:
            os.close(directory_fd)
            raise

    @classmethod
    def _open_chain_directory(
        cls, parent_fd: int, name: str, *, create: bool, display_path: Path, final: bool
    ) -> int:
        child_fd = cls._open_directory_entry(
            parent_fd, name, create=create, display_path=display_path
        )
        try:
            cls._validate_directory_stat(os.fstat(child_fd), path=display_path, final=final)
        except Exception:
            os.close(child_fd)
            raise
        os.close(parent_fd)
        return child_fd

    @classmethod
    def _validate_directory_identity(
        cls, directory_fd: int, expected_identity: tuple[int, int] | None, path: Path
    ) -> None:
        if expected_identity is not None and (
            cls._identity(os.fstat(directory_fd)) != expected_identity
        ):
            raise ManagedConfigError(
                f"Managed configuration directory changed after initialization: {path}"
            )

    @classmethod
    def _open_directory_entry(
        cls, parent_fd: int, name: str, *, create: bool, display_path: Path
    ) -> int:
        flags = cls._directory_open_flags()
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            if not create:
                raise ManagedConfigError(
                    f"Managed configuration path is unavailable: {display_path}"
                ) from None
            return cls._create_directory_entry(parent_fd, name, flags, display_path)
        except OSError as exc:
            raise ManagedConfigError(
                f"Managed configuration path contains a symlink or non-directory: {display_path}"
            ) from exc

    @staticmethod
    def _create_directory_entry(
        parent_fd: int,
        name: str,
        flags: int,
        display_path: Path,
    ) -> int:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise ManagedConfigError(
                f"Managed configuration path is unsafe: {display_path}"
            ) from exc

    @classmethod
    def _open_child_directory(
        cls,
        parent_fd: int,
        name: str,
        *,
        create: bool,
        display_path: Path,
        expected_identity: tuple[int, int] | None = None,
    ) -> int:
        child_fd = cls._open_directory_entry(
            parent_fd, name, create=create, display_path=display_path
        )
        try:
            path_stat = os.fstat(child_fd)
            cls._validate_directory_stat(path_stat, path=display_path, final=True)
            if expected_identity is not None and cls._identity(path_stat) != expected_identity:
                raise ManagedConfigError(
                    f"Managed configuration directory changed after initialization: {display_path}"
                )
            return child_fd
        except Exception:
            os.close(child_fd)
            raise

    def _open_state_directory(self) -> int:
        if self._state_identity is None:
            return os.open(self.state_dir, os.O_RDONLY)
        return self._open_directory_chain(
            self.state_dir,
            create=False,
            expected_identity=self._state_identity,
        )

    def _open_revisions_directory(self) -> int:
        if self._revisions_identity is None:
            return os.open(self.revisions_dir, os.O_RDONLY)
        state_fd = self._open_state_directory()
        try:
            return self._open_child_directory(
                state_fd,
                "revisions",
                create=False,
                display_path=self.revisions_dir,
                expected_identity=self._revisions_identity,
            )
        finally:
            os.close(state_fd)
