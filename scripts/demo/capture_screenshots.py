from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

SHOT_FILENAMES = [
    "01-admin-token-screen.png",
    "02-admin-queue-stats.png",
    "03-admin-history-all.png",
    "04-admin-history-filtered-ticket.png",
    "05-admin-retry-action.png",
    "06-admin-dlq-before-drain.png",
    "07-admin-dlq-after-drain.png",
    "08-api-401-unauthorized.png",
    "09-api-503-backend-unavailable.png",
    "10-admin-mobile-viewport.png",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture local demo screenshots via Playwright"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/assets/demo"))
    parser.add_argument("--token", default="demo-admin-token")
    parser.add_argument("--filter-ticket-id", type=int, default=1101)
    parser.add_argument("--retry-ticket-id", type=int, default=1104)
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.demo.yml"))
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _compose(compose_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(compose_file), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _wait_http_ok(label: str, url: str, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=3.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # pragma: no cover - defensive
            last_error = str(exc)
        time.sleep(1.0)
    raise RuntimeError(f"{label} not ready: {url} ({last_error})")


def _dry_run(args: argparse.Namespace) -> int:
    print("DRY RUN: screenshot capture plan")
    print(f"- Base URL: {args.base_url}")
    print(f"- Output directory: {args.output_dir}")
    print("- Expected files:")
    for name in SHOT_FILENAMES:
        print(f"  - {name}")
    print(f"- docker compose -f {args.compose_file} stop redis-demo")
    print(f"- docker compose -f {args.compose_file} start redis-demo")
    return 0


def _import_playwright() -> Any:
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install dev dependencies first, "
            "e.g. pip install -e '.[dev]'."
        ) from exc
    return sync_playwright, Error, TimeoutError


def _check_browser_installation(*, headed: bool) -> None:
    sync_playwright, Error, _ = _import_playwright()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            browser.close()
    except Error as exc:
        raise RuntimeError(
            "Playwright browser is not installed. Run: python -m playwright install chromium"
        ) from exc


def _write_shot(page: Any, path: Path) -> None:
    page.screenshot(path=str(path), full_page=True)


def _wait_for_admin_payload(page: Any, selector: str, *, timeout_ms: int = 10_000) -> None:
    page.wait_for_function(
        "(sel) => { const el = document.querySelector(sel);"
        " if (!el) return false;"
        " const txt = (el.textContent || '').trim();"
        " return txt !== '-' && txt.length > 2; }",
        arg=selector,
        timeout=timeout_ms,
    )


def _capture(args: argparse.Namespace) -> int:
    sync_playwright, Error, TimeoutError = _import_playwright()

    out_dir = args.output_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    _wait_http_ok("archiver", f"{args.base_url}/healthz", timeout_s=args.timeout_seconds)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        desktop = browser.new_context(viewport={"width": 1366, "height": 900})
        page = desktop.new_page()

        redis_was_stopped = False
        try:
            page.goto(f"{args.base_url}/admin", wait_until="networkidle")
            page.wait_for_selector("#token", timeout=10_000)
            _write_shot(page, out_dir / SHOT_FILENAMES[0])

            page.fill("#token", args.token)
            page.click("button:has-text('Refresh')")
            _wait_for_admin_payload(page, "#queue")
            _write_shot(page, out_dir / SHOT_FILENAMES[1])

            page.click("button:has-text('Load History')")
            _wait_for_admin_payload(page, "#history")
            _write_shot(page, out_dir / SHOT_FILENAMES[2])

            page.fill("#historyTicket", str(args.filter_ticket_id))
            page.click("button:has-text('Load History')")
            page.wait_for_function(
                "(tid) => (document.querySelector('#history')?.textContent || '')"
                ".includes(String(tid))",
                arg=args.filter_ticket_id,
                timeout=10_000,
            )
            _write_shot(page, out_dir / SHOT_FILENAMES[3])

            page.fill("#retryTicket", str(args.retry_ticket_id))
            page.click("button:has-text('Retry Ticket')")
            _wait_for_admin_payload(page, "#actions")
            _write_shot(page, out_dir / SHOT_FILENAMES[4])

            # Capture DLQ state before drain.
            page.fill("#historyTicket", "")
            page.click("button:has-text('Refresh')")
            _wait_for_admin_payload(page, "#queue")
            _write_shot(page, out_dir / SHOT_FILENAMES[5])

            page.fill("#drainLimit", "100")
            page.click("button:has-text('Drain DLQ')")
            _wait_for_admin_payload(page, "#actions")
            _write_shot(page, out_dir / SHOT_FILENAMES[6])

            unauthorized_page = desktop.new_page()
            unauthorized_page.goto(
                f"{args.base_url}/admin/api/history?limit=10",
                wait_until="networkidle",
            )
            _write_shot(unauthorized_page, out_dir / SHOT_FILENAMES[7])
            unauthorized_page.close()

            stop = _compose(args.compose_file, "stop", "redis-demo")
            if stop.returncode != 0:
                raise RuntimeError(f"unable to stop redis-demo: {stop.stderr.strip()}")
            redis_was_stopped = True

            page.click("button:has-text('Refresh')")
            page.wait_for_function(
                "() => (document.querySelector('#queue')?.textContent || '').includes('503')",
                timeout=10_000,
            )
            _write_shot(page, out_dir / SHOT_FILENAMES[8])

            start = _compose(args.compose_file, "start", "redis-demo")
            if start.returncode != 0:
                raise RuntimeError(f"unable to start redis-demo: {start.stderr.strip()}")
            redis_was_stopped = False
            _wait_http_ok("archiver", f"{args.base_url}/healthz", timeout_s=args.timeout_seconds)

            mobile = browser.new_context(viewport={"width": 390, "height": 844})
            mobile_page = mobile.new_page()
            mobile_page.goto(f"{args.base_url}/admin", wait_until="networkidle")
            mobile_page.fill("#token", args.token)
            mobile_page.click("button:has-text('Refresh')")
            _wait_for_admin_payload(mobile_page, "#queue")
            _write_shot(mobile_page, out_dir / SHOT_FILENAMES[9])
            mobile.close()

        except TimeoutError as exc:
            raise RuntimeError(f"timeout while capturing screenshots: {exc}") from exc
        except Error as exc:
            raise RuntimeError(f"playwright error: {exc}") from exc
        finally:
            if redis_was_stopped:
                _compose(args.compose_file, "start", "redis-demo")
            desktop.close()
            browser.close()

    print(f"Captured {len(SHOT_FILENAMES)} screenshots in {out_dir}")
    return 0


def main() -> int:
    args = _parse_args()

    if args.dry_run:
        return _dry_run(args)

    if args.check_only:
        _check_browser_installation(headed=args.headed)
        print("Playwright Chromium check OK")
        return 0

    try:
        return _capture(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
