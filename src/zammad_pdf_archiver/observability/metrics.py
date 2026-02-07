from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Histogram,
    generate_latest,
)

processed_total = Counter(
    "processed_total",
    "Number of successfully processed tickets.",
)
failed_total = Counter(
    "failed_total",
    "Number of failed ticket processing attempts.",
)

render_seconds = Histogram(
    "render_seconds",
    "Seconds spent rendering the PDF.",
)
sign_seconds = Histogram(
    "sign_seconds",
    "Seconds spent signing the PDF.",
)
total_seconds = Histogram(
    "total_seconds",
    "Seconds spent processing a ticket end-to-end.",
)


def render_latest(*, registry=REGISTRY) -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
