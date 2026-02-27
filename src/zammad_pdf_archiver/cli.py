"""CLI commands for zammad-pdf-archiver.

This module provides command-line utilities for:
- Validating configuration
- Dumping configuration (with secrets redacted)
- Showing deprecated environment variables
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import structlog

from zammad_pdf_archiver.app.jobs.history import read_history
from zammad_pdf_archiver.app.jobs.redis_queue import drain_dlq, get_queue_stats
from zammad_pdf_archiver.config.env_aliases import _DEPRECATED_ALIASES
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.redact import redact_settings_dict

log = structlog.get_logger(__name__)

def cmd_validate_config(args: argparse.Namespace) -> int:
    """Validate configuration and exit with appropriate code.
    
    Exit codes:
        0: Configuration is valid
        1: Configuration is invalid
        2: Configuration file not found (when CONFIG_PATH is set)
    """
    try:
        settings = load_settings()
        print("✓ Configuration is valid")
        print(f"  - Zammad URL: {settings.zammad.base_url}")
        print(f"  - Storage root: {settings.storage.root}")
        print(f"  - Signing enabled: {settings.signing.enabled}")
        print(f"  - Metrics enabled: {settings.observability.metrics_enabled}")
        return 0
    except FileNotFoundError as e:
        print(f"✗ Configuration file not found: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"✗ Configuration is invalid: {e}", file=sys.stderr)
        return 1


def cmd_dump_config(args: argparse.Namespace) -> int:
    """Dump current configuration as JSON (with secrets redacted)."""
    try:
        settings = load_settings()
        # Convert to dict and redact
        data = settings.model_dump(mode="json")
        redacted = redact_settings_dict(data)
        print(json.dumps(redacted, indent=2, default=str))
        return 0
    except Exception as e:
        print(f"✗ Failed to load configuration: {e}", file=sys.stderr)
        return 1


def cmd_show_deprecated(args: argparse.Namespace) -> int:
    """Show deprecated environment variables that are in use."""
    import os
    
    found = []
    for old_name, new_name in _DEPRECATED_ALIASES.items():
        if old_name in os.environ:
            found.append((old_name, new_name, os.environ.get(new_name) is None))
    
    if not found:
        print("No deprecated environment variables in use.")
        return 0
    
    print("Deprecated environment variables detected:")
    print()
    for old_name, new_name, needs_migration in found:
        status = "⚠️  NEEDS MIGRATION" if needs_migration else "ℹ️  Has canonical override"
        print(f"  {old_name} → {new_name} {status}")
    
    print()
    print("These variables will be removed in a future version.")
    print("Please migrate to the canonical names.")
    return 0


def cmd_queue_stats(args: argparse.Namespace) -> int:
    """Show queue stats as JSON for operational diagnostics."""
    try:
        settings = load_settings()
        stats = asyncio.run(get_queue_stats(settings))
        print(json.dumps(stats, indent=2, default=str))
        return 0
    except Exception as e:
        print(f"✗ Failed to read queue stats: {e}", file=sys.stderr)
        return 1


def cmd_queue_drain_dlq(args: argparse.Namespace) -> int:
    """Drain dead-letter queue entries (bounded by --limit)."""
    try:
        settings = load_settings()
        backend = (settings.workflow.execution_backend or "inprocess").strip().lower()
        if backend != "redis_queue":
            print(
                "✗ queue-drain-dlq requires workflow.execution_backend=redis_queue",
                file=sys.stderr,
            )
            return 1

        drained = asyncio.run(drain_dlq(settings, limit=int(args.limit)))
        print(json.dumps({"status": "ok", "drained": drained}, indent=2))
        return 0
    except Exception as e:
        print(f"✗ Failed to drain DLQ: {e}", file=sys.stderr)
        return 1


def cmd_queue_history(args: argparse.Namespace) -> int:
    """Show queue history events as JSON."""
    try:
        settings = load_settings()
        backend = (settings.workflow.execution_backend or "inprocess").strip().lower()
        if backend != "redis_queue" and not settings.workflow.redis_url:
            payload = {"status": "ok", "count": 0, "items": []}
            print(json.dumps(payload, indent=2))
            return 0

        items = asyncio.run(
            read_history(
                settings,
                limit=int(args.limit),
                ticket_id=getattr(args, "ticket_id", None),
            )
        )
        payload = {"status": "ok", "count": len(items), "items": items}
        print(json.dumps(payload, indent=2, default=str))
        return 0
    except Exception as e:
        print(f"✗ Failed to read queue history: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="zammad-pdf-archiver",
        description="Zammad PDF Archiver CLI utilities",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # validate-config
    validate_parser = subparsers.add_parser(
        "validate-config",
        help="Validate configuration and exit",
    )
    validate_parser.set_defaults(func=cmd_validate_config)
    
    # dump-config
    dump_parser = subparsers.add_parser(
        "dump-config",
        help="Dump configuration as JSON (secrets redacted)",
    )
    dump_parser.set_defaults(func=cmd_dump_config)
    
    # show-deprecated
    deprecated_parser = subparsers.add_parser(
        "show-deprecated",
        help="Show deprecated environment variables in use",
    )
    deprecated_parser.set_defaults(func=cmd_show_deprecated)

    queue_stats_parser = subparsers.add_parser(
        "queue-stats",
        help="Show queue stats (redis_queue backend)",
    )
    queue_stats_parser.set_defaults(func=cmd_queue_stats)

    queue_drain_parser = subparsers.add_parser(
        "queue-drain-dlq",
        help="Drain dead-letter queue entries",
    )
    queue_drain_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of DLQ entries to drain (default: 100, max: 1000)",
    )
    queue_drain_parser.set_defaults(func=cmd_queue_drain_dlq)

    queue_history_parser = subparsers.add_parser(
        "queue-history",
        help="Show processing history from Redis stream",
    )
    queue_history_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of entries to return (default: 100)",
    )
    queue_history_parser.add_argument(
        "--ticket-id",
        type=int,
        default=None,
        help="Optional ticket_id filter",
    )
    queue_history_parser.set_defaults(func=cmd_queue_history)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return 0
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
