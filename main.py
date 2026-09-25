"""
HTTP API wrapping the v2 scheduling engine, for deployment on Render.

The docs/ frontend (deployed separately via GitHub Pages) calls POST
/solve with a config JSON body (see sample_config.json for the shape,
minus the unused v1-era `case_supervisors` key) and gets back the solved
schedule plus the independent verifier's hard-rule check. /solve requires
an X-API-Key header matching the API_KEY env var (see require_api_key).

Run locally: uvicorn main:app --reload
"""

import os

from fastapi import Depends, FastAPI, Header, HTTPException
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


def require_api_key(x_api_key: str = Header(None)):
    """Gate on a static shared key, checked via the X-API-Key header.

    This is NOT real authentication — the frontend is a public static
    site, so this key is visible to anyone who reads its JS source. It
    exists only to block casual/automated drive-by requests against the
    open Render URL, not a determined actor. Deliberately not applied to
    /health, since Render's own health checks send no custom headers.
    """
    expected = os.environ.get("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


@app.get("/health")
def health():
    """Render's health check target. Intentionally unauthenticated."""
    return {"status": "ok"}


@app.post("/solve")
def solve_endpoint(config: dict, _: None = Depends(require_api_key)):
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
