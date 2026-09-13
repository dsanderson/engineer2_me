"""Mission replay: the event log turned back into the states it produced.

A mission page shows the *current* state. This reconstructs every intermediate one, so a
slider can walk a mission from empty to now — which item appeared when, which reference
joined them, and (the thing worth watching) when each item changed status.

State is rebuilt forward from `events.jsonl` rather than from the revisions on disk: the
log is already ordered, already the answer to "what changed", and it is one read instead
of one per revision per item. Every `set_status`, run verdict and invalidation writes an
explicit `status` event, so those frames are exact. Claims, confirmations and payload
edits move status as a *side effect* of another event, so they are re-derived here using
the same rules `service.py` applies — `_next_status` is that table, and it is the one
place in this module that has to agree with the service.
"""

from __future__ import annotations

from typing import Any

from app.index import Index

# What `service.create` gives a new item when the caller does not name a status.
CREATE_STATUS = {"fact": "open", "idea": "open"}

# Frames worth stopping on when stepping "interesting things only".
STATUS_EVENTS = ("created", "status", "run", "invalidated", "claimed", "released", "confirmed")

VERDICT_LABEL = {
    "pass": "run passed",
    "fail": "run disagreed with the claimed answer",
    "error": "run errored — nothing was learned either way",
}


def edge_key(frm: str, to: str) -> str:
    return f"{frm}|{to}"


def _create_status(kind: str) -> str:
    return CREATE_STATUS.get(kind, "draft")


def _initial_state(
    item_id: str, own: list[dict[str, Any]], index: Index, store: Any = None
) -> tuple[str, bool]:
    """Status and human-confirmation as this item first appeared.

    The `created` event states the status outright for anything written since that field
    was added, and a brand new item is never confirmed. Older items are answered by
    revision 1 on disk — one small read, and exact. The event log cannot answer it alone:
    the `previous` on an item's first *status* event is the state just before that event,
    which a claim in between has already moved.
    """
    kind = ""
    for e in own:
        detail = e.get("detail") or {}
        if e["type"] == "created":
            if detail.get("status"):
                return str(detail["status"]), False
            kind = str(detail.get("kind") or "")
            break
    if store is not None:
        try:
            first = store.get_revision(item_id, 1)
            return str(first.status), bool(first.human_confirmed)
        except Exception:  # noqa: BLE001 - a missing revision is not worth failing a page over
            pass
    summary = index.summary(item_id) or {}
    fallback = _create_status(kind) if kind else str(summary.get("status") or "draft")
    return fallback, bool(summary.get("human_confirmed"))


def _next_status(event_type: str, detail: dict[str, Any], before: str, summary: dict[str, Any]) -> str:
    """The status an event leaves behind. Mirrors `service.py` / `verify.py`.

    A `run` is deliberately absent: the verifier follows every run with an explicit
    `status` event, and an `error` verdict must not move the item in either direction.
    """
    if event_type == "status":
        return str(detail.get("status") or before)
    if event_type == "claimed":
        return "claimed" if before == "open" else before
    if event_type == "released":
        return "open" if before == "claimed" else before
    if event_type == "confirmed":
        # attested + human-confirmed is what verified means for that tier
        if detail.get("confirmed") and summary.get("tier") == "attested" and before == "proposed":
            return "verified"
        return before
    if event_type == "updated":
        # content moved under a verified item: it is no longer the thing that was checked
        return "proposed" if detail.get("payload_changed") and before == "verified" else before
    if event_type == "invalidated":
        return "proposed" if before == "verified" else before
    return before


def _label(event: dict[str, Any], detail: dict[str, Any], before: str, after: str) -> str:
    """One phrase saying what happened, in the vocabulary the rest of the UI uses."""
    kind = event["type"]
    if kind == "created":
        return f"added as {detail.get('kind', 'an item')} · {after}"
    if kind == "status":
        reason = detail.get("reason") or ""
        moved = f"{before} → {after}" if before != after else f"status {after}"
        return f"{moved}{' · ' + reason if reason else ''}"
    if kind == "run":
        return VERDICT_LABEL.get(str(detail.get("verdict")), "run finished")
    if kind == "invalidated":
        return f"went stale — {detail.get('reason', 'something upstream changed')}"
    if kind == "claimed":
        return f"claimed until {detail.get('until', '')}".strip()
    if kind == "released":
        return "released"
    if kind == "confirmed":
        return "human-confirmed" if detail.get("confirmed") else "confirmation withdrawn"
    if kind == "ref_added":
        return f"{detail.get('rel', 'references')} →"
    if kind == "ref_removed":
        return f"dropped {detail.get('rel', 'reference')} — {detail.get('reason', '')}".strip(" —")
    if kind == "updated":
        return "edited" + (" (content changed)" if detail.get("payload_changed") else "")
    return kind


