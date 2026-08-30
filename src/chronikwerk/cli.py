"""CLI entry point for config validation and diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from chronikwerk.configuration.load import load_settings
from chronikwerk.configuration.redaction import redact_settings_dict
from chronikwerk.configuration.revisions import ManagedConfigStore, validate_candidate
from chronikwerk.configuration.validation import ConfigValidationError


def _missing_config_path_from_error(error: ConfigValidationError) -> str | None:
    for issue in error.issues:
        if issue.path != "CONFIG_PATH":
            continue
        prefix = "Config file not found:"
        if issue.message.startswith(prefix):
            return issue.message.removeprefix(prefix).strip()
    return None


def cmd_validate_config(args: argparse.Namespace) -> int:
    """Validate the effective configuration before an operator starts the service."""
    try:
        load_settings(config_path=args.config)
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
