#!/usr/bin/env python3
"""Push a .fs file into a Feature Studio in an Onshape document, and optionally evaluate it.

Standalone: needs only httpx and ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY. It does not need
engineer2.me to be running, so an agent can iterate on FeatureScript before registering anything.

    python apply_featurescript.py --url "https://cad.onshape.com/documents/DID/w/WID/e/EID" \
        --file eval.fs --studio ArmMetrics --evaluate

--evaluate runs the script against the Part Studio named by the URL's element id and prints the
decoded result as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

BASE = os.environ.get("E2_ONSHAPE_BASE", "https://cad.onshape.com")
API = os.environ.get("E2_ONSHAPE_API_VERSION", "v9")

UNIT_ABBREV = {
    "meter": "m",
    "millimeter": "mm",
    "kilogram": "kg",
    "second": "s",
    "radian": "rad",
    "degree": "deg",
    "newton": "N",
    "pascal": "Pa",
}


def decode_fs(value):
    """Collapse Onshape's FS value encoding into ordinary Python (mirrors app/onshape.py)."""
    if isinstance(value, list):
        return [decode_fs(v) for v in value]
    if not isinstance(value, dict):
        return value
    # Onshape returns value objects fully qualified and map entries bare; match the tail.
    bt = value.get("btType", "").rsplit(".", 1)[-1]
    if bt.startswith("BTFSValueMap") and not bt.startswith("BTFSValueMapEntry"):
        out = {}
        for pair in value.get("value", []):
            key = decode_fs(pair.get("key"))
            out[key if isinstance(key, str) else repr(key)] = decode_fs(pair.get("value"))
        return out
    if bt.startswith("BTFSValueArray"):
        return [decode_fs(v) for v in value.get("value", [])]
    if bt.startswith("BTFSValueWithUnits"):
        units = "*".join(
            UNIT_ABBREV.get(u["key"], u["key"]) + ("" if u.get("value") == 1 else f"^{int(u['value'])}")
            for u in sorted(value.get("unitToPower") or [], key=lambda u: -u.get("value", 0))
        )
        return {"value": value.get("value"), "units": units}
    if bt.startswith(("BTFSValueNumber", "BTFSValueString", "BTFSValueBoolean")):
        return value.get("value")
    return value


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
    return httpx.Client(auth=(key, secret), timeout=120, base_url=f"{BASE}/api/{API}")


def find_or_create_studio(c: httpx.Client, did: str, wid: str, name: str) -> str:
    elements = c.get(f"/documents/d/{did}/w/{wid}/elements").raise_for_status().json()
    for el in elements:
        if el.get("name") == name and el.get("elementType") == "FEATURESTUDIO":
            return el["id"]
    created = c.post(f"/featurestudios/d/{did}/w/{wid}", json={"name": name}).raise_for_status().json()
    return created["id"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="Onshape Part Studio URL")
    ap.add_argument("--file", required=True, help="path to the .fs file")
    ap.add_argument("--studio", default="AgentMetrics", help="Feature Studio name to write into")
    ap.add_argument(
        "--evaluate", action="store_true", help="also evaluate the script against the Part Studio"
    )
    args = ap.parse_args()

    did, wid, eid = parse_url(args.url)
    source = open(args.file, encoding="utf-8").read()

    with client() as c:
        studio_id = find_or_create_studio(c, did, wid, args.studio)
        c.post(
            f"/featurestudios/d/{did}/w/{wid}/e/{studio_id}/contents",
            json={"contents": source},
        ).raise_for_status()
        print(f"wrote {args.file} → Feature Studio {args.studio} ({studio_id})", file=sys.stderr)

        if args.evaluate:
            resp = c.post(
                f"/partstudios/d/{did}/w/{wid}/e/{eid}/featurescript",
                json={"script": source, "queries": {}, "rejectMicroversionSkew": False},
            )
            if resp.status_code >= 400:
                print(resp.text, file=sys.stderr)
                return 1
            data = resp.json()
            notices = [n for n in (data.get("notices") or []) if n.get("level") in ("ERROR", "WARNING")]
            for n in notices:
                print(f"{n.get('level')}: {n.get('message')}", file=sys.stderr)
            print(
                json.dumps(
                    {
                        "result": decode_fs(data.get("result")),
                        "sourceMicroversion": data.get("sourceMicroversion"),
                    },
                    indent=2,
                )
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
