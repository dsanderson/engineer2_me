"""The item model: envelope, per-kind payload validation, status transitions, ref vocabulary.

Plain dataclasses and dicts. No ORM, no pydantic — an item is a JSON envelope on disk
and the validators below are the only thing standing between an agent and a broken graph.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1

KINDS = ("fact", "calculator", "calculation", "cad_model", "cad_evaluation", "idea")

# Tier of rigor: what a "verified" status actually means for this kind (design §1.2).
TIERS = {
    "calculation": "reproduced",
    "cad_evaluation": "reproduced",
    "calculator": "reproduced",  # self-tested by its own examples
    "fact": "attested",
    "cad_model": "attested",
    "idea": "asserted",
}

STATUSES = (
    "draft",
    "open",
    "claimed",
    "proposed",
    "verified",
    "failed",
    "deprecated",
    "abandoned",
)

# Statuses that mean "this needs someone to do something".
OPEN_STATUSES = ("open", "claimed", "proposed", "failed")
# Terminal: retired, never deleted, still fetchable and still resolvable as a ref target.
TERMINAL_STATUSES = ("deprecated", "abandoned")

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("open", "proposed", "deprecated", "abandoned"),
    "open": ("draft", "claimed", "proposed", "deprecated", "abandoned"),
    "claimed": ("open", "proposed", "deprecated", "abandoned"),
    "proposed": ("open", "claimed", "verified", "failed", "deprecated", "abandoned"),
    "verified": ("proposed", "failed", "deprecated", "abandoned"),
    "failed": ("open", "claimed", "proposed", "verified", "deprecated", "abandoned"),
    "deprecated": (),
    "abandoned": (),
}

# rel -> (allowed source kinds or None for any, allowed target kinds or None for any)
REF_RELS: dict[str, tuple[tuple[str, ...] | None, tuple[str, ...] | None]] = {
    "part_of": (None, ("idea",)),
    "depends_on": (None, None),
    "uses_calculator": (("calculation",), ("calculator",)),
    "evaluates": (("cad_evaluation",), ("cad_model",)),
    "sources": (None, ("fact",)),
    "supports": (None, None),
    "contradicts": (None, None),
    "supersedes": (None, None),
    "related": (None, None),
}

# Edges along which staleness propagates (design §4.5).
INVALIDATING_RELS = ("depends_on", "uses_calculator", "evaluates", "sources")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ValidationError(Exception):
    """Raised with a list of human-readable problems."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def tier_for(kind: str) -> str:
    return TIERS.get(kind, "asserted")


def can_transition(old: str, new: str) -> bool:
    if old == new:
        return True
    return new in TRANSITIONS.get(old, ())


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(UUID_RE.match(value.lower()))


# --------------------------------------------------------------------------- envelope


@dataclass
class Actor:
    type: str = "agent"  # agent | human
    name: str = "unknown"

    @staticmethod
    def parse(value: Any) -> Actor:
        if isinstance(value, Actor):
            return value
        if isinstance(value, str):
            return Actor(type="agent", name=value)
        if isinstance(value, dict):
            return Actor(type=str(value.get("type", "agent")), name=str(value.get("name", "unknown")))
        return Actor()


@dataclass
class Ref:
    rel: str
    to: str
    note: str = ""
    added_at: str = field(default_factory=now_iso)


@dataclass
class Attachment:
    name: str
    sha256: str
    bytes: int
    content_type: str = "application/octet-stream"
    added_at: str = field(default_factory=now_iso)
    source_url: str = ""


@dataclass
class Claim:
    by: str
    at: str
    expires_at: str
    note: str = ""

    def is_live(self, at: str | None = None) -> bool:
        return (at or now_iso()) < self.expires_at


@dataclass
class Deprecation:
    reason: str
    at: str = field(default_factory=now_iso)
    by: str = "unknown"
    superseded_by: str | None = None


