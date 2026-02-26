from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse

from zammad_pdf_archiver.app.constants import REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs.history import read_history
from zammad_pdf_archiver.app.jobs.redis_queue import drain_dlq, get_queue_stats
from zammad_pdf_archiver.app.routes.ingest import _dispatch_ticket
from zammad_pdf_archiver.config.settings import Settings

router = APIRouter()
log = structlog.get_logger(__name__)


def _settings_or_503(request: Request) -> Settings:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")
    return settings


def _verify_admin_auth(request: Request, settings: Settings) -> None:
    if not settings.admin.enabled:
        raise HTTPException(status_code=404, detail="admin_disabled")

    token = settings.admin.bearer_token
    expected = token.get_secret_value().encode("utf-8") if token is not None else b""
    if not expected:
        raise HTTPException(status_code=503, detail="admin_token_not_configured")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 8:
        raise HTTPException(status_code=401, detail="unauthorized")

    provided = auth[7:].strip().encode("utf-8")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard() -> HTMLResponse:
    html = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>zammad-pdf-archiver admin</title>
  <style>
    :root {
      --bg:#f4f6f9; --panel:#fff; --fg:#0f172a;
      --muted:#475569; --line:#dbe1ea; --ok:#166534; --warn:#9a3412;
    }
    * { box-sizing:border-box; }
    body {
      margin:0; padding:20px;
      font-family: ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
      color:var(--fg); background:linear-gradient(180deg,#f8fafc,#eef2ff);
    }
    .wrap { max-width:1100px; margin:0 auto; display:grid; gap:14px; }
    .panel {
      background:var(--panel); border:1px solid var(--line);
      border-radius:12px; padding:14px;
    }
    h1 { margin:0 0 8px 0; font-size:24px; }
    h2 { margin:0 0 10px 0; font-size:16px; }
    .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    input, button {
      border:1px solid var(--line); border-radius:8px;
      padding:8px 10px; font-size:14px;
    }
    input { min-width:220px; }
    button { background:#111827; color:#fff; cursor:pointer; }
    button.secondary { background:#334155; }
    pre {
      margin:0; padding:10px; border:1px solid var(--line);
      background:#f8fafc; border-radius:8px; max-height:360px;
      overflow:auto; font-size:12px;
    }
    .status { color:var(--muted); font-size:13px; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"panel\">
      <h1>Admin Dashboard</h1>
      <p class=\"status\">Queue stats, history, retry and DLQ maintenance.</p>
      <div class=\"row\">
        <input id=\"token\" type=\"password\" placeholder=\"Bearer token\" />
        <button onclick=\"loadAll()\">Refresh</button>
      </div>
    </div>

    <div class=\"panel\">
      <h2>Queue Stats</h2>
      <pre id=\"queue\">-</pre>
    </div>

    <div class=\"panel\">
      <h2>History</h2>
      <div class=\"row\">
        <input id=\"historyLimit\" value=\"100\" />
        <input id=\"historyTicket\" placeholder=\"ticket_id (optional)\" />
        <button class=\"secondary\" onclick=\"loadHistory()\">Load History</button>
      </div>
      <pre id=\"history\">-</pre>
    </div>

    <div class=\"panel\">
      <h2>Actions</h2>
      <div class=\"row\">
        <input id=\"retryTicket\" placeholder=\"ticket_id\" />
        <button class=\"secondary\" onclick=\"retryTicket()\">Retry Ticket</button>
      </div>
      <div class=\"row\">
        <input id=\"drainLimit\" value=\"100\" />
        <button class=\"secondary\" onclick=\"drainDlq()\">Drain DLQ</button>
      </div>
      <pre id=\"actions\">-</pre>
    </div>
  </div>

  <script>
    function authHeaders() {
      const token = document.getElementById('token').value.trim();
      return token ? { 'Authorization': `Bearer ${token}` } : {};
    }

    async function requestJson(url, options = {}) {
      const headers = Object.assign({}, authHeaders(), options.headers || {});
      const resp = await fetch(url, Object.assign({}, options, { headers }));
      const text = await resp.text();
      let data;
      try { data = JSON.parse(text); } catch { data = { raw: text }; }
      return { status: resp.status, data };
    }

    async function loadQueue() {
      const out = await requestJson('/admin/api/queue/stats');
      document.getElementById('queue').textContent = JSON.stringify(out, null, 2);
    }

    async function loadHistory() {
      const limit = encodeURIComponent(document.getElementById('historyLimit').value || '100');
      const tid = document.getElementById('historyTicket').value.trim();
      const suffix = tid ? `&ticket_id=${encodeURIComponent(tid)}` : '';
      const out = await requestJson(`/admin/api/history?limit=${limit}${suffix}`);
      document.getElementById('history').textContent = JSON.stringify(out, null, 2);
    }

    async function retryTicket() {
      const id = document.getElementById('retryTicket').value.trim();
      if (!id) return;
      const out = await requestJson(
        `/admin/api/retry/${encodeURIComponent(id)}`,
        { method: 'POST' },
      );
      document.getElementById('actions').textContent = JSON.stringify(out, null, 2);
    }

    async function drainDlq() {
      const limit = encodeURIComponent(document.getElementById('drainLimit').value || '100');
      const out = await requestJson(`/admin/api/dlq/drain?limit=${limit}`, { method: 'POST' });
      document.getElementById('actions').textContent = JSON.stringify(out, null, 2);
      await loadQueue();
      await loadHistory();
    }

    async function loadAll() {
      await loadQueue();
      await loadHistory();
    }
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/admin/api/queue/stats")
async def admin_queue_stats(request: Request) -> dict[str, object]:
    settings = _settings_or_503(request)
    _verify_admin_auth(request, settings)
    try:
        stats = await get_queue_stats(settings)
    except Exception as exc:
        log.warning("admin.queue_stats_unavailable")
        raise HTTPException(status_code=503, detail="queue_unavailable") from exc
    return {str(k): v for k, v in stats.items()}


@router.get("/admin/api/history")
async def admin_history(
    request: Request,
    limit: int | None = None,
    ticket_id: int | None = None,
) -> dict[str, object]:
    settings = _settings_or_503(request)
    _verify_admin_auth(request, settings)

    resolved_limit = limit if limit is not None else settings.admin.history_limit
    bounded_limit = max(1, min(int(resolved_limit), 5000))
    try:
        items = await read_history(settings, limit=bounded_limit, ticket_id=ticket_id)
    except Exception as exc:
        log.warning("admin.history_unavailable")
        raise HTTPException(status_code=503, detail="history_unavailable") from exc
    return {"status": "ok", "count": len(items), "items": items}


@router.post("/admin/api/retry/{ticket_id}")
async def admin_retry_ticket(request: Request, ticket_id: int) -> dict[str, object]:
    settings = _settings_or_503(request)
    _verify_admin_auth(request, settings)

    payload: dict[str, object] = {
        "ticket_id": ticket_id,
        REQUEST_ID_KEY: getattr(request.state, "request_id", None),
    }
    try:
        await _dispatch_ticket(
            delivery_id=None,
            payload_for_job=payload,
            settings=settings,
        )
    except Exception as exc:
        log.warning("admin.retry_dispatch_unavailable", ticket_id=ticket_id)
        raise HTTPException(status_code=503, detail="queue_unavailable") from exc
    return {"status": "accepted", "ticket_id": ticket_id}


@router.post("/admin/api/dlq/drain")
async def admin_drain_dlq(request: Request, limit: int = 100) -> dict[str, object]:
    settings = _settings_or_503(request)
    _verify_admin_auth(request, settings)

    bounded_limit = max(1, min(int(limit), 1000))
    try:
        drained = await drain_dlq(settings, limit=bounded_limit)
    except Exception as exc:
        log.warning("admin.dlq_unavailable")
        raise HTTPException(status_code=503, detail="dlq_unavailable") from exc
    return {"status": "ok", "drained": drained}
