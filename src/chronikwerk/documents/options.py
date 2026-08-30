"""Immutable runtime values consumed by document production."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TimestampOptions:
    """Configure optional RFC 3161 timestamping for one signing operation."""

    enabled: bool
    tsa_url: str | None
    timeout_seconds: float
    ca_bundle_path: Path | None
    user: str | None
    password: str | None
    trust_env: bool
    allow_insecure_http: bool
    allow_private_networks: bool


@dataclass(frozen=True, slots=True)
class SigningOptions:
    """Configure optional PAdES signing without exposing configuration models."""

    enabled: bool
    pfx_path: Path | None
    pfx_password: str | None
    reason: str
    location: str
    timestamp: TimestampOptions


@dataclass(frozen=True, slots=True)
class DocumentOptions:
    """Configure snapshot limits, rendering locale, and optional signing."""

    max_articles: int
    article_limit_mode: str
    locale: str
    timezone: str
    signing: SigningOptions
