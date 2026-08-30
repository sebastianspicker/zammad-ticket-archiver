"""Define Prometheus metrics shared by operational services."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

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

admission_pending = Gauge(
    "admission_pending",
    "Number of admitted ticket jobs waiting for a running slot.",
)
admission_running = Gauge(
    "admission_running",
    "Number of ticket jobs currently running.",
)
admission_rejected_total = Counter(
    "admission_rejected_total",
    "Number of ticket jobs rejected because admission capacity was exhausted.",
)
