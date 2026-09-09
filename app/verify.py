"""Run orchestration and invalidation.

A verify does four things, in this order: resolve inputs (capturing provenance), execute
(runner or Onshape), compare against the item's claimed answer, and write an immutable run
record. The run record is the product — it names the exact revision of every fact consumed.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from app.compare import compare
from app.config import Settings
from app.events import EventLog
from app.index import Index
from app.models import (
    INVALIDATING_RELS,
    Actor,
    Item,
    now_iso,
)
from app.onshape import NotConfigured, OnshapeClient, OnshapeError, has_undecoded
from app.refs import RefError, resolve_inputs
from app.runner_client import RunnerClient
from app.store import NotFound, Store

VERIFIABLE = ("calculation", "calculator", "cad_evaluation", "cad_model")

PLATFORM = Actor(type="agent", name="engineer2.me")


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:20]


class Verifier:
    def __init__(
        self,
        store: Store,
        index: Index,
        events: EventLog,
        settings: Settings,
        runner: RunnerClient | None = None,
        onshape: OnshapeClient | None = None,
    ):
        self.store = store
        self.index = index
        self.events = events
        self.settings = settings
        self.runner = runner or RunnerClient(settings.runner_url, settings.run_timeout_s, settings.run_mem_mb)
        self.onshape = onshape or OnshapeClient(
            settings.onshape_access_key,
            settings.onshape_secret_key,
            settings.onshape_base,
            settings.onshape_api_version,
        )

    # -- entry point ------------------------------------------------------

    async def verify(self, item: Item, by: Actor | None = None, run_id: str | None = None) -> dict[str, Any]:
        by = by or PLATFORM
        if item.kind not in VERIFIABLE:
            raise ValueError(
                f"{item.kind} items cannot be reproduced — they are {item.tier}. "
                "Use POST /api/v1/items/{id}/confirm to record a human check instead."
            )
        started = time.time()
        run: dict[str, Any] = {
            "run_id": run_id or new_run_id(),
            "item_id": item.id,
            "item_rev": item.rev,
            "kind": item.kind,
            "verdict": "pending",
            "started_at": now_iso(),
            "by": by.name,
        }
        # Write the record before running, so a caller that got a run_id back from a
        # non-blocking POST can poll it immediately instead of racing a 404.
        self.store.write_run(item.id, run)
        run["verdict"] = "error"
        handler = {
            "calculation": self._run_calculation,
            "calculator": self._run_calculator,
            "cad_evaluation": self._run_cad_evaluation,
            "cad_model": self._run_cad_model,
        }[item.kind]
        await handler(item, run)
        run["finished_at"] = now_iso()
        run.setdefault("duration_ms", int((time.time() - started) * 1000))

        self.store.write_run(item.id, run)
        item.latest_run = run["run_id"]
        self._apply_verdict(item, run)
        self.store.write(item, by, event="run", verdict=run["verdict"], run_id=run["run_id"])
        self.index.update_on_write(item)
        self.events.append(
            "status", item.id, by.name, status=item.status, run_id=run["run_id"], verdict=run["verdict"]
        )
        if run.get("drift"):
            # The geometry moved under us: evaluations of this model are stale until re-run.
            self.invalidate_dependents(item, reason=run["attestation"])
        return run

    def _apply_verdict(self, item: Item, run: dict[str, Any]) -> None:
        """pass → verified, fail → failed, error → status untouched.

        An error means the run told us nothing about the claim (the script raised, the runner
        is down, Onshape is unreachable), so it must not move the item in either direction.
        """
        if item.status in ("deprecated", "abandoned"):
            return
        if run["verdict"] == "pass":
            item.status = "verified"
        elif run["verdict"] == "fail":
            item.status = "failed"

    # -- per-kind ---------------------------------------------------------

    def _script_of(self, item: Item) -> tuple[str, str]:
        entry = item.payload.get("entrypoint") or ""
        try:
            raw = self.store.read_file(item.id, entry)
        except NotFound as exc:
            raise RefError(f"{item.kind} {item.id} has no script file {entry!r}") from exc
        return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()

    async def _run_calculator(self, item: Item, run: dict[str, Any]) -> None:
        """Self-test: every example must pass, or the calculator cannot be trusted downstream."""
        try:
            script, sha = self._script_of(item)
        except RefError as exc:
            run["error"] = {"type": "MissingScript", "message": str(exc)}
            return
        run["calculator"] = {"item": item.id, "rev": item.rev, "sha256": sha}
        examples = item.payload.get("examples") or []
        if not examples:
            run["verdict"] = "error"
            run["error"] = {
                "type": "NoExamples",
                "message": "a calculator with no examples cannot be self-tested; add payload.examples",
            }
            return
        results = []
        verdict = "pass"
        for i, ex in enumerate(examples):
            out = await self.runner.run(script, ex.get("inputs") or {}, self.settings.run_timeout_s)
            entry = {"index": i, "inputs": ex.get("inputs"), "expected": ex.get("expect")}
            if not out.get("ok"):
                entry["verdict"] = "error"
                entry["error"] = out.get("error")
                verdict = "error"
            else:
                diffs = compare(ex.get("expect") or {}, out.get("result"), ex.get("tolerance"))
                entry["output"] = out.get("result")
                entry["diffs"] = diffs
                entry["verdict"] = "pass" if not diffs else "fail"
                if diffs and verdict != "error":
                    verdict = "fail"
            entry["stdout"] = out.get("stdout", "")
            entry["stderr"] = out.get("stderr", "")
            run.setdefault("runtime", out.get("runtime"))
            results.append(entry)
        run["examples"] = results
        run["verdict"] = verdict
        run["output"] = {"examples_passed": sum(1 for r in results if r["verdict"] == "pass")}
        run["expected"] = {"examples_passed": len(results)}

    async def _run_calculation(self, item: Item, run: dict[str, Any]) -> None:
        payload = item.payload
        try:
            calculator = self.store.get(payload["calculator"])
        except NotFound:
            run["error"] = {
                "type": "MissingCalculator",
                "message": f"calculator {payload.get('calculator')} does not exist",
            }
            return
        if calculator.kind != "calculator":
            run["error"] = {
                "type": "NotACalculator",
                "message": f"payload.calculator points at a {calculator.kind}",
            }
            return
        try:
            script, sha = self._script_of(calculator)
        except RefError as exc:
            run["error"] = {"type": "MissingScript", "message": str(exc)}
            return
        run["calculator"] = {"item": calculator.id, "rev": calculator.rev, "sha256": sha}

        try:
            resolved, provenance = resolve_inputs(self.store, payload.get("inputs") or {})
        except RefError as exc:
            run["error"] = {"type": "RefError", "message": str(exc)}
            return
        run["resolved_inputs"] = resolved
        run["input_provenance"] = provenance
        stale = [k for k, p in provenance.items() if p.get("warning")]
        if stale:
            run["warnings"] = [f"input {k}: {provenance[k]['warning']}" for k in stale]

        out = await self.runner.run(script, resolved, self.settings.run_timeout_s)
        run["stdout"] = out.get("stdout", "")
        run["stderr"] = out.get("stderr", "")
        run["duration_ms"] = out.get("duration_ms", 0)
        run["runtime"] = out.get("runtime")
        run["expected"] = payload.get("expect")
        if not out.get("ok"):
            run["error"] = out.get("error")
            run["verdict"] = "error"
            return
        run["output"] = out.get("result")
        run["diffs"] = compare(payload.get("expect") or {}, out.get("result"), payload.get("tolerance"))
        run["verdict"] = "pass" if not run["diffs"] else "fail"

    async def _run_cad_model(self, item: Item, run: dict[str, Any]) -> None:
        """Reachability probe. This is an attestation refresh, not proof of anything."""
        p = item.payload
        try:
            meta = await self.onshape.element_metadata(p["did"], p["wid"], p["eid"], p.get("pin"))
        except NotConfigured as exc:
            run["error"] = {"type": "NotConfigured", "message": str(exc)}
            return
        except OnshapeError as exc:
            run["error"] = {"type": "OnshapeError", "message": str(exc)}
            return
        previous = p.get("last_seen_microversion")
        micro = meta.get("microversion")
        run["output"] = meta
        run["expected"] = {"reachable": True}
        run["microversion"] = micro
        run["verdict"] = "pass"
        drifted = bool(previous and micro and previous != micro)
        run["drift"] = drifted
        run["attestation"] = f"element reachable at {now_iso()}; microversion {micro}" + (
            f" (drifted from {previous})" if drifted else ""
        )
        if drifted:
            run.setdefault("warnings", []).append(
                f"model drift: microversion moved {previous} → {micro}; evaluations of this model are stale"
            )
        if micro:
            item.payload["last_seen_microversion"] = micro

    async def _run_cad_evaluation(self, item: Item, run: dict[str, Any]) -> None:
        p = item.payload
        try:
            model = self.store.get(p["model"])
        except NotFound:
            run["error"] = {"type": "MissingModel", "message": f"cad_model {p.get('model')} does not exist"}
            return
        try:
            script, sha = self._script_of(item)
        except RefError as exc:
            run["error"] = {"type": "MissingScript", "message": str(exc)}
            return
        run["script"] = {"item": item.id, "rev": item.rev, "sha256": sha}
        run["model"] = {"item": model.id, "rev": model.rev}
        mp = model.payload
        try:
            got = await self.onshape.eval_featurescript(
                mp["did"], mp["wid"], mp["eid"], script, pin=mp.get("pin")
            )
        except NotConfigured as exc:
            run["error"] = {"type": "NotConfigured", "message": str(exc)}
            return
        except OnshapeError as exc:
            run["error"] = {"type": "OnshapeError", "message": str(exc)}
            return

        micro = got.get("source_microversion")
        run["microversion"] = micro
        run["output"] = got["result"]
        run["expected"] = p.get("expect")
        run["notices"] = got.get("notices")
        if got.get("notices"):
            run["warnings"] = [f"{n.get('level')}: {n.get('message')}" for n in got["notices"]]
        if has_undecoded(got["result"]):
            run["verdict"] = "error"
            run["error"] = {
                "type": "UndecodedFeatureScriptValue",
                "message": "the FeatureScript result contains a btType we do not decode; "
                "return plain numbers, strings, maps, arrays or values with units",
            }
            return
        run["diffs"] = compare(p.get("expect") or {}, got["result"], p.get("tolerance"))
        run["verdict"] = "pass" if not run["diffs"] else "fail"

        previous = mp.get("last_seen_microversion")
        if micro and previous and micro != previous:
            run.setdefault("warnings", []).append(
                f"model drift: microversion moved {previous} → {micro} since the last evaluation"
            )
        if micro and micro != previous:
            model.payload["last_seen_microversion"] = micro
            self.store.write(model, PLATFORM, event="updated", note="microversion refreshed")
            self.index.update_on_write(model)

    # -- invalidation -----------------------------------------------------

    def invalidate_dependents(self, item: Item, reason: str) -> list[str]:
        """A changed item makes its verified dependents *stale*, not wrong: verified → proposed.

        One level deep per write; the cascade happens as each invalidated item is itself
        re-verified or edited (design §4.5).
        """
        touched = []
        for dependent_id in self.index.dependents(item.id, INVALIDATING_RELS):
            summary = self.index.summary(dependent_id)
            if not summary or summary["status"] != "verified" or dependent_id == item.id:
                continue
            try:
                dependent = self.store.get(dependent_id)
            except NotFound:
                continue
            dependent.status = "proposed"
            note = f"upstream {item.title or item.id} changed at rev {item.rev}"
            dependent.body = (dependent.body + f"\n\n_Stale: {note}._").strip()
            self.store.write(dependent, PLATFORM, event="invalidated", reason=reason, upstream=item.id)
            self.index.update_on_write(dependent)
            self.events.append("status", dependent_id, PLATFORM.name, status="proposed", reason=note)
            touched.append(dependent_id)
        return touched
