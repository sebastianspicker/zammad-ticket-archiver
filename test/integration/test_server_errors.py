from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app


def test_global_exception_handler_returns_consistent_api_error(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path)))

    @app.get("/boom")
    def _boom() -> dict[str, str]:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom", headers={"X-Request-Id": "req-boom-1"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "An internal server error occurred.",
        "code": "internal_error",
        "request_id": "req-boom-1",
    }
    assert response.headers.get("X-Request-Id") == "req-boom-1"
