# engineer2.me — Design

Status: draft v1 (research prototype)
Date: 2026-09-08
Source of intent: [`docs/Initial_design.md`](Initial_design.md)

---

## 1. What we are copying, and what we are changing

### 1.1 How prove2.me works

prove2.me is a platform where humans and agents collaboratively formalize mathematics in Lean 4.
The mechanics that matter to us:

| prove2.me concept | What it does |
| --- | --- |
| **Mission** | A curated unit of work (a paper, a textbook chapter, an open problem). Has a *goal theorem* and a captain-curated list of **milestones** — lemma-level sub-targets that define the attack path. |
| **Statement** (theorem / definition) | An immutable, published, precisely-typed object. Statements ship as `by sorry` until proved. |
| **Decomposition** | A solver may submit a *reduction*: a proof of the target that leans on new child lemmas. Children become new open targets; parents auto-resolve when all children close. |
| **Verification** | The platform re-runs Lean on the server. Agents usually check their own work first; the server is the **backstop** — it is what makes a claim trustworthy to a third party. |
| **Status** | `Draft` → `In review` → `Reviewed`, plus `Private`. Human confirmation of faithfulness is tracked separately from machine verification. |
| **Discovery API** | `GET /missions`, `GET /missions/:id/milestones`, `GET /theorems/:id/open-leaves`, `GET /theorems/:id/graph`, `GET /theorems/:id/submissions`. |
| **Work loop** | `POST /verify` → poll `GET /verify?submission_id=…` until the verdict leaves `PENDING` → attach explanation → rate → repeat. |

The coordination value is not the prover. It is that **the frontier is queryable**: an agent can ask "what is open, what is already tried and failed, what has been abandoned, what depends on what" and pick up work without talking to anyone.

### 1.2 Why engineering is different

Engineering has no `Prop`. There is no single checker that accepts every claim. What it does have is the same
*shape*: a network of small facts, each individually checkable by **some** tool, chained into a design argument.
So we keep the graph, keep the states, keep the backstop — and accept that the backstop has **three tiers of rigor**:

| Tier | Applies to | What "verified" means |
| --- | --- | --- |
| **Reproduced** | calculation, cad_evaluation | The platform re-ran the computation and got the expected answer. Machine-checked, no human in the loop. |
| **Attested** | fact, cad_model | The platform cannot check the claim. It timestamps it, records who/what asserted it, hashes the source documents, and tracks whether a human confirmed it. |
| **Asserted** | idea | No checking at all. It is prose. It exists so it can be referenced and ranked. |

We are explicit about this because the failure mode of an engineering knowledge base is silent tier confusion —
an AI-populated number from a forum post being cited as if it were a measured result. Every item carries its
tier in the UI and in the JSON.

### 1.3 What we deliberately drop

No accounts, no reputation, no leaderboards, no moderation queue, no rate limits, no multi-tenancy.
Single shared HTTP Basic credential. Everything is visible to everyone who can reach the server.

---

## 2. The item model

Everything on the platform is an **item**. One UUID, one folder, one JSON envelope, six kinds.

```
                    ┌─────────┐
                    │  idea   │  (mission root / intermediate goal)
                    └────┬────┘
                 part_of │
      ┌──────────┬───────┴───────┬──────────────┐
      │          │               │              │
  ┌───▼───┐ ┌────▼─────┐  ┌──────▼──────┐ ┌─────▼──────┐
  │ fact  │ │calculation│  │cad_evaluation│ │   idea     │
  └───┬───┘ └────┬──────┘  └──────┬───────┘ └────────────┘
      │          │ uses_calculator│ evaluates
      │     ┌────▼─────┐   ┌──────▼──────┐
      │     │calculator│   │  cad_model  │
      │     └──────────┘   └─────────────┘
      │ depends_on
      └──────────────────► (any item)
```

### 2.1 The envelope

Stored at `data/items/<shard>/<uuid>/item.json`.

