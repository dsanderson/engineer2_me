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
    Div,
    Li,
    Nav,
    NotStr,
    P,
    Pre,
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
