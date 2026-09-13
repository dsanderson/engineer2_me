"""Shared FT components: badges, ref lists, diff tables, the page shell.

The item detail page must always answer four questions above the fold: what is claimed,
who claimed it, what checked it, and what breaks if it is wrong (design §7).
"""

from __future__ import annotations

import json
from typing import Any

from fasthtml.common import (
    H1,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Li,
    Nav,
    NotStr,
    Option,
    P,
    Pre,
    Script,
    Select,
    Small,
    Span,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
    Ul,
)
from markdown_it import MarkdownIt

from app.models import TIERS

MD = MarkdownIt("commonmark", {"linkify": True}).enable("table")

KIND_ICON = {
    "fact": "◆",
    "calculator": "ƒ",
    "calculation": "=",
    "cad_model": "▣",
    "cad_evaluation": "⌗",
    "idea": "✦",
}

TIER_HELP = {
    "reproduced": "the platform re-ran this and got the expected answer",
    "attested": "the platform cannot check this; it is timestamped, sourced and attributed",
    "asserted": "prose — no checking at all",
}


def markdown(text: str):
    return NotStr(MD.render(text or ""))


def kind_badge(kind: str):
    return Span(f"{KIND_ICON.get(kind, '•')} {kind}", cls=f"badge kind kind-{kind}")


def status_pill(status: str):
    return Span(status, cls=f"badge status st-{status}")


def tier_badge(kind_or_tier: str):
    tier = TIERS.get(kind_or_tier, kind_or_tier)
    return Span(tier, cls=f"badge tier tier-{tier}", title=TIER_HELP.get(tier, ""))


def confirmed_badge(confirmed: bool):
    if not confirmed:
        return Span("unconfirmed", cls="badge conf conf-no", title="no human has checked this")
    return Span("human-confirmed", cls="badge conf conf-yes", title="a person read this and agreed")


def claim_badge(claim: dict[str, Any] | None):
    if not claim:
        return None
    return Span(f"claimed by {claim['by']} until {claim['expires_at']}", cls="badge claim")


def item_link(index, item_id: str, with_kind: bool = True):
    summary = index.summary(item_id)
    if not summary:
        return Span(f"missing item {item_id[:8]}", cls="muted")
    label = summary["title"] or item_id[:8]
    inner = [A(label, href=f"/items/{item_id}")]
    if with_kind:
        inner.append(Small(f" {summary['kind']} · {summary['status']}", cls="muted"))
    return Span(*inner)


def item_row(index, summary: dict[str, Any]):
    return Tr(
        Td(kind_badge(summary["kind"])),
        Td(A(summary["title"] or "(untitled)", href=f"/items/{summary['id']}")),
        Td(status_pill(summary["status"])),
        Td(tier_badge(summary["tier"])),
        Td("✓" if summary["human_confirmed"] else ""),
        Td(Small(" ".join(summary.get("tags") or []), cls="muted")),
        Td(Small(summary["updated_at"][:16].replace("T", " "), cls="muted")),
        cls="retired" if summary["status"] in ("deprecated", "abandoned") else None,
    )


def item_table(index, rows: list[dict[str, Any]]):
    if not rows:
        return P("Nothing here yet.", cls="muted")
    return Table(
        Thead(Tr(*[Th(h) for h in ("kind", "title", "status", "tier", "human", "tags", "updated")])),
        Tbody(*[item_row(index, r) for r in rows]),
        cls="items",
    )


def refs_out_list(index, item):
    if not item.refs:
        return P("No outbound references.", cls="muted")
    return Ul(
        *[
            Li(
                Span(r.rel, cls="rel"),
                " → ",
                item_link(index, r.to),
                Small(f" — {r.note}", cls="muted") if r.note else "",
                cls="contradicts" if r.rel == "contradicts" else None,
            )
            for r in item.refs
        ],
        cls="refs",
    )


def refs_in_list(index, item_id: str):
    backs = index.refs_in(item_id)
    if not backs:
        return P("Nothing references this yet.", cls="muted")
    return Ul(
        *[
            Li(
                item_link(index, b.frm),
                " ",
                Span(b.rel, cls="rel"),
                " → this",
                Small(f" — {b.note}", cls="muted") if b.note else "",
                cls="contradicts" if b.rel == "contradicts" else None,
            )
            for b in backs
        ],
        cls="refs",
    )


def json_block(value: Any, cls: str = "json"):
    return Pre(json.dumps(value, indent=2, default=str), cls=cls)


def kv_table(data: dict[str, Any], units: dict[str, Any] | None = None):
    units = units or {}
    return Table(
        Thead(Tr(Th("field"), Th("value"), Th("units"))),
        Tbody(
            *[Tr(Td(k), Td(_fmt(v)), Td(Small(str(units.get(k, "")), cls="units"))) for k, v in data.items()]
        ),
        cls="kv",
    )


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, dict | list):
        return json.dumps(v, default=str)
    return str(v)