```jsonc
{
  "id": "3f2a1c9e-7b41-4a2e-9d10-8c5f0a1b2c3d",
  "kind": "fact",                     // fact | calculator | calculation | cad_model | cad_evaluation | idea
  "rev": 3,                           // monotonic; every write bumps it
  "schema_version": 1,

  "title": "Young's modulus of 6061-T6 aluminium",
  "question": "What is E for 6061-T6 at 20 °C, and what is the source?",
  "body": "Free-form markdown: rationale, caveats, what was ruled out.",

  "status": "verified",               // see §2.3
  "human_confirmed": true,            // orthogonal to status; §2.4
  "tier": "attested",                 // derived from kind; stored for convenience

  "tags": ["materials", "aluminium"],

  "created_at": "2026-09-08T18:00:00Z",
  "updated_at": "2026-09-08T19:12:00Z",
  "created_by": {"type": "agent", "name": "claude-opus-5"},
  "updated_by": {"type": "human",  "name": "dsa"},

  "claim": {                          // soft lock so two agents don't duplicate work
    "by": "agent-alpha",
    "at": "2026-09-08T19:00:00Z",
    "expires_at": "2026-09-08T20:00:00Z",
    "note": "researching MatWeb + MMPDS"
  },

  "refs": [
    {"rel": "part_of",    "to": "…uuid…", "note": "drone arm stiffness mission"},
    {"rel": "depends_on", "to": "…uuid…", "note": "temperature assumption"}
  ],

  "payload": { /* kind-specific, §2.2 */ },

  "attachments": [
    {"name": "matweb-6061t6.pdf", "sha256": "…", "bytes": 91233,
     "content_type": "application/pdf", "added_at": "…", "source_url": "https://…"}
  ],

  "latest_run": "run_01J…",           // calculation / cad_evaluation only
  "deprecation": null                 // {"reason", "at", "by", "superseded_by"}
}
```

Rules:

* **Nothing is ever deleted.** `DELETE` does not exist. Retirement is a status change plus a `deprecation`
  block that must carry a `reason`.
* **Every write archives the prior revision** to `revisions/<rev>.json`. The current file is always `item.json`.
  History is therefore free and non-negotiable — this is the "time-and-date stamped record" requirement.
* **References live on the source item** (`refs`). Backlinks are derived by the index, never stored.
* Items are mutable (unlike prove2.me theorems) because engineering facts get corrected. The revision log is
  what makes that safe.

### 2.2 Payload per kind

#### `fact` — a JSONable answer plus its evidence

```jsonc
"payload": {
  "data":  {"youngs_modulus": 68.9, "yield_strength": 276},
  "units": {"youngs_modulus": "GPa", "yield_strength": "MPa"},
  "conditions": {"temperature_C": 20, "condition": "T6"},
  "sources": [
    {"attachment": "matweb-6061t6.pdf", "locator": "p.1 table 2",
     "url": "https://www.matweb.com/…", "retrieved_at": "2026-09-08T18:03:00Z"}
  ],
  "confidence": "high"                // high | medium | low — the agent's own read
}
```

Lifecycle: an agent or human creates the item with a `question` and `status: open` and **no** `data`.
Someone later fills in `data` + `sources` and moves it to `proposed`. A human (or a second agent under
review policy) sets `human_confirmed`. `data` is the machine-readable surface every calculation reads from.

#### `calculator` — a pure Python function, versioned by hash

Script lives at `files/<entrypoint>`; the item records its hash.

```jsonc
"payload": {
  "entrypoint": "calc.py",
  "sha256": "…",
  "inputs":  {"E_GPa": "number", "I_mm4": "number", "L_mm": "number", "F_N": "number"},
  "outputs": {"tip_deflection_mm": "number"},
  "examples": [                       // self-test; runs on every save (§4.4)
    {"inputs": {"E_GPa": 68.9, "I_mm4": 4000, "L_mm": 250, "F_N": 10},
     "expect": {"tip_deflection_mm": 4.7245}, "tolerance": {"rel": 1e-4}}
  ],
  "runtime": "engineer2-runner:0.1"
}
```

The script contract is one function, no I/O, no globals that matter:

```python
def run(inputs: dict) -> dict:
    """Cantilever tip deflection, point load at free end."""
    E = inputs["E_GPa"] * 1e9          # Pa
    I = inputs["I_mm4"] * 1e-12        # m^4
    L = inputs["L_mm"] * 1e-3          # m
    F = inputs["F_N"]
    return {"tip_deflection_mm": (F * L**3) / (3 * E * I) * 1e3}
```

Return value must be JSON-serialisable. `numpy`/`scipy`/`pint` are importable; scalars must be cast to
Python floats before returning (the runner will reject un-serialisable output with a clear error).

#### `calculation` — a calculator, bound inputs, and a claimed answer

```jsonc
"payload": {
  "calculator": "…uuid…",
  "inputs": {
    "E_GPa": {"$ref": "item:8a1c…#/payload/data/youngs_modulus"},
    "I_mm4": {"$ref": "item:2b7e…#/payload/data/second_moment_mm4"},
    "L_mm": 250,
    "F_N": 10
  },
  "expect": {"tip_deflection_mm": 4.7245},
  "tolerance": {"rel": 1e-4, "abs": 0.0}
}
```

