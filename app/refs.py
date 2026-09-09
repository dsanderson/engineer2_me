"""`$ref` parsing and resolution: `item:<uuid>#<json-pointer>`.

Resolution happens server-side at run time and captures provenance, so a run record
names the exact revision of every item it consumed (design §4.4).
"""

from __future__ import annotations

import re
from typing import Any

from app.models import TERMINAL_STATUSES
from app.store import NotFound, Store

REF_RE = re.compile(r"^item:(?P<id>[0-9a-fA-F-]{36})(?:#(?P<pointer>.*))?$")


class RefError(Exception):
    pass


def is_ref(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"$ref"} and isinstance(value["$ref"], str)


def parse_ref(ref: str) -> tuple[str, str]:
    m = REF_RE.match(ref.strip())
    if not m:
        raise RefError(f"bad $ref {ref!r}: expected 'item:<uuid>#<json-pointer>'")
    return m.group("id").lower(), m.group("pointer") or ""


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901 JSON pointer. Empty pointer returns the whole document."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise RefError(f"bad json pointer {pointer!r}: must start with '/'")
    cur = doc
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if token not in cur:
                raise RefError(f"json pointer {pointer!r}: no key {token!r}")
            cur = cur[token]
        elif isinstance(cur, list):
            try:
                cur = cur[int(token)]
            except (ValueError, IndexError) as exc:
                raise RefError(f"json pointer {pointer!r}: bad list index {token!r}") from exc
        else:
            raise RefError(f"json pointer {pointer!r}: cannot descend into {type(cur).__name__}")
    return cur


def resolve_ref(store: Store, ref: str) -> tuple[Any, dict[str, Any]]:
    """Return (value, provenance). Provenance names item, rev and pointer."""
    item_id, pointer = parse_ref(ref)
    try:
        item = store.get(item_id)
    except NotFound as exc:
        raise RefError(f"$ref {ref}: no such item") from exc
    value = resolve_pointer(item.to_json(), pointer)
    prov = {
        "item": item.id,
        "rev": item.rev,
        "pointer": pointer,
        "title": item.title,
        "status": item.status,
    }
    if item.status in TERMINAL_STATUSES:
        prov["warning"] = f"source item is {item.status}"
    return value, prov


def resolve_inputs(store: Store, inputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve every `$ref` in an inputs mapping, one level deep per key (values may nest)."""
    resolved: dict[str, Any] = {}
    provenance: dict[str, Any] = {}

    def walk(value: Any, path: str) -> Any:
        if is_ref(value):
            got, prov = resolve_ref(store, value["$ref"])
            provenance[path] = prov
            return got
        if isinstance(value, dict):
            return {k: walk(v, f"{path}/{k}" if path else k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return value

    for key, value in inputs.items():
        resolved[key] = walk(value, key)
    return resolved, provenance


def referenced_items(inputs: dict[str, Any]) -> set[str]:
    """Every item id mentioned by a `$ref` anywhere in the structure."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if is_ref(value):
            try:
                found.add(parse_ref(value["$ref"])[0])
            except RefError:
                pass
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(inputs)
    return found