def diff_table(diffs: list[dict[str, Any]]):
    if not diffs:
        return P("No differences.", cls="muted")
    return Table(
        Thead(Tr(Th("path"), Th("expected"), Th("actual"), Th("why"))),
        Tbody(
            *[
                Tr(
                    Td(Pre(d["path"], cls="inline")),
                    Td(_fmt(d.get("expected"))),
                    Td(_fmt(d.get("actual"))),
                    Td(Small(d.get("reason", ""))),
                )
                for d in diffs
            ],
            cls="diffs",
        ),
    )


def verdict_badge(verdict: str):
    return Span(verdict, cls=f"badge verdict v-{verdict}")


def progress_bar(progress: dict[str, Any]):
    tiers = progress["tiers"]
    parts = []
    for tier in ("reproduced", "attested", "asserted"):
        t = tiers.get(tier) or {"total": 0, "done": 0}
        if t["total"]:
            parts.append(Small(f"{tier}: {t['done']}/{t['total']}", cls=f"tierline tier-{tier}"))
    return Div(
        Div(Div(style=f"width:{progress['percent']}%", cls="fill"), cls="bar"),
        Div(*parts, cls="tierlines"),
        cls="progress",
        title="reproduced and attested are counted separately, never summed",
    )


def event_line(index, event: dict[str, Any]):
    detail = ", ".join(f"{k}={v}" for k, v in (event.get("detail") or {}).items() if v not in (None, ""))
    return Li(
        Small(event["at"].replace("T", " ").rstrip("Z"), cls="muted ts"),
        " ",
        Span(event["type"], cls=f"badge ev ev-{event['type']}"),
        " ",
        item_link(index, event["item"], with_kind=False) if event.get("item") else "",
        " ",
        Small(f"by {event['by']}", cls="muted") if event.get("by") else "",
        " ",
        Small(detail, cls="muted detail"),
    )


def page_nav(active: str = ""):
    links = [
        ("/", "dashboard"),
        ("/items", "items"),
        ("/missions", "missions"),
        ("/events", "events"),
        ("/start.md", "agent guide"),
    ]
    return Nav(
        Ul(Li(A(H1("engineer2.me", cls="brand"), href="/"))),
        Ul(*[Li(A(label, href=href, cls="active" if label == active else None)) for href, label in links]),
    )


def banner(message: str, kind: str = "warn"):
    return Div(message, cls=f"banner {kind}")


# --------------------------------------------------------------------------- html graph

RETIRED = ("deprecated", "abandoned")


def _conf_tick(node: dict[str, Any]):
    """The human-confirmed tick. Replay always renders the slot, hidden until the frame
    that earns it — a node that never rendered one could not grow one as the slider ran."""
    if node.get("replay"):
        cls = "gconf" if node["human_confirmed"] else "gconf gconf-off"
        return Span("✓", cls=cls, title="human-confirmed")
    return Span("✓", cls="gconf", title="human-confirmed") if node["human_confirmed"] else ""


def graph_node(node: dict[str, Any], focus: str | None = None):
    """One item in the graph: an icon, the title as a link, and status in the left border."""
    title = node["title"] or node["id"][:8]
    tip = f"{title} — {node['kind']} · {node['status']} · {node['tier']}"
    if node["human_confirmed"]:
        tip += " · human-confirmed"
    classes = ["gnode", f"st-{node['status']}", f"kind-{node['kind']}"]
    if node["status"] in RETIRED:
        classes.append("retired")
    if node.get("is_mission"):
        classes.append("mission")
    if node.get("absent"):
        classes.append("gabsent")  # replay: it exists in the layout, not yet in the mission
    if node["id"] == focus:
        classes.append("focus")
    return Div(
        Span(KIND_ICON.get(node["kind"], "•"), cls="gicon", aria_hidden="true"),
        A(title, href=f"/items/{node['id']}", cls="gtitle"),
        _conf_tick(node),
        cls=" ".join(classes),
        title=tip,
        data_id=node["id"],
    )


def graph_view(layout: dict[str, Any], focus: str | None = None):
    """The graph itself. Nodes are server-rendered and readable on their own; graph.js draws
    the edges over them once, and dims everything else while a node is hovered."""
    layers = layout["layers"]
    if not layers:
        return P("Nothing matches these filters.", cls="muted")
    edges = [{"f": e["from"], "t": e["to"], "r": e["rel"]} for e in layout["edges"] if _safe_token(e["rel"])]
    return Div(
        Div(
            NotStr('<svg class="gedges" aria-hidden="true"></svg>'),
            Div(
                *[Div(*[graph_node(n, focus) for n in layer], cls="glayer") for layer in layers],
                cls="glayers",
            ),
            cls="gcanvas",
        ),
        Script(
            json.dumps(edges, separators=(",", ":")).replace("</", "<\\/"),
            type="application/json",
            cls="gdata",
        ),
        cls="hgraph",
    )


def _safe_token(value: str) -> bool:
    return bool(value) and all(c.isalnum() or c in "-_" for c in value)


def graph_legend(statuses):
    """Status is the only thing colour means here, so the legend is only statuses."""
    return Div(
        Small("status:", cls="muted"),
        *[Span(s, cls=f"gkey st-{s}") for s in statuses],
        Small("edges run downwards — an item sits above what it leans on.", cls="muted"),
        cls="glegend",
    )


