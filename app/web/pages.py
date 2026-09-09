"""Server-rendered pages. Plain forms and redirects; HTMX only for claim/release and run polling."""

from __future__ import annotations

import json
from typing import Any

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Li,
    Main,
    Option,
    P,
    Pre,
    RedirectResponse,
    Request,
    Response,
    Script,
    Section,
    Select,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Title,
    Tr,
    Ul,
)

from app.graph import (
    blockers,
    mermaid,
    milestone_state,
    mission_closure,
    mission_progress,
    neighbourhood,
    open_queue,
)
from app.models import KINDS, REF_RELS, STATUSES, Actor
from app.onshape import model_url
from app.service import Platform
from app.store import NotFound
from app.web.components import (
    banner,
    claim_badge,
    confirmed_badge,
    diff_table,
    event_line,
    item_link,
    item_table,
    json_block,
    kind_badge,
    kv_table,
    markdown,
    page_nav,
    progress_bar,
    refs_in_list,
    refs_out_list,
    status_pill,
    tier_badge,
    verdict_badge,
)
from app.web.forms import KIND_HELP, item_form, parse_form

_platform: Platform | None = None


def script_text(item) -> str:
    """The mirrored script for a calculator / cad_evaluation, or '' if it has none yet."""
    entry = item.payload.get("entrypoint")
    if not entry or _platform is None:
        return ""
    try:
        return _platform.store.read_file(item.id, entry).decode("utf-8", "replace")
    except (NotFound, OSError):
        return ""


def shell(title: str, *content, active: str = ""):
    """Nav, then an h1, then the page. One <main>, so Pico's container rules apply once."""
    return Title(f"{title} — engineer2.me"), Main(page_nav(active), H1(title), *content, cls="container")


