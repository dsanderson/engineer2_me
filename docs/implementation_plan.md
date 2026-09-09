# engineer2.me — Implementation Plan

Companion to [`design.md`](design.md). Nine phases, each independently runnable and demoable.
Order is chosen so the thing is useful early: after Phase 3 you have a working knowledge graph in a browser;
verification, CAD, and deployment layer on top without reshaping anything underneath.

Estimates assume one agent working with a human reviewing at phase boundaries.

---

## Phase 0 — Scaffolding

**Goal:** `uv run python main.py` serves a page; tests run; lint is clean.

| # | Task | File(s) |
| --- | --- | --- |
| 0.1 | Add deps: `python-fasthtml`, `httpx`, `python-multipart`, `markdown-it-py`; dev extras `pytest`, `pytest-asyncio`, `ruff` | `pyproject.toml` |
| 0.2 | Settings dataclass read from env with defaults: `E2_DATA_DIR=./data`, `E2_BASE_URL=http://localhost:8000`, `E2_RUNNER_URL=http://localhost:8001`, `E2_ONSHAPE_BASE=https://cad.onshape.com`, `ONSHAPE_ACCESS_KEY`, `ONSHAPE_SECRET_KEY`, `E2_RUN_TIMEOUT_S=30` | `app/config.py` |
| 0.3 | `main.py` builds the FastHTML app and calls `serve()`; `/api/v1/health` returns `{"ok": true, "items": n}` | `main.py`, `app/web/api.py` |
| 0.4 | `Makefile`: `dev`, `test`, `lint`, `fmt`, `up`, `down`, `seed` | `Makefile` |
| 0.5 | `.gitignore` add `data/`, `.env`; `README.md` quickstart stub | `.gitignore`, `README.md` |

**Done when:** `make dev` serves `/api/v1/health`; `make test` and `make lint` pass on an empty suite.

---

## Phase 1 — Core model and store

**Goal:** items can be created, read, revised, referenced and retired — from Python, no HTTP yet.
This is the phase that matters most; everything else is a shell around it.

| # | Task | File(s) |
| --- | --- | --- |
| 1.1 | `Item` dataclass matching the envelope in design §2.1; `to_json`/`from_json`; `Ref`, `Attachment`, `Deprecation`, `Claim` | `app/models.py` |
| 1.2 | Per-kind payload validators (`fact`, `calculator`, `calculation`, `cad_model`, `cad_evaluation`, `idea`). Plain functions returning a list of error strings — no pydantic. Reject unknown payload keys. | `app/models.py` |
| 1.3 | Status transition table + `can_transition(from, to)`; `tier_for(kind)` | `app/models.py` |
| 1.4 | Ref vocabulary constant; validate `rel`, require `note` on `related`, forbid self-refs, forbid refs to unknown ids | `app/models.py` |
| 1.5 | `Store`: `create`, `get`, `update` (archives prior rev to `revisions/<rev>.json`, bumps `rev`), `get_revision`, `list_revisions`. Atomic write helper (`.tmp` + `fsync` + `os.replace`). Shard path from first 2 UUID chars. | `app/store.py` |
| 1.6 | Attachments: `add_file` (streams to `files/`, computes sha256, dedupes by name), `open_file`, `list_files` | `app/store.py` |
| 1.7 | Runs: `write_run`, `get_run`, `list_runs` under `runs/` | `app/store.py` |
| 1.8 | Per-id `asyncio.Lock` registry so concurrent updates serialise | `app/store.py` |
| 1.9 | `events.jsonl` append + tail-since; event types `created`, `updated`, `status`, `claimed`, `released`, `ref_added`, `ref_removed`, `run`, `confirmed` | `app/events.py` |
| 1.10 | `Index`: startup scan of `data/items/**/item.json`, in-memory maps (by id, by kind, by status, by tag, backlinks), `update_on_write`, substring search over title/question/body/tags, `index.json` cache with mtime-based staleness check | `app/index.py` |
| 1.11 | `$ref` parse (`item:<uuid>#<json-pointer>`), resolve against the store, return `(value, provenance)`; clear errors for bad pointer / missing item / deprecated source | `app/refs.py` |

**Tests:** round-trip every kind; revision archiving; atomic write survives a simulated crash mid-write;
backlinks appear and disappear correctly; illegal transitions raise; `$ref` resolution incl. failure modes;
index rebuild from scratch matches incremental index.

