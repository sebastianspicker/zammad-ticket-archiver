"""NFR10: No mandatory external queue; in-memory dedupe and in-flight guard."""
from __future__ import annotations

from pathlib import Path


def test_nfr10_no_redis_or_celery_in_dependencies() -> None:
    """NFR10: Design constraint: no Redis/Celery/RabbitMQ as required runtime deps."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = repo_root / "pyproject.toml"
    text = pyproject.read_text()
    forbidden = ("redis", "celery", "rabbitmq", "pika", "kombu")
    for word in forbidden:
        assert word not in text.lower(), f"NFR10: optional queue dependency {word!r} must not be in pyproject"
