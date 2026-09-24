"""
HTTP API wrapping the v2 scheduling engine, for deployment on Render.

A separate frontend project calls POST /solve with a config JSON body
(same shape as config_from_real_data.json, minus the unused v1-era
`case_supervisors` key) and gets back the solved schedule plus the
independent verifier's hard-rule check.

Run locally: uvicorn main:app --reload
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from v2.solve import solve
from v2.verify import verify_schedule

app = FastAPI(
    title="Psych Fellow Scheduling Engine",
    description="CP-SAT solver for UCLA psychiatry fellow rotation scheduling.",
    version="1.0.0",
)

# ALLOWED_ORIGINS is a comma-separated list of frontend origins, e.g.
# "https://<org>.github.io". Defaults to "*" (open) until a frontend
# origin exists to restrict it to — there's no PHI/sensitive data here,
# but tighten this once the frontend's real domain is known.
_origins = os.environ.get("ALLOWED_ORIGINS", "*")
allowed_origins = ["*"] if _origins == "*" else [o.strip() for o in _origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Render's health check target."""
    return {"status": "ok"}


@app.post("/solve")
def solve_endpoint(config: dict):
    # v1-era leftover some config files may still carry — unused now that
    # supervisor assignment is solved for, not fixed input.
    config.pop("case_supervisors", None)

    try:
        result = solve(config)
    except ValueError as e:
        # e.g. a fellow/supervisor combination with no feasible Mondays at
        # all, raised directly by solve() during domain construction.
        raise HTTPException(status_code=422, detail=str(e))

    violations = (
        verify_schedule(config, result) if result["status"] != "INFEASIBLE" else []
    )

    return {"result": result, "hard_rule_violations": violations}
