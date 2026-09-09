"""The JSON API at /api/v1 — the surface agents actually use.

JSON in, JSON out. Errors are `{"error": {"code", "message", "detail"}}`:
400 validation, 404 missing, 409 illegal transition or contested claim.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fasthtml.common import FileResponse, JSONResponse, Request, Response

from app.graph import (
    mermaid,
    milestone_state,
    mission_closure,
    mission_progress,
    neighbourhood,
    open_queue,
    subgraph,
)
from app.models import Actor, ValidationError
from app.service import Conflict, Platform
from app.store import NotFound
from app.verify import new_run_id

API = "/api/v1"


def err(code: str, message: str, status: int, **detail: Any) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "detail": detail}}, status_code=status)


def actor_from(req: Request, body: dict[str, Any] | None = None) -> Actor:
    """Self-reported, by design: there is one shared credential and `created_by` is a label."""
    body = body or {}
    name = (
        body.get("by")
        or body.get("agent")
        or req.headers.get("X-Agent")
        or req.query_params.get("by")
        or "anonymous"
    )
    if isinstance(name, dict):
        return Actor.parse(name)
    kind = body.get("by_type") or req.headers.get("X-Agent-Type") or "agent"
    return Actor(type=kind if kind in ("agent", "human") else "agent", name=str(name))


async def json_body(req: Request) -> dict[str, Any]:
    raw = await req.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError([f"body is not valid JSON: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ValidationError(["body must be a JSON object"])
    return data


def _bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.lower() in ("1", "true", "yes", "on")


def _paginate(rows: list[Any], req: Request) -> tuple[list[Any], str | None]:
    """Opaque cursor = the offset. Honest about what it is; sufficient at this scale."""
    try:
        limit = max(1, min(int(req.query_params.get("limit", 100)), 1000))
    except ValueError:
        limit = 100
    try:
        offset = max(0, int(req.query_params.get("cursor", 0)))
    except ValueError:
        offset = 0
    page = rows[offset : offset + limit]
    next_cursor = str(offset + limit) if offset + limit < len(rows) else None
    return page, next_cursor


def register_api(app, platform: Platform) -> None:  # noqa: C901 - a route table, not a function
    store, index, events = platform.store, platform.index, platform.events

    def route(path: str, method: str = "GET"):
        def deco(fn):
            app.route(API + path, methods=[method])(fn)
            return fn

        return deco

    def item_json(item, include_files: bool = True) -> dict[str, Any]:
        d = item.to_json()
        d["url"] = f"{platform.settings.base_url}/items/{item.id}"
        d["backlinks"] = [
            {"from": b.frm, "rel": b.rel, "note": b.note, "title": index.title(b.frm)}
            for b in index.refs_in(item.id)
        ]
        if include_files:
            d["files"] = store.list_files(item.id)
        return d

    # -- health, stats, onboarding ---------------------------------------

    @route("/health")
    async def health(req: Request):
        return JSONResponse(platform.health())

    @route("/stats")
    async def stats(req: Request):
        return JSONResponse(index.stats())

    @route("/events")
    async def event_feed(req: Request):
        since = req.query_params.get("since")
        limit = int(req.query_params.get("limit", 100))
        return JSONResponse({"events": events.tail(since, limit)})

    @route("/export")
    async def export(req: Request):
        """The whole graph as one blob, for offline analysis."""
        return JSONResponse(
            {
                "generated_at": platform.health(),
                "items": [item_json(i, include_files=False) for i in store.iter_items()],
            }
        )

    # -- items ------------------------------------------------------------

    @route("/items")
    async def list_items(req: Request):
        q = req.query_params
        rows = index.search(
            kind=q.get("kind"),
            status=q.get("status"),
            tag=q.get("tag"),
            q=q.get("q"),
            mission=q.get("mission"),
            claimed_by=q.get("claimed_by"),
            tier=q.get("tier"),
            has_open_deps=_bool(q.get("has_open_deps")),
            include_retired=_bool(q.get("include_retired")) is not False,
        )
        page, cursor = _paginate(rows, req)
        return JSONResponse({"items": page, "total": len(rows), "next_cursor": cursor})

    @route("/items", "POST")
    async def create_item(req: Request):
        body = await json_body(req)
        item = platform.create(body, actor_from(req, body))
        return JSONResponse(item_json(item), status_code=201)

    @route("/items/{id}")
    async def get_item(req: Request, id: str):
        return JSONResponse(item_json(store.get(id)))

    @route("/items/{id}", "PATCH")
    async def patch_item(req: Request, id: str):
        body = await json_body(req)
        async with store.lock(id):
            item, stale = platform.update(id, body, actor_from(req, body))
        return JSONResponse({**item_json(item), "invalidated": stale})

    @route("/items/{id}/revisions")
    async def revisions(req: Request, id: str):
        return JSONResponse({"revisions": store.list_revisions(id)})

    @route("/items/{id}/revisions/{rev}")
    async def revision(req: Request, id: str, rev: int):
        return JSONResponse(store.get_revision(id, int(rev)).to_json())

    @route("/items/{id}/status", "POST")
    async def set_status(req: Request, id: str):
        body = await json_body(req)
        async with store.lock(id):
            item = platform.set_status(
                id,
                body.get("status", ""),
                actor_from(req, body),
                body.get("reason", ""),
                body.get("superseded_by"),
            )
        return JSONResponse(item_json(item))

    @route("/items/{id}/confirm", "POST")
    async def confirm(req: Request, id: str):
        body = await json_body(req)
        async with store.lock(id):
            item = platform.confirm(
                id, bool(body.get("confirmed", True)), actor_from(req, body), body.get("note", "")
            )
        return JSONResponse(item_json(item))

    @route("/items/{id}/claim", "POST")
    async def claim(req: Request, id: str):
        body = await json_body(req)
        agent = body.get("agent") or actor_from(req, body).name
        async with store.lock(id):
            item = platform.claim(id, agent, int(body.get("ttl_s", 3600)), body.get("note", ""))
        return JSONResponse(item_json(item))

    @route("/items/{id}/release", "POST")
    async def release(req: Request, id: str):
        body = await json_body(req)
        agent = body.get("agent") or actor_from(req, body).name
        async with store.lock(id):
            item = platform.release(id, agent)
        return JSONResponse(item_json(item))

    # -- refs -------------------------------------------------------------

    @route("/items/{id}/refs")
    async def get_refs(req: Request, id: str):
        item = store.get(id)
        return JSONResponse(
            {
                "out": [
                    {"rel": r.rel, "to": r.to, "note": r.note, "title": index.title(r.to)} for r in item.refs
                ],
                "in": [
                    {"from": b.frm, "rel": b.rel, "note": b.note, "title": index.title(b.frm)}
                    for b in index.refs_in(id)
                ],
            }
        )

    @route("/items/{id}/refs", "POST")
    async def add_ref(req: Request, id: str):
        body = await json_body(req)
        async with store.lock(id):
            item = platform.add_ref(
                id, body.get("rel", ""), body.get("to", ""), body.get("note", ""), actor_from(req, body)
            )
        return JSONResponse(item_json(item), status_code=201)

    @route("/items/{id}/refs/remove", "POST")
    async def remove_ref(req: Request, id: str):
        body = await json_body(req)
        async with store.lock(id):
            item = platform.remove_ref(
                id, body.get("rel", ""), body.get("to", ""), body.get("reason", ""), actor_from(req, body)
            )
        return JSONResponse(item_json(item))

    # -- files ------------------------------------------------------------

    @route("/items/{id}/files", "POST")
    async def upload(req: Request, id: str):
        form = await req.form()
        upload_file = form.get("file")
        if upload_file is None or not hasattr(upload_file, "read"):
            raise ValidationError(["multipart field 'file' is required"])
        data = await upload_file.read()
        name = form.get("name") or getattr(upload_file, "filename", "upload.bin")
        async with store.lock(id):
            meta = platform.attach_bytes(
                id,
                str(name),
                data,
                getattr(upload_file, "content_type", None) or "application/octet-stream",
                actor_from(req, dict(form)),
                str(form.get("source_url") or ""),
            )
        return JSONResponse(meta, status_code=201)

    @route("/items/{id}/files")
    async def list_files(req: Request, id: str):
        return JSONResponse({"files": store.list_files(id)})

    @route("/items/{id}/files/{name}")
    async def get_file(req: Request, id: str, name: str):
        return FileResponse(store.file_path(id, name))

    # -- verification -----------------------------------------------------

    @route("/items/{id}/verify", "POST")
    async def verify(req: Request, id: str):
        body = await json_body(req)
        actor = actor_from(req, body)
        wait = req.query_params.get("wait") or body.get("wait")
        run_id = new_run_id()
        pending = {"run_id": run_id, "status": "pending", "item": id, "poll": f"{API}/runs/{run_id}"}
        task = asyncio.create_task(_verify_locked(id, actor, run_id))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        if wait:
            try:
                run = await asyncio.wait_for(asyncio.shield(task), timeout=float(wait))
                return JSONResponse(run)
            except TimeoutError:
                return JSONResponse(pending, status_code=202)
        return JSONResponse(pending, status_code=202)

    async def _verify_locked(item_id: str, actor: Actor, run_id: str | None = None):
        async with store.lock(item_id):
            item = store.get(item_id)
            return await platform.verifier.verify(item, actor, run_id)

    @route("/items/{id}/runs")
    async def item_runs(req: Request, id: str):
        return JSONResponse({"runs": store.list_runs(id)})

    @route("/runs/{run_id}")
    async def get_run(req: Request, run_id: str):
        return JSONResponse(store.find_run(run_id))

    @route("/verify-all", "POST")
    async def verify_all(req: Request):
        """Re-check a whole mission (or everything) after a fact moves."""
        body = await json_body(req)
        mission = req.query_params.get("mission") or body.get("mission")
        actor = actor_from(req, body)
        ids = mission_closure(index, mission) if mission else set(index.entries)
        results = []
        for item_id in sorted(ids):
            summary = index.summary(item_id)
            if not summary or summary["kind"] not in ("calculation", "calculator", "cad_evaluation"):
                continue
            run = await _verify_locked(item_id, actor)
            results.append({"item": item_id, "run_id": run["run_id"], "verdict": run["verdict"]})
        return JSONResponse({"verified": results})

    # -- missions and discovery -------------------------------------------

    @route("/missions")
    async def missions(req: Request):
        out = []
        for m in index.missions():
            out.append({**m, "progress": mission_progress(index, m["id"])})
        return JSONResponse({"missions": out})

    @route("/missions/{id}/graph")
    async def mission_graph(req: Request, id: str):
        ids = mission_closure(index, id)
        data = subgraph(index, ids)
        data["mermaid"] = mermaid(index, ids)
        return JSONResponse(data)

    @route("/missions/{id}/open")
    async def mission_open(req: Request, id: str):
        limit = int(req.query_params.get("limit", 200))
        return JSONResponse({"open": open_queue(index, id, limit)})

    @route("/missions/{id}/milestones")
    async def milestones(req: Request, id: str):
        summary = index.summary(id)
        if not summary:
            raise NotFound(f"no item {id}")
        return JSONResponse({"milestones": milestone_state(index, summary)})

    @route("/open")
    async def global_open(req: Request):
        limit = int(req.query_params.get("limit", 200))
        return JSONResponse({"open": open_queue(index, None, limit)})

    @route("/items/{id}/graph")
    async def item_graph(req: Request, id: str):
        depth = int(req.query_params.get("depth", 2))
        ids = neighbourhood(index, id, depth)
        data = subgraph(index, ids)
        data["mermaid"] = mermaid(index, ids)
        return JSONResponse(data)


def register_error_handlers(app) -> None:
    async def on_validation(req: Request, exc: ValidationError):
        return err("invalid", "; ".join(exc.errors), 400, errors=exc.errors)

    async def on_not_found(req: Request, exc: NotFound):
        return err("not_found", str(exc), 404)

    async def on_conflict(req: Request, exc: Conflict):
        return err("conflict", str(exc), 409)

    async def on_value(req: Request, exc: ValueError):
        return err("invalid", str(exc), 400)

    app.exception_handlers[ValidationError] = on_validation
    app.exception_handlers[NotFound] = on_not_found
    app.exception_handlers[Conflict] = on_conflict
    app.exception_handlers[ValueError] = on_value


__all__ = ["register_api", "register_error_handlers", "Response"]