@dataclass
class Item:
    id: str = field(default_factory=new_id)
    kind: str = "idea"
    rev: int = 1
    schema_version: int = SCHEMA_VERSION

    title: str = ""
    question: str = ""
    body: str = ""

    status: str = "draft"
    human_confirmed: bool = False
    tier: str = "asserted"

    tags: list[str] = field(default_factory=list)

    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    created_by: Actor = field(default_factory=Actor)
    updated_by: Actor = field(default_factory=Actor)

    claim: Claim | None = None
    refs: list[Ref] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)

    latest_run: str | None = None
    deprecation: Deprecation | None = None
    confirmation: dict[str, Any] | None = None

    def __post_init__(self):
        # tier is a pure function of kind; stored only so consumers need not know the table
        self.tier = tier_for(self.kind)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if self.claim is None:
            d["claim"] = None
        return d

    @staticmethod
    def from_json(d: dict[str, Any]) -> Item:
        return Item(
            id=d["id"],
            kind=d["kind"],
            rev=int(d.get("rev", 1)),
            schema_version=int(d.get("schema_version", SCHEMA_VERSION)),
            title=d.get("title", ""),
            question=d.get("question", ""),
            body=d.get("body", ""),
            status=d.get("status", "draft"),
            human_confirmed=bool(d.get("human_confirmed", False)),
            tier=d.get("tier") or tier_for(d["kind"]),
            tags=list(d.get("tags") or []),
            created_at=d.get("created_at", now_iso()),
            updated_at=d.get("updated_at", now_iso()),
            created_by=Actor.parse(d.get("created_by")),
            updated_by=Actor.parse(d.get("updated_by")),
            claim=Claim(**d["claim"]) if d.get("claim") else None,
            refs=[Ref(**r) for r in (d.get("refs") or [])],
            payload=dict(d.get("payload") or {}),
            attachments=[Attachment(**a) for a in (d.get("attachments") or [])],
            latest_run=d.get("latest_run"),
            deprecation=Deprecation(**d["deprecation"]) if d.get("deprecation") else None,
            confirmation=d.get("confirmation"),
        )

    # -- convenience ------------------------------------------------------

    def live_claim(self) -> Claim | None:
        """Claims expire lazily: a stale claim is simply not a claim."""
        if self.claim and self.claim.is_live():
            return self.claim
        return None

    def refs_to(self, rel: str) -> list[str]:
        return [r.to for r in self.refs if r.rel == rel]

    def attachment(self, name: str) -> Attachment | None:
        for a in self.attachments:
            if a.name == name:
                return a
        return None

    def is_mission(self) -> bool:
        return self.kind == "idea" and bool(self.payload.get("is_mission"))

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "rev": self.rev,
            "title": self.title,
            "status": self.status,
            "tier": self.tier,
            "human_confirmed": self.human_confirmed,
            "tags": list(self.tags),
            "updated_at": self.updated_at,
            "is_mission": self.is_mission(),
        }


# --------------------------------------------------------------------------- validation

_PAYLOAD_KEYS: dict[str, set[str]] = {
    "fact": {"data", "units", "conditions", "sources", "confidence"},
    # `script` is a write-only convenience: the API materialises it into files/<entrypoint>
    # and stores only the entrypoint + hash, per design §2.2.
    "calculator": {"entrypoint", "sha256", "inputs", "outputs", "examples", "runtime"},
    "calculation": {"calculator", "inputs", "expect", "tolerance"},
    "cad_model": {
        "provider",
        "did",
        "wid",
        "eid",
        "element_type",
        "url",
        "pin",
        "last_seen_microversion",
        "description",
    },
    "cad_evaluation": {
        "model",
        "entrypoint",
        "sha256",
        "onshape_source",
        "expect",
        "tolerance",
        "onshape_api_version",
    },
    "idea": {"markdown", "is_mission", "goal", "milestones"},
}

CONFIDENCE = ("high", "medium", "low")


def _check_unknown(kind: str, payload: dict[str, Any]) -> list[str]:
    unknown = set(payload) - _PAYLOAD_KEYS[kind]
    return [f"payload: unknown key {k!r} for kind {kind!r}" for k in sorted(unknown)]


