"""In-memory index over the item files: lookups, backlinks, filters, search.

Rebuilt by walking `data/items/**/item.json` at startup and updated in place on every
write. `index.json` is a cache keyed by file mtimes; if it looks stale it is silently
rebuilt, and deleting it is always safe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models import OPEN_STATUSES, TERMINAL_STATUSES, Item, now_iso
from app.store import Store, atomic_write, dumps

INDEX_VERSION = 2


@dataclass
class Backlink:
    frm: str
    rel: str
    note: str = ""


@dataclass
class Entry:
    summary: dict[str, Any]
    refs: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    mtime: float = 0.0

    @property
    def id(self) -> str:
        return self.summary["id"]


def _text_of(item: Item) -> str:
    parts = [item.title, item.question, item.body, " ".join(item.tags), item.kind]
    payload = item.payload
    for key in ("markdown", "goal", "description"):
        if isinstance(payload.get(key), str):
            parts.append(payload[key])
    if isinstance(payload.get("data"), dict):
        parts.append(" ".join(str(k) for k in payload["data"]))
    return " ".join(parts).lower()


class Index:
    def __init__(self, store: Store):
        self.store = store
        self.entries: dict[str, Entry] = {}
        self.backlinks: dict[str, list[Backlink]] = {}

    # -- building ---------------------------------------------------------

    @property
    def cache_path(self) -> Path:
        return self.store.root / "index.json"

    def load(self) -> None:
        """Load from cache if every mtime still matches, else rebuild."""
        if not self._load_cache():
            self.rebuild()
            self.save()

    def _load_cache(self) -> bool:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if data.get("version") != INDEX_VERSION:
            return False
        cached = data.get("entries", {})
        on_disk = {p.parent.name: p.stat().st_mtime for p in self.store.items_dir.glob("*/*/item.json")}
        if set(cached) != set(on_disk):
            return False
        if any(abs(cached[i]["mtime"] - on_disk[i]) > 1e-6 for i in on_disk):
            return False
        self.entries = {
            i: Entry(summary=e["summary"], refs=e["refs"], text=e["text"], mtime=e["mtime"])
            for i, e in cached.items()
        }
        self._rebuild_backlinks()
        return True

    def save(self) -> None:
        payload = {
            "version": INDEX_VERSION,
            "entries": {
                i: {"summary": e.summary, "refs": e.refs, "text": e.text, "mtime": e.mtime}
                for i, e in self.entries.items()
            },
        }
        atomic_write(self.cache_path, dumps(payload))

    def rebuild(self) -> None:
        self.entries = {}
        for item in self.store.iter_items():
            self._put(item)
        self._rebuild_backlinks()

    def _put(self, item: Item) -> None:
        try:
            mtime = self.store.item_path(item.id).stat().st_mtime
        except OSError:
            mtime = 0.0
        summary = item.summary()
        summary["question"] = item.question
        summary["created_at"] = item.created_at
        summary["claim"] = {"by": item.claim.by, "expires_at": item.claim.expires_at} if item.claim else None
        summary["deprecated"] = item.status in TERMINAL_STATUSES
        summary["latest_run"] = item.latest_run
        if item.kind == "idea":
            summary["goal"] = item.payload.get("goal", "")
            summary["milestones"] = item.payload.get("milestones", [])
        self.entries[item.id] = Entry(
            summary=summary,
            refs=[{"rel": r.rel, "to": r.to, "note": r.note} for r in item.refs],
            text=_text_of(item),
            mtime=mtime,
        )

    def _rebuild_backlinks(self) -> None:
        self.backlinks = {}
        for entry in self.entries.values():
            for ref in entry.refs:
                self.backlinks.setdefault(ref["to"], []).append(
                    Backlink(frm=entry.id, rel=ref["rel"], note=ref.get("note", ""))
                )

    def update_on_write(self, item: Item) -> None:
        self._put(item)
        self._rebuild_backlinks()

    # -- reads ------------------------------------------------------------

    def __contains__(self, item_id: str) -> bool:
        return item_id in self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def summary(self, item_id: str) -> dict[str, Any] | None:
        entry = self.entries.get(item_id)
        return entry.summary if entry else None

    def title(self, item_id: str) -> str:
        entry = self.entries.get(item_id)
        return entry.summary["title"] if entry else f"(missing {item_id[:8]})"

    def refs_out(self, item_id: str) -> list[dict[str, Any]]:
        entry = self.entries.get(item_id)
        return list(entry.refs) if entry else []

    def refs_in(self, item_id: str) -> list[Backlink]:
        return list(self.backlinks.get(item_id, []))

    def dependents(self, item_id: str, rels: tuple[str, ...]) -> list[str]:
        return [b.frm for b in self.refs_in(item_id) if b.rel in rels]

    def missions(self) -> list[dict[str, Any]]:
        out = [e.summary for e in self.entries.values() if e.summary.get("is_mission")]
        out.sort(key=lambda s: s["updated_at"], reverse=True)
        return out

    def all_tags(self) -> list[str]:
        tags: set[str] = set()
        for e in self.entries.values():
            tags.update(e.summary.get("tags") or [])
        return sorted(tags)

    def stats(self) -> dict[str, Any]:
        by_kind: dict[str, dict[str, int]] = {}
        by_status: dict[str, int] = {}
        by_tier: dict[str, int] = {}
        for e in self.entries.values():
            s = e.summary
            by_kind.setdefault(s["kind"], {}).setdefault(s["status"], 0)
            by_kind[s["kind"]][s["status"]] += 1
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
            by_tier[s["tier"]] = by_tier.get(s["tier"], 0) + 1
        return {
            "items": len(self.entries),
            "by_kind": by_kind,
            "by_status": by_status,
            "by_tier": by_tier,
            "missions": len(self.missions()),
            "human_confirmed": sum(1 for e in self.entries.values() if e.summary["human_confirmed"]),
        }

    def search(
        self,
        kind: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        mission: str | None = None,
        claimed_by: str | None = None,
        has_open_deps: bool | None = None,
        tier: str | None = None,
        include_retired: bool = True,
        sort: str = "updated_at",
    ) -> list[dict[str, Any]]:
        from app.graph import mission_closure  # local import: graph reads the index

        member_of: set[str] | None = None
        if mission:
            member_of = mission_closure(self, mission)

        out = []
        for entry in self.entries.values():
            s = entry.summary
            if kind and s["kind"] != kind:
                continue
            if status and s["status"] != status:
                continue
            if tier and s["tier"] != tier:
                continue
            if tag and tag not in (s.get("tags") or []):
                continue
            if member_of is not None and s["id"] not in member_of:
                continue
            if claimed_by:
                claim = s.get("claim")
                # claims expire lazily: an expired claim is simply not a claim
                if not claim or claim["by"] != claimed_by or claim["expires_at"] <= now_iso():
                    continue
            if not include_retired and s["status"] in TERMINAL_STATUSES:
                continue
            if q and q.lower() not in entry.text:
                continue
            if has_open_deps is not None and self.has_open_deps(s["id"]) != has_open_deps:
                continue
            out.append(s)
        reverse = sort in ("updated_at", "created_at")
        out.sort(key=lambda s: s.get(sort) or "", reverse=reverse)
        return out

    def has_open_deps(self, item_id: str) -> bool:
        """True if anything this item leans on is still unfinished."""
        for ref in self.refs_out(item_id):
            if ref["rel"] not in ("depends_on", "uses_calculator", "evaluates", "sources"):
                continue
            dep = self.summary(ref["to"])
            if dep and dep["status"] in OPEN_STATUSES:
                return True
        return False
