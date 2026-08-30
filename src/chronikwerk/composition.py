"""Assemble validated configuration, operations, archiving, and web delivery."""

from __future__ import annotations

from fastapi import FastAPI

from chronikwerk.archiving.options import ArchiveRuntimeOptions, ArchiveWorkflowOptions
from chronikwerk.archiving.processor import build_ticket_processor
from chronikwerk.configuration.load import load_settings
from chronikwerk.configuration.models import Settings
from chronikwerk.documents.options import DocumentOptions, SigningOptions, TimestampOptions
from chronikwerk.operations.admission import JobAdmission
from chronikwerk.operations.logging import configure_logging
from chronikwerk.operations.scheduling import TicketSchedulingService
from chronikwerk.storage.options import ArchiveStorageOptions
from chronikwerk.web.app import create_app


def build_runtime_application() -> tuple[Settings, FastAPI]:
    """Build the configured process application from its explicit dependencies."""
    settings = load_settings()
    configure_logging(
        log_level=settings.observability.log_level,
        log_format=settings.observability.log_format,
    )
    admission = JobAdmission(
        max_pending=settings.admission.max_pending,
        max_running=settings.admission.max_running,
    )
    scheduler = TicketSchedulingService(
        admission=admission,
        process_ticket=build_ticket_processor(_archive_runtime_options(settings)),
    )
    return settings, create_app(settings, admission=admission, scheduler=scheduler)


def _archive_runtime_options(settings: Settings) -> ArchiveRuntimeOptions:
    """Translate validated aggregate configuration at the composition boundary only."""
    signing = settings.signing
    timestamp = signing.timestamp.rfc3161
    return ArchiveRuntimeOptions(
        connection=settings.zammad_connection,
        workflow=ArchiveWorkflowOptions(
            trigger_tag=str(settings.workflow.trigger_tag).strip() or "pdf:sign",
            require_trigger_tag=bool(settings.workflow.require_tag),
            acknowledge_on_success=settings.workflow.acknowledge_on_success,
            delivery_id_ttl_seconds=settings.workflow.delivery_id_ttl_seconds,
            archive_path_field_name=settings.fields.archive_path,
            archive_user_mode_field_name=settings.fields.archive_user_mode,
            archive_user_field_name=settings.fields.archive_user,
        ),
        documents=DocumentOptions(
            max_articles=settings.pdf.max_articles,
            article_limit_mode=settings.pdf.article_limit_mode,
            locale=settings.pdf.locale,
            timezone=settings.pdf.timezone,
            signing=SigningOptions(
                enabled=signing.enabled,
                pfx_path=signing.pfx_path,
                pfx_password=(
                    signing.pfx_password.get_secret_value()
                    if signing.pfx_password is not None
                    else None
                ),
                reason=signing.pades.reason,
                location=signing.pades.location,
                timestamp=TimestampOptions(
                    enabled=signing.timestamp.enabled,
                    tsa_url=str(timestamp.tsa_url) if timestamp.tsa_url is not None else None,
                    timeout_seconds=timestamp.timeout_seconds,
                    ca_bundle_path=timestamp.ca_bundle_path,
                    user=timestamp.user,
                    password=(
                        timestamp.password.get_secret_value()
                        if timestamp.password is not None
                        else None
                    ),
                    trust_env=settings.hardening.transport.trust_env,
                    allow_insecure_http=settings.hardening.transport.allow_insecure_http,
                    allow_private_networks=settings.hardening.transport.allow_private_networks,
                ),
            ),
        ),
        storage=ArchiveStorageOptions(
            root=settings.storage.root,
            fsync=settings.storage.fsync,
            filename_pattern=settings.storage.filename_pattern,
        ),
    )
