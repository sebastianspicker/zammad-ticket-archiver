from __future__ import annotations

import json
from pathlib import Path

import yaml


def _load_schema(repo_root: Path) -> dict:
    return json.loads((repo_root / "config" / "config.schema.json").read_text(encoding="utf-8"))


def _load_example(repo_root: Path) -> dict:
    raw = yaml.safe_load((repo_root / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def test_config_schema_includes_runtime_settings_extensions() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    schema = _load_schema(repo_root)
    props = schema["properties"]

    workflow_props = props["workflow"]["properties"]
    assert "execution_backend" in workflow_props
    assert "idempotency_backend" in workflow_props
    assert "redis_url" in workflow_props
    assert "queue_stream" in workflow_props
    assert "queue_group" in workflow_props
    assert "queue_read_block_ms" in workflow_props
    assert "queue_read_count" in workflow_props
    assert "queue_retry_max_attempts" in workflow_props
    assert "queue_retry_backoff_seconds" in workflow_props
    assert "queue_dlq_stream" in workflow_props
    assert "history_stream" in workflow_props
    assert "history_retention_maxlen" in workflow_props

    fields_props = props["fields"]["properties"]
    assert "archive_user" in fields_props

    pdf_props = props["pdf"]["properties"]
    assert "article_limit_mode" in pdf_props
    assert "include_attachment_binary" in pdf_props
    assert "max_attachment_bytes_per_file" in pdf_props
    assert "max_total_attachment_bytes" in pdf_props

    obs_props = props["observability"]["properties"]
    assert "metrics_bearer_token" in obs_props
    assert "healthz_omit_version" in obs_props

    webhook_props = props["hardening"]["properties"]["webhook"]["properties"]
    assert "allow_unsigned_when_no_secret" in webhook_props

    rate_limit_props = props["hardening"]["properties"]["rate_limit"]["properties"]
    assert "client_key_header" in rate_limit_props

    transport_props = props["hardening"]["properties"]["transport"]["properties"]
    assert "allow_local_upstreams" in transport_props

    admin_props = props["admin"]["properties"]
    assert "enabled" in admin_props
    assert "bearer_token" in admin_props
    assert "history_limit" in admin_props


def test_config_example_contains_supported_keys() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = _load_example(repo_root)

    assert "workflow" in config and isinstance(config["workflow"], dict)
    assert "execution_backend" in config["workflow"]
    assert "idempotency_backend" in config["workflow"]
    assert "redis_url" in config["workflow"]
    assert "queue_stream" in config["workflow"]
    assert "queue_group" in config["workflow"]
    assert "queue_read_block_ms" in config["workflow"]
    assert "queue_read_count" in config["workflow"]
    assert "queue_retry_max_attempts" in config["workflow"]
    assert "queue_retry_backoff_seconds" in config["workflow"]
    assert "queue_dlq_stream" in config["workflow"]
    assert "history_stream" in config["workflow"]
    assert "history_retention_maxlen" in config["workflow"]

    assert "fields" in config and isinstance(config["fields"], dict)
    assert "archive_user" in config["fields"]

    assert "pdf" in config and isinstance(config["pdf"], dict)
    assert "article_limit_mode" in config["pdf"]
    assert "include_attachment_binary" in config["pdf"]
    assert "max_attachment_bytes_per_file" in config["pdf"]
    assert "max_total_attachment_bytes" in config["pdf"]

    assert "observability" in config and isinstance(config["observability"], dict)
    assert "metrics_bearer_token" in config["observability"]
    assert "healthz_omit_version" in config["observability"]

    assert "hardening" in config and isinstance(config["hardening"], dict)
    assert "allow_unsigned_when_no_secret" in config["hardening"]["webhook"]
    assert "client_key_header" in config["hardening"]["rate_limit"]
    assert "allow_local_upstreams" in config["hardening"]["transport"]

    assert "admin" in config and isinstance(config["admin"], dict)
    assert "enabled" in config["admin"]
    assert "bearer_token" in config["admin"]
    assert "history_limit" in config["admin"]
