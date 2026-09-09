"""The platform: store + index + events + verifier, and the operations that combine them.

Both the JSON API and the HTML pages call into this. Neither of them contains rules.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.config import settings as default_settings
from app.events import EventLog
from app.index import Index
from app.models import (
    KINDS,
    TERMINAL_STATUSES,
    Actor,
    Claim,
    Deprecation,
    Item,
    Ref,
    ValidationError,
    can_transition,
    now_iso,
    validate_payload,
    validate_ref,
)
from app.onshape import OnshapeClient
from app.runner_client import RunnerClient
from app.store import NotFound, Store
from app.verify import Verifier

# Fields a caller may set directly on the envelope. Everything else is derived or
# owned by the platform (rev, timestamps, tier, latest_run, ...).
WRITABLE = ("title", "question", "body", "tags", "status", "payload")

ENTRYPOINT_DEFAULT = {"calculator": "calc.py", "cad_evaluation": "eval.fs"}


class Conflict(Exception):
    """A legal request that the current state refuses: bad transition, contested claim."""


class Platform:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or default_settings
        self.events = EventLog(self.settings.data_dir / "events.jsonl")
        self.store = Store(self.settings.data_dir, self.events)
        self.index = Index(self.store)
        self.index.load()
        self.runner = RunnerClient(
            self.settings.runner_url, self.settings.run_timeout_s, self.settings.run_mem_mb
        )
        self.onshape = OnshapeClient(
            self.settings.onshape_access_key,
            self.settings.onshape_secret_key,
            self.settings.onshape_base,
            self.settings.onshape_api_version,
        )
        self.verifier = Verifier(
            self.store, self.index, self.events, self.settings, self.runner, self.onshape
        )

    # -- helpers ----------------------------------------------------------

    def get(self, item_id: str) -> Item:
        return self.store.get(item_id)

    def _materialise_script(self, item: Item, payload: dict[str, Any]) -> dict[str, Any]:
        """`payload.script` is a write-only convenience: it becomes files/<entrypoint> + sha256.

        Keeps the stored envelope exactly as design §2.2 describes while letting an agent
        (or a form) post a calculator in one call.
        """
        script = payload.pop("script", None)
        if script is None:
            return payload
        if item.kind not in ENTRYPOINT_DEFAULT:
            raise ValidationError([f"payload.script is only meaningful for {list(ENTRYPOINT_DEFAULT)}"])
        entrypoint = (
            payload.get("entrypoint") or item.payload.get("entrypoint") or ENTRYPOINT_DEFAULT[item.kind]
        )
        data = script.encode("utf-8")
        attachment = self.store.add_bytes(item.id, entrypoint, data, "text/plain")
        self.store.attach(item, attachment)
        payload["entrypoint"] = entrypoint
        payload["sha256"] = hashlib.sha256(data).hexdigest()
        return payload

    def _check_refs(self, item: Item) -> None:
        for ref in item.refs:
            if not self.store.exists(ref.to):
                raise ValidationError([f"ref target {ref.to} does not exist"])
            target = self.store.get(ref.to)
            errs = validate_ref(ref.rel, item.kind, target.kind, ref.note, item.id, ref.to)
            if errs:
                raise ValidationError(errs)

    def _implied_refs(self, item: Item) -> None:
        """`uses_calculator` / `evaluates` are implied by the payload; materialise them as edges."""
        implied = []
        if item.kind == "calculation" and item.payload.get("calculator"):
            implied.append(("uses_calculator", item.payload["calculator"]))
        if item.kind == "cad_evaluation" and item.payload.get("model"):
            implied.append(("evaluates", item.payload["model"]))
        for rel, to in implied:
            if not any(r.rel == rel and r.to == to for r in item.refs):
                item.refs.append(Ref(rel=rel, to=to, note="implied by payload"))

    # -- create / update --------------------------------------------------

    def create(self, data: dict[str, Any], actor: Actor) -> Item:
        kind = data.get("kind")
        if kind not in KINDS:
            raise ValidationError([f"kind must be one of {', '.join(KINDS)}"])
        item = Item(kind=kind, created_by=actor, updated_by=actor)
        for field in WRITABLE:
            if field in data and data[field] is not None:
                setattr(item, field, data[field])
        item.status = data.get("status") or ("open" if kind in ("fact", "idea") else "draft")
        item.payload = self._materialise_script(item, dict(item.payload or {}))
        item.refs = [Ref(rel=r["rel"], to=r["to"], note=r.get("note", "")) for r in (data.get("refs") or [])]
        self._implied_refs(item)
        self._check_refs(item)
        errs = validate_payload(item.kind, item.payload)
        if errs:
            raise ValidationError(errs)
        self.store.create(item, actor)
        self.index.update_on_write(item)
        for ref in item.refs:
            self.events.append("ref_added", item.id, actor.name, rel=ref.rel, to=ref.to)
        return item

    def update(self, item_id: str, patch: dict[str, Any], actor: Actor) -> tuple[Item, list[str]]:
        """Apply a partial update. Returns the item and any dependents it made stale."""
        item = self.store.get(item_id)
        if item.status in TERMINAL_STATUSES and "status" not in patch:
            raise Conflict(f"item is {item.status}; retired items are not edited, they are superseded")
        before_payload = dict(item.payload)
        if "status" in patch and patch["status"] != item.status:
            if not can_transition(item.status, patch["status"]):
                raise Conflict(f"cannot go from {item.status} to {patch['status']}")
        for field in WRITABLE:
            if field in patch and patch[field] is not None:
                setattr(item, field, patch[field])
        item.payload = self._materialise_script(item, dict(item.payload or {}))
        if "refs" in patch and patch["refs"] is not None:
            item.refs = [Ref(rel=r["rel"], to=r["to"], note=r.get("note", "")) for r in patch["refs"]]
        self._implied_refs(item)
        self._check_refs(item)
        errs = validate_payload(item.kind, item.payload)
        if errs:
            raise ValidationError(errs)

        payload_changed = item.payload != before_payload
        if payload_changed and item.status == "verified" and "status" not in patch:
            # Content moved under a verified item: it is no longer the thing that was checked.
            item.status = "proposed"
        self.store.write(item, actor, event="updated", payload_changed=payload_changed)
        self.index.update_on_write(item)
        stale: list[str] = []
        if payload_changed:
            stale = self.verifier.invalidate_dependents(item, reason=f"{item.id} edited at rev {item.rev}")
        return item, stale

    # -- status, confirmation, claims -------------------------------------

    def set_status(
        self,
        item_id: str,
        status: str,
        actor: Actor,
        reason: str = "",
        superseded_by: str | None = None,
    ) -> Item:
        item = self.store.get(item_id)
        if not can_transition(item.status, status):
            raise Conflict(
                f"cannot go from {item.status} to {status}"
                + (" (retired items are terminal)" if item.status in TERMINAL_STATUSES else "")
            )
        if status == "deprecated":
            if not reason.strip():
                raise ValidationError(["deprecating an item requires a reason"])
            item.deprecation = Deprecation(reason=reason, by=actor.name, superseded_by=superseded_by)
            if superseded_by:
                if not self.store.exists(superseded_by):
                    raise ValidationError([f"superseded_by {superseded_by} does not exist"])
                replacement = self.store.get(superseded_by)
                if not any(r.rel == "supersedes" and r.to == item.id for r in replacement.refs):
                    replacement.refs.append(Ref(rel="supersedes", to=item.id, note=reason or "supersedes"))
                    self.store.write(replacement, actor, event="ref_added", rel="supersedes", to=item.id)
                    self.index.update_on_write(replacement)
        if status == "abandoned" and reason.strip():
            item.deprecation = Deprecation(reason=reason, by=actor.name, superseded_by=superseded_by)
        previous = item.status
        item.status = status
        self.store.write(item, actor, event="status", status=status, previous=previous, reason=reason)
        self.index.update_on_write(item)
        return item

    def confirm(self, item_id: str, confirmed: bool, by: Actor, note: str = "") -> Item:
        """Human faithfulness check — orthogonal to status (design §2.4)."""
        item = self.store.get(item_id)
        item.human_confirmed = bool(confirmed)
        item.confirmation = {"by": by.name, "at": now_iso(), "note": note} if confirmed else None
        if confirmed and item.tier == "attested" and item.status == "proposed":
            item.status = "verified"  # attested + human-confirmed is what verified means here
        self.store.write(item, by, event="confirmed", confirmed=bool(confirmed), note=note)
        self.index.update_on_write(item)
        return item

    def claim(self, item_id: str, agent: str, ttl_s: int = 3600, note: str = "") -> Item:
        item = self.store.get(item_id)
        live = item.live_claim()
        if live and live.by != agent:
            raise Conflict(f"claimed by {live.by} until {live.expires_at}")
        expires = datetime.now(UTC) + timedelta(seconds=max(60, min(int(ttl_s), 86400)))
        item.claim = Claim(
            by=agent,
            at=now_iso(),
            expires_at=expires.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            note=note,
        )
        if item.status == "open":
            item.status = "claimed"
        self.store.write(item, Actor("agent", agent), event="claimed", until=item.claim.expires_at)
        self.index.update_on_write(item)
        return item

    def release(self, item_id: str, agent: str) -> Item:
        item = self.store.get(item_id)
        live = item.live_claim()
        if live and live.by != agent:
            raise Conflict(f"claim is held by {live.by}, not {agent}")
        item.claim = None
        if item.status == "claimed":
            item.status = "open"
        self.store.write(item, Actor("agent", agent), event="released")
        self.index.update_on_write(item)
        return item

    # -- refs -------------------------------------------------------------

    def add_ref(self, item_id: str, rel: str, to: str, note: str, actor: Actor) -> Item:
        item = self.store.get(item_id)
        if not self.store.exists(to):
            raise ValidationError([f"ref target {to} does not exist"])
        target = self.store.get(to)
        errs = validate_ref(rel, item.kind, target.kind, note, item.id, to)
        if errs:
            raise ValidationError(errs)
        if any(r.rel == rel and r.to == to for r in item.refs):
            return item
        item.refs.append(Ref(rel=rel, to=to, note=note))
        self.store.write(item, actor, event="ref_added", rel=rel, to=to, note=note)
        self.index.update_on_write(item)
        return item

    def remove_ref(self, item_id: str, rel: str, to: str, reason: str, actor: Actor) -> Item:
        item = self.store.get(item_id)
        if not reason.strip():
            raise ValidationError(["removing a reference requires a reason — it is logged, not silent"])
        before = len(item.refs)
        item.refs = [r for r in item.refs if not (r.rel == rel and r.to == to)]
        if len(item.refs) == before:
            raise NotFound(f"no {rel} ref to {to}")
        self.store.write(item, actor, event="ref_removed", rel=rel, to=to, reason=reason)
        self.index.update_on_write(item)
        return item

    # -- files ------------------------------------------------------------

    def attach_bytes(
        self,
        item_id: str,
        name: str,
        data: bytes,
        content_type: str,
        actor: Actor,
        source_url: str = "",
    ) -> dict[str, Any]:
        item = self.store.get(item_id)
        attachment = self.store.add_bytes(item.id, name, data, content_type, source_url)
        self.store.attach(item, attachment)
        if item.kind in ENTRYPOINT_DEFAULT and item.payload.get("entrypoint") == attachment.name:
            item.payload["sha256"] = attachment.sha256
        self.store.write(item, actor, event="file_added", name=attachment.name, sha256=attachment.sha256)
        self.index.update_on_write(item)
        return {
            "name": attachment.name,
            "sha256": attachment.sha256,
            "bytes": attachment.bytes,
            "content_type": attachment.content_type,
        }

    # -- verification -----------------------------------------------------

    async def verify(self, item_id: str, actor: Actor) -> dict[str, Any]:
        item = self.store.get(item_id)
        return await self.verifier.verify(item, actor)

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "items": len(self.index),
            "data_dir": str(self.settings.data_dir),
            "data_writable": self.settings.data_dir.exists(),
            "runner_url": self.settings.runner_url,
            "onshape_configured": self.settings.onshape_configured,
        }
