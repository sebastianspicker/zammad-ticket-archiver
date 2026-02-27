from __future__ import annotations

from pathlib import Path

from zammad_pdf_archiver.adapters.pdf import render_pdf as render_pdf_module
from zammad_pdf_archiver.domain.snapshot_models import Snapshot


def _snapshot() -> Snapshot:
    return Snapshot.model_validate(
        {
            "ticket": {
                "id": 1,
                "number": "T1",
                "title": "template-root-test",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "tags": ["pdf:sign"],
                "custom_fields": {"archive_path": ["A"], "archive_user_mode": "owner"},
            },
            "articles": [],
        }
    )


def test_render_pdf_passes_templates_root_to_render_html(tmp_path: Path, monkeypatch) -> None:
    templates_root = tmp_path / "templates"
    template_dir = templates_root / "default"
    template_dir.mkdir(parents=True)
    (template_dir / "styles.css").write_text("body { font-size: 12px; }", encoding="utf-8")
    (template_dir / "ticket.html").write_text("<html></html>", encoding="utf-8")

    captured: dict[str, Path | None] = {"templates_root": None}

    def _stub_render_html(
        snapshot: Snapshot,  # noqa: ARG001
        template_name: str,  # noqa: ARG001
        *,
        locale: str = "de_DE",  # noqa: ARG001
        timezone: str = "Europe/Berlin",  # noqa: ARG001
        templates_root: Path | None = None,
    ) -> str:
        captured["templates_root"] = templates_root
        return "<html><body>ok</body></html>"

    monkeypatch.setattr(render_pdf_module, "render_html", _stub_render_html)

    pdf_bytes = render_pdf_module.render_pdf(
        _snapshot(),
        "default",
        templates_root=templates_root,
    )

    assert pdf_bytes.startswith(b"%PDF")
    assert captured["templates_root"] == templates_root