**Done when:** a pytest fixture builds a 10-item graph purely through `Store` + `Index` and every query in
design §6.3 can be answered from Python.

---

## Phase 2 — JSON API

**Goal:** every operation available over HTTP to `httpx`.

| # | Task |
| --- | --- |
| 2.1 | Wire `/api/v1` routes onto FastHTML; JSON error envelope helper; `4xx` for validation, `409` for illegal transitions and contested claims |
| 2.2 | `GET/POST /items`, `GET/PATCH /items/{id}`, revisions endpoints |
| 2.3 | `POST /items/{id}/status`, `/confirm`, `/claim`, `/release` — claims expire lazily on read; `409` only if a *live* claim is held by a different agent |
| 2.4 | `GET/POST /items/{id}/refs`, `POST /items/{id}/refs/remove` (logs an event with the reason) |
| 2.5 | `POST/GET /items/{id}/files` (multipart upload, streamed) |
| 2.6 | List filters: `kind`, `status`, `tag`, `q`, `mission`, `claimed_by`, `has_open_deps`; opaque cursor pagination |
| 2.7 | `GET /events`, `GET /stats`, `GET /health` |
| 2.8 | Serve `/start.md` and `/skill.md` from `app/content/` as `text/markdown` |

**Tests:** an httpx-driven end-to-end script that creates the full seed graph over HTTP and asserts every
list filter. Claim contention test with two clients.

**Done when:** `examples/seed_mission.py` can build the entire §10 graph over HTTP (verification steps stubbed).

---

## Phase 3 — Web UI

**Goal:** a human can navigate missions and items, see references both ways, and create items by hand.

| # | Task |
| --- | --- |
| 3.1 | Shared components: page shell, kind badge, status pill, tier badge, human-confirmed badge, claim widget, ref list, backlink list, timestamp/author line | `app/web/components.py` |
| 3.2 | `/items` list with the same filters as the API, `q` search box, deprecated/abandoned collapsed behind a toggle |
| 3.3 | `/items/{id}` detail: per-kind payload rendering (fact → data table with units + source links; calculator → syntax-highlighted script + examples; calculation → resolved inputs vs expected; cad_model → clickable Onshape link + ids; cad_evaluation → script + expected; idea → rendered markdown), refs out/in, attachments, revision list, action buttons |
| 3.4 | `/new/{kind}` and `/items/{id}/edit` forms, one per kind, with inline help text and server-side validation errors rendered next to the field |
| 3.5 | `/` dashboard: missions with progress, global open queue, recent events, kind × status counts |
| 3.6 | `/events` feed |
| 3.7 | `style.css` on top of Pico; status colour scheme consistent between badges and graph nodes; readable in light and dark |

**Done when:** the seed graph is fully navigable in a browser and a new fact can be created, edited,
referenced and deprecated without touching the API.

---

## Phase 4 — Calculators, calculations and the runner

**Goal:** the first tier-1 "reproduced" verdict.

| # | Task | File(s) |
| --- | --- | --- |
| 4.1 | Runner service: `POST /run` per design §4.2; fork + `setrlimit` + timeout + process-group kill; temp dir per run, removed after | `runner/server.py` |
| 4.2 | Bootstrap: `exec` the script into a fresh namespace, call `run(inputs)`, JSON-dump to a pipe; reject non-serialisable output with a named error | `runner/bootstrap.py` |
| 4.3 | Runner image: python 3.12-slim + `numpy`, `scipy`, `pint`; non-root user; pinned versions reported back in `/run` responses | `runner/Dockerfile`, `runner/requirements.txt` |
| 4.4 | `runner_client.py`: httpx client, timeout, connection-error → `verdict: error` with a clear message when the runner is down | `app/runner_client.py` |
| 4.5 | `compare.py` per design §4.3: recursive walk, rel/abs tolerance, per-key overrides, non-finite handling, dict key mismatch as failure, `{value, units}` objects require exact unit match | `app/compare.py` |
| 4.6 | `verify.py`: orchestrate a calculation run — resolve `$ref`s with provenance, load calculator script + hash, call runner, compare, write run record, update `status` and `latest_run`, append event | `app/verify.py` |
| 4.7 | Calculator self-test: run `payload.examples` on save and on `verify`; a calculator with a failing example cannot reach `verified` | `app/verify.py` |
| 4.8 | `POST /items/{id}/verify` with `?wait=`; background task + `GET /runs/{run_id}` polling | `app/web/api.py` |
| 4.9 | Invalidation per design §4.5: on payload change, demote `verified` dependents to `proposed` with a note; emit events | `app/verify.py`, `app/store.py` |
| 4.10 | UI: run history table, pass/fail/error badges, diff table with JSON paths, "Verify now" button with HTMX polling, stale-upstream banner | `app/web/pages.py` |

