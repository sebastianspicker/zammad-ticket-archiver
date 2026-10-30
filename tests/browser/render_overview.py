"""Render real localized admin pages with isolated synthetic application state."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from chronikwerk.operations.history import JournalHistoryView
from chronikwerk.operations.journal import JournalOptions, RequestJournal, RequestSource
from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings

_ADMIN_TOKEN = "synthetic-browser-token"
_TICKET_ID = 4815


def _login(client: TestClient, locale: str) -> None:
    """Create one synthetic administrator session in the requested locale."""
    response = client.post(
        "/admin/login",
        data={"access_token": _ADMIN_TOKEN, "locale": locale},
        follow_redirects=False,
    )
    if response.status_code != 303:
        response.raise_for_status()


def _render_locale(client: TestClient, locale: str) -> dict[str, str]:
    """Render every authenticated page plus the signed-out login page."""
    _login(client, locale)
    prefix = "" if locale == "en-GB" else "/de"
    routes = (
        "/admin/",
        "/admin/configuration",
        "/admin/jobs",
        f"/admin/jobs/{_TICKET_ID}",
        "/admin/configuration/revisions",
    )
    pages: dict[str, str] = {}
    for route in routes:
        response = client.get(route)
        response.raise_for_status()
        pages[f"{prefix}{route}"] = response.text
    client.cookies.clear()
    login = client.get(f"/admin/login?lang={locale}")
    login.raise_for_status()
    pages[f"{prefix}/admin/login"] = login.text
    return pages


def _record_history(journal: RequestJournal) -> None:
    """Journal one failed and one completed synthetic request for the same ticket."""
    for outcome in ("failed", "processed"):
        journal.admit(ticket_id=_TICKET_ID, source=RequestSource.WEBHOOK)
        leased = journal.lease_next()
        assert leased is not None
        if outcome == "failed":
            journal.fail_permanent(
                leased.request_id,
                error_code="permanent_error",
                error_detail="Synthetic archive publication failed",
            )
        else:
            journal.complete(leased.request_id, revision_id="browser-revision")


def main() -> None:
    """Write a JSON page bundle without starting workers or contacting live services."""
    with TemporaryDirectory(prefix="chronikwerk-browser-") as directory:
        root = Path(directory).resolve()
        previous_timeout = os.environ.get("ZAMMAD_TIMEOUT_SECONDS")
        os.environ["ZAMMAD_TIMEOUT_SECONDS"] = "17.5"
        try:
            settings = make_settings(
                str(root / "archive"),
                overrides={
                    "admin": {
                        "enabled": True,
                        "access_token": _ADMIN_TOKEN,
                        "cookie_secure": False,
                        "default_locale": "en-GB",
                        "state_dir": str(root / "admin-state"),
                    },
                    "signing": {
                        "enabled": True,
                        "pfx_path": str(root / "synthetic-signer.pfx"),
                        "timestamp": {
                            "enabled": True,
                            "rfc3161": {"tsa_url": "https://tsa.example.test"},
                        },
                    },
                    "hardening": {
                        "transport": {
                            "allow_private_networks": False,
                            "allow_insecure_http": False,
                            "trust_env": False,
                        }
                    },
                },
            )
            journal = RequestJournal(JournalOptions(path=root / "journal.sqlite3"))
            app = create_app(settings, history=JournalHistoryView(journal))
            store = app.state.managed_config_store
            store.stage(
                {"admission": {"max_pending": 12}},
                expected_revision=store.current_revision(),
                request_id="browser-fixture-stage",
            )
            _record_history(journal)
            with TestClient(app, base_url="http://127.0.0.1:4177") as client:
                pages = {
                    **_render_locale(client, "en-GB"),
                    **_render_locale(client, "de-DE"),
                }
            print(json.dumps(pages, ensure_ascii=False))
            journal.close()
        finally:
            if previous_timeout is None:
                os.environ.pop("ZAMMAD_TIMEOUT_SECONDS", None)
            else:
                os.environ["ZAMMAD_TIMEOUT_SECONDS"] = previous_timeout


if __name__ == "__main__":
    main()
