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

# gitignored — local reference/real data only, never pushed:
reference_material/
  rule_engine.py                    # v1 prototype (fixed-supervisor input)
  config_from_real_data.json        # real H2 2026 schedule input (has real fellow names)
  real_cases.json                   # real H2 2026 ground-truth schedule
  Assessment Blocks ....xlsx/docx   # coordinator's original source files
  claude_code_prompt.md             # full rule specification
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
JSON body (see `sample_config.json` for the shape — interactive docs at
`/docs`).

## API

- `GET /health` — returns `{"status": "ok"}`. Render's health check target.
- `POST /solve` — body is a config dict (clinic dates, holidays, fellows,
  supervisors — no pre-assigned case supervisors; those are solved for).
  Returns `{"result": {...}, "hard_rule_violations": [...]}`, where
  `result` is `solve()`'s full output (status, per-rule soft-violation
  breakdown, per-supervisor case load, and the schedule itself) and
  `hard_rule_violations` is the independent verifier's output (empty list
  = clean).

## Deployment (Render)

`render.yaml` defines a single Python web service: `pip install -r
requirements.txt`, then `uvicorn main:app --host 0.0.0.0 --port $PORT`,
with `/health` as the health check path.

`ALLOWED_ORIGINS` (env var, comma-separated) controls CORS — defaults to
`*`. Once the frontend has a real domain (e.g. a GitHub Pages URL), set
this to that origin instead of leaving it open.
