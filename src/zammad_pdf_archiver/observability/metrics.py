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
skipped_total = Counter(
    "skipped_total",
    "Number of skipped ticket processing attempts.",
    labelnames=("reason",),
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

queue_enqueued_total = Counter(
    "queue_enqueued_total",
    "Number of jobs enqueued to the durable queue.",
)
queue_processed_total = Counter(
    "queue_processed_total",
    "Number of queued jobs processed successfully.",
)
queue_retried_total = Counter(
    "queue_retried_total",
    "Number of queued jobs re-scheduled for retry.",
)
queue_failed_total = Counter(
    "queue_failed_total",
    "Number of queued jobs that failed to process in a worker.",
)
queue_dlq_total = Counter(
    "queue_dlq_total",
    "Number of queued jobs moved to dead-letter queue.",
)


def render_latest(*, registry=REGISTRY) -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