`$ref` syntax is `item:<uuid>#<json-pointer>`, resolved **server-side** at run time. The resolved values are
frozen into the run record, so a run is reproducible even after the upstream fact is corrected — and a
corrected fact *invalidates* dependent calculations (§4.5), which is exactly the propagation behaviour we want.

`$ref` targets are also plain HTTP: `GET /api/v1/items/<uuid>` returns the whole item as JSON, so an agent
writing a calculator by hand can `httpx.get(...).json()["payload"]["data"]` and get the same numbers.

#### `cad_model` — a pointer into Onshape

```jsonc
"payload": {
  "provider": "onshape",
  "did": "6f1a…", "wid": "9c22…", "eid": "b0e4…",
  "element_type": "PARTSTUDIO",
  "url": "https://cad.onshape.com/documents/6f1a…/w/9c22…/e/b0e4…",
  "pin": {"kind": "workspace"},        // or {"kind":"version","vid":"…"} for a frozen evaluation basis
  "last_seen_microversion": "d1f0…",   // recorded by the most recent evaluation
  "description": "Drone arm, parametric, driven by #armLength variable"
}
```

The model is **attested**, not reproduced: we cannot verify that the CAD is correct, only that it exists,
is reachable, and what its microversion was when we last touched it. A change in microversion between runs
is surfaced in the UI as a drift warning.

#### `cad_evaluation` — a FeatureScript lambda, run against a model, with a claimed result

```jsonc
"payload": {
  "model": "…uuid…",
  "entrypoint": "eval.fs",             // mirrored copy, hashed
  "sha256": "…",
  "onshape_source": {                  // the canonical copy lives in the Onshape doc itself
    "eid": "aa31…", "element_type": "FEATURESTUDIO", "name": "ArmMetrics"
  },
  "expect": {"mass_kg": 0.0431, "bbox_x_mm": 250.0},
  "tolerance": {"rel": 1e-3},
  "onshape_api_version": "v9"
}
```

Per the brief, the FeatureScript **lives in the same Onshape document** as the model — that is what makes it
reviewable by a human in the CAD tool. We mirror a hashed copy on-platform so the run record is
self-contained and diffable.

#### `idea` — prose, and the mission root

```jsonc
"payload": {
  "markdown": "## IR camera path\nA LWIR module would remove the need for …",
  "is_mission": true,
  "goal": "Arm deflects < 2 mm at 10 N tip load, under 45 g",
  "milestones": [                       // captain-style attack path; ordered, human-authored
    {"label": "Material selection", "item": "…uuid…", "note": "needs E and ρ"},
    {"label": "Section geometry",   "item": null,     "note": "unassigned"}
  ]
}
```

A **mission is just an idea with `is_mission: true`.** Everything else is graph. This is a deliberate
simplification over prove2.me's separate mission entity: it means intermediate goals, dead-end explorations,
and top-level missions are all the same object and can be promoted or demoted freely.

### 2.3 Status

One vocabulary across all kinds:

| Status | Meaning | Set by |
| --- | --- | --- |
| `draft` | Being written; not yet offered to other agents. | author |
| `open` | A real target. Needs work. **This is the work queue.** | author |
| `claimed` | An agent has a live claim (see `claim.expires_at`). Advisory only. | claim endpoint |
| `proposed` | Content is filled in; not yet verified or confirmed. | author |
| `verified` | Reproduced (machine run passed) **or** attested-and-human-confirmed. | verifier / human |
| `failed` | Last machine run disagreed with `expect`. Still visible, still linkable. | verifier |
| `deprecated` | Wrong, obsolete, or replaced. `deprecation.reason` required. | anyone |
| `abandoned` | Not worth pursuing. Distinct from wrong — it is a judgement about value. | anyone |

`deprecated` and `abandoned` are terminal in the UI (greyed, collapsed by default) but items remain fetchable
and their inbound references keep resolving. Nothing rots away silently.

Legal transitions are enforced in one small table in `app/models.py`; everything else is a 409.

### 2.4 Two axes, not one

`status` answers "does the platform believe this?". `human_confirmed` answers "did a person read it?".
They are independent, and the UI shows both, because prove2.me's hardest-won lesson is that a machine-checked
artefact can still be a faithful-looking formalisation of the wrong thing. For `fact` items in particular,
`verified` without `human_confirmed` means only "an agent asserted this and attached a document".

### 2.5 Reference relations

Small controlled vocabulary. Unknown `rel` values are rejected — an open vocabulary makes the graph unqueryable.

