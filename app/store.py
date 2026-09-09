"""The filesystem *is* the database.

data/
  items/<shard>/<uuid>/item.json           current revision
                      /revisions/0001.json every prior revision, forever
                      /files/…             attachments and scripts
                      /runs/run_….json     verification records
  events.jsonl
  index.json                               cache only; deleting it is safe

Writes are atomic (`.tmp` + fsync + os.replace) and every write archives the prior
revision. Nothing is ever deleted.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from app.events import EventLog
from app.models import (
    Actor,
    Attachment,
    Item,
    ValidationError,
    new_id,
    now_iso,
    validate_item,
)

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class NotFound(Exception):
    pass


def safe_filename(name: str) -> str:
    """Refuse anything path-like outright rather than silently rewriting it to a basename."""
    original = (name or "").strip()
    if not original or original != os.path.basename(original) or original.startswith("."):
        raise ValidationError([f"unsafe filename {original!r}: a plain filename, no path separators"])
    if not SAFE_NAME.match(original):
        raise ValidationError([f"unsafe filename {original!r}: use letters, digits, dot, dash, underscore"])
    return original


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with open(tmp, mode, **kwargs) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    with contextlib.suppress(OSError):  # directory fsync is best-effort
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=False, default=str) + "\n"


class Store:
    def __init__(self, data_dir: str | Path, events: EventLog | None = None):
        self.root = Path(data_dir).expanduser()
        self.items_dir = self.root / "items"
        try:
            self.items_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise RuntimeError(
                f"cannot write to the data directory {self.root}: {exc}. "
                "In Docker, the mounted directory must be writable by the container user — "
                "set E2_UID/E2_GID in deploy/.env to the owner of that directory, or chown it."
            ) from exc
        self.events = events or EventLog(self.root / "events.jsonl")
        self._locks: dict[str, asyncio.Lock] = {}

    # -- paths ------------------------------------------------------------

    def item_dir(self, item_id: str) -> Path:
        return self.items_dir / item_id[:2] / item_id

    def item_path(self, item_id: str) -> Path:
        return self.item_dir(item_id) / "item.json"

    def files_dir(self, item_id: str) -> Path:
        return self.item_dir(item_id) / "files"

    def runs_dir(self, item_id: str) -> Path:
        return self.item_dir(item_id) / "runs"

    def revisions_dir(self, item_id: str) -> Path:
        return self.item_dir(item_id) / "revisions"

    def lock(self, item_id: str) -> asyncio.Lock:
        """Per-item lock so concurrent writes to one item serialise. One writer process."""
        return self._locks.setdefault(item_id, asyncio.Lock())

    # -- items ------------------------------------------------------------

    def exists(self, item_id: str) -> bool:
        return self.item_path(item_id).exists()

    def get(self, item_id: str) -> Item:
        path = self.item_path(item_id)
        if not path.exists():
            raise NotFound(f"no item {item_id}")
        return Item.from_json(json.loads(path.read_text(encoding="utf-8")))

    def create(self, item: Item, by: Actor | None = None) -> Item:
        if not item.id:
            item.id = new_id()
        if self.exists(item.id):
            raise ValidationError([f"item {item.id} already exists"])
        actor = by or item.created_by
        item.rev = 1
        item.created_at = item.updated_at = now_iso()
        item.created_by = item.updated_by = actor
        errors = validate_item(item)
        if errors:
            raise ValidationError(errors)
        atomic_write(self.item_path(item.id), dumps(item.to_json()))
        atomic_write(self.revisions_dir(item.id) / f"{item.rev:04d}.json", dumps(item.to_json()))
        self.events.append("created", item.id, actor.name, kind=item.kind, title=item.title)
        return item

    def write(self, item: Item, by: Actor, event: str = "updated", **detail: Any) -> Item:
        """Persist a modified item: validate, bump rev, archive the new revision."""
        errors = validate_item(item)
        if errors:
            raise ValidationError(errors)
        item.rev += 1
        item.updated_at = now_iso()
        item.updated_by = by
        atomic_write(self.item_path(item.id), dumps(item.to_json()))
        atomic_write(self.revisions_dir(item.id) / f"{item.rev:04d}.json", dumps(item.to_json()))
        if event:
            self.events.append(event, item.id, by.name, rev=item.rev, **detail)
        return item

    def iter_items(self) -> Iterator[Item]:
        for path in sorted(self.items_dir.glob("*/*/item.json")):
            try:
                yield Item.from_json(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, KeyError):
                continue  # a half-written item never becomes the whole index's problem

    # -- revisions --------------------------------------------------------

    def list_revisions(self, item_id: str) -> list[dict[str, Any]]:
        out = []
        for path in sorted(self.revisions_dir(item_id).glob("*.json")):
            d = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                {
                    "rev": d["rev"],
                    "updated_at": d["updated_at"],
                    "updated_by": d.get("updated_by"),
                    "status": d.get("status"),
                    "title": d.get("title"),
                }
            )
        return out

    def get_revision(self, item_id: str, rev: int) -> Item:
        path = self.revisions_dir(item_id) / f"{int(rev):04d}.json"
        if not path.exists():
            raise NotFound(f"item {item_id} has no revision {rev}")
        return Item.from_json(json.loads(path.read_text(encoding="utf-8")))

    # -- attachments ------------------------------------------------------

    def add_bytes(
        self,
        item_id: str,
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        source_url: str = "",
    ) -> Attachment:
        name = safe_filename(name)
        path = self.files_dir(item_id) / name
        atomic_write(path, data)
        return Attachment(
            name=name,
            sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data),
            content_type=content_type,
            source_url=source_url,
        )

    def add_file(
        self,
        item_id: str,
        name: str,
        fileobj: BinaryIO,
        content_type: str = "application/octet-stream",
        source_url: str = "",
    ) -> Attachment:
        """Stream an upload to files/<name>, hashing as it goes."""
        name = safe_filename(name)
        dest = self.files_dir(item_id) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        h = hashlib.sha256()
        total = 0
        with open(tmp, "wb") as out:
            while chunk := fileobj.read(1 << 20):
                h.update(chunk)
                total += len(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, dest)
        return Attachment(
            name=name,
            sha256=h.hexdigest(),
            bytes=total,
            content_type=content_type,
            source_url=source_url,
        )

    def attach(self, item: Item, attachment: Attachment) -> Item:
        """Record (or replace, by name) an attachment on the item envelope."""
        item.attachments = [a for a in item.attachments if a.name != attachment.name]
        item.attachments.append(attachment)
        return item

    def file_path(self, item_id: str, name: str) -> Path:
        path = self.files_dir(item_id) / safe_filename(name)
        if not path.exists():
            raise NotFound(f"item {item_id} has no file {name}")
        return path

    def read_file(self, item_id: str, name: str) -> bytes:
        return self.file_path(item_id, name).read_bytes()

    def list_files(self, item_id: str) -> list[str]:
        d = self.files_dir(item_id)
        return sorted(p.name for p in d.glob("*") if p.is_file() and not p.name.endswith(".tmp"))

    # -- runs -------------------------------------------------------------

    def write_run(self, item_id: str, run: dict[str, Any]) -> dict[str, Any]:
        atomic_write(self.runs_dir(item_id) / f"{run['run_id']}.json", dumps(run))
        return run

    def get_run(self, item_id: str, run_id: str) -> dict[str, Any]:
        path = self.runs_dir(item_id) / f"{safe_filename(run_id)}.json"
        if not path.exists():
            raise NotFound(f"no run {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def find_run(self, run_id: str) -> dict[str, Any]:
        """Runs are addressable without knowing their item (GET /api/v1/runs/{id})."""
        run_id = safe_filename(run_id)
        for path in self.items_dir.glob(f"*/*/runs/{run_id}.json"):
            return json.loads(path.read_text(encoding="utf-8"))
        raise NotFound(f"no run {run_id}")

    def list_runs(self, item_id: str) -> list[dict[str, Any]]:
        """Newest first."""
        runs = []
        for path in self.runs_dir(item_id).glob("run_*.json"):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return runs

    # -- housekeeping -----------------------------------------------------

    def disk_usage(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def backup_to(self, dest: str | Path) -> Path:
        """Backup is a tar. Restore is an untar. That is the whole story."""
        dest = Path(dest)
        return Path(shutil.make_archive(str(dest), "gztar", root_dir=self.root))
