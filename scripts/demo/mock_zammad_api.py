from __future__ import annotations

import argparse
import copy
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class TagMutation(BaseModel):
    object: str
    o_id: int
    item: str


class NewArticle(BaseModel):
    ticket_id: int
    subject: str = ""
    body: str = ""
    content_type: str = "text/html"
    internal: bool = True


class DemoStore:
    def __init__(self, dataset_path: Path) -> None:
        self._dataset_path = dataset_path
        self._lock = threading.Lock()
        self._dataset_template = self._load_dataset(dataset_path)
        self._tickets: dict[int, dict[str, Any]] = {}
        self._tags: dict[int, list[str]] = {}
        self._articles: dict[int, list[dict[str, Any]]] = {}
        self._events: list[dict[str, Any]] = []
        self._next_article_id: int = 1
        self.reset()

    @staticmethod
    def _load_dataset(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("dataset must be a JSON object")
        tickets = payload.get("tickets")
        if not isinstance(tickets, list) or not tickets:
            raise ValueError("dataset.tickets must be a non-empty list")
        return payload

    def reset(self) -> dict[str, Any]:
        with self._lock:
            template = copy.deepcopy(self._dataset_template)
            tickets = template.get("tickets", [])

            self._tickets = {}
            self._tags = {}
            self._articles = {}
            self._events = []

            max_article_id = 1
            for item in tickets:
                ticket_id = int(item["id"])
                created = item.get("created_at") or _iso_now()
                updated = item.get("updated_at") or created
                self._tickets[ticket_id] = {
                    "id": ticket_id,
                    "number": str(item.get("number") or f"UNI-{ticket_id}"),
                    "title": item.get("title"),
                    "owner": {"login": item.get("owner_login")},
                    "updated_by": {"login": item.get("updated_by_login")},
                    "customer": item.get("customer") or {},
                    "preferences": {
                        "custom_fields": item.get("custom_fields") or {},
                    },
                    "created_at": created,
                    "updated_at": updated,
                }
                self._tags[ticket_id] = [str(t) for t in item.get("tags", [])]

                articles: list[dict[str, Any]] = []
                for article in item.get("articles", []):
                    article_id = int(article.get("id") or max_article_id)
                    max_article_id = max(max_article_id, article_id + 1)
                    articles.append(
                        {
                            "id": article_id,
                            "created_at": article.get("created_at") or _iso_now(),
                            "internal": bool(article.get("internal", False)),
                            "subject": article.get("subject") or "",
                            "body": article.get("body") or "",
                            "content_type": article.get("content_type") or "text/plain",
                            "from": article.get("from") or "",
                            "to": article.get("to") or "",
                            "attachments": article.get("attachments") or [],
                        }
                    )
                self._articles[ticket_id] = articles

            self._next_article_id = max_article_id
            self._events.append({"ts": _iso_now(), "event": "reset", "tickets": len(self._tickets)})

            return {
                "status": "ok",
                "tickets": len(self._tickets),
                "seed_plan_count": len(template.get("seed_plan", [])),
            }

    def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise KeyError(ticket_id)
            return copy.deepcopy(ticket)

    def get_tags(self, ticket_id: int) -> list[str]:
        with self._lock:
            return list(self._tags.get(ticket_id, []))

    def set_tag(self, ticket_id: int, *, tag: str, present: bool) -> dict[str, Any]:
        with self._lock:
            if ticket_id not in self._tickets:
                raise KeyError(ticket_id)
            tags = self._tags.setdefault(ticket_id, [])
            if present:
                if tag not in tags:
                    tags.append(tag)
                action = "tag_add"
            else:
                tags = [x for x in tags if x != tag]
                self._tags[ticket_id] = tags
                action = "tag_remove"
            self._events.append(
                {
                    "ts": _iso_now(),
                    "event": action,
                    "ticket_id": ticket_id,
                    "tag": tag,
                    "tags": list(tags),
                }
            )
            return {"status": "ok", "ticket_id": ticket_id, "tags": list(tags)}

    def list_articles(self, ticket_id: int) -> list[dict[str, Any]]:
        with self._lock:
            if ticket_id not in self._tickets:
                raise KeyError(ticket_id)
            return copy.deepcopy(self._articles.get(ticket_id, []))

    def add_article(self, payload: NewArticle) -> dict[str, Any]:
        with self._lock:
            if payload.ticket_id not in self._tickets:
                raise KeyError(payload.ticket_id)
            article = {
                "id": self._next_article_id,
                "created_at": _iso_now(),
                "internal": bool(payload.internal),
                "subject": payload.subject,
                "body": payload.body,
                "content_type": payload.content_type,
                "from": "archiver@demo.local",
                "to": "",
                "attachments": [],
            }
            self._next_article_id += 1
            self._articles.setdefault(payload.ticket_id, []).append(article)
            self._events.append(
                {
                    "ts": _iso_now(),
                    "event": "article_created",
                    "ticket_id": payload.ticket_id,
                    "article_id": article["id"],
                    "subject": payload.subject,
                }
            )
            return copy.deepcopy(article)

    def state(self) -> dict[str, Any]:
        with self._lock:
            items = []
            for ticket_id in sorted(self._tickets.keys()):
                ticket = self._tickets[ticket_id]
                items.append(
                    {
                        "ticket_id": ticket_id,
                        "number": ticket["number"],
                        "title": ticket.get("title"),
                        "tags": list(self._tags.get(ticket_id, [])),
                        "article_count": len(self._articles.get(ticket_id, [])),
                    }
                )

            return {
                "status": "ok",
                "dataset": str(self._dataset_path),
                "ticket_count": len(self._tickets),
                "tickets": items,
                "events_tail": self._events[-50:],
            }


class AppConfig(BaseModel):
    dataset_path: Path
    api_token: str = Field(min_length=1)


class AppState:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = DemoStore(config.dataset_path)


def _auth_dependency(state: AppState):
    def _verify(authorization: str | None = Header(default=None)) -> None:
        expected = f"Token token={state.config.api_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    return _verify


def create_app(*, dataset_path: Path, api_token: str) -> FastAPI:
    config = AppConfig(dataset_path=dataset_path, api_token=api_token)
    state = AppState(config)
    app = FastAPI(title="mock-zammad-api", version="1.0")
    auth = _auth_dependency(state)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "time": _iso_now(), "tickets": state.store.state()["ticket_count"]}

    @app.post("/__demo/reset")
    async def demo_reset() -> dict[str, Any]:
        return state.store.reset()

    @app.get("/__demo/state")
    async def demo_state() -> dict[str, Any]:
        return state.store.state()

    @app.get("/api/v1/tickets/{ticket_id}")
    async def get_ticket(ticket_id: int, _: None = Depends(auth)) -> dict[str, Any]:
        try:
            return state.store.get_ticket(ticket_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc

    @app.get("/api/v1/tags")
    async def get_tags(
        object: str | None = None,  # noqa: A002
        o_id: int | None = None,
        _: None = Depends(auth),
    ) -> dict[str, Any]:
        if object != "Ticket" or o_id is None:
            raise HTTPException(status_code=400, detail="invalid_tag_query")
        return {"tags": state.store.get_tags(o_id)}

    @app.post("/api/v1/tags/add")
    async def add_tag(payload: TagMutation, _: None = Depends(auth)) -> dict[str, Any]:
        if payload.object != "Ticket":
            raise HTTPException(status_code=400, detail="unsupported_object")
        try:
            return state.store.set_tag(payload.o_id, tag=payload.item, present=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc

    @app.post("/api/v1/tags/remove")
    async def remove_tag(payload: TagMutation, _: None = Depends(auth)) -> dict[str, Any]:
        if payload.object != "Ticket":
            raise HTTPException(status_code=400, detail="unsupported_object")
        try:
            return state.store.set_tag(payload.o_id, tag=payload.item, present=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc

    @app.get("/api/v1/ticket_articles/by_ticket/{ticket_id}")
    async def list_articles(ticket_id: int, _: None = Depends(auth)) -> list[dict[str, Any]]:
        try:
            return state.store.list_articles(ticket_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc

    @app.post("/api/v1/ticket_articles")
    async def create_article(payload: NewArticle, _: None = Depends(auth)) -> dict[str, Any]:
        try:
            return state.store.add_article(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc

    return app


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local mock Zammad API service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--dataset",
        default="examples/demo/mock_university_dataset.json",
        help="Path to dataset JSON",
    )
    parser.add_argument("--token", default="demo-token", help="Expected API token")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dataset_path = Path(args.dataset).expanduser().resolve()
    if not dataset_path.is_file():
        raise SystemExit(f"Dataset not found: {dataset_path}")

    app = create_app(dataset_path=dataset_path, api_token=args.token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
