#!/usr/bin/env python3
"""Enforce concise purpose documentation on repository-owned code surfaces."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOTS = (
    Path("src/chronikwerk"),
    Path("scripts"),
    Path("tests"),
)
DOCUMENTED_PYTHON_SURFACES = (
    Path("src/chronikwerk"),
    Path("scripts"),
)
TEST_ROOT = Path("tests")
TYPESCRIPT_ROOTS = (Path("frontend"),)
TYPESCRIPT_FILES: tuple[Path, ...] = ()
SHELL_ROOTS = (Path("scripts"),)
PLACEHOLDER_DOCSTRING_FRAGMENTS = (
    "Regression coverage for ",
    "across application, API, and storage boundaries",
    "Test helper for ",
    "Shared test helpers for ",
    " for this module.",
    " used by this module.",
    "Register mock routes for ",
    "Install mock behavior for ",
    "Assert the expected  ",
)


def _files_under(root: Path, pattern: str) -> list[Path]:
    absolute_root = REPO_ROOT / root
    if not absolute_root.exists():
        return []
    return sorted(path for path in absolute_root.rglob(pattern) if path.is_file())


def _python_files() -> list[Path]:
    return sorted({path for root in PYTHON_ROOTS for path in _files_under(root, "*.py")})


def _typescript_files() -> list[Path]:
    discovered = {path for root in TYPESCRIPT_ROOTS for path in _files_under(root, "*.ts")}
    discovered.update(
        path for relative in TYPESCRIPT_FILES if (path := REPO_ROOT / relative).is_file()
    )
    return sorted(discovered)


def _shell_files() -> list[Path]:
    return sorted({path for root in SHELL_ROOTS for path in _files_under(root, "*.sh")})


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _missing_definition_doc(
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    *,
    label: str,
) -> list[str]:
    if ast.get_docstring(node) is not None:
        return []
    return [f"{_relative(path)}:{node.lineno}: {label} lacks a docstring"]


def _top_level_callables(
    tree: ast.Module,
) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    """Yield the callable definitions that belong to a module's public surface."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node


def _public_method_errors(path: Path, class_node: ast.ClassDef) -> list[str]:
    return [
        error
        for child in class_node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        if not child.name.startswith("_")
        for error in _missing_definition_doc(
            path,
            child,
            label=f"public {class_node.name}.{child.name}",
        )
    ]


def _public_definition_errors(path: Path, tree: ast.Module) -> list[str]:
    """Require docs on the callable surface that maintainers are expected to reuse."""
    relative = path.relative_to(REPO_ROOT)
    if not any(relative.is_relative_to(root) for root in DOCUMENTED_PYTHON_SURFACES):
        return []

    errors: list[str] = []
    for node in _top_level_callables(tree):
        if node.name.startswith("_"):
            continue
        errors.extend(_missing_definition_doc(path, node, label=f"public {node.name}"))
        if isinstance(node, ast.ClassDef):
            errors.extend(_public_method_errors(path, node))
    return errors


def _test_helper_errors(path: Path, tree: ast.Module) -> list[str]:
    """Document reusable test scaffolding while letting precise test names carry intent."""
    relative = path.relative_to(REPO_ROOT)
    if not relative.is_relative_to(TEST_ROOT):
        return []

    errors: list[str] = []
    for node in _top_level_callables(tree):
        if node.name.startswith("test_"):
            continue
        if ast.get_docstring(node) is None:
            errors.append(
                f"{_relative(path)}:{node.lineno}: test helper {node.name} lacks a docstring"
            )
    return errors


def _placeholder_docstring_errors(path: Path, tree: ast.Module) -> list[str]:
    """Reject placeholder phrases that provide coverage without useful intent."""
    documented_nodes: list[tuple[int, str, str]] = []
    module_docstring = ast.get_docstring(tree)
    if module_docstring is not None:
        documented_nodes.append((1, "module", module_docstring))
    documented_nodes.extend(
        (node.lineno, node.name, docstring)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        if (docstring := ast.get_docstring(node)) is not None
    )

    errors: list[str] = []
    for line, subject, docstring in documented_nodes:
        for fragment in PLACEHOLDER_DOCSTRING_FRAGMENTS:
            if fragment not in docstring:
                continue
            errors.append(
                f"{_relative(path)}:{line}: {subject} uses placeholder documentation "
                f"containing {fragment!r}"
            )
            break
    return errors


def _python_errors(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [f"{_relative(path)}: cannot parse Python source: {exc}"]

    errors: list[str] = []
    if ast.get_docstring(tree) is None:
        errors.append(f"{_relative(path)}: module lacks a purpose docstring")
    errors.extend(_public_definition_errors(path, tree))
    errors.extend(_test_helper_errors(path, tree))
    errors.extend(_placeholder_docstring_errors(path, tree))
    return errors


def _first_code_line(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError, UnicodeError:
        return ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#!"):
            continue
        return stripped
    return ""


def _header_errors(paths: Iterable[Path], prefixes: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        first_line = _first_code_line(path)
        if not first_line.startswith(prefixes):
            errors.append(f"{_relative(path)}: file lacks a purpose header comment")
    return errors


def main() -> int:
    """Report missing code-purpose documentation and return a CI-friendly status."""
    python_files = _python_files()
    typescript_files = _typescript_files()
    shell_files = _shell_files()

    errors = [error for path in python_files for error in _python_errors(path)]
    errors.extend(_header_errors(typescript_files, ("//", "/*")))
    errors.extend(_header_errors(shell_files, ("#",)))

    if errors:
        for error in errors:
            print(error)
        print(f"code-docs-check: FAILED ({len(errors)} issues)")
        return 1

    print(
        "code-docs-check: OK "
        f"({len(python_files)} Python, {len(typescript_files)} TypeScript, "
        f"{len(shell_files)} shell files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
