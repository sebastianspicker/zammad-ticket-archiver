"""CLI commands for zammad-pdf-archiver.

This module provides command-line utilities for:
- Validating configuration
- Dumping configuration (with secrets redacted)
- Showing deprecated environment variables
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import structlog

from zammad_pdf_archiver.config.env_aliases import _DEPRECATED_ALIASES
from zammad_pdf_archiver.config.load import load_settings

log = structlog.get_logger(__name__)


def _redact_settings_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact sensitive values in a settings dict.
    
    Args:
        data: Settings dictionary
        
    Returns:
        Dictionary with redacted secrets
    """
    sensitive_keys = {
        "api_token", "webhook_hmac_secret", "webhook_shared_secret",
        "pfx_password", "key_password", "password", "metrics_bearer_token",
    }
    
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = _redact_settings_dict(value)
        elif isinstance(value, str) and key in sensitive_keys:
            result[key] = "***REDACTED***"
        else:
            result[key] = value
    
    return result


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
        redacted = _redact_settings_dict(data)
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
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return 0
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
