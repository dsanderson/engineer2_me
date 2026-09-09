"""Mission closure, the work queue, and Mermaid emission.

`open_queue` is the single most important thing the platform computes: it answers
"what should I work on" in one call, ordered so the first entry is never blocked.
"""

from __future__ import annotations

from typing import Any

from app.index import Index
from app.models import INVALIDATING_RELS, OPEN_STATUSES, TERMINAL_STATUSES

CLOSURE_RELS = ("part_of", "depends_on", "uses_calculator", "evaluates", "sources")

KIND_SHAPE = {
    "idea": ("([", "])"),
    "fact": ("[", "]"),
    "calculator": ("[/", "/]"),
    "calculation": ("{{", "}}"),
    "cad_model": ("[(", ")]"),
    "cad_evaluation": (">", "]"),
}

STATUS_CLASS = {
    "draft": "st-draft",
    "open": "st-open",
    "claimed": "st-claimed",
    "proposed": "st-proposed",
    "verified": "st-verified",
    "failed": "st-failed",
    "deprecated": "st-retired",
    "abandoned": "st-retired",
}


def mission_closure(index: Index, root_id: str, max_nodes: int = 5000) -> set[str]:
    """Everything reachable from a mission: members (inbound part_of) and what they lean on."""
    seen: set[str] = set()
    frontier = [root_id]
    while frontier and len(seen) < max_nodes:
        node = frontier.pop()
        if node in seen or node not in index:
            continue
        seen.add(node)
        # members point *at* the mission with part_of
        for back in index.refs_in(node):
            if back.rel == "part_of" and back.frm not in seen:
                frontier.append(back.frm)
        # and each member drags in what it depends on
        for ref in index.refs_out(node):
            if ref["rel"] in CLOSURE_RELS and ref["to"] not in seen:
                frontier.append(ref["to"])
    return seen


def subgraph(index: Index, ids: set[str]) -> dict[str, Any]:
    nodes = [index.summary(i) for i in ids if i in index]
    edges = []
    for i in ids:
        for ref in index.refs_out(i):
            if ref["to"] in ids:
                edges.append({"from": i, "rel": ref["rel"], "to": ref["to"], "note": ref.get("note", "")})
    nodes.sort(key=lambda n: (n["kind"], n["title"]))
    return {"nodes": nodes, "edges": edges}


def neighbourhood(index: Index, item_id: str, depth: int = 2) -> set[str]:
    seen = {item_id}
    frontier = {item_id}
    for _ in range(depth):
        nxt: set[str] = set()
        for node in frontier:
            nxt.update(r["to"] for r in index.refs_out(node))
            nxt.update(b.frm for b in index.refs_in(node))
        nxt -= seen
        seen |= nxt
        frontier = nxt
    return {i for i in seen if i in index}


def blockers(index: Index, item_id: str) -> list[str]:
    """Upstream items this one leans on that are not finished."""
    out = []
    for ref in index.refs_out(item_id):
        if ref["rel"] not in INVALIDATING_RELS:
            continue
        dep = index.summary(ref["to"])
        if dep and dep["status"] in OPEN_STATUSES:
            out.append(ref["to"])
    return out


def _reason_for(summary: dict[str, Any], index: Index) -> str | None:
    status = summary["status"]
    if status in ("open", "claimed"):
        return "needs work"
    if status == "failed":
        return "last run disagreed with the claimed answer"
    if status == "proposed":
        if summary["tier"] == "reproduced":
            return "filled in but never reproduced"
        return "filled in but not human-confirmed" if not summary["human_confirmed"] else "awaiting review"
    if status == "verified" and index.has_open_deps(summary["id"]):
        return "verified, but something upstream is unfinished"
    return None


