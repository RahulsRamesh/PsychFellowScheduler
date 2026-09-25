# Psych Fellow Scheduling

A CP-SAT (Google OR-Tools) constraint solver that builds a 6-month
psychiatry fellow rotation schedule — matching fellows to supervisors,
appointment dates, and modality (in-person/telehealth) against a set of
hard and soft scheduling rules. Built for a UCLA Health psychiatry
research lab to replace a manual Excel-based scheduling process.

No PHI is involved. Real fellow names and schedule data never leave this
machine — see "Data privacy" below.

## Structure

```
main.py                          # FastAPI app: GET /health, POST /solve
requirements.txt
render.yaml                      # Render deployment config
sample_config.json                # fictional config (fake fellow names) — CLI/test default
v2/                               # the engine (this is what main.py wraps)
  helpers.py                      # pure date-math + case-template helpers
  solve.py                        # solve(config) -> schedule (CP-SAT model)
  verify.py                       # verify_schedule(config, result) -> hard-rule violations
  run.py                          # CLI: solve + verify, print summary (--config to override input)
  test_verify.py                  # pytest: verifier catches a corrupted schedule per hard rule
docs/                             # the frontend — deployed via GitHub Pages (see "Frontend" below)
  index.html                      # form + results page
  style.css
  config.js                       # API_BASE / API_KEY — see "Frontend" for why this is public
  app.js                          # form logic, validation, fetch, results rendering
```

`verify.py` deliberately imports only `helpers.py`, never `solve.py` —
it re-derives and checks every hard rule independently from the solver's
plain output, rather than trusting the solver's own bookkeeping.

## Data privacy

This is a public repo, but the lab's actual fellows shouldn't be. Real
fellow names and the real H2 2026 schedule live only under
`reference_material/` (gitignored — see `.gitignore`), which stays local
and is never pushed. `sample_config.json` — a structurally identical but
fictional config (fake fellow names, real supervisor names, since
supervisor identity is part of the actual rule set — see Rule 10) — is
what's checked into git and used by default by the CLI and test suite, so
both run standalone on a fresh clone without needing any real data.

## Local setup

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run the solver against the checked-in sample data from the CLI:

```
.venv/bin/python -m v2.run
```

Or against your own local (gitignored) real data:

```
.venv/bin/python -m v2.run --config reference_material/config_from_real_data.json
```

Run the tests:

```
.venv/bin/python -m pytest v2/test_verify.py -v
```

Run the API locally:

```
.venv/bin/uvicorn main:app --reload
```

then `GET http://127.0.0.1:8000/health`, or `POST /solve` with a config
JSON body + `X-API-Key` header (see `sample_config.json` for the shape —
interactive docs at `/docs`, a FastAPI URL path, unrelated to the
repo's `docs/` frontend folder despite the name coincidence).

Run the frontend locally against it:

```
python3 -m http.server 8080 --directory docs
```

then open `http://127.0.0.1:8080/index.html` — `docs/config.js`
auto-detects `localhost`/`127.0.0.1` and points at the local API.

## API

- `GET /health` — returns `{"status": "ok"}`. Render's health check
  target. Intentionally unauthenticated.
- `POST /solve` — requires an `X-API-Key` header matching the `API_KEY`
  env var (see "Frontend" below for what this is and isn't). Body is a
  config dict (clinic dates, holidays, fellows, supervisors — no
  pre-assigned case supervisors; those are solved for). Returns
  `{"result": {...}, "hard_rule_violations": [...]}`, where `result` is
  `solve()`'s full output (status, per-rule soft-violation breakdown,
  per-supervisor case load, and the schedule itself) and
  `hard_rule_violations` is the independent verifier's output (empty list
  = clean).

## Frontend

`docs/` is a static, zero-build-step site (plain HTML/CSS/vanilla JS):
a form for clinic dates, holidays, supervisor vacations, and fellows,
which calls `POST /solve` and renders the resulting schedule. PhD
supervisor names (Walshaw/Ellis/Marvin) are fixed in the form, not
editable — they're hardcoded in `v2/helpers.py`'s `SUPERVISOR_INDEX` for
the same reason (rule 10 is tied to the specific name "Marvin").

**Deploying it**: GitHub repo → Settings → Pages → Source: "Deploy from a
branch" → Branch `main`, folder `/docs` → Save. No Actions workflow
needed; it redeploys automatically on every push to `main` that touches
`docs/`.

**The `X-API-Key` in `docs/config.js` is not real security** — it's a
static value baked into public, viewable JS source (anyone can read it
via "view source"). It exists only to block casual/automated drive-by
requests against the open Render URL, not a determined actor. This was a
deliberate tradeoff (zero login friction for the coordinator, no PHI at
stake) — see `main.py`'s `require_api_key()` for the server-side check.

**Render's free tier spins down after ~15 min idle** and cold-starts in
~30-60s on the next request — the solve itself is fast (well under 1s at
this problem size), so that wake-up is the only real latency the UI has
to handle. `app.js` pings `/health` on page load to start the wake-up
early, and shows a "waking up the server" message if the solve request
runs long.

## Deployment (Render)

`render.yaml` defines a single Python web service: `pip install -r
requirements.txt`, then `uvicorn main:app --host 0.0.0.0 --port $PORT`,
with `/health` as the health check path.

Env vars:
- `ALLOWED_ORIGINS` (comma-separated) controls CORS — set to the real
  GitHub Pages origin (scheme+host only, no path, e.g.
  `https://rahulsramesh.github.io`), not `*`, once Pages is live.
- `API_KEY` — the shared value `docs/config.js` sends as `X-API-Key`.
  Declared in `render.yaml` with `sync: false`, so only its existence is
  committed; set the actual value in the Render dashboard.

Because the service was likely already provisioned before these env vars
were added to `render.yaml`, don't assume a blueprint sync will apply
them — set/confirm both directly in the Render dashboard for the live
service.
