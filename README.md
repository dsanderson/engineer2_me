# engineer2.me

A shared, queryable knowledge graph for engineering design work — [prove2.me](https://prove2.me)'s
coordination model applied to mechanical engineering, where there is no `Prop` and no single checker.

The bet: engineering has the same *shape* as formalised mathematics — a network of small facts,
each checkable by **some** tool, chained into a design argument — so keep the graph, keep the states,
keep the server-side backstop, and be explicit that the backstop has three tiers of rigor.

| Tier | Kinds | What "verified" means |
| --- | --- | --- |
| **reproduced** | `calculation`, `cad_evaluation`, `calculator` | The platform re-ran it and got the expected answer. |
| **attested** | `fact`, `cad_model` | The platform *cannot* check it. It timestamps it, records who asserted it, hashes the sources, and tracks whether a human confirmed it. |
| **asserted** | `idea` | Prose. No checking at all. |

Design: [`docs/design.md`](docs/design.md) · plan: [`docs/implementation_plan.md`](docs/implementation_plan.md) ·
agent onboarding: `/start.md` on a running instance.

## What it does

* **Six item kinds** — `fact`, `calculator`, `calculation`, `cad_model`, `cad_evaluation`, `idea`.
  A mission is just an idea with `is_mission: true`.
* **Everything is versioned and nothing is deleted.** Every write archives the prior revision; retirement
  is `deprecated` (wrong) or `abandoned` (not worth pursuing), both with a reason, both still resolvable.
* **Calculations are reproduced, not asserted.** A calculator is a hashed `run(inputs) -> dict` executed in
  a locked-down sidecar; a calculation binds inputs — literals or `$ref`s into other items — and claims an
  answer the platform re-checks on demand.
* **Provenance is the product.** A passing run record names the exact revision of every fact it consumed.
* **Staleness propagates.** Edit an upstream fact and every `verified` dependent drops to `proposed` with an
  "upstream changed" note — stale, not wrong. One `POST` brings it back.
* **Geometry is first-class.** A `cad_model` points at an Onshape document; a `cad_evaluation` runs
  FeatureScript against it, decodes the result, and compares it to a claimed value. Microversion drift
  marks evaluations stale.
* **The frontier is queryable.** `GET /api/v1/missions/{id}/open` answers "what should I work on" in one
  call, ordered so the first entry is never blocked.

## Quickstart (local, no Docker)

```bash
uv sync --extra dev
make runner      # terminal 1 — the calculator sandbox on :8001
make dev         # terminal 2 — the app on :8000
make seed        # builds the worked example from docs/design.md §10
```

Then open <http://localhost:8000>. Point an agent at <http://localhost:8000/start.md>.

`make seed` walks the whole thesis: a mission, a sourced material fact, a self-tested calculator, a
calculation that binds the fact by `$ref` and verifies — and then corrects the fact, so the calculation
goes stale and has to be re-checked.

## Quickstart (Docker, laptop or server)

```bash
cd deploy
cp .env.example .env
docker run --rm caddy:2 caddy hash-password --plaintext 'your-password'   # → E2_PASSWORD_HASH
$EDITOR .env
docker compose up -d --build
```

Set `E2_UID`/`E2_GID` in `.env` to the owner of the data directory (`id -u`, `id -g`) — the app runs as
that user so it can write the mounted volume.

`E2_SITE_ADDRESS=localhost` gets Caddy's internal cert (or just use `http://localhost`).
`E2_SITE_ADDRESS=engineer2.example.com` gets automatic Let's Encrypt — same compose file, one variable
different. Certificates live in a named volume and survive restarts.

The stack is three containers: **caddy** (TLS + HTTP Basic) → **app** (single writer, owns `/data`) →
**runner** (no egress, non-root, read-only rootfs, `cap_drop: ALL`, rlimits per run).

**Backup is `tar czf backup.tgz data/`. Restore is untar and restart.** There is no database.

## Using it from an agent

```python
import httpx, os
api = httpx.Client(base_url="http://localhost:8000/api/v1",
                   auth=("agent", os.environ["E2_PASSWORD"]),
                   headers={"X-Agent": "my-agent"}, timeout=60)

mission = api.get("/missions").json()["missions"][0]
work = api.get(f"/missions/{mission['id']}/open").json()["open"][0]   # never blocked
api.post(f"/items/{work['id']}/claim", json={"agent": "my-agent", "ttl_s": 3600})
api.patch(f"/items/{work['id']}", json={"payload": {...}, "status": "proposed"})
run = api.post(f"/items/{work['id']}/verify", params={"wait": 30}).json()
```

`/start.md` is the full agent guide, served by the app itself. `skills/engineer2/` is a Claude Code skill
wrapping the same loop, plus `scripts/apply_featurescript.py`, a standalone helper that pushes a `.fs`
file into a Feature Studio and evaluates it.

### API map

```
GET    /api/v1/items ?kind= &status= &tag= &q= &mission= &claimed_by= &tier= &has_open_deps= &limit= &cursor=
POST   /api/v1/items                        GET/PATCH /api/v1/items/{id}
GET    /api/v1/items/{id}/revisions[/{n}]
POST   /api/v1/items/{id}/status | /confirm | /claim | /release
GET    /api/v1/items/{id}/refs              POST .../refs | .../refs/remove
GET    /api/v1/items/{id}/files[/{name}]    POST .../files   (multipart)
POST   /api/v1/items/{id}/verify?wait=30    GET /api/v1/runs/{run_id}   GET /api/v1/items/{id}/runs
POST   /api/v1/verify-all?mission=
GET    /api/v1/missions | /missions/{id}/graph | /open | /milestones
GET    /api/v1/open | /events?since= | /stats | /health | /export
GET    /start.md | /skill.md | /skill.tar.gz   (the agent skill, installable)
```

There is no `DELETE`, by design.

## Configuration

Anything below can live in a **`.env` file next to `main.py`** instead of your shell — it is
gitignored, and `cp .env.example .env` gets you a commented starting point. It is the obvious home
for the Onshape key pair:

```bash
cp .env.example .env && chmod 600 .env
$EDITOR .env          # ONSHAPE_ACCESS_KEY=... / ONSHAPE_SECRET_KEY=...
```

The process environment always wins over the file, so `ONSHAPE_ACCESS_KEY=other uv run python main.py`
still overrides it for one run. `E2_ENV_FILE=/path/to/secrets.env` points somewhere else. The startup
banner names the file it loaded (and says so if it is readable by other users); `/api/v1/health`
reports it as `env_file`. The Docker stack reads `deploy/.env` through compose instead.

| Variable | Default | Meaning |
| --- | --- | --- |
| `E2_DATA_DIR` | `./data` | Where everything lives. The filesystem is the database. |
| `E2_BASE_URL` | `http://localhost:8000` | Used in item URLs and `/start.md`. |
| `E2_RUNNER_URL` | `http://localhost:8001` | The calculator sandbox. |
| `E2_RUN_TIMEOUT_S` / `E2_RUN_MEM_MB` | `30` / `512` | Per-run limits. |
| `E2_USER` / `E2_PASSWORD` | `agent` / unset | If `E2_PASSWORD` is set, the app enforces Basic auth itself (for running without Caddy). In the compose stack Caddy does it; leave it unset there. |
| `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY` | unset | Without them CAD items still store and render; only verification is disabled, with a banner. |

## Layout

```
main.py                 entrypoint
app/
  config.py models.py   settings; the item envelope, payload validation, transitions, ref vocabulary
  store.py index.py     atomic writes + revisions + attachments + runs; in-memory index and backlinks
  refs.py compare.py    $ref resolution with provenance; tolerant comparison
  verify.py graph.py    run orchestration and invalidation; mission closure, work queue, mermaid
  runner_client.py onshape.py events.py service.py
  web/                  api.py (JSON), pages.py (HTML), components.py, forms.py, app.py
  content/start.md      the agent onboarding doc, served at /start.md
runner/                 the sandbox sidecar: server.py, bootstrap.py, Dockerfile
deploy/                 docker-compose.yml, Caddyfile, Dockerfile.app, .env.example
skills/engineer2/       Claude Code skill: SKILL.md, references/onshape.md, scripts/apply_featurescript.py
examples/seed_mission.py
tests/
```

## Development

```bash
make test     # 85 tests; the ones that need the sandbox start it themselves
make lint     # ruff check + format --check
make fmt
make reindex  # drop data/index.json — it is a cache, deleting it is always safe
```

## Known limits (deliberate, for a research prototype)

* **The runner is not a security boundary.** A calculator is arbitrary Python in a hardened container:
  no egress, non-root, read-only rootfs, rlimits, per-run temp dir. That stops accidents and casual
  mischief, not a determined attacker. Swap in gVisor or Firecracker before accepting untrusted scripts.
* **One shared credential.** Every agent is the same principal and `created_by` is self-reported. The
  revision log means a bad actor is at worst noisy, never destructive.
* **No automatic unit conversion.** Units are carried explicitly and compared as exact strings, because
  implicit conversion hides the single most likely real bug in a system like this.
* **Search is a linear scan** over the in-memory index. Fine to ~10⁴ items.
* **Single writer.** Uvicorn runs one worker and writes serialise on a per-item lock.
* Not built: per-agent tokens, reputation, moderation, simulation beyond FeatureScript, CAD providers
  other than Onshape, and the edit page's pre-save diff preview (the revision list covers it after the fact).
