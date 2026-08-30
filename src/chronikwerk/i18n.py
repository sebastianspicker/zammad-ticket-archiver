"""Small shared locale catalog for the PDF and administration surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SUPPORTED_LOCALES = ("de-DE", "en-GB")
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
            "Auftragsverlauf, Sitzungen und Zulassungszähler sind prozesslokal und flüchtig."
        ),
        "admin.scope": "Geltungsbereich",
        "admin.status_record": "Instanznachweis",
        "admin.overview_intro": (
            "Aktuelle Nachweise für diesen Prozess: Zulassungskapazität, "
            "Speichererreichbarkeit, Konfigurationsrevisionen und kürzliche "
            "Archivierungsfehler."
        ),
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
        "admin.failures_in_history": "{count} im flüchtigen Verlauf",
        "admin.history_clears_on_restart": (
            "Einträge werden gelöscht, wenn der Prozess neu startet."
        ),
        "admin.trigger_tag": "Auslöse-Tag",
        "admin.view_history": "Flüchtigen Auftragsverlauf anzeigen",
        "admin.recent_failures": "Letzte Fehler",
        "admin.no_failures": "Keine Fehler im flüchtigen Verlauf.",
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
        "admin.no_jobs": "Keine passenden Auftragsereignisse im flüchtigen Verlauf.",
        "admin.next_page": "Ältere Ereignisse",
        "admin.ticket_history": "Ticketverlauf",
        "admin.retry": "Ticket erneut verarbeiten",
        "admin.retry_warning": (
            "Eine erneute Verarbeitung kann vorhandene PDF- und Sidecar-Dateien ersetzen."
        ),
        "admin.retry_ack": "Ich habe das Überschreibungsrisiko geprüft.",
        "admin.retry_submit": "Erneute Verarbeitung anfordern",
        "admin.retry_unavailable": (
            "Die erneute Verarbeitung konnte nicht angenommen werden. "
            "Warten Sie kurz und versuchen Sie es erneut."
        ),
        "admin.accepted": (
            "Die Anfrage wurde angenommen; die Archivierung ist noch nicht abgeschlossen."
        ),
        "admin.config_intro": (
            "Nur freigegebene, nicht geheime Werte können bereitgestellt werden. "
            "Umgebungswerte sind schreibgeschützt."
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
        "admin.staged_success": "Revision bereitgestellt. Ein externer Neustart ist erforderlich.",
        "admin.path": "Pfad",
        "admin.before": "Vorher",
        "admin.after": "Nachher",
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
        "admin.session_volatile": "Sessions are process-local and end when the service restarts.",
        "admin.login_scope": (
            "Check service status, inspect jobs, and stage non-secret configuration values."
        ),
        "admin.admin_access": "Administration access",
        "admin.process_local": (
            "Job history, sessions, and admission counters are process-local and volatile."
        ),
        "admin.scope": "Scope",
        "admin.status_record": "Instance record",
        "admin.overview_intro": (
            "Current evidence for this process: admission capacity, storage reachability, "
            "configuration revisions, and recent archival failures."
        ),
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
        "admin.failures_in_history": "{count} in volatile history",
        "admin.history_clears_on_restart": "Entries clear when the process restarts.",
        "admin.trigger_tag": "Trigger tag",
        "admin.view_history": "View volatile job history",
        "admin.recent_failures": "Recent failures",
        "admin.no_failures": "No failures in volatile history.",
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
        "admin.no_jobs": "No matching job events in volatile history.",
        "admin.next_page": "Older events",
        "admin.ticket_history": "Ticket history",
        "admin.retry": "Reprocess ticket",
        "admin.retry_warning": "Reprocessing can replace existing PDF and sidecar files.",
        "admin.retry_ack": "I have reviewed the overwrite risk.",
        "admin.retry_submit": "Request reprocessing",
        "admin.retry_unavailable": (
            "Reprocessing could not be accepted. Wait briefly and try again."
        ),
        "admin.accepted": "The request was accepted; archiving has not completed yet.",
        "admin.config_intro": (
            "Only allowlisted non-secret values can be staged. Environment-owned values "
            "are read-only."
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
        "admin.security_ack": "I acknowledge the effect of security-sensitive transport changes.",
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
