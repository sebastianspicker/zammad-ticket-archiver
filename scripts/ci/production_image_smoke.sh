#!/usr/bin/env bash
# Verify the built production image can render accessible PDFs and persist admin state read-only.
set -euo pipefail

image="chronikwerk:verify"
state_volume="chronikwerk-admin-state-smoke-$$"

cleanup() {
  # Keep the smoke volume ephemeral even when a preceding Docker assertion fails.
  docker volume rm -f "$state_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -f Dockerfile -t "$image" .
docker run --rm "$image" python -c \
  'import weasyprint, pyhanko, asn1crypto; print("production-imports-ok")'
docker run --rm "$image" python -c \
  'import asyncio; from io import BytesIO; from pyhanko.pdf_utils.reader import PdfFileReader; from chronikwerk.documents.pdf import render_pdf; from chronikwerk.documents.models import Snapshot; s=Snapshot.model_validate({"ticket":{"id":1,"number":"VERIFY-1","title":"Production PDF"},"articles":[{"id":1,"body_html":"<p>Hello</p>"}]}); p=asyncio.run(render_pdf(s, locale="en-GB")); assert p.startswith(b"%PDF"); r=PdfFileReader(BytesIO(p)); assert str(r.root["/Lang"]) == "en-GB"; assert bool(r.root["/MarkInfo"]["/Marked"]); assert r.root.get("/Outlines") is not None; page=r.root["/Pages"]["/Kids"][0].get_object(); fonts=page["/Resources"]["/Font"]; names=[str(ref.get_object().get("/BaseFont")) for ref in fonts.values()]; assert any("DejaVu" in name for name in names), names; print("unsigned-render-ok", names)'

docker volume create "$state_volume" >/dev/null
docker run --rm --read-only --tmpfs /tmp:noexec,nosuid,size=64m \
  --mount "type=volume,src=${state_volume},dst=/var/lib/chronikwerk/admin" \
  -e ZAMMAD__BASE_URL=https://zammad.example.invalid \
  -e ZAMMAD__API_TOKEN=production-smoke-token \
  -e ZAMMAD__WEBHOOK_HMAC_SECRET=production-smoke-webhook-secret-at-least-32-characters \
  -e STORAGE__ROOT=/tmp/archive \
  -e ADMIN__ENABLED=true \
  -e ADMIN__ACCESS_TOKEN=production-smoke-admin-token-at-least-32-characters \
  "$image" python -c \
  'from fastapi.testclient import TestClient; from chronikwerk.configuration.load import load_settings; from chronikwerk.web.app import create_app; app=create_app(load_settings()); assert app.state.managed_config_store.current_revision(); response=TestClient(app).get("/admin", follow_redirects=False); assert response.status_code == 303, response.status_code; print("admin-read-only-state-ok")'
