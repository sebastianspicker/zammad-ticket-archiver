"""CLI entry point for config validation and diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from chronikwerk.configuration.load import load_settings
from chronikwerk.configuration.redaction import redact_settings_dict
from chronikwerk.configuration.revisions import ManagedConfigStore, validate_candidate
from chronikwerk.configuration.validation import ConfigValidationError
from chronikwerk.failures import PermanentError

if TYPE_CHECKING:
    from chronikwerk.preservation.verify import VerificationReport

EXIT_USAGE = 2
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _missing_config_path_from_error(error: ConfigValidationError) -> str | None:
    for issue in error.issues:
        if issue.path != "CONFIG_PATH":
            continue
        prefix = "Config file not found:"
        if issue.message.startswith(prefix):
            return issue.message.removeprefix(prefix).strip()
    return None


def cmd_validate_config(args: argparse.Namespace) -> int:
    """Validate the effective configuration and the preservation requirements the service checks."""
    # Imported here: the startup checks load the seal and archive stack the other commands skip.
    from chronikwerk.composition_preservation import startup_problems

    try:
        settings = load_settings(config_path=args.config)
        problems = startup_problems(settings)
        if problems:
            raise ConfigValidationError(problems)
        print("✓ Configuration valid")
        return 0
    except ConfigValidationError as exc:
        missing_path = _missing_config_path_from_error(exc)
        if missing_path:
            print(f"✗ Config not found: {missing_path}", file=sys.stderr)
            return 1
        print(f"✗ Configuration invalid: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"✗ Configuration invalid: {exc}", file=sys.stderr)
        return 1


def cmd_dump_config(_args: argparse.Namespace) -> int:
    """Dump current configuration JSON with secrets redacted."""
    try:
        settings = load_settings()
        data = settings.model_dump(mode="json")
        redacted = redact_settings_dict(data)
        print(json.dumps(redacted, indent=2, default=str))
        return 0
    except (ConfigValidationError, ValueError, OSError) as exc:
        print(f"✗ Failed to load configuration: {exc}", file=sys.stderr)
        return 1


def cmd_list_config_revisions(args: argparse.Namespace) -> int:
    """List managed non-secret configuration revision metadata."""
    try:
        settings = load_settings(config_path=args.config, include_managed=False)
        store = ManagedConfigStore(settings.admin.state_dir)
        print(json.dumps(store.list_revisions(), indent=2))
        return 0
    except (ConfigValidationError, ValueError, OSError) as exc:
        print(f"✗ Failed to list configuration revisions: {exc}", file=sys.stderr)
        return 1


def cmd_stage_config_rollback(args: argparse.Namespace) -> int:
    """Stage a prior non-secret overlay for the next external restart."""
    try:
        settings = load_settings(config_path=args.config, include_managed=False)
        store = ManagedConfigStore(settings.admin.state_dir)
        overlay = store.revision_overlay(args.revision)
        _candidate, normalized = validate_candidate(settings, overlay)
        metadata = store.stage(
            normalized,
            expected_revision=store.current_revision(),
            request_id=f"cli-{uuid.uuid4().hex}",
        )
        print(json.dumps({**metadata, "restart_required": True}, indent=2))
        return 0
    except (ConfigValidationError, ValueError, OSError) as exc:
        print(f"✗ Failed to stage configuration rollback: {exc}", file=sys.stderr)
        return 1


def _verification_directory(args: argparse.Namespace) -> Path:
    """Return the package directory to verify.

    This is the single place that maps command-line arguments to a package location. A
    store-based ``--revision ID`` source would resolve its key here and return a local copy.
    """
    directory = Path(args.path)
    if not directory.is_dir():
        raise NotADirectoryError(f"Package directory not found: {directory}")
    return directory


def _print_verification(report: VerificationReport) -> None:
    print(f"Revision: {report.revision_id or 'unknown'}")
    for check in report.checks:
        print(f"[{'ok' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    print("Result: PASSED" if report.passed else "Result: FAILED")


def _write_report(path: Path, report: VerificationReport) -> None:
    with path.open("xb") as handle:
        handle.write(report.to_json())


def _expected_signers(args: argparse.Namespace) -> tuple[str, ...] | None:
    """Return the normalized ``--expected-signer`` fingerprints, or ``None`` when not given."""
    if not args.expected_signer:
        return None
    signers = tuple(value.replace(":", "").strip().lower() for value in args.expected_signer)
    for signer in signers:
        if not _FINGERPRINT_PATTERN.fullmatch(signer):
            raise PermanentError(f"--expected-signer is not a SHA-256 fingerprint: {signer!r}")
    return signers


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify a package directory offline; needs no configuration, Zammad or network."""
    # Imported here: verification needs the optional signing extra, the other commands do not.
    from chronikwerk.preservation.seal import load_trust_anchors
    from chronikwerk.preservation.verify import verify_package_directory

    report_path = Path(args.report) if args.report else None
    try:
        directory = _verification_directory(args)
        expected_signers = _expected_signers(args)
        anchors = load_trust_anchors(Path(args.trust_anchors))
        if report_path is not None and report_path.exists():
            raise FileExistsError(f"Report file already exists: {report_path}")
    except (PermanentError, OSError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = asyncio.run(
        verify_package_directory(
            directory, trust_anchors=anchors, expected_signer_sha256=expected_signers
        )
    )
    _print_verification(report)
    if report_path is not None:
        try:
            _write_report(report_path, report)
        except OSError as exc:
            print(f"✗ Could not write report: {exc}", file=sys.stderr)
            return EXIT_USAGE
    return 0 if report.passed else 1


def _add_basic_commands(subparsers: argparse._SubParsersAction) -> None:
    validate_parser = subparsers.add_parser("validate-config", help="Validate configuration")
    validate_parser.add_argument("--config", default=None, help="Path to YAML config file")
    validate_parser.set_defaults(func=cmd_validate_config)

    dump_parser = subparsers.add_parser("dump-config", help="Dump redacted config JSON")
    dump_parser.set_defaults(func=cmd_dump_config)

    list_parser = subparsers.add_parser(
        "list-config-revisions",
        help="List managed non-secret configuration revisions",
    )
    list_parser.add_argument("--config", default=None, help="Path to YAML config file")
    list_parser.set_defaults(func=cmd_list_config_revisions)

    rollback_parser = subparsers.add_parser(
        "stage-config-rollback",
        help="Stage a prior managed revision for the next restart",
    )
    rollback_parser.add_argument("revision", help="Full revision hash")
    rollback_parser.add_argument("--config", default=None, help="Path to YAML config file")
    rollback_parser.set_defaults(func=cmd_stage_config_rollback)

    verify_parser = subparsers.add_parser(
        "verify", help="Verify an archive package offline (no configuration or network)"
    )
    verify_parser.add_argument("--path", required=True, help="Package directory to verify")
    verify_parser.add_argument(
        "--trust-anchors", required=True, help="PEM bundle with the seal and TSA trust anchors"
    )
    verify_parser.add_argument(
        "--expected-signer",
        action="append",
        default=None,
        metavar="SHA256",
        help="Accept only a seal signer with this certificate SHA-256 fingerprint (repeatable)",
    )
    verify_parser.add_argument("--report", default=None, help="Write the JSON report (new file)")
    verify_parser.set_defaults(func=cmd_verify)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chronikwerk-admin",
        description="Chronikwerk administration utilities",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    _add_basic_commands(subparsers)
    return parser


def main() -> int:
    """Dispatch a maintenance command or print help when none is selected."""
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