def register_pages(app, platform: Platform) -> None:  # noqa: C901 - a route table, not a function
    global _platform
    _platform = platform
    store, index, events, settings = platform.store, platform.index, platform.events, platform.settings

    def actor(form) -> Actor:
        name = (form.get("by") or "anonymous").strip() if hasattr(form, "get") else "anonymous"
        return Actor(type="human", name=name or "anonymous")

    # -- dashboard --------------------------------------------------------

    @app.get("/")
    def dashboard():
        stats = index.stats()
        missions = index.missions()
        queue = open_queue(index, None, limit=12)
        cards = [
            Div(H3(str(stats["items"])), P("items"), cls="card"),
            Div(
                H3(str(stats["by_status"].get("open", 0) + stats["by_status"].get("claimed", 0))),
                P("open"),
                cls="card",
            ),
            Div(H3(str(stats["by_status"].get("verified", 0))), P("verified"), cls="card"),
            Div(H3(str(stats["by_status"].get("failed", 0))), P("failed"), cls="card"),
            Div(H3(str(stats["human_confirmed"])), P("human-confirmed"), cls="card"),
        ]
        mission_rows = [
            Div(
                H3(A(m["title"], href=f"/missions/{m['id']}")),
                P(m.get("goal") or "", cls="muted"),
                progress_bar(mission_progress(index, m["id"])),
                cls="mission-card",
            )
            for m in missions
        ]
        return shell(
            "engineer2.me",
            _onshape_banner(),
            Div(*cards, cls="cards"),
            Section(
                H2("Missions"),
                Div(*mission_rows, cls="missions")
                if mission_rows
                else P("No missions yet. ", A("Create one", href="/new/idea"), cls="muted"),
            ),
            Section(H2("What needs work"), _queue_table(queue), A("full queue →", href="/items?status=open")),
            Section(
                H2("Counts by kind"),
                Table(
                    Thead(Tr(Th("kind"), *[Th(s) for s in STATUSES])),
                    Tbody(
                        *[
                            Tr(
                                Td(kind_badge(kind)),
                                *[Td(str(stats["by_kind"].get(kind, {}).get(s, "") or "")) for s in STATUSES],
                            )
                            for kind in KINDS
                        ]
                    ),
                ),
            ),
            Section(
                H2("Recent activity"), Ul(*[event_line(index, e) for e in events.recent(12)], cls="events")
            ),
            active="dashboard",
        )

    def _onshape_banner():
        if settings.onshape_configured:
            return ""
        return banner(
            "Onshape credentials are not configured — CAD items store and render, but cannot be verified. "
            "Set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY to enable evaluation.",
            "info",
        )

    def _queue_table(queue: list[dict[str, Any]]):
        if not queue:
            return P("Nothing open. Everything is verified or retired.", cls="muted")
        return Table(
            Thead(Tr(Th("item"), Th("kind"), Th("status"), Th("why"), Th("blocked by"))),
            Tbody(
                *[
                    Tr(
                        Td(A(q["title"] or q["id"][:8], href=f"/items/{q['id']}")),
                        Td(kind_badge(q["kind"])),
                        Td(status_pill(q["status"])),
                        Td(Small(q["reason"])),
                        Td(
                            Small(
                                ", ".join(b["title"] for b in q["blocked_by"]) if q["blocked_by"] else "—",
                                cls="muted",
                            )
                        ),
                    )
                    for q in queue
                ]
            ),
            cls="queue",
        )

    # -- items list -------------------------------------------------------

    @app.get("/items")
    def items_list(req: Request):
        q = req.query_params
        include_retired = q.get("retired") in ("1", "true", "on")
        rows = index.search(
            kind=q.get("kind") or None,
            status=q.get("status") or None,
            tag=q.get("tag") or None,
            q=q.get("q") or None,
            tier=q.get("tier") or None,
            mission=q.get("mission") or None,
            include_retired=include_retired,
        )
        filters = Form(
            Input(
                type="search",
                name="q",
                value=q.get("q", ""),
                placeholder="search title, question, notes, tags",
            ),
            Select(
                Option("any kind", value=""),
                *[Option(k, value=k, selected=q.get("kind") == k) for k in KINDS],
                name="kind",
            ),
            Select(
                Option("any status", value=""),
                *[Option(s, value=s, selected=q.get("status") == s) for s in STATUSES],
                name="status",
            ),
            Label(Input(type="checkbox", name="retired", checked=include_retired), " show retired"),
            Button("Filter", type="submit"),
            method="get",
            action="/items",
            cls="filters",
        )
        new_links = Div(*[A(f"+ {k}", href=f"/new/{k}", cls="newlink") for k in KINDS], cls="newlinks")
        return shell(
            "Items",
            filters,
            new_links,
            P(f"{len(rows)} items", cls="muted"),
            item_table(index, rows),
            active="items",
        )

    # -- item detail ------------------------------------------------------

    @app.get("/items/{id}")
    def item_detail(id: str):
        item = store.get(id)
        runs = store.list_runs(id)
        header = Div(
            kind_badge(item.kind),
            status_pill(item.status),
            tier_badge(item.tier),
            confirmed_badge(item.human_confirmed),
            claim_badge(
                {"by": item.claim.by, "expires_at": item.claim.expires_at} if item.live_claim() else None
            ),
            Small(
                f" rev {item.rev} · created by {item.created_by.name} · updated {item.updated_at} by {item.updated_by.name}",
                cls="muted",
            ),
            cls="item-header",
        )
        blocked = blockers(index, id)
        warnings = []
        if item.status == "verified" and blocked:
            warnings.append(
                banner(
                    "This is verified, but something it depends on is still unfinished: "
                    + ", ".join(index.title(b) for b in blocked),
                    "warn",
                )
            )
        if item.deprecation:
            warnings.append(
                banner(
                    f"{item.status}: {item.deprecation.reason} (by {item.deprecation.by} at {item.deprecation.at})",
                    "retired",
                )
            )
        return shell(
            item.title or "(untitled)",
            *warnings,
            header,
            P(item.question, cls="question") if item.question else "",
            Section(H2("What is claimed"), _payload_view(item)),
            Div(markdown(item.body), cls="body") if item.body else "",
            Section(H2("References out"), refs_out_list(index, item), _add_ref_form(item)),
            Section(H2("Referenced by"), refs_in_list(index, item.id)),
            Section(H2("Attachments"), _attachments(item)),
            Section(H2("What checked it"), _runs_view(item, runs)),
            Section(H2("Actions"), _actions(item)),
            Section(
                H2("History"),
                Ul(
                    *[
                        Li(
                            A(f"rev {r['rev']}", href=f"/items/{item.id}/revisions/{r['rev']}"),
                            Small(
                                f" {r['updated_at']} · {r['status']} · {(r.get('updated_by') or {}).get('name', '')}",
                                cls="muted",
                            ),
                        )
                        for r in reversed(store.list_revisions(item.id))
                    ],
                    cls="revisions",
                ),
                P(
                    A("neighbourhood graph →", href=f"/items/{item.id}/graph"),
                    " ",
                    A("raw JSON →", href=f"/api/v1/items/{item.id}"),
                ),
            ),
        )

    def _payload_view(item):
        p = item.payload
        if item.kind == "fact":
            if not p.get("data"):
                return P(
                    "No data yet — this fact is still a question. Fill in data and sources.", cls="muted"
                )
            return Div(
                kv_table(p["data"], p.get("units")),
                P(Small(f"conditions: {json.dumps(p.get('conditions') or {})}", cls="muted")),
                P(Small(f"confidence: {p.get('confidence', 'unstated')}", cls="muted")),
                H3("Sources"),
                Ul(*[Li(_source_line(item, s)) for s in (p.get("sources") or [])])
                if p.get("sources")
                else P("No sources.", cls="muted"),
            )
        if item.kind == "calculator":
            return Div(
                Pre(script_text(item) or "(no script uploaded yet)", cls="code"),
                Small(f"{p.get('entrypoint', '')} · sha256 {str(p.get('sha256', ''))[:16]}…", cls="muted"),
                H3("Signature"),
                json_block({"inputs": p.get("inputs"), "outputs": p.get("outputs")}),
                H3("Examples (self-test)"),
                json_block(p.get("examples") or []),
            )
        if item.kind == "calculation":
            calc = index.summary(p.get("calculator", ""))
            return Div(
                P(
                    "calculator: ",
                    item_link(index, p["calculator"]) if calc else Span(p.get("calculator", ""), cls="muted"),
                ),
                H3("Inputs"),
                _inputs_table(p.get("inputs") or {}),
                H3("Expected"),
                kv_table(p.get("expect") or {}),
                Small(f"tolerance: {json.dumps(p.get('tolerance') or {'rel': 1e-6})}", cls="muted"),
            )
        if item.kind == "cad_model":
            url = model_url(p, settings.onshape_base)
            return Div(
                P(A(url, href=url, target="_blank", rel="noopener")),
                Table(
                    Tbody(
                        *[
                            Tr(Th(k), Td(Pre(str(p.get(k, "")), cls="inline")))
                            for k in ("did", "wid", "eid", "element_type", "last_seen_microversion")
                        ]
                    )
                ),
                P(Small(f"pin: {json.dumps(p.get('pin') or {'kind': 'workspace'})}", cls="muted")),
                P(p.get("description", ""), cls="muted"),
            )
        if item.kind == "cad_evaluation":
            return Div(
                P(
                    "model: ",
                    item_link(index, p["model"])
                    if index.summary(p.get("model", ""))
                    else Span(p.get("model", "")),
                ),
                Pre(script_text(item) or "(no FeatureScript uploaded yet)", cls="code"),
                Small(f"{p.get('entrypoint', '')} · sha256 {str(p.get('sha256', ''))[:16]}…", cls="muted"),
                H3("Expected"),
                kv_table(p.get("expect") or {}),
                P(Small(f"onshape source: {json.dumps(p.get('onshape_source') or {})}", cls="muted")),
            )
        # idea
        parts = []
        if p.get("goal"):
            parts.append(P(Span("Goal: ", cls="rel"), p["goal"], cls="goal"))
        if p.get("is_mission"):
            parts.append(P(A("open the mission board →", href=f"/missions/{item.id}")))
        parts.append(Div(markdown(p.get("markdown", "")), cls="body"))
        return Div(*parts)

    def _source_line(item, source: dict[str, Any]):
        bits = []
        if source.get("attachment"):
            bits.append(A(source["attachment"], href=f"/api/v1/items/{item.id}/files/{source['attachment']}"))
        if source.get("url"):
            bits.append(A(source["url"], href=source["url"], target="_blank", rel="noopener"))
        if source.get("locator"):
            bits.append(Small(f" {source['locator']}", cls="muted"))
        if source.get("retrieved_at"):
            bits.append(Small(f" retrieved {source['retrieved_at']}", cls="muted"))
        return Span(*bits)

    def _inputs_table(inputs: dict[str, Any]):
        rows = []
        for key, value in inputs.items():
            if isinstance(value, dict) and "$ref" in value:
                target = value["$ref"].split("#")[0].replace("item:", "")
                rows.append(Tr(Td(key), Td(Pre(value["$ref"], cls="inline")), Td(item_link(index, target))))
            else:
                rows.append(
                    Tr(Td(key), Td(Pre(json.dumps(value), cls="inline")), Td(Small("literal", cls="muted")))
                )
        return Table(Thead(Tr(Th("input"), Th("bound to"), Th("source"))), Tbody(*rows), cls="kv")

    def _attachments(item):
        if not item.attachments:
            return Div(P("No attachments.", cls="muted"), _upload_form(item))
        return Div(
            Ul(
                *[
                    Li(
                        A(a.name, href=f"/api/v1/items/{item.id}/files/{a.name}"),
                        Small(f" {a.bytes} bytes · sha256 {a.sha256[:16]}… · {a.added_at}", cls="muted"),
                        Small(f" from {a.source_url}", cls="muted") if a.source_url else "",
                    )
                    for a in item.attachments
                ]
            ),
            _upload_form(item),
        )

    def _upload_form(item):
        return Form(
            Input(type="file", name="file", required=True),
            Input(type="text", name="source_url", placeholder="source url (optional)"),
            Input(type="text", name="by", placeholder="your name", value="anonymous"),
            Button("Attach", type="submit"),
            method="post",
            action=f"/items/{item.id}/upload",
            enctype="multipart/form-data",
            cls="inline-form",
        )

    def _runs_view(item, runs: list[dict[str, Any]]):
        if item.kind in ("fact", "idea"):
            return P(
                f"Nothing to reproduce — this is {item.tier}. A human confirmation is the only check that means anything here.",
                cls="muted",
            )
        rows = []
        for run in runs:
            rows.append(
                Tr(
                    Td(A(run["run_id"][:12], href=f"/runs/{run['run_id']}")),
                    Td(verdict_badge(run.get("verdict", "?"))),
                    Td(Small(run.get("started_at", ""))),
                    Td(Small(f"{run.get('duration_ms', 0)} ms")),
                    Td(Small(str((run.get("error") or {}).get("message", ""))[:120], cls="muted")),
                )
            )
        table = (
            Table(Thead(Tr(Th("run"), Th("verdict"), Th("started"), Th("ms"), Th("note"))), Tbody(*rows))
            if rows
            else P("Never run.", cls="muted")
        )
        verify_form = Form(
            Input(type="hidden", name="by", value="anonymous"),
            Button("Verify now", type="submit"),
            method="post",
            action=f"/items/{item.id}/verify",
            cls="inline-form",
        )
        latest = runs[0] if runs else None
        detail = ""
        if latest and latest.get("diffs"):
            detail = Div(H3("Latest diff"), diff_table(latest["diffs"]))
        elif latest and latest.get("error"):
            detail = Div(H3("Latest error"), json_block(latest["error"]))
        return Div(verify_form, table, detail)

    def _actions(item):
        status_form = Form(
            Select(*[Option(s, value=s) for s in STATUSES], name="status"),
            Input(type="text", name="reason", placeholder="reason (required to deprecate)"),
            Input(type="text", name="by", placeholder="your name", value="anonymous"),
            Button("Set status", type="submit"),
            method="post",
            action=f"/items/{item.id}/status",
            cls="inline-form",
        )
        confirm_form = Form(
            Input(type="text", name="note", placeholder="what you checked"),
            Input(type="text", name="by", placeholder="your name", value="anonymous"),
            Button("Unconfirm" if item.human_confirmed else "Human-confirm", type="submit"),
            Input(type="hidden", name="confirmed", value="0" if item.human_confirmed else "1"),
            method="post",
            action=f"/items/{item.id}/confirm",
            cls="inline-form",
        )
        claim_form = Form(
            Input(type="text", name="agent", placeholder="agent name", value="anonymous"),
            Button("Release" if item.live_claim() else "Claim", type="submit"),
            method="post",
            action=f"/items/{item.id}/{'release' if item.live_claim() else 'claim'}",
            cls="inline-form",
        )
        return Div(
            P(A("Edit this item", href=f"/items/{item.id}/edit")),
            status_form,
            confirm_form,
            claim_form,
            cls="actions",
        )

    def _add_ref_form(item):
        return Details(
            Summary("add a reference"),
            Form(
                Select(*[Option(r, value=r) for r in sorted(REF_RELS)], name="rel"),
                Input(type="text", name="to", placeholder="target item uuid", required=True),
                Input(type="text", name="note", placeholder="note (required for 'related')"),
                Input(type="text", name="by", placeholder="your name", value="anonymous"),
                Button("Add", type="submit"),
                method="post",
                action=f"/items/{item.id}/refs",
                cls="inline-form",
            ),
        )

    # -- revisions and runs ----------------------------------------------

    @app.get("/items/{id}/revisions/{rev}")
    def revision_view(id: str, rev: int):
        item = store.get_revision(id, int(rev))
        return shell(
            f"{item.title} — rev {rev}",
            P(A("← back to current", href=f"/items/{id}")),
            json_block(item.to_json()),
        )

    @app.get("/runs/{run_id}")
    def run_view(run_id: str):
        run = store.find_run(run_id)
        return shell(
            f"Run {run_id[:12]}",
            P(item_link(index, run["item_id"]), " · ", verdict_badge(run.get("verdict", "?"))),
            Section(H2("Diffs"), diff_table(run.get("diffs") or [])) if run.get("diffs") is not None else "",
            Section(H2("Provenance"), json_block(run.get("input_provenance") or {})),
            Section(H2("Record"), json_block(run)),
        )

    # -- graphs -----------------------------------------------------------

    def _graph_section(ids: set[str]):
        source = mermaid(index, ids)
        return Div(
            Div(Pre(source, cls="mermaid"), cls="graph"),
            Details(Summary("mermaid source"), Pre(source, cls="code")),
            Script(
                """import mermaid from 'https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.esm.min.mjs';
                   mermaid.initialize({startOnLoad: true, securityLevel: 'loose'});""",
                type="module",
            ),
        )

    @app.get("/items/{id}/graph")
    def item_graph(id: str, depth: int = 2):
        item = store.get(id)
        return shell(
            f"{item.title} — neighbourhood",
            P(A("← back", href=f"/items/{id}"), f" · depth {depth}"),
            _graph_section(neighbourhood(index, id, depth)),
        )

    # -- missions ---------------------------------------------------------

    @app.get("/missions")
    def missions_list():
        rows = index.missions()
        return shell(
            "Missions",
            P(A("+ new mission", href="/new/idea")),
            Div(
                *[
                    Div(
                        H3(A(m["title"], href=f"/missions/{m['id']}")),
                        P(m.get("goal") or "", cls="muted"),
                        progress_bar(mission_progress(index, m["id"])),
                        cls="mission-card",
                    )
                    for m in rows
                ],
                cls="missions",
            )
            if rows
            else P("No missions yet.", cls="muted"),
            active="missions",
        )

    @app.get("/missions/{id}")
    def mission_view(id: str):
        item = store.get(id)
        summary = index.summary(id) or {}
        progress = mission_progress(index, id)
        queue = open_queue(index, id)
        ids = mission_closure(index, id)
        milestones = milestone_state(index, summary)
        milestone_rows = [
            Tr(
                Td(m["label"]),
                Td(
                    A(m["title"], href=f"/items/{m['item']}")
                    if m["item"]
                    else Small("unassigned", cls="muted")
                ),
                Td(status_pill(m["status"]) if m["item"] else Small("—", cls="muted")),
                Td(Small(m["note"], cls="muted")),
            )
            for m in milestones
        ]
        return shell(
            item.title,
            P(Span("Goal: ", cls="rel"), item.payload.get("goal", ""), cls="goal"),
            progress_bar(progress),
            Div(markdown(item.payload.get("markdown", "")), cls="body"),
            Section(
                H2("Milestones"),
                Table(
                    Thead(Tr(Th("milestone"), Th("item"), Th("status"), Th("note"))), Tbody(*milestone_rows)
                )
                if milestone_rows
                else P("No milestones defined. Edit the idea to add an attack path.", cls="muted"),
            ),
            Section(
                H2("Open queue"),
                P(Small("Ordered so the first row is never blocked.", cls="muted")),
                _queue_table(queue),
            ),
            Section(H2(f"Graph ({len(ids)} items)"), _graph_section(ids)),
            Section(
                H2("Members"),
                item_table(index, [index.summary(i) for i in ids if i != id and index.summary(i)]),
            ),
            Section(
                H2("Activity"),
                Ul(
                    *[event_line(index, e) for e in events.recent(200) if e.get("item") in ids][:20],
                    cls="events",
                ),
            ),
            P(
                A("edit mission", href=f"/items/{id}/edit"),
                " · ",
                A("as JSON", href=f"/api/v1/missions/{id}/open"),
            ),
            active="missions",
        )

    # -- events -----------------------------------------------------------

    @app.get("/events")
    def events_page():
        return shell(
            "Events", Ul(*[event_line(index, e) for e in events.recent(200)], cls="events"), active="events"
        )

    # -- create / edit ----------------------------------------------------

    @app.get("/new/{kind}")
    def new_item(kind: str):
        if kind not in KINDS:
            raise NotFound(f"no such kind {kind}")
        return shell(
            f"New {kind}",
            P(KIND_HELP[kind], cls="muted"),
            item_form(kind, action=f"/new/{kind}"),
        )

    @app.post("/new/{kind}")
    async def create_item(req: Request, kind: str):
        form = await req.form()
        data, errors = parse_form(kind, dict(form))
        if not errors:
            try:
                item = platform.create(data, actor(form))
                return RedirectResponse(f"/items/{item.id}", status_code=303)
            except Exception as exc:  # noqa: BLE001 - shown to the user, next to the form
                errors = getattr(exc, "errors", None) or [str(exc)]
        return shell(
            f"New {kind}", item_form(kind, errors=errors, action=f"/new/{kind}", by=form.get("by", ""))
        )

    @app.get("/items/{id}/edit")
    def edit_item(id: str):
        item = store.get(id)
        return shell(
            f"Edit {item.title}",
            P(A("← back", href=f"/items/{id}")),
            item_form(item.kind, item=item, action=f"/items/{id}/edit"),
        )

    @app.post("/items/{id}/edit")
    async def save_item(req: Request, id: str):
        item = store.get(id)
        form = await req.form()
        data, errors = parse_form(item.kind, dict(form))
        if not errors:
            try:
                async with store.lock(id):
                    platform.update(id, data, actor(form))
                return RedirectResponse(f"/items/{id}", status_code=303)
            except Exception as exc:  # noqa: BLE001
                errors = getattr(exc, "errors", None) or [str(exc)]
        return shell(
            f"Edit {item.title}", item_form(item.kind, item=item, errors=errors, action=f"/items/{id}/edit")
        )

    # -- form-post actions ------------------------------------------------

    @app.post("/items/{id}/status")
    async def post_status(req: Request, id: str):
        form = await req.form()
        async with store.lock(id):
            platform.set_status(id, form.get("status", ""), actor(form), form.get("reason", ""))
        return RedirectResponse(f"/items/{id}", status_code=303)

    @app.post("/items/{id}/confirm")
    async def post_confirm(req: Request, id: str):
        form = await req.form()
        async with store.lock(id):
            platform.confirm(id, form.get("confirmed", "1") == "1", actor(form), form.get("note", ""))
        return RedirectResponse(f"/items/{id}", status_code=303)

    @app.post("/items/{id}/claim")
    async def post_claim(req: Request, id: str):
        form = await req.form()
        async with store.lock(id):
            platform.claim(id, form.get("agent", "anonymous"), 3600, form.get("note", ""))
        return RedirectResponse(f"/items/{id}", status_code=303)

    @app.post("/items/{id}/release")
    async def post_release(req: Request, id: str):
        form = await req.form()
        async with store.lock(id):
            platform.release(id, form.get("agent", "anonymous"))
        return RedirectResponse(f"/items/{id}", status_code=303)

    @app.post("/items/{id}/refs")
    async def post_ref(req: Request, id: str):
        form = await req.form()
        async with store.lock(id):
            platform.add_ref(id, form.get("rel", ""), form.get("to", ""), form.get("note", ""), actor(form))
        return RedirectResponse(f"/items/{id}", status_code=303)

    @app.post("/items/{id}/upload")
    async def post_upload(req: Request, id: str):
        form = await req.form()
        upload = form.get("file")
        data = await upload.read()
        async with store.lock(id):
            platform.attach_bytes(
                id,
                upload.filename,
                data,
                upload.content_type or "application/octet-stream",
                actor(form),
                form.get("source_url", ""),
            )
        return RedirectResponse(f"/items/{id}", status_code=303)

    @app.post("/items/{id}/verify")
    async def post_verify(req: Request, id: str):
        form = await req.form()
        async with store.lock(id):
            item = store.get(id)
            await platform.verifier.verify(item, actor(form))
        return RedirectResponse(f"/items/{id}", status_code=303)

    # -- onboarding docs --------------------------------------------------

    @app.get("/start.md")
    def start_md():
        return _markdown_doc("start.md")

    @app.get("/skill.md")
    def skill_md():
        return _markdown_doc("skill.md")

    def _markdown_doc(name: str):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent.parent
        # skill.md is served straight from the agent skill, so there is one copy of it
        candidates = {
            "start.md": root / "app" / "content" / "start.md",
            "skill.md": root / "skills" / "engineer2" / "SKILL.md",
        }
        path = candidates.get(name)
        if path is None or not path.exists():
            raise NotFound(f"no document {name}")
        text = path.read_text(encoding="utf-8").replace("{{BASE_URL}}", settings.base_url)
        return Response(text, media_type="text/markdown; charset=utf-8")
