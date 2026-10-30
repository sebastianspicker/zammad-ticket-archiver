"""Assemble validated configuration, operations, archiving, and web delivery."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable

from fastapi import FastAPI

from chronikwerk.archiving.options import ArchiveWorkflowOptions
from chronikwerk.composition_preservation import (
    PreservationServices,
    build_preservation_services,
    load_preservation_material,
    timestamp_options,
)
from chronikwerk.configuration.load import load_settings
from chronikwerk.configuration.models import Settings
from chronikwerk.configuration.sections import WorkflowSettings
from chronikwerk.configuration.validation import ConfigValidationError
from chronikwerk.documents.options import DocumentOptions, SigningOptions
from chronikwerk.operations.logging import configure_logging
from chronikwerk.web.app import create_app
from chronikwerk.zammad.gateway import AsyncZammadClient

_DEFAULT_TRIGGER_TAG: str = WorkflowSettings.model_fields["trigger_tag"].default


def build_runtime_application() -> tuple[Settings, FastAPI]:
    """Build the configured process application from its explicit dependencies.

    Refuses to start (``ConfigValidationError`` listing every problem) when a preservation
    requirement is unmet, before any client, database or store is opened.
    """
    settings = load_settings()
    configure_logging(
        log_level=settings.observability.log_level,
        log_format=settings.observability.log_format,
    )
    material = load_preservation_material(settings)
    client = AsyncZammadClient(connection=settings.zammad_connection)
    services = build_preservation_services(
        settings,
        material,
        client=client,
        workflow=_workflow_options(settings),
        documents=_document_options(settings),
    )
    return settings, create_app(
        settings,
        scheduler=services.scheduler,
        history=services.scheduler.history,
        cleanup=_cleanup(client, services),
    )


def build_runtime_application_or_exit() -> tuple[Settings, FastAPI]:
    """Build the process application, or print every startup problem and exit with status 1.

    Used by the server entry point and the ASGI module, so a refused configuration ends the
    process with a readable problem list instead of a traceback.
    """
    try:
        return build_runtime_application()
    except ConfigValidationError as exc:
        print(f"chronikwerk cannot start.\n{exc}", file=sys.stderr)
        raise SystemExit(1) from None


def _cleanup(
    client: AsyncZammadClient, services: PreservationServices
) -> Callable[[], Awaitable[None]]:
    """Return the shutdown hook the lifespan runs after the scheduler's ``aclose``."""

    async def close() -> None:
        """Close the Zammad client, then the catalog, then the journal."""
        try:
            await client.aclose()
        finally:
            try:
                services.catalog.close()
            finally:
                services.journal.close()

    return close


def _workflow_options(settings: Settings) -> ArchiveWorkflowOptions:
    """Translate the workflow tags and archive-location field names."""
    return ArchiveWorkflowOptions(
        trigger_tag=str(settings.workflow.trigger_tag).strip() or _DEFAULT_TRIGGER_TAG,
        require_trigger_tag=bool(settings.workflow.require_tag),
        acknowledge_on_success=settings.workflow.acknowledge_on_success,
        archive_path_field_name=settings.fields.archive_path,
        archive_user_mode_field_name=settings.fields.archive_user_mode,
        archive_user_field_name=settings.fields.archive_user,
    )


def _document_options(settings: Settings) -> DocumentOptions:
    """Translate validated rendering and PAdES settings at the composition boundary only."""
    signing = settings.signing
    return DocumentOptions(
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
            timestamp=timestamp_options(settings),
        ),
    )
