from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

DEFAULT_DATASET = Path("examples/demo/mock_university_dataset.json")
DEFAULT_REPORT = Path("docs/assets/demo/demo-seed-report.json")
DEFAULT_ARCHIVER_URL = "http://127.0.0.1:18080"
DEFAULT_MOCK_URL = "http://127.0.0.1:18090"
DEFAULT_ADMIN_TOKEN = "demo-admin-token"
DEFAULT_COMPOSE_FILE = Path("docker-compose.demo.yml")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed deterministic demo data into local demo stack"
    )
    parser.add_argument("--archiver-url", default=DEFAULT_ARCHIVER_URL)
    parser.add_argument("--mock-url", default=DEFAULT_MOCK_URL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--admin-token", default=DEFAULT_ADMIN_TOKEN)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument(
        "--simulate-backend-unavailable",
        action="store_true",
        help="Temporarily stop redis-demo and verify admin API returns 503",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dataset must be a JSON object")
    seed_plan = payload.get("seed_plan")
    if not isinstance(seed_plan, list) or not seed_plan:
        raise ValueError("dataset.seed_plan must be a non-empty list")
    return payload


def _wait_for_ready(client: httpx.Client, label: str, url: str, *, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # pragma: no cover - defensive
            last_error = str(exc)
        time.sleep(1.0)
    raise RuntimeError(f"{label} not ready at {url}: {last_error}")


def _request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
) -> tuple[int, Any]:
    response = client.request(method, url, headers=headers, json=json_body)
    text = response.text
    try:
        parsed: Any = response.json()
    except Exception:
        parsed = {"raw": text}
    return response.status_code, parsed


def _compose(compose_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(compose_file), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _dry_run(args: argparse.Namespace, dataset: dict[str, Any]) -> int:
    seed_plan = dataset["seed_plan"]
    print("DRY RUN: demo seed actions")
    print(f"- Wait for: GET {args.mock_url}/healthz")
    print(f"- Wait for: GET {args.archiver_url}/healthz")
    print(f"- POST /__demo/reset -> {args.mock_url}/__demo/reset")
    for item in seed_plan:
        print(
            "- POST /ingest "
            f"ticket_id={item.get('ticket_id')} delivery_id={item.get('delivery_id')} "
            f"expected={item.get('expected_status')}"
        )
    if args.simulate_backend_unavailable:
        print(f"- docker compose -f {args.compose_file} stop redis-demo")
        print(f"- GET /admin/api/history (expect 503) -> {args.archiver_url}/admin/api/history")
        print(f"- docker compose -f {args.compose_file} start redis-demo")
    print(f"- Write report: {args.report}")
    return 0


def _simulate_backend_unavailable(
    *,
    client: httpx.Client,
    archiver_url: str,
    admin_token: str,
    compose_file: Path,
) -> dict[str, Any]:
    stop = _compose(compose_file, "stop", "redis-demo")
    if stop.returncode != 0:
        raise RuntimeError(f"failed to stop redis-demo: {stop.stderr.strip()}")

    status_code, payload = _request_json(
        client,
        "GET",
        f"{archiver_url}/admin/api/history?limit=10",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    start = _compose(compose_file, "start", "redis-demo")
    if start.returncode != 0:
        raise RuntimeError(f"failed to start redis-demo: {start.stderr.strip()}")

    _wait_for_ready(client, "archiver", f"{archiver_url}/healthz", timeout_s=45.0)

    return {
        "status_code": status_code,
        "payload": payload,
        "expected_status_code": 503,
        "ok": status_code == 503,
    }


def main() -> int:
    args = _parse_args()
    dataset_path = args.dataset.expanduser().resolve()
    dataset = _load_dataset(dataset_path)

    if args.dry_run:
        return _dry_run(args, dataset)

    seed_plan: list[dict[str, Any]] = dataset["seed_plan"]
    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=20.0) as client:
        _wait_for_ready(client, "mock-zammad", f"{args.mock_url}/healthz")
        _wait_for_ready(client, "archiver", f"{args.archiver_url}/healthz")

        reset_code, reset_payload = _request_json(
            client,
            "POST",
            f"{args.mock_url}/__demo/reset",
        )
        if reset_code != 200:
            raise RuntimeError(f"mock reset failed ({reset_code}): {reset_payload}")

        ingests: list[dict[str, Any]] = []
        for item in seed_plan:
            ticket_id = int(item["ticket_id"])
            delivery_id = str(item.get("delivery_id") or f"demo-delivery-{ticket_id}")
            user_login = str(item.get("user_login") or "demo.agent")
            expected_status = str(item.get("expected_status") or "unknown")

            status_code, payload = _request_json(
                client,
                "POST",
                f"{args.archiver_url}/ingest",
                headers={"X-Zammad-Delivery": delivery_id},
                json_body={"ticket": {"id": ticket_id}, "user": {"login": user_login}},
            )
            ingests.append(
                {
                    "ticket_id": ticket_id,
                    "delivery_id": delivery_id,
                    "expected_status": expected_status,
                    "http_status": status_code,
                    "response": payload,
                }
            )

        # Queue worker is async; wait until we see at least one history event per seed action.
        target_count = len(seed_plan)
        history_payload: dict[str, Any] = {}
        for _ in range(30):
            history_code, data = _request_json(
                client,
                "GET",
                f"{args.archiver_url}/admin/api/history?limit=200",
                headers={"Authorization": f"Bearer {args.admin_token}"},
            )
            if history_code == 200 and isinstance(data, dict):
                history_payload = data
                if int(data.get("count", 0)) >= target_count:
                    break
            time.sleep(1.0)

        queue_code, queue_payload = _request_json(
            client,
            "GET",
            f"{args.archiver_url}/admin/api/queue/stats",
            headers={"Authorization": f"Bearer {args.admin_token}"},
        )
        mock_state_code, mock_state_payload = _request_json(
            client,
            "GET",
            f"{args.mock_url}/__demo/state",
        )

        backend_unavailable: dict[str, Any] | None = None
        if args.simulate_backend_unavailable:
            backend_unavailable = _simulate_backend_unavailable(
                client=client,
                archiver_url=args.archiver_url,
                admin_token=args.admin_token,
                compose_file=args.compose_file,
            )

    raw_items = history_payload.get("items") if isinstance(history_payload, dict) else []
    items = raw_items if isinstance(raw_items, list) else []
    status_counts = Counter(
        str(item.get("status", "unknown")) for item in items if isinstance(item, dict)
    )

    report = {
        "dataset": str(dataset_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ingest_requests": ingests,
        "history_status_counts": dict(status_counts),
        "history": history_payload,
        "queue_stats": {
            "status_code": queue_code,
            "payload": queue_payload,
        },
        "mock_state": {
            "status_code": mock_state_code,
            "payload": mock_state_payload,
        },
        "mock_reset": {
            "status_code": reset_code,
            "payload": reset_payload,
        },
    }
    if backend_unavailable is not None:
        report["backend_unavailable_test"] = backend_unavailable

    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Seed complete. Report written to {report_path}")
    print("History status counts:")
    print(json.dumps(dict(status_counts), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