def graph_edge_table(index, edges: list[dict[str, Any]], limit: int = 250):
    """Every edge as text: the reading that survives with JS off, and the precise one."""
    if not edges:
        return P("No references between these items.", cls="muted")
    rows = sorted(edges, key=lambda e: (index.title(e["from"]).lower(), e["rel"]))
    note = (
        P(f"Showing {limit} of {len(rows)} references — narrow the filters to see the rest.", cls="muted")
        if len(rows) > limit
        else ""
    )
    rows = rows[:limit]
    return Div(
        note,
        Table(
            Thead(Tr(Th("from"), Th("rel"), Th("to"), Th("note"))),
            Tbody(
                *[
                    Tr(
                        Td(A(index.title(e["from"]), href=f"/items/{e['from']}")),
                        Td(Span(e["rel"], cls="rel")),
                        Td(A(index.title(e["to"]), href=f"/items/{e['to']}")),
                        Td(Small(e.get("note", ""), cls="muted")),
                        cls="contradicts" if e["rel"] == "contradicts" else None,
                    )
                    for e in rows
                ]
            ),
            cls="edges",
        ),
    )


# --------------------------------------------------------------------------- replay

REPLAY_COUNTS = ("verified", "proposed", "failed", "open", "claimed", "draft")


def replay_caption(frame: dict[str, Any] | None, index, at: int, total: int):
    """What happened at the frame under the slider.

    Every part is always present, empty when there is nothing to say: replay.js writes into
    these elements rather than rebuilding them, and a caption it had to rebuild once would
    have lost the handles it needs the next time.
    """
    item = frame["item"] if frame else ""
    return Div(
        Small(frame["at"].replace("T", " ").rstrip("Z") if frame else "", cls="muted ts rts"),
        " ",
        Span(
            frame["type"] if frame else "",
            cls=f"badge ev ev-{frame['type']} rev" if frame else "badge ev rev",
            hidden=not frame,
        ),
        " ",
        A(
            (index.title(item) or item[:8]) if frame else "",
            href=f"/items/{item}" if frame else "#",
            cls="rtitle",
            hidden=not frame,
        ),
        " ",
        Span(frame["label"] if frame else "before anything happened", cls="rlabel"),
        " ",
        Small(f"by {frame['by']}" if frame and frame.get("by") else "", cls="muted rby"),
        cls="rcap" + (" moved" if frame and frame.get("status") else ""),
        data_at=str(at),
        data_total=str(total),
    )


def replay_counts(counts: dict[str, int]):
    """A running tally, so the slider says how far along the mission is, not just when."""
    return Div(
        Span(Span(str(counts.get("items", 0)), cls="rnum", data_count="items"), " items", cls="rcount"),
        *[
            Span(
                Span(str(counts.get(st, 0)), cls="rnum", data_count=st),
                " ",
                st,
                cls=f"rcount rc-{st}",
            )
            for st in REPLAY_COUNTS
        ],
        cls="rcounts",
    )


def replay_controls(action: str, at: int, total: int):
    """A GET form, so the slider still works with JS off: drag, then press go."""
    steps = [
        ("⏮", 0, "start", "start"),
        ("◀", max(0, at - 1), "step back", "prev"),
        ("▶", min(total, at + 1), "step forward", "next"),
        ("⏭", total, "end", "end"),
    ]
    return Form(
        Div(
            *[
                A(
                    glyph,
                    href=f"{action}?at={target}",
                    cls="rstep",
                    title=title,
                    data_role=role,
                )
                for glyph, target, title, role in steps
            ],
            Button("play", type="button", cls="rplay", hidden=True),
            Select(
                *[
                    Option(label, value=str(ms), selected=(ms == 220))
                    for label, ms in (("slow", 600), ("medium", 220), ("fast", 70))
                ],
                cls="rspeed",
                hidden=True,
                aria_label="replay speed",
            ),
            Label(
                Input(type="checkbox", cls="ronly", role="switch"),
                Small("status changes only"),
                cls="ronly-label",
                hidden=True,
            ),
            cls="rbuttons",
        ),
        Input(
            type="range",
            name="at",
            min="0",
            max=str(total),
            value=str(at),
            step="1",
            cls="rrange",
            aria_label="replay position",
        ),
        Div(
            Small(f"event {at} of {total}", cls="muted rpos"),
            Button("go", type="submit", cls="rgo"),
            cls="rfoot",
        ),
        method="get",
        action=action,
        cls="rcontrols",
    )


def replay_tail(frames: list[dict[str, Any]], index, limit: int = 10):
    """The handful of events leading up to the cursor — the replay's own activity feed."""
    rows = [
        Li(
            Small(f["at"].replace("T", " ").rstrip("Z"), cls="muted ts"),
            " ",
            Span(f["type"], cls=f"badge ev ev-{f['type']}"),
            " ",
            A(index.title(f["item"]) or f["item"][:8], href=f"/items/{f['item']}"),
            " ",
            Small(f["label"], cls="muted detail"),
            cls="rmoved" if f.get("status") else None,
        )
        for f in frames[-limit:][::-1]
    ]
    return Ul(*rows, cls="events rtail")
