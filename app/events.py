"""Append-only activity feed: one JSON line per thing that happened.

This is what `GET /api/v1/events?since=` serves and how an agent asks
"what changed since I last looked".
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from app.models import now_iso

EVENT_TYPES = (
    "created",
    "updated",
    "status",
    "claimed",
    "released",
    "ref_added",
    "ref_removed",
    "run",
    "confirmed",
    "invalidated",
    "file_added",
)


class EventLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = self._count()

    def _count(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("rb") as f:
            return sum(1 for line in f if line.strip())

    def append(self, type: str, item_id: str = "", by: str = "", **detail: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "at": now_iso(),
                "type": type,
                "item": item_id,
                "by": by,
                "detail": detail,
            }
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
                f.flush()
                os.fsync(f.fileno())
        return event

    def tail(self, since: str | int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Events after `since` (a seq number or an ISO timestamp), oldest first."""
        events = self.all()
        if since is not None and since != "":
            try:
                seq = int(since)
                events = [e for e in events if e["seq"] > seq]
            except (TypeError, ValueError):
                events = [e for e in events if e["at"] > str(since)]
        return events[-limit:] if limit else events

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first, for the UI."""
        return list(reversed(self.all()[-limit:]))

    def all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # a torn last line is not worth crashing over
        return out