**Tests:** passing run; failing run with a numeric diff; script raising; infinite loop hitting the timeout;
memory hog hitting `RLIMIT_AS`; non-serialisable return; runner unreachable; invalidation cascade after a
fact edit; tolerance boundary cases.

**Done when:** editing a fact flips a downstream calculation to `proposed`, and one `POST …/verify` returns
it to `verified` with a run record naming the fact's new revision.

---

## Phase 5 — Onshape

**Goal:** geometry becomes a first-class, re-checkable input.

| # | Task | File(s) |
| --- | --- | --- |
| 5.1 | Onshape client: Basic auth from env, `eval_featurescript(did, wid, eid, script)`, element metadata fetch, sensible timeouts, error mapping (401/403/404/429 → readable messages) | `app/onshape.py` |
| 5.2 | `decode_fs()` per design §5.2, incl. `unitToPower` → unit string; unknown `btType` passes through and poisons any compared path | `app/onshape.py` |
| 5.3 | `cad_model` verify = reachability probe: fetch metadata, record `sourceMicroversion` as `last_seen_microversion`, attest | `app/verify.py` |
| 5.4 | `cad_evaluation` verify: eval FS → decode → compare to `expect` → run record incl. microversion; drift warning + demote to `proposed` when the microversion moved since the last passing run | `app/verify.py` |
| 5.5 | Graceful degradation when `ONSHAPE_*` is unset: CAD kinds still store/render, verify returns a clear "credentials not configured" error, UI shows a banner | `app/config.py`, `app/web/pages.py` |
| 5.6 | UI: clickable Onshape document link, ids, microversion with drift indicator, FeatureScript source display | `app/web/pages.py` |
| 5.7 | `scripts/apply_featurescript.py`: read a `.fs` file → PUT into a named Feature Studio in the target document → optionally evaluate against a Part Studio → print decoded JSON. Standalone, no platform dependency. | `skills/engineer2/scripts/` |

**Tests:** `decode_fs` unit tests against recorded fixtures (maps, arrays, nested, units, unknown types);
client tests against a mocked transport; one opt-in live test gated on credentials being present.

**Done when:** a real Part Studio's mass and bounding box are pulled in by a `cad_evaluation`, and that
evaluation's output is consumed as a `$ref` input by a `calculation` that verifies.

---

## Phase 6 — Missions and graph

**Goal:** an agent can ask "what should I work on" and get a straight answer.

| # | Task | File(s) |
| --- | --- | --- |
| 6.1 | Mission closure: BFS over `part_of` + `depends_on` from an idea root, cycle-safe | `app/graph.py` |
| 6.2 | `GET /missions`, `/missions/{id}/graph`, `/missions/{id}/milestones` | `app/web/api.py` |
| 6.3 | `GET /missions/{id}/open`: open + failed + stale items, topologically ordered so unblocked work is first; each entry carries why it is in the queue and what blocks it | `app/graph.py` |
| 6.4 | Mermaid emitter: node shape by kind, colour by status, links to item pages; `part_of`-skeleton collapse above 150 nodes | `app/graph.py` |
| 6.5 | `/missions/{id}` page: goal, milestone checklist with per-milestone state, open queue, graph, activity | `app/web/pages.py` |
| 6.6 | `/items/{id}/graph` neighbourhood view, depth 2, expandable | `app/web/pages.py` |
| 6.7 | Progress accounting that reports *reproduced* and *attested* counts separately, never summed | `app/graph.py`, `app/web/components.py` |

**Tests:** closure over a cyclic graph terminates; topological ordering puts unblocked items first;
mermaid output parses; progress counts split correctly by tier.

**Done when:** `GET /missions/{id}/open` on the seed mission returns exactly the items a human would pick.

---

## Phase 7 — Deployment

**Goal:** `docker compose up` works identically on a laptop and a server.