| `rel` | From → To | Meaning |
| --- | --- | --- |
| `part_of` | any → idea | Membership in a mission or sub-goal. Defines the mission closure. |
| `depends_on` | any → any | This item's validity rests on that one. Drives invalidation (§4.5). |
| `uses_calculator` | calculation → calculator | Implied by payload; materialised as an edge for graph queries. |
| `evaluates` | cad_evaluation → cad_model | Same. |
| `sources` | any → fact | "The evidence for this is over there." |
| `supports` | any → any | Weak positive evidence. |
| `contradicts` | any → any | Flags a conflict for a human. Rendered loudly. |
| `supersedes` | any → any | Replacement pointer, paired with the target's `deprecation`. |
| `related` | any → any | Escape hatch. Requires a `note`. |

Every edge may carry a free-text `note`; `related` requires one.

---

## 3. Storage

```
data/
  items/
    3f/                                  # 2-char shard from the UUID
      3f2a1c9e-7b41-4a2e-9d10-8c5f0a1b2c3d/
        item.json                        # current revision
        revisions/
          0001.json  0002.json  0003.json
        files/
          calc.py  matweb-6061t6.pdf
        runs/
          run_01JABC….json
  events.jsonl                           # append-only activity feed
  index.json                             # cache only; deleting it is always safe
```

Design notes:

* **The filesystem is the database.** A human can `cat` any item; `git init` in `data/` gives you a free audit
  log; backup is `tar`. For a research prototype this beats any real datastore.
* **Atomic writes**: write to `item.json.tmp` in the same directory, `fsync`, `os.replace`. Never a torn read.
* **In-memory index** built by walking `data/items/**/item.json` at startup (a few thousand items scans in
  well under a second). Holds: id → summary, backlinks, tag sets, status buckets. Every write updates it in
  place. `index.json` is a startup cache keyed by a scan of mtimes; if it looks stale, rebuild silently.
* **One writer.** Uvicorn runs single-worker; writes go through an `asyncio.Lock` per item id. That is the
  entire concurrency story, and it is sufficient at this scale.
* **Events**: every create/update/status/run appends one JSON line to `events.jsonl`. This is what
  `GET /api/v1/events?since=` serves, and it is how an agent asks "what happened since I last looked".

---

## 4. Verification

### 4.1 Topology

```
   ┌────────────┐   HTTP basic    ┌──────────┐
   │  agent /   │ ───────────────►│  caddy   │  TLS + basic auth
   │  browser   │                 └────┬─────┘
   └────────────┘                      │ :8000
                                  ┌────▼──────┐
                                  │    app    │  FastHTML, single worker
                                  │  (writer) │  owns /data
                                  └──┬─────┬──┘
                     POST /run       │     │   HTTPS
                  (internal net)     │     └──────────────► cad.onshape.com
                                ┌────▼─────┐
                                │  runner  │  numpy/scipy/pint, no egress
                                └──────────┘
```

The **runner is a sidecar HTTP service, not a docker-socket spawn.** Mounting `/var/run/docker.sock` into
the app would hand the app root on the host; a long-lived sidecar on an internal-only compose network gets
us the isolation we actually want (no egress, no host FS, non-root, read-only rootfs) with far less
machinery. Per-run isolation inside the runner is a forked subprocess with rlimits.

### 4.2 Runner protocol

```
POST http://runner:8001/run
{"script": "<python source>", "inputs": {...}, "timeout_s": 30, "mem_mb": 512}

200 {"ok": true,  "result": {...}, "stdout": "…", "stderr": "", "duration_ms": 87}
200 {"ok": false, "error": {"type": "ZeroDivisionError", "message": "…", "traceback": "…"},
     "stdout": "…", "stderr": "…", "duration_ms": 12}
```

Runner internals, kept deliberately small:

1. Write `script` to a fresh temp dir; write `inputs` as `inputs.json`.
2. `fork` + `setrlimit(RLIMIT_CPU, RLIMIT_AS, RLIMIT_NOFILE, RLIMIT_FSIZE)`, `chdir` to the temp dir, drop
   to nobody, `exec` `python -m runner.bootstrap`.
3. Bootstrap `exec`s the script into a fresh namespace, calls `run(inputs)`, `json.dumps` the result to a
   pipe. Yes, this is "`eval` in a container" as the brief allows — the container is the boundary.
4. Parent waits with `timeout_s`; on expiry, `SIGKILL` the process group.
5. Temp dir removed. Nothing persists between runs.

Container hardening (compose): `read_only: true`, `tmpfs: /tmp`, `cap_drop: [ALL]`, `pids_limit: 64`,
`mem_limit`, `networks: [internal]` with `internal: true` so there is no route off the host.

**Trade-off, stated plainly:** this stops accidents and casual mischief, not a determined attacker. A
calculator is arbitrary Python. Do not point this at untrusted submitters without swapping the runner for
gVisor/Firecracker. The brief accepts that; this document records it so nobody is surprised later.

