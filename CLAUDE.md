# engineer2.me — notes for Claude

A shared engineering knowledge graph: facts, calculators, calculations, CAD models and CAD
evaluations, chained into missions and re-checked by the server. Read `docs/design.md` before
changing anything structural — it states the *why* behind most of what looks arbitrary here.

## Run it

```bash
uv sync --extra dev
make runner   # sandbox on :8001 (calculations need it; everything else works without it)
make dev      # app on :8000
make seed     # build the worked example from docs/design.md §10 against a running app
make test     # 120 tests; ones needing the sandbox start it themselves
make lint     # ruff check + format --check — run before committing
```

Tests need `PYTHONPATH` only for the runner module; `tests/conftest.py` handles it.

## The shape of the code

```
app/models.py    the item envelope, per-kind payload validation, transitions, ref vocabulary
app/store.py     atomic writes, revisions, attachments, runs — the filesystem IS the database
app/index.py     in-memory index: summaries, backlinks, search, stats
app/service.py   Platform: the operations. Both api.py and pages.py call into this and hold no rules
app/verify.py    run orchestration + invalidation. The run record is the product
app/graph.py     mission closure, the open queue, progress, graph layering, mermaid
app/replay.py    the event log folded back into the states it produced, for the mission replay
app/web/         api.py (JSON), pages.py (HTML), components.py, forms.py, app.py (builder)
runner/          the sandbox sidecar; bootstrap.py runs inside the forked child
```

Rules live in `models.py` and `service.py`. If you find yourself adding a validation rule to a web
handler, it belongs in one of those two instead.

## Invariants — do not break these

* **Nothing is ever deleted.** There is no `DELETE` route and there must not be one. Retirement is
  `deprecated` (wrong/obsolete, reason required) or `abandoned` (a judgement about value). Both are
  terminal, both stay fetchable, both keep resolving as reference targets.
* **Every write archives the prior revision** to `revisions/<rev>.json` and bumps `rev`. That is what
  makes mutable engineering facts safe.
* **Tier is derived from kind, never set.** `Item.__post_init__` recomputes it. Reproduced and attested
  counts are reported separately and never summed — blurring them is the failure mode this whole
  system exists to prevent.
* **`status` and `human_confirmed` are independent axes.** A machine-checked artefact can still be a
  faithful-looking formalisation of the wrong thing.
* **A `fact` with data and no source is rejected.** Deliberate.
* **Units compare as exact strings.** No automatic conversion, anywhere. A silent metre/millimetre swap
  is the most likely real bug in this system, so it must fail loudly.
* **`fail` and `error` are different.** `fail` = the claimed answer is wrong. `error` = we learned
  nothing (script raised, timed out, runner down, Onshape unreachable). An `error` leaves `status`
  untouched in both directions — it is not evidence for or against the claim.
* **Invalidation is one level deep per write** and moves `verified → proposed`, never to `failed`. The
  cascade happens as each stale item is itself re-verified. Cycles are legal; the walk is cycle-safe.
* **References live on the source item; backlinks are derived** by the index and never stored.
* `index.json` is a cache. Deleting it must always be safe (`make reindex`).

* The mission **replay** reconstructs past state from `events.jsonl`, not from the revisions on disk:
  the log is already ordered and it is one read. `_next_status` in `replay.py` is the one place that
  has to stay in step with `service.py` — a claim, a confirmation or a payload edit moves status as a
  *side effect*, without a `status` event of its own. Creation status comes off the `created` event
  (or revision 1 for items written before that field existed): the `previous` on an item's first
  `status` event is *not* its creation status if a claim happened in between.

## Conventions

* Plain dataclasses and `json`. No ORM, no pydantic, no task queue — that is a design decision, not an
  oversight. Validators are plain functions returning lists of error strings.
* Payload validators reject unknown keys, so adding a payload field means adding it to `_PAYLOAD_KEYS`.
* `payload.script` is a write-only convenience on `calculator` / `cad_evaluation`: `service.py`
  materialises it into `files/<entrypoint>` with a sha256 and drops it from the stored envelope.
* Settings are read from the environment once at import, seeded first from an optional gitignored
  `.env` (`load_env_file` in `config.py`, `setdefault` so the environment wins). Credential changes
  need a restart — there is no hot reload.
* Errors: `ValidationError` → 400, `NotFound` → 404, `Conflict` → 409. Raise them from `service.py`;
  `web/api.py` maps them.
* HTML is server-rendered FT components over Pico. Every page works with JS off — plain form POST plus
  a 303 redirect, and graph filters are a GET form. Mermaid (from cdnjs) is the only third-party
  client-side dependency; `static/graph.js` is ours and only draws edges over an already-rendered graph.
* Ruff, line length 110. `make fmt` before committing.

## Things that surprised me while building this

* FastHTML 0.14 has no `picolink` in `fasthtml.common`; it is `fasthtml.pico.picolink`.
* `subprocess` closes fds above 2 *after* `preexec_fn` runs, so the runner cannot `dup2` its result
  pipe to fd 3 there. It passes the fd number to the child in `E2_RESULT_FD` instead.
* Don't wrap pages in `Titled` and a `Main` — you get nested `<main>`. `shell()` in `pages.py` is the
  one place that builds a page.
* Onshape's `featurescript` endpoint types `queries` as a **map**. An empty *array* 400s during
  deserialisation, before the script is compiled — so the error mentions neither your script nor
  `queries`, and every `cad_evaluation` reads as flaky rather than broken.
* Onshape returns `btType` fully qualified on value objects (`com.belmonttech.serialize.fsvalue.
  BTFSValueNumber`) but bare on map entries (`BTFSValueMapEntry-2077`). `decode_fs` matches the trailing
  segment for that reason, and excludes `BTFSValueMapEntry` — it is a string prefix of `BTFSValueMap`.
* Both of the above were invisible to a green test suite: the fixtures were hand-written in the bare form
  and the transport was mocked, so the suite asserted our beliefs about the wire format rather than
  Onshape's behaviour. Capture fixtures from real responses when the bug could be a contract change.
* `Settings` reads Onshape credentials from the environment and from `.env`, so `tests/conftest.py` pins
  them empty. Without that, "unconfigured" tests pass or fail depending on whose machine runs them.
* `unitToPower` is a mapping with upper-cased keys (`{"METER": 1}`) on the current API, not the
  list of `{key, value}` pairs the older shape used. Fold case before looking up `UNIT_ABBREV`.
* Onshape answers in base SI: `2 * millimeter` returns `0.002` with `METER: 1`. An `expect` written
  as `2 mm` cannot match. Divide the units out in the script and expect a bare number.

## Where the tests are

`tests/test_models.py` (envelope/validation/transitions), `test_store.py` (atomicity, revisions,
index), `test_refs_compare.py` (`$ref` provenance, tolerances, units), `test_verify.py` (runs against a
real sandbox subprocess, plus the invalidation cascade), `test_onshape.py` (FS decoding, mocked
transport), `test_graph.py` (closure, queue ordering, progress), `test_replay.py` (reconstructing
past states from the event log), `test_api.py` (the HTTP surface and that every page renders).