def open_queue(index: Index, mission_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Open + failed + stale items, topologically ordered so unblocked work comes first."""
    ids = mission_closure(index, mission_id) if mission_id else set(index.entries)
    ids = ids - {mission_id} if mission_id else ids  # a mission is not work on itself

    candidates: list[dict[str, Any]] = []
    for item_id in ids:
        summary = index.summary(item_id)
        if not summary or summary["status"] in TERMINAL_STATUSES:
            continue
        reason = _reason_for(summary, index)
        if reason is None:
            continue
        blocked_by = [b for b in blockers(index, item_id) if b in ids or not mission_id]
        candidates.append({**summary, "reason": reason, "blocked_by": blocked_by})

    # Depth = longest chain of blockers, so leaves (unblocked work) sort first. Cycle-safe.
    depth_cache: dict[str, int] = {}
    by_id = {c["id"]: c for c in candidates}

    def depth(node: str, seen: frozenset[str] = frozenset()) -> int:
        if node in depth_cache:
            return depth_cache[node]
        if node in seen:
            return 0  # cycles are legal in design iteration; they just stop the walk
        entry = by_id.get(node)
        if not entry or not entry["blocked_by"]:
            depth_cache[node] = 0
            return 0
        d = 1 + max(depth(b, seen | {node}) for b in entry["blocked_by"])
        depth_cache[node] = d
        return d

    status_rank = {"failed": 0, "open": 1, "claimed": 2, "proposed": 3, "verified": 4, "draft": 5}
    candidates.sort(
        key=lambda c: (depth(c["id"]), status_rank.get(c["status"], 9), c["updated_at"]),
    )
    for c in candidates:
        c["blocked_by"] = [{"id": b, "title": index.title(b)} for b in c["blocked_by"]]
    return candidates[:limit]


def mission_progress(index: Index, mission_id: str) -> dict[str, Any]:
    """Counts per tier. Reproduced and attested are reported separately, never summed."""
    ids = mission_closure(index, mission_id) - {mission_id}
    tiers: dict[str, dict[str, int]] = {
        t: {"total": 0, "done": 0} for t in ("reproduced", "attested", "asserted")
    }
    open_count = retired = 0
    for i in ids:
        s = index.summary(i)
        if not s:
            continue
        if s["status"] in TERMINAL_STATUSES:
            retired += 1
            continue
        tier = tiers.setdefault(s["tier"], {"total": 0, "done": 0})
        tier["total"] += 1
        done = s["status"] == "verified" and (s["tier"] != "attested" or s["human_confirmed"])
        tier["done"] += 1 if done else 0
        if s["status"] in OPEN_STATUSES:
            open_count += 1
    total = sum(t["total"] for t in tiers.values())
    done = sum(t["done"] for t in tiers.values())
    return {
        "mission": mission_id,
        "items": total,
        "done": done,
        "open": open_count,
        "retired": retired,
        "percent": round(100 * done / total) if total else 0,
        "tiers": tiers,
    }


def milestone_state(index: Index, mission_summary: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for m in mission_summary.get("milestones") or []:
        target = index.summary(m["item"]) if m.get("item") else None
        out.append(
            {
                "label": m.get("label", ""),
                "note": m.get("note", ""),
                "item": m.get("item"),
                "title": target["title"] if target else None,
                "status": target["status"] if target else "unassigned",
                "tier": target["tier"] if target else None,
                "human_confirmed": target["human_confirmed"] if target else False,
            }
        )
    return out


def _node_label(summary: dict[str, Any]) -> str:
    title = summary["title"][:40].replace('"', "'")
    return f'"{title}<br/>{summary["kind"]}"'


def mermaid(index: Index, ids: set[str], collapse_above: int = 150) -> str:
    """`graph LR` source. Node shape encodes kind, colour encodes status, nodes link to items."""
    if len(ids) > collapse_above:
        ids = {i for i in ids if (index.summary(i) or {}).get("kind") == "idea"}
    lines = ["graph LR"]
    for i in sorted(ids):
        s = index.summary(i)
        if not s:
            continue
        open_b, close_b = KIND_SHAPE.get(s["kind"], ("[", "]"))
        nid = "n" + i.replace("-", "")[:12]
        lines.append(f"  {nid}{open_b}{_node_label(s)}{close_b}")
        lines.append(f"  class {nid} {STATUS_CLASS.get(s['status'], 'st-draft')};")
        lines.append(f'  click {nid} href "/items/{i}"')
    for i in sorted(ids):
        for ref in index.refs_out(i):
            if ref["to"] not in ids:
                continue
            a = "n" + i.replace("-", "")[:12]
            b = "n" + ref["to"].replace("-", "")[:12]
            arrow = "-.->" if ref["rel"] in ("related", "supports") else "-->"
            lines.append(f"  {a} {arrow}|{ref['rel']}| {b}")
    lines += [
        "  classDef st-draft fill:#eceff1,stroke:#90a4ae,color:#263238;",
        "  classDef st-open fill:#fff3e0,stroke:#fb8c00,color:#3e2723;",
        "  classDef st-claimed fill:#ede7f6,stroke:#7e57c2,color:#311b92;",
        "  classDef st-proposed fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1;",
        "  classDef st-verified fill:#e8f5e9,stroke:#43a047,color:#1b5e20;",
        "  classDef st-failed fill:#ffebee,stroke:#e53935,color:#b71c1c;",
        "  classDef st-retired fill:#f5f5f5,stroke:#bdbdbd,color:#9e9e9e;",
    ]
    return "\n".join(lines)