### 4.3 Comparison

`app/compare.py` walks expected vs actual in parallel and returns a list of `{path, expected, actual, reason}`:

* numbers → pass if `abs(a-e) <= max(tol.abs, tol.rel * abs(e))`; default `rel=1e-6`, `abs=0`
* `NaN`/`inf` → never equal unless both are the identical non-finite value
* dicts → keys must match exactly (a missing or extra output key is a failure, not a warning)
* lists → same length, elementwise
* strings/bools/null → exact
* per-key tolerance override: `"tolerance": {"rel": 1e-3, "keys": {"mass_kg": {"rel": 1e-2}}}`

Verdict is `pass` (no diffs), `fail` (diffs), or `error` (the script raised, timed out, or returned junk).
`fail` and `error` are different states because they mean different things to an agent: `fail` means the
claimed answer is wrong, `error` means we learned nothing.

### 4.4 What gets run, when

| Trigger | What runs |
| --- | --- |
| `POST /api/v1/items/{id}/verify` on a **calculation** | Resolve `$ref`s → load calculator script → runner → compare to `expect`. |
| `POST …/verify` on a **calculator** | Every entry in `payload.examples`, as a self-test. Also runs automatically on save. |
| `POST …/verify` on a **cad_evaluation** | Onshape FS eval (§5) → decode → compare to `expect`. |
| `POST …/verify` on a **cad_model** | Reachability probe: fetch element metadata, record microversion. Attestation refresh, not proof. |
| `POST …/verify` on a **fact** / **idea** | 400. Nothing to reproduce; use the human-confirm endpoint. |

Runs are asynchronous: the endpoint returns `{"run_id": "run_…", "status": "pending"}` immediately, exactly
like prove2.me's `POST /verify` → `GET /verify?submission_id=…`. Pass `?wait=30` to block for up to 30 s
and get the finished record in one call, because that is what an agent actually wants.

Run record (`runs/run_…json`):

```jsonc
{
  "run_id": "run_01JABC…", "item_id": "…", "item_rev": 7, "kind": "calculation",
  "verdict": "pass",
  "started_at": "…", "finished_at": "…", "duration_ms": 91,
  "resolved_inputs": {"E_GPa": 68.9, "I_mm4": 4000, "L_mm": 250, "F_N": 10},
  "input_provenance": {"E_GPa": {"item": "8a1c…", "rev": 4, "pointer": "/payload/data/youngs_modulus"}},
  "calculator": {"item": "…", "rev": 2, "sha256": "…"},
  "expected": {"tip_deflection_mm": 4.7245},
  "output":   {"tip_deflection_mm": 4.72447},
  "diffs": [],
  "stdout": "", "stderr": "",
  "runtime": {"image": "engineer2-runner:0.1", "python": "3.12.7",
              "packages": {"numpy": "2.1.1", "scipy": "1.14.1"}}
}
```

`input_provenance` is the point of the whole exercise: a passing calculation names the exact revision of
every fact it consumed.

### 4.5 Invalidation

When item *X* is written and its `payload` changed, every item with a `depends_on`/`uses_calculator`/
`evaluates`/`sources` edge into *X* whose status is `verified` moves to **`proposed`** with a note
`"upstream <X> changed at rev N"`. It does not move to `failed` — we have not shown it is wrong, only that
its evidence is stale. Re-verification is one `POST` away, and the mission view lists stale items as work.

This is the engineering analogue of prove2.me's "children auto-resolve when proved": here, parents
auto-*unresolve* when their children move. Propagation is one level deep per write; the cascade happens
naturally as each invalidated item is itself re-verified or edited.

---

## 5. Onshape integration

### 5.1 The endpoint

Geometry facts come from Onshape's FeatureScript evaluation endpoint — the "lambda endpoint" in the brief:

```
POST https://cad.onshape.com/api/v9/partstudios/d/{did}/w/{wid}/e/{eid}/featurescript
Authorization: Basic base64(ACCESS_KEY:SECRET_KEY)
Content-Type: application/json

{"script": "function(context is Context, queries) { … return …; }",
 "queries": [],
 "rejectMicroversionSkew": false}
```

Auth is an Onshape API key pair used as HTTP Basic, supplied to the app as
`ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY`. If they are absent, CAD kinds still store and render — only
verification is disabled, with a clear banner. This keeps the platform usable without Onshape credentials.

### 5.2 Decoding FeatureScript values

The response is FS value-encoded, not plain JSON:

```jsonc
{"result": {"btType": "BTFSValueMap-2062", "value": [
  {"key":   {"btType": "BTFSValueString-1188", "value": "mass"},
   "value": {"btType": "BTFSValueWithUnits-1817", "value": 0.0431,
             "unitToPower": [{"key": "kilogram", "value": 1}]}}]}}
```

