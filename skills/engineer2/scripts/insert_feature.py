#!/usr/bin/env python3
"""Insert a custom feature into a Part Studio's feature list, at the end.

Writing a Feature Studio only *defines* a feature; nothing appears in the Part Studio until an
instance of it is added to the feature list. This does that second half, so an agent can go from
a `.fs` file to real geometry without a human clicking anything.

Standalone: needs only httpx and ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY.

    # what custom features does this document define, and what do they take?
    python insert_feature.py --url "$PS_URL" --list

    # push a .fs and instantiate it in one go
    python insert_feature.py --url "$PS_URL" --file cube.fs --studio CubeFeature \
        --feature-type agentCube --name "Base cube" --param side="50 mm"

Parameters are typed from the feature's own spec: a LENGTH takes an Onshape expression string
("50 mm", "#width * 2"), a boolean takes true/false, a query or array parameter takes JSON.
Naming a parameter the feature does not declare is an error rather than a silent no-op.

Exits non-zero if the feature lands with a non-OK status, so a failed regeneration is not
mistaken for success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

BASE = os.environ.get("E2_ONSHAPE_BASE", "https://cad.onshape.com")
API = os.environ.get("E2_ONSHAPE_API_VERSION", "v9")

# Each parameter spec carries a `defaultValue` that is already a fully-formed BTMParameter of the
# right btType, so the instance is built by copying that and overwriting one field. This maps the
# btType to the field that holds the value; anything not listed here can still be set with
# `--param id:=<json>`, which replaces the whole parameter object.
VALUE_FIELD = {
    "BTMParameterQuantity-147": "expression",
    "BTMParameterBoolean-144": "value",
    "BTMParameterString-149": "value",
    "BTMParameterEnum-145": "value",
    "BTMParameterQueryList-148": "queries",
    "BTMParameterFeatureList-1749": "featureIds",
    "BTMParameterArray-2025": "items",
}
JSON_VALUED = {"BTMParameterQueryList-148", "BTMParameterFeatureList-1749", "BTMParameterArray-2025"}


def parse_url(url: str) -> tuple[str, str, str]:
    parts = url.split("?")[0].rstrip("/").split("/")
    try:
        did = parts[parts.index("documents") + 1]
        wid = parts[parts.index("w") + 1]
        eid = parts[parts.index("e") + 1]
    except (ValueError, IndexError):
        sys.exit("could not parse did/wid/eid from the URL; expected .../documents/<did>/w/<wid>/e/<eid>")
    return did, wid, eid


def client() -> httpx.Client:
    key, secret = os.environ.get("ONSHAPE_ACCESS_KEY"), os.environ.get("ONSHAPE_SECRET_KEY")
    if not (key and secret):
        sys.exit("set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY")
    return httpx.Client(
        auth=(key, secret),
        timeout=120,
        base_url=f"{BASE}/api/{API}",
        headers={"Accept": "application/json;charset=UTF-8;qs=0.09"},
    )


def elements(c: httpx.Client, did: str, wid: str) -> list[dict]:
    return c.get(f"/documents/d/{did}/w/{wid}/elements").raise_for_status().json()


def find_or_create_studio(c: httpx.Client, did: str, wid: str, name: str) -> str:
    for el in elements(c, did, wid):
        if el.get("name") == name and el.get("elementType") == "FEATURESTUDIO":
            return el["id"]
    created = c.post(f"/featurestudios/d/{did}/w/{wid}", json={"name": name}).raise_for_status().json()
    print(f"created Feature Studio {name} ({created['id']})", file=sys.stderr)
    return created["id"]


def write_studio(c: httpx.Client, did: str, wid: str, studio_id: str, source: str) -> None:
    # Note the bare element path: `/contents` is not an endpoint on v9.
    c.post(f"/featurestudios/d/{did}/w/{wid}/e/{studio_id}", json={"contents": source}).raise_for_status()


def feature_specs(c: httpx.Client, did: str, wid: str, studio_ids: list[str]) -> list[dict]:
    """Every custom feature defined by the given Feature Studios, with a fresh namespace.

    The namespace embeds the studio's current microversion, so it must be read at insert time:
    resolving it once and reusing it later pins the instance to a stale definition.
    A studio that fails to compile simply reports no specs.
    """
    specs = []
    for sid in studio_ids:
        resp = c.get(f"/featurestudios/d/{did}/w/{wid}/e/{sid}/featurespecs")
        if resp.status_code >= 400:
            print(f"could not read specs from {sid}: HTTP {resp.status_code}", file=sys.stderr)
            continue
        for spec in resp.json().get("featureSpecs") or []:
            specs.append(spec | {"_studioId": sid})
    return specs


def describe(spec: dict) -> dict:
    return {
        "featureType": spec.get("featureType"),
        "name": spec.get("featureTypeName"),
        "studioId": spec.get("_studioId"),
        "parameters": [
            {
                "id": p.get("parameterId"),
                "type": (p.get("defaultValue") or {}).get("btType", "?"),
                "quantityType": p.get("quantityType"),
                "options": p.get("options"),
            }
            for p in spec.get("parameters") or []
        ],
    }


def build_parameters(spec: dict, values: dict[str, object], raw: dict[str, object]) -> list[dict]:
    """Turn `{parameterId: value}` into BTMParameter objects, typed from the feature's own spec.

    Only named parameters are sent; Onshape fills the rest from the spec's defaults.
    """
    by_id = {p.get("parameterId"): p for p in spec.get("parameters") or []}
    unknown = sorted((set(values) | set(raw)) - set(by_id))
    if unknown:
        sys.exit(
            f"feature '{spec.get('featureType')}' has no parameter(s) {', '.join(unknown)}; "
            f"it declares {', '.join(sorted(k for k in by_id if k))}"
        )

    out = []
    for pid, value in raw.items():
        out.append(dict(value) | {"parameterId": pid} if isinstance(value, dict) else value)
    for pid, value in values.items():
        default = (by_id[pid].get("defaultValue") or {}).copy()
        bt = default.get("btType")
        field = VALUE_FIELD.get(bt)
        if not field:
            sys.exit(f"parameter '{pid}' is a {bt}; set it with --param {pid}:=<json> instead")
        default.pop("nodeId", None)  # server-assigned; sending a stale one confuses the feature list
        if bt in JSON_VALUED:
            value = json.loads(str(value))
        elif bt == "BTMParameterBoolean-144":
            value = str(value).strip().lower() in ("1", "true", "yes")
        else:
            value = str(value)
        if bt == "BTMParameterQuantity-147":
            # An expression wins over value/units only if those are not also carrying a stale number.
            default["value"], default["units"] = 0.0, ""
        out.append(default | {field: value, "parameterId": pid})
    return out


def existing_names(c: httpx.Client, did: str, wid: str, eid: str) -> list[str]:
    feats = c.get(f"/partstudios/d/{did}/w/{wid}/e/{eid}/features").raise_for_status().json()
    return [f.get("message", f).get("name", "") for f in feats.get("features") or []]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="Onshape Part Studio URL")
    ap.add_argument("--feature-type", help="the exported feature name, e.g. agentCube (not the UI name)")
    ap.add_argument("--name", help="name for the instance in the feature list (default: the UI name)")
    ap.add_argument("--studio", help="Feature Studio to look in, and to write --file into")
    ap.add_argument("--file", help="a .fs file to push into --studio before inserting")
    ap.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="ID=VALUE",
        help="set a parameter; ID:=VALUE takes a JSON value (queries, arrays, feature lists)",
    )
    ap.add_argument("--params", help="set several parameters at once, as a JSON object")
    ap.add_argument("--list", action="store_true", help="list the document's custom features and exit")
    ap.add_argument(
        "--if-absent",
        action="store_true",
        help="do nothing if the feature list already has a feature with this --name",
    )
    args = ap.parse_args()

    if not args.list and not args.feature_type:
        return ap.error("--feature-type is required unless --list is given")
    if args.file and not args.studio:
        return ap.error("--file needs --studio to say which Feature Studio to write into")

    did, wid, eid = parse_url(args.url)
    values: dict[str, object] = json.loads(args.params) if args.params else {}
    raw: dict[str, object] = {}
    for entry in args.param:
        if ":=" in entry and entry.index(":=") + 1 == entry.index("="):
            pid, _, text = entry.partition(":=")
            raw[pid] = json.loads(text)
        elif "=" in entry:
            pid, _, text = entry.partition("=")
            values[pid] = text
        else:
            return ap.error(f"--param needs ID=VALUE or ID:=JSON, got {entry!r}")

    with client() as c:
        els = elements(c, did, wid)
        target = next((e for e in els if e["id"] == eid), None)
        if target is None:
            sys.exit(f"no element {eid} in this workspace")
        if target.get("elementType") != "PARTSTUDIO":
            sys.exit(
                f"--url points at {target.get('name')!r}, which is a {target.get('elementType')}. "
                "A feature list lives in a Part Studio; open that tab and use its URL."
            )

        if args.file:
            studio_id = find_or_create_studio(c, did, wid, args.studio)
            write_studio(c, did, wid, studio_id, open(args.file, encoding="utf-8").read())
            print(f"wrote {args.file} → Feature Studio {args.studio} ({studio_id})", file=sys.stderr)
            els = elements(c, did, wid)  # a studio created just now is not in the earlier listing

        studios = [e["id"] for e in els if e.get("elementType") == "FEATURESTUDIO"]
        if args.studio:
            named = [
                e["id"] for e in els if e.get("elementType") == "FEATURESTUDIO" and e["name"] == args.studio
            ]
            studios = named or studios
        specs = feature_specs(c, did, wid, studios)

        if args.list:
            print(json.dumps([describe(s) for s in specs], indent=2))
            return 0

        spec = next((s for s in specs if s.get("featureType") == args.feature_type), None)
        if spec is None:
            known = ", ".join(sorted(s.get("featureType", "?") for s in specs)) or "none"
            sys.exit(
                f"no custom feature '{args.feature_type}' in this document (found: {known}). "
                "A Feature Studio that fails to compile reports no features at all — check it "
                "does not redefine a std name such as `cube` or `sphere`."
            )

        name = args.name or spec.get("featureTypeName") or args.feature_type
        if args.if_absent and name in existing_names(c, did, wid, eid):
            print(json.dumps({"skipped": True, "reason": f"feature {name!r} already present"}, indent=2))
            return 0

        feature = {
            "btType": "BTMFeature-134",
            "featureType": args.feature_type,
            "name": name,
            "namespace": spec["namespace"],
            "suppressed": False,
            "parameters": build_parameters(spec, values, raw),
        }
        resp = c.post(
            f"/partstudios/d/{did}/w/{wid}/e/{eid}/features",
            json={"btType": "BTFeatureDefinitionCall-1406", "feature": feature},
        )
        if resp.status_code >= 400:
            print(resp.text[:1000], file=sys.stderr)
            return 1
        data = resp.json()
        added = data.get("feature") or {}
        feature_id = added.get("featureId")
        status = (data.get("featureState") or {}).get("featureStatus", "UNKNOWN")
        out = {
            "featureId": feature_id,
            "name": added.get("name"),
            "featureType": added.get("featureType"),
            "namespace": added.get("namespace"),
            "status": status,
            "sourceMicroversion": data.get("sourceMicroversion"),
            "url": f"{BASE}/documents/{did}/w/{wid}/e/{eid}",
        }
        if status not in ("OK", "INFO"):
            # `BTFeatureState` carries the status but not the message text, and no endpoint
            # exposes it — the wording only exists in the UI's feature list.
            out["hint"] = (
                "the feature was added but did not regenerate; open the URL to read the error, "
                "or re-run with parameters that produce valid geometry"
            )
        print(json.dumps(out, indent=2))
        # A feature that regenerates with an error is still in the list; say so rather than
        # letting a zero exit read as working geometry.
        return 0 if status in ("OK", "INFO") else 1


if __name__ == "__main__":
    sys.exit(main())
