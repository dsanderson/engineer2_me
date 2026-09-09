#!/usr/bin/env python3
"""Build the worked example from docs/design.md §10 over HTTP. Demo and integration test.

    uv run python examples/seed_mission.py --base-url http://localhost:8000

Steps 1-6 build a chained argument: a mission, a material fact, a calculator, and a calculation
that binds the fact by $ref. Step 7 is the thesis in one action — correct the fact, watch the
calculation go stale, re-verify, and see the run record name the fact's new revision.

CAD items (the Onshape model and its FeatureScript evaluation) are created too, but only verified
when ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY and --onshape-url are supplied.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

AGENT = "seed-script"

CALC_SCRIPT = '''def run(inputs: dict) -> dict:
    """Cantilever tip deflection, point load at the free end."""
    E = inputs["E_GPa"] * 1e9          # Pa
    I = inputs["I_mm4"] * 1e-12        # m^4
    L = inputs["L_mm"] * 1e-3          # m
    F = inputs["F_N"]
    return {"tip_deflection_mm": (F * L**3) / (3 * E * I) * 1e3}
'''

ARM_FS = """function(context is Context, queries) {
    var solids = qBodyType(qEverything(EntityType.BODY), BodyType.SOLID);
    var box = evBox3d(context, {"topology": solids});
    return {
        "mass_kg": massProperties(context, solids).mass[0] / kilogram,
        "bbox_x_mm": (box.maxCorner[0] - box.minCorner[0]) / millimeter
    };
}
"""


class Api:
    def __init__(self, base_url: str, password: str):
        self.http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            auth=("agent", password) if password else None,
            headers={"X-Agent": AGENT},
            timeout=90,
        )

    def post(self, path: str, **kw):
        resp = self.http.post(path, **kw)
        if resp.status_code >= 400:
            sys.exit(f"POST {path} → {resp.status_code}: {resp.text}")
        return resp.json()

    def patch(self, path: str, **kw):
        resp = self.http.patch(path, **kw)
        if resp.status_code >= 400:
            sys.exit(f"PATCH {path} → {resp.status_code}: {resp.text}")
        return resp.json()

    def get(self, path: str, **kw):
        resp = self.http.get(path, **kw)
        resp.raise_for_status()
        return resp.json()

    def create(self, **body) -> str:
        body.setdefault("by", AGENT)
        item = self.post("/items", json=body)
        print(f"  {item['kind']:<15} {item['id']}  {item['title']}")
        return item["id"]

    def verify(self, item_id: str, wait: int = 60) -> dict:
        return self.post(f"/items/{item_id}/verify", params={"wait": wait}, json={"by": AGENT})


def parse_onshape_url(url: str) -> dict:
    parts = url.split("?")[0].rstrip("/").split("/")
    out = {"provider": "onshape", "url": url}
    for key, marker in (("did", "documents"), ("wid", "w"), ("eid", "e")):
        if marker in parts:
            out[key] = parts[parts.index(marker) + 1]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("E2_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--password", default=os.environ.get("E2_PASSWORD", ""))
    ap.add_argument("--onshape-url", default=os.environ.get("E2_SEED_ONSHAPE_URL", ""))
    args = ap.parse_args()

    api = Api(args.base_url, args.password)
    print(f"seeding {args.base_url}")

    # 1 — the mission
    print("\n1. mission")
    mission = api.create(
        kind="idea",
        title="Drone arm: stiff enough, light enough",
        body="Carbon is out of budget; this is the aluminium path.",
        tags=["drone", "structures"],
        payload={
            "is_mission": True,
            "goal": "Arm deflects < 2 mm at 10 N tip load, under 45 g",
            "markdown": (
                "## Drone arm\n\nA cantilever arm carrying a motor at the tip. Two things can kill it: "
                "too much tip deflection (props strike) and too much mass (flight time).\n"
            ),
            "milestones": [
                {"label": "Material selection", "note": "needs E and density"},
                {"label": "Section geometry", "note": "unassigned"},
                {"label": "Deflection verified", "note": "the goal"},
            ],
        },
    )

    # 2 — a fact, created as a question and then populated
    print("\n2. fact (open question, then populated)")
    fact = api.create(
        kind="fact",
        title="Young's modulus of 6061-T6 aluminium",
        question="What is E for 6061-T6 at 20 °C, and what is the source?",
        status="open",
        tags=["materials", "aluminium"],
        refs=[{"rel": "part_of", "to": mission, "note": "material selection milestone"}],
    )
    api.post(
        f"/items/{fact}/files",
        files={"file": ("matweb-6061t6.txt", b"6061-T6: E = 68.9 GPa, Sy = 276 MPa\n", "text/plain")},
        data={"by": AGENT, "source_url": "https://www.matweb.com/"},
    )
    api.patch(
        f"/items/{fact}",
        json={
            "status": "proposed",
            "by": AGENT,
            "payload": {
                "data": {"youngs_modulus": 68.9, "yield_strength": 276},
                "units": {"youngs_modulus": "GPa", "yield_strength": "MPa"},
                "conditions": {"temperature_C": 20, "condition": "T6"},
                "sources": [
                    {
                        "attachment": "matweb-6061t6.txt",
                        "locator": "line 1",
                        "url": "https://www.matweb.com/",
                    }
                ],
                "confidence": "medium",
            },
        },
    )
    print("  populated with E = 68.9 GPa and a source document (tier: attested)")

    # 3, 4 — CAD
    print("\n3. cad_model and 4. cad_evaluation")
    cad_payload = (
        parse_onshape_url(args.onshape_url)
        if args.onshape_url
        else {
            "provider": "onshape",
            "did": "0000000000000000000000dd",
            "wid": "0000000000000000000000ww",
            "eid": "0000000000000000000000ee",
            "description": "Placeholder — pass --onshape-url to point at a real Part Studio.",
        }
    )
    cad_payload.setdefault("element_type", "PARTSTUDIO")
    model = api.create(
        kind="cad_model",
        title="Drone arm Part Studio",
        body="Parametric arm; the cross-section is driven by #armWall and #armHeight.",
        refs=[{"rel": "part_of", "to": mission}],
        payload=cad_payload,
    )
    evaluation = api.create(
        kind="cad_evaluation",
        title="Arm cross-section metrics",
        status="proposed",
        question="What are the mass and bounding box of the arm as modelled?",
        refs=[{"rel": "part_of", "to": mission}],
        payload={
            "model": model,
            "script": ARM_FS,
            "onshape_source": {"element_type": "FEATURESTUDIO", "name": "ArmMetrics"},
            "expect": {"mass_kg": 0.0431, "bbox_x_mm": 250.0},
            "tolerance": {"rel": 1e-3},
        },
    )

    # 5 — the calculator
    print("\n5. calculator")
    calculator = api.create(
        kind="calculator",
        title="Cantilever tip deflection",
        question="How much does a cantilever deflect under a point load at the free end?",
        refs=[{"rel": "part_of", "to": mission}],
        payload={
            "script": CALC_SCRIPT,
            "inputs": {"E_GPa": "number", "I_mm4": "number", "L_mm": "number", "F_N": "number"},
            "outputs": {"tip_deflection_mm": "number"},
            "examples": [
                {
                    "inputs": {"E_GPa": 68.9, "I_mm4": 160, "L_mm": 250, "F_N": 10},
                    "expect": {"tip_deflection_mm": 4.7245},
                    "tolerance": {"rel": 1e-4},
                }
            ],
        },
    )
    run = api.verify(calculator)
    print(f"  self-test: {run['verdict']}")

    # 6 — the calculation, binding the fact by $ref
    print("\n6. calculation (binds the fact by $ref)")
    calculation = api.create(
        kind="calculation",
        title="Arm tip deflection at 10 N",
        question="Does the arm meet the < 2 mm deflection goal?",
        status="proposed",
        refs=[
            {"rel": "part_of", "to": mission},
            {"rel": "depends_on", "to": fact, "note": "material stiffness"},
        ],
        payload={
            "calculator": calculator,
            "inputs": {
                "E_GPa": {"$ref": f"item:{fact}#/payload/data/youngs_modulus"},
                "I_mm4": 160,
                "L_mm": 250,
                "F_N": 10,
            },
            "expect": {"tip_deflection_mm": 4.7245},
            "tolerance": {"rel": 1e-3},
        },
    )
    run = api.verify(calculation)
    print(
        f"  verdict: {run['verdict']}  E came from rev {run['input_provenance']['E_GPa']['rev']} of the fact"
    )
    if run["verdict"] != "pass":
        print(f"  (is the runner up? {run.get('error', {}).get('message', '')})")

    # 7 — the thesis: correct the fact, watch the chain go stale
    print("\n7. correct the fact → the calculation goes stale")
    updated = api.patch(
        f"/items/{fact}",
        json={
            "by": AGENT,
            "payload": {
                "data": {"youngs_modulus": 70.0, "yield_strength": 276},
                "units": {"youngs_modulus": "GPa", "yield_strength": "MPa"},
                "conditions": {"temperature_C": 20, "condition": "T6"},
                "sources": [
                    {"attachment": "matweb-6061t6.txt", "locator": "line 1", "url": "https://www.matweb.com/"}
                ],
                "confidence": "high",
            },
        },
    )
    print(f"  fact is now rev {updated['rev']}; invalidated: {updated['invalidated']}")
    print(f"  calculation status: {api.get(f'/items/{calculation}')['status']}")
    run = api.verify(calculation)
    print(f"  re-verified: {run['verdict']} (expect fail — 70 GPa is a stiffer arm than 68.9)")
    print(f"  new run names fact rev {run['input_provenance']['E_GPa']['rev']}")

    if args.onshape_url and os.environ.get("ONSHAPE_ACCESS_KEY"):
        print("\n   verifying CAD against Onshape")
        print(f"   model: {api.verify(model)['verdict']}")
        print(f"   evaluation: {api.verify(evaluation)['verdict']}")

    queue = api.get(f"/missions/{mission}/open")["open"]
    print(f"\nmission board: {args.base_url}/missions/{mission}")
    print(f"open queue ({len(queue)}):")
    for entry in queue:
        print(f"  {entry['status']:<9} {entry['kind']:<15} {entry['title']}  — {entry['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