def timeline(events: list[dict[str, Any]], ids: set[str], index: Index, store: Any = None) -> dict[str, Any]:
    """Every event that touched these items, each carrying the change it made.

    A frame is a *delta*, not a snapshot: `state_at` folds them, and so does replay.js, so
    the rules above are stated once and the browser only ever applies them.
    """
    ordered = sorted((e for e in events if e.get("item") in ids), key=lambda e: e.get("seq", 0))
    by_item: dict[str, list[dict[str, Any]]] = {}
    for e in ordered:
        by_item.setdefault(e["item"], []).append(e)

    start = {i: _initial_state(i, by_item.get(i, []), index, store) for i in ids}
    status = {i: st for i, (st, _) in start.items()}
    confirmed = {i: c for i, (_, c) in start.items()}
    initial = dict(status)
    initial_confirmed = dict(confirmed)
    # Anything the log never saw created was already there when the log starts.
    born = {i for i, own in by_item.items() if any(e["type"] == "created" for e in own)}
    already = {i for i in ids if i not in born}
    present = set(already)

    announced: set[str] = set()
    frames: list[dict[str, Any]] = []
    for e in ordered:
        item = e["item"]
        detail = e.get("detail") or {}
        before = status.get(item, "draft")
        after = _next_status(e["type"], detail, before, index.summary(item) or {})
        frame: dict[str, Any] = {
            "seq": e.get("seq", 0),
            "at": e.get("at", ""),
            "type": e["type"],
            "item": item,
            "by": e.get("by", ""),
        }
        if e["type"] == "created" and item not in present:
            present.add(item)
            frame["add"] = True
        if after != before:
            status[item] = after
            frame["status"] = after
            frame["from"] = before
        if e["type"] == "confirmed":
            now_confirmed = bool(detail.get("confirmed"))
            if now_confirmed != confirmed.get(item):
                confirmed[item] = now_confirmed
                frame["confirmed"] = now_confirmed
        target = detail.get("to")
        if e["type"] in ("ref_added", "ref_removed") and target in ids:
            key = edge_key(item, str(target))
            announced.add(key)
            frame["edge_add" if e["type"] == "ref_added" else "edge_del"] = key
        frame["label"] = _label(e, detail, before, after)
        frames.append(frame)

    return {
        "frames": frames,
        "initial": initial,
        "initial_confirmed": initial_confirmed,
        "present": sorted(already),
        # Edges no event ever announced (refs rewritten wholesale by an update) have no
        # moment of their own; they follow their endpoints instead of never showing up.
        "announced": sorted(announced),
    }


def state_at(line: dict[str, Any], at: int | None = None) -> dict[str, Any]:
    """Fold the frames up to and including `at` (default: all of them)."""
    frames = line["frames"]
    cut = len(frames) if at is None else max(0, min(int(at), len(frames)))
    status = dict(line["initial"])
    confirmed = dict(line["initial_confirmed"])
    present = set(line["present"])
    edges = set()
    current = None
    for frame in frames[:cut]:
        if frame.get("add"):
            present.add(frame["item"])
        if frame.get("status"):
            status[frame["item"]] = frame["status"]
        if "confirmed" in frame:
            confirmed[frame["item"]] = frame["confirmed"]
        if frame.get("edge_add"):
            edges.add(frame["edge_add"])
        if frame.get("edge_del"):
            edges.discard(frame["edge_del"])
        current = frame
    return {
        "index": cut,
        "present": present,
        "status": status,
        "confirmed": confirmed,
        "edges": edges,
        "frame": current,
        "counts": counts(present, status),
    }


def counts(present: set[str], status: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {"items": len(present)}
    for i in present:
        st = status.get(i, "draft")
        out[st] = out.get(st, 0) + 1
    return out


def visible_edges(state: dict[str, Any], announced: set[str], layout_edges: list[dict[str, Any]]) -> set[str]:
    """Which layout edges are drawn at this frame."""
    out = set()
    for e in layout_edges:
        key = edge_key(e["from"], e["to"])
        both = e["from"] in state["present"] and e["to"] in state["present"]
        if key in announced:
            if key in state["edges"]:
                out.add(key)
        elif both:
            out.add(key)
    return out


def apply_state(layout: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """The mission's graph as it stood at one frame.

    Positions come from the finished graph and never move — a node that jumped around as
    the mission grew would make the replay unreadable — so an item that does not exist yet
    holds its final place, marked `absent`.
    """
    layers = []
    for layer in layout["layers"]:
        layers.append(
            [
                {
                    **node,
                    "status": state["status"].get(node["id"], node["status"]),
                    "human_confirmed": state["confirmed"].get(node["id"], node["human_confirmed"]),
                    "absent": node["id"] not in state["present"],
                    "replay": True,
                }
                for node in layer
            ]
        )
    return {"layers": layers, "edges": layout["edges"]}


def wire(line: dict[str, Any]) -> dict[str, Any]:
    """The timeline as replay.js wants it: short keys, no field it does not read."""
    frames = []
    for f in line["frames"]:
        out: dict[str, Any] = {"q": f["seq"], "a": f["at"], "t": f["type"], "i": f["item"], "l": f["label"]}
        if f.get("by"):
            out["b"] = f["by"]
        if f.get("add"):
            out["n"] = 1
        if f.get("status"):
            out["s"] = f["status"]
            out["p"] = f.get("from", "")
        if "confirmed" in f:
            out["c"] = 1 if f["confirmed"] else 0
        if f.get("edge_add"):
            out["e"] = f["edge_add"]
        if f.get("edge_del"):
            out["x"] = f["edge_del"]
        frames.append(out)
    return {
        "frames": frames,
        "status0": line["initial"],
        "conf0": {i: 1 for i, c in line["initial_confirmed"].items() if c},
        "present0": line["present"],
        "announced": line["announced"],
    }