def _check_tolerance(tol: Any, where: str) -> list[str]:
    if tol is None:
        return []
    if not isinstance(tol, dict):
        return [f"{where}: tolerance must be an object"]
    errs = []
    for key in ("rel", "abs"):
        if key in tol and not isinstance(tol[key], int | float):
            errs.append(f"{where}: tolerance.{key} must be a number")
    keys = tol.get("keys")
    if keys is not None:
        if not isinstance(keys, dict):
            errs.append(f"{where}: tolerance.keys must be an object")
        else:
            for k, v in keys.items():
                errs.extend(_check_tolerance(v, f"{where}: tolerance.keys.{k}"))
    for k in set(tol) - {"rel", "abs", "keys"}:
        errs.append(f"{where}: unknown tolerance key {k!r}")
    return errs


def validate_payload(kind: str, payload: dict[str, Any]) -> list[str]:
    """Return a list of problems; empty means valid."""
    if kind not in KINDS:
        return [f"unknown kind {kind!r}"]
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    errs = _check_unknown(kind, payload)
    errs.extend(globals()[f"_validate_{kind}"](payload))
    return errs


def _validate_fact(p: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    data = p.get("data")
    if data is not None and not isinstance(data, dict):
        errs.append("payload.data must be an object (a JSONable answer keyed by field name)")
    for key in ("units", "conditions"):
        if p.get(key) is not None and not isinstance(p[key], dict):
            errs.append(f"payload.{key} must be an object")
    units = p.get("units") or {}
    if isinstance(data, dict) and isinstance(units, dict):
        for k in units:
            if k not in data:
                errs.append(f"payload.units has no matching data key {k!r}")
    sources = p.get("sources")
    if sources is not None:
        if not isinstance(sources, list):
            errs.append("payload.sources must be a list")
        else:
            for i, s in enumerate(sources):
                if not isinstance(s, dict):
                    errs.append(f"payload.sources[{i}] must be an object")
                elif not (s.get("attachment") or s.get("url")):
                    errs.append(f"payload.sources[{i}] needs an 'attachment' or a 'url'")
    if data:
        # A populated fact without evidence is exactly the failure mode we exist to prevent.
        if not sources:
            errs.append("a fact with data needs at least one source (attachment or url)")
    conf = p.get("confidence")
    if conf is not None and conf not in CONFIDENCE:
        errs.append(f"payload.confidence must be one of {CONFIDENCE}")
    return errs


def _validate_calculator(p: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    entry = p.get("entrypoint")
    if not entry or not isinstance(entry, str):
        errs.append("payload.entrypoint is required (the script filename, e.g. calc.py)")
    elif "/" in entry or entry.startswith("."):
        errs.append("payload.entrypoint must be a plain filename")
    for key in ("inputs", "outputs"):
        v = p.get(key)
        if v is not None and not isinstance(v, dict):
            errs.append(f"payload.{key} must be an object mapping name -> type description")
    examples = p.get("examples")
    if examples is not None:
        if not isinstance(examples, list):
            errs.append("payload.examples must be a list")
        else:
            for i, ex in enumerate(examples):
                if not isinstance(ex, dict):
                    errs.append(f"payload.examples[{i}] must be an object")
                    continue
                if not isinstance(ex.get("inputs"), dict):
                    errs.append(f"payload.examples[{i}].inputs must be an object")
                if not isinstance(ex.get("expect"), dict):
                    errs.append(f"payload.examples[{i}].expect must be an object")
                errs.extend(_check_tolerance(ex.get("tolerance"), f"payload.examples[{i}]"))
                for k in set(ex) - {"inputs", "expect", "tolerance", "name"}:
                    errs.append(f"payload.examples[{i}]: unknown key {k!r}")
    return errs


def _validate_calculation(p: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if not is_uuid(p.get("calculator")):
        errs.append("payload.calculator must be the uuid of a calculator item")
    if not isinstance(p.get("inputs"), dict):
        errs.append('payload.inputs must be an object (literals or {"$ref": "item:<uuid>#<pointer>"})')
    if not isinstance(p.get("expect"), dict):
        errs.append("payload.expect is required — a calculation claims an answer (design §11.8)")
    errs.extend(_check_tolerance(p.get("tolerance"), "payload"))
    return errs


def _validate_cad_model(p: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    provider = p.get("provider", "onshape")
    if provider != "onshape":
        errs.append("payload.provider must be 'onshape' (the only provider in v1)")
    for key in ("did", "wid", "eid"):
        if not p.get(key) or not isinstance(p[key], str):
            errs.append(f"payload.{key} is required")
    pin = p.get("pin")
    if pin is not None:
        if not isinstance(pin, dict) or pin.get("kind") not in ("workspace", "version"):
            errs.append("payload.pin must be {'kind': 'workspace'} or {'kind': 'version', 'vid': ...}")
        elif pin.get("kind") == "version" and not pin.get("vid"):
            errs.append("payload.pin.vid is required when pin.kind == 'version'")
    return errs


def _validate_cad_evaluation(p: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if not is_uuid(p.get("model")):
        errs.append("payload.model must be the uuid of a cad_model item")
    entry = p.get("entrypoint")
    if not entry or not isinstance(entry, str):
        errs.append("payload.entrypoint is required (the mirrored FeatureScript filename, e.g. eval.fs)")
    elif "/" in entry or entry.startswith("."):
        errs.append("payload.entrypoint must be a plain filename")
    if not isinstance(p.get("expect"), dict):
        errs.append("payload.expect is required")
    errs.extend(_check_tolerance(p.get("tolerance"), "payload"))
    src = p.get("onshape_source")
    if src is not None and not isinstance(src, dict):
        errs.append("payload.onshape_source must be an object")
    return errs


def _validate_idea(p: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if p.get("markdown") is not None and not isinstance(p["markdown"], str):
        errs.append("payload.markdown must be a string")
    if p.get("is_mission") is not None and not isinstance(p["is_mission"], bool):
        errs.append("payload.is_mission must be a boolean")
    if p.get("is_mission") and not p.get("goal"):
        errs.append("a mission needs a goal — one sentence stating what done looks like")
    ms = p.get("milestones")
    if ms is not None:
        if not isinstance(ms, list):
            errs.append("payload.milestones must be a list")
        else:
            for i, m in enumerate(ms):
                if not isinstance(m, dict):
                    errs.append(f"payload.milestones[{i}] must be an object")
                    continue
                if not m.get("label"):
                    errs.append(f"payload.milestones[{i}].label is required")
                if m.get("item") is not None and not is_uuid(m["item"]):
                    errs.append(f"payload.milestones[{i}].item must be a uuid or null")
                for k in set(m) - {"label", "item", "note"}:
                    errs.append(f"payload.milestones[{i}]: unknown key {k!r}")
    return errs


def validate_ref(rel: str, src_kind: str, dst_kind: str, note: str, src_id: str, dst_id: str) -> list[str]:
    errs: list[str] = []
    if rel not in REF_RELS:
        return [f"unknown ref rel {rel!r}; allowed: {', '.join(sorted(REF_RELS))}"]
    if src_id == dst_id:
        errs.append("an item cannot reference itself")
    src_ok, dst_ok = REF_RELS[rel]
    if src_ok and src_kind not in src_ok:
        errs.append(f"rel {rel!r} is only valid from {', '.join(src_ok)} (this is a {src_kind})")
    if dst_ok and dst_kind not in dst_ok:
        errs.append(f"rel {rel!r} must point at {', '.join(dst_ok)} (target is a {dst_kind})")
    if rel == "related" and not note.strip():
        errs.append("rel 'related' requires a note explaining the relation")
    return errs


def validate_item(item: Item) -> list[str]:
    errs: list[str] = []
    if item.kind not in KINDS:
        errs.append(f"unknown kind {item.kind!r}")
        return errs
    if not item.title.strip():
        errs.append("title is required")
    if item.status not in STATUSES:
        errs.append(f"unknown status {item.status!r}")
    if item.status == "deprecated" and not (item.deprecation and item.deprecation.reason):
        errs.append("deprecated items need deprecation.reason")
    if not all(isinstance(t, str) for t in item.tags):
        errs.append("tags must be strings")
    errs.extend(validate_payload(item.kind, item.payload))
    return errs