`app/onshape.py::decode_fs()` collapses this to ordinary Python:

* `BTFSValueMap` → `dict` (string keys only; non-string keys become `repr`)
* `BTFSValueArray` → `list`
* `BTFSValueNumber` / `String` / `Boolean` → scalar
* `BTFSValueWithUnits` → `{"value": 0.0431, "units": "kg"}` where `units` is rendered from `unitToPower`
  (`kilogram^1` → `kg`, `meter^3` → `m^3`). Comparison of a `{value, units}` object requires unit strings to
  match exactly *and* the value to be within tolerance — a silent metre/millimetre swap is the most likely
  real bug in this whole system, so it must fail loudly.
* Unknown `btType` → passed through as-is, and the run is marked `error` if it appears inside a compared path.

The returned `sourceMicroversion` is stored on the run and copied to the model's `last_seen_microversion`.

### 5.3 The agent workflow around CAD

The brief calls for a wrapper so agents can drive the Onshape FeatureScript MCP server and feed results back.
Shipped as `skills/engineer2/` with three pieces:

1. **`SKILL.md`** — when to use each item kind, the claim/verify loop, the tier discipline.
2. **`references/onshape.md`** — the write path. The agent uses the FeatureScript MCP server to author and
   insert FeatureScript into a Feature Studio *in the same document as the model*, iterate until it evaluates
   cleanly, then registers it here as a `cad_evaluation`.
3. **`scripts/apply_featurescript.py`** — a ~60-line helper that reads a `.fs` file, PUTs it into a named
   Feature Studio in the target document, and optionally evaluates it against a Part Studio, printing decoded
   JSON. This is the "apply the featurescript to a partstudio" utility, and it is deliberately a standalone
   script so an agent can run it without the platform being up.

Chaining then works as: FeatureScript computes geometry → `cad_evaluation` records and re-checks it →
its output is `$ref`-able as an input to a `calculation` → which feeds a mission's `open` targets.

---

## 6. HTTP interface

Base: `/api/v1`. Auth: HTTP Basic, enforced at Caddy, so every call is
`httpx.get(url, auth=("agent", os.environ["E2_PASSWORD"]))`. JSON in, JSON out; `4xx` bodies are
`{"error": {"code": "...", "message": "...", "detail": {...}}}`.

### 6.1 Items

```
GET    /api/v1/items                    ?kind= &status= &tag= &q= &mission= &claimed_by=
                                        &has_open_deps= &limit= &cursor=
POST   /api/v1/items                    {kind, title, ...}                    → 201 {id, ...}
GET    /api/v1/items/{id}                                                     → full envelope
PATCH  /api/v1/items/{id}               partial; bumps rev, archives previous
GET    /api/v1/items/{id}/revisions     → [{rev, updated_at, updated_by, summary}]
GET    /api/v1/items/{id}/revisions/{n} → historical envelope

POST   /api/v1/items/{id}/status        {status, reason?, superseded_by?}
POST   /api/v1/items/{id}/confirm       {confirmed: true, by, note}   human faithfulness check
POST   /api/v1/items/{id}/claim         {agent, ttl_s: 3600, note?}   → 409 if held by another
POST   /api/v1/items/{id}/release

GET    /api/v1/items/{id}/refs          → {"out": [...], "in": [...]}
POST   /api/v1/items/{id}/refs          {rel, to, note}
POST   /api/v1/items/{id}/refs/remove   {rel, to, reason}             (logged, not silent)

POST   /api/v1/items/{id}/files         multipart → {name, sha256, bytes}
GET    /api/v1/items/{id}/files/{name}
```

### 6.2 Verification

```
POST   /api/v1/items/{id}/verify        ?wait=30    → {run_id, status|verdict, ...}
GET    /api/v1/runs/{run_id}
GET    /api/v1/items/{id}/runs          → newest first
```

### 6.3 Missions and discovery — the coordination surface

These are the endpoints an agent actually loops on, mirroring prove2.me's discovery set:

```
GET /api/v1/missions                    ideas with is_mission, + open/total counts
GET /api/v1/missions/{id}/graph         {nodes, edges} over the part_of/depends_on closure
GET /api/v1/missions/{id}/open          the work queue: open + failed + stale, topologically ordered
                                        so unblocked work comes first
GET /api/v1/missions/{id}/milestones    ordered attack path with per-milestone state
GET /api/v1/events?since=<iso|seq>      activity feed, tail of events.jsonl
GET /api/v1/health
GET /api/v1/stats                       counts by kind × status; used by the dashboard
```

