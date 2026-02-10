"""NFR10: No mandatory external queue; in-memory dedupe and in-flight guard."""
from __future__ import annotations

import tomllib
from pathlib import Path


def test_nfr10_no_redis_or_celery_in_dependencies() -> None:
    """NFR10: Design constraint: no Redis/Celery/RabbitMQ as required runtime deps (optional deps allowed)."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = repo_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    deps = data.get("project", {}).get("dependencies", [])
    forbidden = ("redis", "celery", "rabbitmq", "pika", "kombu")
    for dep in deps:
        dep_lower = dep.lower()
        for word in forbidden:
            assert word not in dep_lower, (
                f"NFR10: required dependency {dep!r} must not contain {word!r}"
            )