| # | Task | File(s) |
| --- | --- | --- |
| 7.1 | `Dockerfile.app`: python 3.12-slim, `uv sync`, non-root, `EXPOSE 8000` | `deploy/Dockerfile.app` |
| 7.2 | `docker-compose.yml`: `caddy` + `app` + `runner`; `internal: true` network for the runner; `./data:/data`; runner hardening (`read_only`, `tmpfs`, `cap_drop: ALL`, `mem_limit`, `pids_limit`) | `deploy/docker-compose.yml` |
| 7.3 | `Caddyfile` with `{$E2_SITE_ADDRESS:localhost}`, `basicauth`, `reverse_proxy app:8000`; persistent `caddy_data` volume so certs survive | `deploy/Caddyfile` |
| 7.4 | `.env.example` documenting every variable, incl. how to produce `E2_PASSWORD_HASH` with `caddy hash-password` | `deploy/.env.example` |
| 7.5 | README: local run (with and without TLS), server run with a real domain, backup = `tar` the data dir, restore = untar and restart | `README.md` |
| 7.6 | Startup self-check: data dir writable, runner reachable, Onshape creds present or not — logged as a clear banner, surfaced at `/api/v1/health` | `main.py` |

**Done when:** compose up on `localhost` and on a domain both work, basic auth challenges `httpx` correctly,
and killing the stack and restarting loses nothing.

---

## Phase 8 — Agent skill and seed data

**Goal:** point an agent at the URL and it starts working without further instruction.

| # | Task | File(s) |
| --- | --- | --- |
| 8.1 | `content/start.md`: what the platform is, the six kinds, the three tiers, the claim → work → verify → link loop, httpx quickstart, the isolation caveat | `app/content/start.md` |
| 8.2 | `SKILL.md`: when to use each kind, how to decompose a mission into items, when to attest vs reproduce, how to deprecate rather than delete, and the rule that a `fact` needs a source document | `skills/engineer2/SKILL.md` |
| 8.3 | `references/onshape.md`: the FeatureScript MCP write path — author FS, insert into a Feature Studio in the *same document* as the model, iterate until it evaluates, then register a `cad_evaluation` here | `skills/engineer2/references/onshape.md` |
| 8.4 | `examples/seed_mission.py`: builds design §10 end to end over HTTP, including the step-7 invalidation demo | `examples/` |
| 8.5 | README quickstart pointing humans at `/` and agents at `/start.md` | `README.md` |

**Done when:** a fresh agent given only the base URL and credentials picks up an open item, claims it,
produces a verified result, and links it into the mission.

---

## Phase 9 — Hardening and polish

| # | Task |
| --- | --- |
| 9.1 | Integration test: seed mission end to end against a live compose stack (`make test-integration`) |
| 9.2 | Index rebuild-on-corruption path; `make reindex`; verify deleting `index.json` is always safe |
| 9.3 | Structured request logging with item ids; run durations; slow-query log |
| 9.4 | `GET /api/v1/export` — whole graph as one JSON blob for offline analysis |
| 9.5 | Bulk verify: `POST /api/v1/verify-all?mission=` to re-check a whole mission after a fact edit |
| 9.6 | Empty states, error pages, and a `/start.md` link in the UI footer |

---

## Sequencing and parallelism

```
0 ──► 1 ──► 2 ──► 3
            │
            ├──► 4 ──┐
            │        ├──► 6 ──► 8 ──► 9
            └──► 5 ──┘
                 7 (any time after 2; do it early to shake out env issues)
```

Phases 4 and 5 are independent of each other and can proceed in parallel once Phase 2 lands.
Phase 7 is worth doing early — deployment surprises are cheaper to find before there is much code.

## Definition of done for v1

* All six kinds create, edit, reference, deprecate — via API and UI.
* Calculations and CAD evaluations produce reproducible, timestamped, provenance-carrying run records.
* Editing an upstream fact demotes its verified dependents; one call restores them.
* `GET /missions/{id}/open` is a usable work queue.
* `docker compose up` works on a laptop and on a server behind TLS + basic auth.
* An agent given only the base URL and `/start.md` can complete a work item unaided.
* Nothing on the platform can be deleted.

## Explicitly out of scope for v1

Per-agent accounts and tokens; reputation and leaderboards; moderation; simulation (FEA/CFD) beyond
FeatureScript evaluation; automatic unit conversion; a real database; horizontal scaling; webhooks;
CAD providers other than Onshape.