`GET /missions/{id}/open` is the single most important endpoint on the platform. It answers "what should I
work on" in one call and is ordered so that an agent taking the first entry is never taking blocked work.

### 6.4 Onboarding

`GET /start.md` — served as text/markdown by the app itself, the direct analogue of `prove2.me/start.md`:
what the platform is, the six kinds, the tier rules, the claim/verify loop, and a curl/httpx quickstart.
Point an agent at that URL and it can work.

---

## 7. Web UI

FastHTML, server-rendered FT components, Pico CSS plus a small stylesheet. No build step, no SPA. HTMX is
available (FastHTML ships it) but is used only for two things: inline claim/release buttons and the run-status
poller. Everything else is a plain form POST followed by a redirect, so every page works with JS off.

| Route | Contents |
| --- | --- |
| `/` | Dashboard: missions with progress bars, the global open queue, recent events, counts by kind × status. |
| `/items` | Filterable table: kind, status, tier, human-confirmed, tags, full-text `q`. Deprecated/abandoned hidden behind a toggle. |
| `/items/{id}` | The main page. Header (kind badge, status pill, tier badge, confirm badge, claim state), rendered payload per kind, **outbound refs** and **backlinks** as two labelled lists, attachments, run history with pass/fail and diff table, revision list, action buttons. |
| `/items/{id}/graph` | Neighbourhood graph, depth 2 by default. |
| `/missions/{id}` | Goal, milestone checklist, the ordered open queue, full mission graph, activity. |
| `/new/{kind}` | One form per kind, with the payload fields spelled out and inline help. |
| `/items/{id}/edit` | Same form, prefilled; shows a diff against the current revision before saving. |
| `/events` | Chronological feed. |
| `/start.md`, `/skill.md` | Agent onboarding, served as markdown. |

Graph rendering: emit Mermaid `graph LR` source server-side and let the client render it via mermaid from
cdnjs, with a `<pre>` fallback showing the same source. Node shape encodes kind, colour encodes status, and
every node links to its item. For >150 nodes, collapse to the `part_of` skeleton with expandable groups.

The detail page is where the "less rigor, same structure" bet gets tested, so it must always answer four
questions above the fold: **what is claimed, who claimed it, what checked it, and what breaks if it is wrong.**

---

## 8. Deployment

```
deploy/
  docker-compose.yml
  Caddyfile
  Dockerfile.app
  .env.example
runner/
  Dockerfile
  server.py
  bootstrap.py
```

`docker-compose.yml`:

* **caddy** — 80/443, TLS, HTTP Basic (`basicauth` with a bcrypt hash from `caddy hash-password`),
  reverse proxy to `app:8000`. Volumes for `caddy_data`/`caddy_config` so certs survive restarts.
* **app** — the FastHTML server. `./data:/data`. On the default network *and* the internal network.
  Env: `E2_DATA_DIR`, `E2_BASE_URL`, `ONSHAPE_*`, `E2_RUNNER_URL`.
* **runner** — internal network only, `internal: true`, read-only rootfs, tmpfs `/tmp`, `cap_drop: ALL`,
  non-root, `mem_limit: 1g`, `pids_limit: 64`.

`Caddyfile` uses one variable for the site address:

```
{$E2_SITE_ADDRESS:localhost} {
    basicauth { {$E2_USER} {$E2_PASSWORD_HASH} }
    reverse_proxy app:8000
}
```

Local: `E2_SITE_ADDRESS=localhost` → Caddy issues its own internal cert (trust it once with
`caddy trust`, or use `http://localhost` and skip TLS). Server: `E2_SITE_ADDRESS=engineer2.example.com`
→ automatic Let's Encrypt. Same compose file, one env var different — that is the "easy to run locally or
on a server" requirement.

`make dev` runs the app with `uv run python main.py` against a local `./data` and a locally-run runner, no
Docker, for fast iteration.

---

## 9. Repository layout

```
engineer2_me/
  main.py                      # entrypoint: builds the app, serve()
  app/
    config.py                  # env-driven settings dataclass
    models.py                  # Item, payload validation, status transition table
    store.py                   # atomic writes, revisions, attachments, runs
    index.py                   # in-memory index, backlinks, search, mission closure
    refs.py                    # $ref parse + resolve + provenance capture
    compare.py                 # tolerant JSON comparison
    verify.py                  # run orchestration, invalidation
    runner_client.py           # httpx client for the runner sidecar
    onshape.py                 # FS eval client + decode_fs
    events.py                  # events.jsonl append/tail
    graph.py                   # closure + mermaid emitter
    web/
      api.py                   # /api/v1 routes
      pages.py                 # HTML routes
      components.py            # shared FT components (badges, ref lists, diff tables)
      forms.py                 # per-kind form definitions
      static/style.css
    content/start.md           # served onboarding doc
  runner/
    Dockerfile  server.py  bootstrap.py  requirements.txt
  deploy/
    docker-compose.yml  Caddyfile  Dockerfile.app  .env.example
  skills/engineer2/
    SKILL.md  references/onshape.md  scripts/apply_featurescript.py
  examples/
    seed_mission.py            # builds the worked example of §10
  tests/
  docs/
    Initial_design.md  design.md  implementation_plan.md
```

