"""Verify packaged administration assets through their public routes."""

from fastapi.testclient import TestClient

from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings


def test_admin_assets_are_served_from_the_reconstructed_package(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-token",
                "state_dir": str(tmp_path / "admin-state"),
            }
        },
    )

    client = TestClient(create_app(settings))
    css = client.get("/admin/static/admin.css")
    javascript = client.get("/admin/static/admin.js")
    mark = client.get("/admin/static/chronikwerk-mark.svg")

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert b":root" in css.content
    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert b"addEventListener" in javascript.content
    assert mark.status_code == 200
    assert mark.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in mark.content
