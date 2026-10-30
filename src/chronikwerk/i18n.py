"""Small shared locale catalog for the PDF and administration surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_LOCALE = "de-DE"

_CATALOGS: dict[str, dict[str, str]] = {
    "de-DE": {
        "pdf.ticket": "Ticket",
        "pdf.ticket_articles": "Ticketartikel",
        "pdf.ticket_metadata": "Ticketmetadaten",
        "pdf.created": "Erstellt",
        "pdf.updated": "Aktualisiert",
        "pdf.requester": "Anfragende Person / Kunde",
        "pdf.owner": "Verantwortlich",
        "pdf.tags": "Tags",
        "pdf.archive_path": "Archivpfad",
        "pdf.archive_user_mode": "Archiv-Benutzermodus",
        "pdf.article_count.one": "{included} von {total} Artikel",
        "pdf.article_count.other": "{included} von {total} Artikeln",
        "pdf.omitted.one": (
            "Dieser Archivexport ist unvollständig: {omitted} Artikel wurde aufgrund "
            "der konfigurierten Begrenzung ausgelassen."
        ),
        "pdf.omitted.other": (
            "Dieser Archivexport ist unvollständig: {omitted} Artikel wurden aufgrund "
            "der konfigurierten Begrenzung ausgelassen."
        ),
        "pdf.internal": "Intern",
        "pdf.external": "Extern",
        "pdf.article": "Artikel",
        "pdf.attachments": "Anhänge",
        "pdf.attachments_index": "Anhangsverzeichnis",
        "pdf.unnamed_file": "Unbenannte Datei",
        "pdf.no_articles": "Keine Artikel.",
        "pdf.no_attachments": "Keine Anhänge.",
        "pdf.archived_by": "Archiviert durch Chronikwerk",
        "pdf.page": "Seite",
        "pdf.of": "von",
        "admin.service_name": "Chronikwerk",
        "admin.control_plane": "Administration",
        "admin.workspace": "Arbeitsbereiche",
        "admin.skip": "Zum Hauptinhalt springen",
        "admin.overview": "Übersicht",
        "admin.jobs": "Aufträge",
        "admin.configuration": "Konfiguration",
        "admin.revisions": "Revisionen",
        "admin.sign_in": "Anmelden",
        "admin.sign_out": "Abmelden",
        "admin.access_token": "Admin-Zugriffstoken",
        "admin.login_help": "Verwenden Sie das extern verwaltete Admin-Zugriffstoken.",
        "admin.invalid_credentials": "Die Anmeldedaten sind ungültig.",
        "admin.session_volatile": "Sitzungen sind prozesslokal und enden bei einem Neustart.",
        "admin.login_scope": (
            "Prüfen Sie den Dienststatus, verfolgen Sie Aufträge und stellen Sie "
            "nicht geheime Konfigurationswerte bereit."
        ),
        "admin.admin_access": "Administrationszugang",
        "admin.process_local": (
            "Sitzungen sind prozesslokal. Anfragen und Auftragsverlauf liegen im Anfragejournal "
            "und überstehen einen Neustart."
        ),
        "admin.scope": "Geltungsbereich",
        "admin.status_record": "Instanznachweis",
        "admin.overview_intro": ("Prozesszustand, Archivaktivität und Konfiguration."),
        "admin.status": "Dienststatus",
        "admin.process": "Prozess",
        "admin.healthy": "Prozess läuft",
        "admin.started": "Prozess gestartet",
        "admin.version": "Version",
        "admin.capacity": "Zulassungskapazität",
        "admin.running": "Laufend",
        "admin.pending": "Wartend",
        "admin.limit": "Grenze",
        "admin.active_revision": "Aktive Revision",
        "admin.staged_revision": "Bereitgestellte Revision",
        "admin.restart_required": (
            "Ein externer Neustart ist erforderlich, damit die bereitgestellte "
            "Konfiguration aktiv wird."
        ),
        "admin.no_staged_revision": "Keine ausstehende Konfigurationsrevision.",
        "admin.storage_check": "Speicher jetzt prüfen",
        "admin.storage": "Speicher",
        "admin.storage_not_checked": "In dieser Sitzung nicht geprüft",
        "admin.storage_passed": "Letzte Prüfung erfolgreich",
        "admin.storage_failed": "Prüfung fehlgeschlagen",
        "admin.current_process": "Aktueller Prozess",
        "admin.restart_short": "Neustart erforderlich",
        "admin.no_staged_short": "Keine ausstehende Revision",
        "admin.accepted_not_completed": "Angenommene Arbeit ist noch nicht archiviert.",
        "admin.archive_evidence": "Archivnachweise",
        "admin.signing": "Signatur",
        "admin.timestamp": "Zeitstempel",
        "admin.optional": "Optional",
        "admin.optional_disabled": "Optional · deaktiviert",
        "admin.live_process": "Live-Prozess",
        "admin.in_memory": "Im Speicher",
        "admin.read_only": "Nur lesen",
        "admin.active_state": "Aktiv",
        "admin.process_alive": "Prozess aktiv",
        "admin.refreshed": "Aktualisiert",
        "admin.failures_in_history": "{count} im Auftragsverlauf",
        "admin.history_clears_on_restart": (
            "Einträge werden gelöscht, wenn der Prozess neu startet."
        ),
        "admin.trigger_tag": "Auslöse-Tag",
        "admin.view_history": "Auftragsverlauf anzeigen",
        "admin.recent_failures": "Letzte Fehler",
        "admin.no_failures": "Keine Fehler im Auftragsverlauf.",
        "admin.last_refresh": "Zuletzt aktualisiert",
        "admin.filter": "Filtern",
        "admin.clear_filters": "Filter zurücksetzen",
        "admin.activity_record": "Flüchtiger Verlauf",
        "admin.jobs_intro": (
            "Prozesslokale Auftragsereignisse durchsuchen und einzelne Tickets prüfen."
        ),
        "admin.ticket_id": "Ticket-ID",
        "admin.job_status": "Status",
        "admin.all_statuses": "Alle Status",
        "admin.time": "Zeit",
        "admin.classification": "Klassifizierung",
        "admin.message": "Meldung",
        "admin.request_id": "Anfrage-ID",
        "admin.no_jobs": "Keine passenden Aufträge im Verlauf.",
        "admin.next_page": "Ältere Ereignisse",
        "admin.ticket_history": "Ticketverlauf",
        "admin.retry": "Ticket erneut verarbeiten",
        "admin.retry_warning": (
            "Eine erneute Verarbeitung erstellt eine neue Revision, wenn sich das Ticket geändert "
            "hat; vorhandene Revisionen bleiben unverändert."
        ),
        "admin.retry_ack": "Ich möchte das Ticket erneut erfassen.",
        "admin.retry_submit": "Erneute Verarbeitung anfordern",
        "admin.retry_unavailable": (
            "Die erneute Verarbeitung konnte nicht angenommen werden. "
            "Warten Sie kurz und versuchen Sie es erneut."
        ),
        "admin.accepted": (
            "Die Anfrage wurde angenommen; die Archivierung ist noch nicht abgeschlossen."
        ),
        "admin.config_intro": (
            "Nicht geheime Werte vor dem Bereitstellen einer Revision prüfen. "
            "Die Aktivierung erfordert einen externen Neustart."
        ),
        "admin.controlled_values": "Kontrollierte Werte",
        "admin.view_revisions": "Revisionen anzeigen",
        "admin.edit_configuration": "Konfiguration bearbeiten",
        "admin.change_record": "Änderungsverlauf",
        "admin.revisions_intro": (
            "Gespeicherte Konfigurationsstände prüfen und als neue Revision bereitstellen."
        ),
        "admin.values": "Werte",
        "admin.no_changes": "Keine ungespeicherten Änderungen",
        "admin.one_change": "1 ungespeicherte Änderung",
        "admin.many_changes": "{count} ungespeicherte Änderungen",
        "admin.no_changes_to_stage": "Es gibt keine Änderungen zum Bereitstellen.",
        "admin.network_error": (
            "Die Konfiguration konnte nicht geprüft werden. Verbindung prüfen und erneut versuchen."
        ),
        "admin.source": "Quelle",
        "admin.editable": "Bearbeitbar",
        "admin.environment": "Umgebung",
        "admin.managed": "Verwaltet",
        "admin.base_or_default": "Basis oder Standard",
        "admin.validate_review": "Änderungen prüfen",
        "admin.stage": "Revision bereitstellen",
        "admin.security_ack": (
            "Ich bestätige die Auswirkungen der sicherheitsrelevanten Transportänderungen."
        ),
        "admin.restore": "Als neue Revision bereitstellen",
        "admin.restore_error": (
            "Die Revision konnte nicht bereitgestellt werden. Aktualisieren Sie die Seite "
            "und prüfen Sie den aktuellen Revisionsstand."
        ),
        "admin.restore_ack_required": (
            "Bestätigen Sie die Sicherheitsauswirkungen, bevor Sie eine Revision bereitstellen."
        ),
        "admin.changed_paths": "Geänderte Pfade",
        "admin.actions": "Aktionen",
        "admin.created_at": "Erstellt",
        "admin.previous_revision": "Vorherige Revision",
        "admin.empty_revisions": "Es sind noch keine Konfigurationsrevisionen vorhanden.",
        "admin.session_expired": (
            "Die Sitzung ist abgelaufen. Melden Sie sich erneut an; nicht gesendete "
            "Formulardaten bleiben in diesem Browser erhalten."
        ),
        "admin.close": "Schließen",
        "admin.language": "Sprache",
        "admin.primary_nav": "Hauptnavigation",
        "admin.storage_ok": "Der Archivspeicher ist beschreibbar.",
        "admin.storage_unavailable": "Der Archivspeicher ist nicht verfügbar.",
        "admin.refresh_failed": (
            "Die Statusaktualisierung ist fehlgeschlagen; die angezeigten Daten "
            "können veraltet sein."
        ),
        "admin.staged_success": (
            "Revision bereitgestellt. Ein externer Neustart ist erforderlich."
        ),
        "admin.path": "Pfad",
        "admin.before": "Vorher",
        "admin.after": "Nachher",
        "admin.changes": "Änderungen",
        "admin.review": "Prüfung",
        "admin.reset_edits": "Eingaben zurücksetzen",
        "admin.config_unchanged": "Die laufende Konfiguration bleibt unverändert.",
        "admin.config_read_only": "Umgebungswerte sind schreibgeschützt.",
        "admin.config_groups": "Konfigurationsbereiche",
        "admin.review_invalidated": (
            "Werte geändert. Prüfen Sie die Änderungen vor dem Bereitstellen erneut."
        ),
        "admin.validating": "Änderungen werden geprüft…",
        "admin.staging": "Revision wird bereitgestellt…",
        "admin.checking_storage": "Speicher wird geprüft…",
        "admin.signing_in": "Anmeldung läuft…",
        "admin.requesting": "Anfrage wird übermittelt…",
        "admin.restoring": "Wiederherstellung wird bereitgestellt…",
        "admin.review_help": "Diese Änderungen werden als neue Revision bereitgestellt.",
        "admin.storage_help": "Prüfen, ob das Archivverzeichnis beschreibbar ist.",
        "admin.capacity_slots": "von {count} Plätzen",
        "admin.config_archive": "Konfiguration und Archiv",
        "admin.open_configuration": "Konfiguration öffnen",
        "admin.view_all_jobs": "Alle Aufträge anzeigen",
        "admin.failures_help": "Fehler dieses Prozesses werden hier angezeigt.",
        "admin.back_jobs": "Zurück zu Aufträgen",
        "admin.filter_empty": "Keine Ereignisse entsprechen diesen Filtern.",
        "admin.filter_empty_help": (
            "Setzen Sie die Filter zurück, um alle Ereignisse dieses Prozesses zu sehen."
        ),
        "admin.history_empty": "Noch keine Auftragsereignisse",
        "admin.history_empty_help": (
            "Ereignisse erscheinen, sobald dieser Prozess Arbeit annimmt. Ein "
            "Neustart löscht den Verlauf."
        ),
        "admin.full_revision": "Vollständige Revisionskennung",
        "admin.edited": "Geändert",
        "admin.enabled": "Aktiviert",
        "admin.disabled": "Deaktiviert",
        "admin.group.workflow": "Archivierungsablauf",
        "admin.group.pdf": "PDF",
        "admin.group.storage": "Speicher",
        "admin.group.zammad": "Zammad",
        "admin.group.observability": "Protokollierung",
        "admin.group.admission": "Auftragsannahme",
        "admin.group.signing": "Signatur",
        "admin.group.security": "Sicherheit",
        "admin.field.workflow.trigger_tag": "Auslösendes Tag",
        "admin.field.workflow.require_tag": "Auslösendes Tag erforderlich",
        "admin.field.workflow.acknowledge_on_success": "Erfolg bestätigen",
        "admin.field.workflow.delivery_id_ttl_seconds": "Deduplizierungszeitraum (Sekunden)",
        "admin.field.pdf.locale": "Dokumentsprache",
        "admin.field.pdf.timezone": "Dokumentzeitzone",
        "admin.field.pdf.max_articles": "Maximale Artikelanzahl",
        "admin.field.pdf.article_limit_mode": "Verhalten am Artikellimit",
        "admin.field.storage.fsync": "Archivdateien auf Datenträger synchronisieren",
        "admin.field.storage.filename_pattern": "Muster für Archivdateinamen",
        "admin.field.zammad.timeout_seconds": "Anfragezeitlimit (Sekunden)",
        "admin.field.observability.log_level": "Protokollierungsstufe",
        "admin.field.observability.healthz_omit_version": ("Version in Zustandsantwort ausblenden"),
        "admin.field.admission.max_pending": "Wartende Aufträge",
        "admin.field.admission.max_running": "Laufende Aufträge",
        "admin.field.admission.shutdown_timeout_seconds": "Zeitlimit beim Beenden (Sekunden)",
        "admin.field.signing.pades.reason": "Signaturgrund",
        "admin.field.signing.pades.location": "Signaturort",
        "admin.field.hardening.transport.trust_env": ("Proxy-Einstellungen der Umgebung verwenden"),
        "admin.field.hardening.transport.allow_insecure_http": ("Unverschlüsseltes HTTP erlauben"),
        "admin.field.hardening.transport.allow_private_networks": (
            "Ziele in privaten Netzwerken erlauben"
        ),
    },
    "en-GB": {
        "pdf.ticket": "Ticket",
        "pdf.ticket_articles": "Ticket articles",
        "pdf.ticket_metadata": "Ticket metadata",
        "pdf.created": "Created",
        "pdf.updated": "Updated",
        "pdf.requester": "Requester / customer",
        "pdf.owner": "Owner",
        "pdf.tags": "Tags",
        "pdf.archive_path": "Archive path",
        "pdf.archive_user_mode": "Archive user mode",
        "pdf.article_count.one": "{included} of {total} article",
        "pdf.article_count.other": "{included} of {total} articles",
        "pdf.omitted.one": (
            "This archive export is incomplete: {omitted} article was omitted because "
            "of the configured limit."
        ),
        "pdf.omitted.other": (
            "This archive export is incomplete: {omitted} articles were omitted because "
            "of the configured limit."
        ),
        "pdf.internal": "Internal",
        "pdf.external": "External",
        "pdf.article": "Article",
        "pdf.attachments": "Attachments",
        "pdf.attachments_index": "Attachment index",
        "pdf.unnamed_file": "Unnamed file",
        "pdf.no_articles": "No articles.",
        "pdf.no_attachments": "No attachments.",
        "pdf.archived_by": "Archived by Chronikwerk",
        "pdf.page": "Page",
        "pdf.of": "of",
        "admin.service_name": "Chronikwerk",
        "admin.control_plane": "Administration",
        "admin.workspace": "Work areas",
        "admin.skip": "Skip to main content",
        "admin.overview": "Overview",
        "admin.jobs": "Jobs",
        "admin.configuration": "Configuration",
        "admin.revisions": "Revisions",
        "admin.sign_in": "Sign in",
        "admin.sign_out": "Sign out",
        "admin.access_token": "Admin access token",
        "admin.login_help": "Use the externally managed admin access token.",
        "admin.invalid_credentials": "The credentials are invalid.",
        "admin.session_volatile": ("Sessions are process-local and end when the service restarts."),
        "admin.login_scope": (
            "Check service status, inspect jobs, and stage non-secret configuration values."
        ),
        "admin.admin_access": "Administration access",
        "admin.process_local": (
            "Sessions are process-local. Requests and job history are kept in the request journal "
            "and survive a restart."
        ),
        "admin.scope": "Scope",
        "admin.status_record": "Instance record",
        "admin.overview_intro": ("Process health, archive activity, and configuration."),
        "admin.status": "Service status",
        "admin.process": "Process",
        "admin.healthy": "Process running",
        "admin.started": "Process started",
        "admin.version": "Version",
        "admin.capacity": "Admission capacity",
        "admin.running": "Running",
        "admin.pending": "Pending",
        "admin.limit": "Limit",
        "admin.active_revision": "Active revision",
        "admin.staged_revision": "Staged revision",
        "admin.restart_required": (
            "An external restart is required before the staged configuration becomes active."
        ),
        "admin.no_staged_revision": "No configuration revision is waiting to be applied.",
        "admin.storage_check": "Check storage now",
        "admin.storage": "Storage",
        "admin.storage_not_checked": "Not checked this session",
        "admin.storage_passed": "Last check passed",
        "admin.storage_failed": "Check failed",
        "admin.current_process": "Current process",
        "admin.restart_short": "Restart required",
        "admin.no_staged_short": "No staged revision",
        "admin.accepted_not_completed": "Accepted work is not completed archiving.",
        "admin.archive_evidence": "Archive evidence",
        "admin.signing": "Signing",
        "admin.timestamp": "Timestamp",
        "admin.optional": "Optional",
        "admin.optional_disabled": "Optional · disabled",
        "admin.live_process": "Live process",
        "admin.in_memory": "In memory",
        "admin.read_only": "Read-only",
        "admin.active_state": "Active",
        "admin.process_alive": "Process alive",
        "admin.refreshed": "Refreshed",
        "admin.failures_in_history": "{count} in job history",
        "admin.history_clears_on_restart": "Entries clear when the process restarts.",
        "admin.trigger_tag": "Trigger tag",
        "admin.view_history": "View job history",
        "admin.recent_failures": "Recent failures",
        "admin.no_failures": "No failures in job history.",
        "admin.last_refresh": "Last refreshed",
        "admin.filter": "Filter",
        "admin.clear_filters": "Clear filters",
        "admin.activity_record": "Volatile history",
        "admin.jobs_intro": "Search process-local job events and inspect individual tickets.",
        "admin.ticket_id": "Ticket ID",
        "admin.job_status": "Status",
        "admin.all_statuses": "All statuses",
        "admin.time": "Time",
        "admin.classification": "Classification",
        "admin.message": "Message",
        "admin.request_id": "Request ID",
        "admin.no_jobs": "No matching jobs in history.",
        "admin.next_page": "Older events",
        "admin.ticket_history": "Ticket history",
        "admin.retry": "Reprocess ticket",
        "admin.retry_warning": (
            "Reprocessing creates a new revision if the ticket has changed; existing revisions "
            "are never replaced."
        ),
        "admin.retry_ack": "I want to capture this ticket again.",
        "admin.retry_submit": "Request reprocessing",
        "admin.retry_unavailable": (
            "Reprocessing could not be accepted. Wait briefly and try again."
        ),
        "admin.accepted": "The request was accepted; archiving has not completed yet.",
        "admin.config_intro": (
            "Review non-secret values before staging a revision. "
            "Activation requires an external restart."
        ),
        "admin.controlled_values": "Controlled values",
        "admin.view_revisions": "View revisions",
        "admin.edit_configuration": "Edit configuration",
        "admin.change_record": "Change record",
        "admin.revisions_intro": (
            "Review retained configuration states and stage one as a new revision."
        ),
        "admin.values": "values",
        "admin.no_changes": "No unsaved changes",
        "admin.one_change": "1 unsaved change",
        "admin.many_changes": "{count} unsaved changes",
        "admin.no_changes_to_stage": "There are no changes to stage.",
        "admin.network_error": (
            "Configuration could not be checked. Check the connection and try again."
        ),
        "admin.source": "Source",
        "admin.editable": "Editable",
        "admin.environment": "Environment",
        "admin.managed": "Managed",
        "admin.base_or_default": "Base or default",
        "admin.validate_review": "Review changes",
        "admin.stage": "Stage revision",
        "admin.security_ack": ("I acknowledge the effect of security-sensitive transport changes."),
        "admin.restore": "Stage as a new revision",
        "admin.restore_error": (
            "The revision could not be staged. Refresh the page and check the current revision."
        ),
        "admin.restore_ack_required": (
            "Acknowledge the security effect before staging a revision."
        ),
        "admin.changed_paths": "Changed paths",
        "admin.actions": "Actions",
        "admin.created_at": "Created",
        "admin.previous_revision": "Previous revision",
        "admin.empty_revisions": "No configuration revisions exist yet.",
        "admin.session_expired": (
            "The session expired. Sign in again; unsent form data remains in this browser."
        ),
        "admin.close": "Close",
        "admin.language": "Language",
        "admin.primary_nav": "Primary navigation",
        "admin.storage_ok": "Archive storage is writable.",
        "admin.storage_unavailable": "Archive storage is unavailable.",
        "admin.refresh_failed": "Status refresh failed; displayed data may be stale.",
        "admin.staged_success": "Revision staged. An external restart is required.",
        "admin.path": "Path",
        "admin.before": "Before",
        "admin.after": "After",
        "admin.changes": "Changes",
        "admin.review": "Review",
        "admin.reset_edits": "Reset edits",
        "admin.config_unchanged": "Your running configuration is unchanged.",
        "admin.config_read_only": "Environment-owned values are read-only.",
        "admin.config_groups": "Configuration sections",
        "admin.review_invalidated": ("Values changed. Review the current changes before staging."),
        "admin.validating": "Checking changes…",
        "admin.staging": "Staging revision…",
        "admin.checking_storage": "Checking storage…",
        "admin.signing_in": "Signing in…",
        "admin.requesting": "Submitting request…",
        "admin.restoring": "Staging restore…",
        "admin.review_help": "These changes will be staged as a new revision.",
        "admin.storage_help": "Check that the archive location is writable.",
        "admin.capacity_slots": "of {count} slots",
        "admin.config_archive": "Configuration & archive",
        "admin.open_configuration": "Open configuration",
        "admin.view_all_jobs": "View all jobs",
        "admin.failures_help": "Failures from this process will appear here.",
        "admin.back_jobs": "Back to jobs",
        "admin.filter_empty": "No events match these filters.",
        "admin.filter_empty_help": "Clear the filters to see all events in this process.",
        "admin.history_empty": "No job events yet",
        "admin.history_empty_help": (
            "Events appear when this process accepts work. History clears on restart."
        ),
        "admin.full_revision": "Full revision identifier",
        "admin.edited": "Edited",
        "admin.enabled": "Enabled",
        "admin.disabled": "Disabled",
        "admin.group.workflow": "Archive workflow",
        "admin.group.pdf": "PDF",
        "admin.group.storage": "Storage",
        "admin.group.zammad": "Zammad",
        "admin.group.observability": "Logging",
        "admin.group.admission": "Admission",
        "admin.group.signing": "Signing",
        "admin.group.security": "Security",
        "admin.field.workflow.trigger_tag": "Trigger tag",
        "admin.field.workflow.require_tag": "Require trigger tag",
        "admin.field.workflow.acknowledge_on_success": "Acknowledge on success",
        "admin.field.workflow.delivery_id_ttl_seconds": ("Delivery deduplication window (seconds)"),
        "admin.field.pdf.locale": "Document language",
        "admin.field.pdf.timezone": "Document timezone",
        "admin.field.pdf.max_articles": "Maximum articles",
        "admin.field.pdf.article_limit_mode": "Article limit behavior",
        "admin.field.storage.fsync": "Sync archive writes to disk",
        "admin.field.storage.filename_pattern": "Archive filename pattern",
        "admin.field.zammad.timeout_seconds": "Request timeout (seconds)",
        "admin.field.observability.log_level": "Log level",
        "admin.field.observability.healthz_omit_version": "Hide version in health response",
        "admin.field.admission.max_pending": "Waiting jobs",
        "admin.field.admission.max_running": "Running jobs",
        "admin.field.admission.shutdown_timeout_seconds": "Shutdown timeout (seconds)",
        "admin.field.signing.pades.reason": "Signing reason",
        "admin.field.signing.pades.location": "Signing location",
        "admin.field.hardening.transport.trust_env": "Use environment proxy settings",
        "admin.field.hardening.transport.allow_insecure_http": "Allow unencrypted HTTP",
        "admin.field.hardening.transport.allow_private_networks": (
            "Allow private network destinations"
        ),
    },
}


def normalize_locale(value: str | None, *, default: str = DEFAULT_LOCALE) -> str:
    """Return a supported BCP 47 locale, accepting legacy underscore forms."""
    candidate = (value or default).strip().replace("_", "-")
    lowered = candidate.lower()
    aliases = {
        "de": "de-DE",
        "de-de": "de-DE",
        "en": "en-GB",
        "en-gb": "en-GB",
    }
    return aliases.get(lowered, default)


def catalog(locale: str | None) -> Mapping[str, str]:
    """Return the translation catalog for a normalized locale."""
    return _CATALOGS[normalize_locale(locale)]


def translate(locale: str | None, key: str, **values: Any) -> str:
    """Translate one stable UI key with an optional fallback value."""
    selected = catalog(locale)
    template = selected.get(key) or _CATALOGS["en-GB"].get(key) or key
    return template.format(**values)


def plural_key(base: str, value: int) -> str:
    """Choose the singular or plural catalog key for a count."""
    return f"{base}.one" if value == 1 else f"{base}.other"