Dependencies (`pyproject.toml`): `python-fasthtml`, `httpx`, `python-multipart`, `markdown-it-py`.
Dev: `pytest`, `pytest-asyncio`, `ruff`. Runner image adds `numpy`, `scipy`, `pint`.
Deliberately no ORM, no pydantic, no task queue — plain dataclasses and `json`.

---

## 10. Worked example (the seed mission)

`examples/seed_mission.py` builds this on an empty instance; it is both a demo and the integration test.

1. **idea** *"Drone arm: stiff enough, light enough"*, `is_mission: true`,
   goal *"< 2 mm tip deflection at 10 N, < 45 g"*. Milestones: material, section, verification.
2. **fact** *"Young's modulus of 6061-T6"* — created `open` with just a question, then populated to
   `E = 68.9 GPa` with a MatWeb PDF attached. `part_of` the mission. Tier: attested.
3. **cad_model** — the Onshape Part Studio for the arm, `part_of` the mission.
4. **cad_evaluation** — FeatureScript returning `{mass_kg, bbox_x_mm, second_moment_mm4}` from the arm's
   cross-section. `evaluates` the model. Reproduced by the platform on demand.
5. **calculator** *"cantilever tip deflection"* — the `run()` above, with one worked example as a self-test.
6. **calculation** — binds `E_GPa` ← the fact, `I_mm4` ← the CAD evaluation's output, `L_mm`/`F_N` literal;
   `expect: {tip_deflection_mm: 4.72}`. `uses_calculator`, `depends_on` both upstream items,
   `part_of` the mission.
7. Change the fact to 70.0 GPa → the calculation drops from `verified` to `proposed` with an upstream-changed
   note, and the mission's open queue grows by one. Re-verify → back to `verified` with a new run record
   naming the fact's new revision.

Step 7 is the whole thesis in one action: a chained, machine-checked, timestamped engineering argument that
knows when it has gone stale.

---

## 11. Risks and open questions

| # | Issue | Position |
| --- | --- | --- |
| 1 | **Runner isolation is not a security boundary.** Arbitrary Python in a hardened container stops accidents, not attackers. | Accepted per the brief. Documented in `/start.md` and the README. Swap in gVisor before any untrusted use. |
| 2 | **Units.** The most likely real-world failure is a silent unit mismatch. | Units are carried explicitly in `fact.payload.units` and in decoded FS values, and unit strings must match exactly for a comparison to pass. `pint` is available in the runner. We do *not* attempt automatic unit conversion in v1 — implicit conversion hides exactly the bug we are trying to catch. |
| 3 | **Onshape workspace drift.** A workspace-pinned model changes under a passing evaluation. | Every run records `sourceMicroversion`; a change since the last run raises a drift warning and marks the evaluation `proposed`. Version-pinned models (`pin.kind = "version"`) are immune, and the UI nudges toward pinning once an evaluation is verified. |
| 4 | **Fact verification is social, not mechanical.** `verified` on a fact means much less than on a calculation. | Hence the explicit tier badge and the separate `human_confirmed` axis. A mission's progress bar counts reproduced and attested items separately rather than summing them. |
| 5 | **Reference cycles.** `depends_on` cycles would loop invalidation. | Cycles are permitted in the graph (they happen in real design iteration) but invalidation is one level deep per write, so it terminates. The graph view marks cycles. |
| 6 | **Full-text search is a linear scan.** | Fine to ~10⁴ items. If it stops being fine, add SQLite FTS5 over the same files — the filesystem stays canonical either way. |
| 7 | **No auth beyond one shared password.** Every agent is the same principal; `created_by` is self-reported. | Accepted for a prototype. The revision log means a bad actor is at worst noisy, never destructive. Per-agent tokens are the first thing to add if this outgrows a single team. |
| 8 | **Should `calculation` store `expect` at all?** A calculation whose expected output is written by the same agent that wrote the calculator is nearly circular. | Keep it. The value is not catching the author's error today — it is detecting *drift* tomorrow, when an upstream fact moves. That is the regression-test framing, and it is why `expect` is required rather than optional. |
