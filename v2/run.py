"""
CLI entry point: load a config, solve, independently verify, and print the
required summary (status, per-rule soft violation breakdown, per-PhD-
supervisor case load) plus the verifier's hard-rule pass/fail.

Defaults to the fictional sample_config.json checked into the repo, so
this runs standalone on a fresh clone. Pass --config to point at a real
(gitignored) data file instead, e.g.:

    python -m v2.run --config config_from_real_data.json

Run from the repo root as a module (relative imports require package
context).
"""

import argparse
import json
import os
import sys

from .solve import solve
from .verify import verify_schedule


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None,
                         help="Path to a config JSON file (default: sample_config.json)")
    args = parser.parse_args()

    default_path = os.path.join(os.path.dirname(__file__), "..", "sample_config.json")
    config_path = args.config or default_path
    with open(config_path) as f:
        config = json.load(f)

    # v1-era leftover, unused now that supervisor is solved for rather than
    # fixed input — drop it rather than erroring so the existing file can
    # be reused as-is.
    config.pop("case_supervisors", None)

    result = solve(config)

    print(f"Status: {result['status']}\n")

    print("Soft rule violations (breakdown):")
    for k, v in result["soft_violations"].items():
        print(f"   {k}: {v}")

    print("\nPer-PhD-supervisor case load (primary assignments):")
    for k, v in result["supervisor_case_load"].items():
        print(f"   {k}: {v}")

    violations = verify_schedule(config, result)
    print(f"\nIndependent verifier: {'PASS — 0 hard-rule violations' if not violations else f'FAIL — {len(violations)} violation(s)'}")
    for v in violations:
        print(f"   - {v}")

    print()
    for case in result["cases"]:
        print(f"{case['fellow']} - case {case['case_index']} (tier {case['tier']}, "
              f"primary {case['primary_supervisor']}, secondary {case['secondary_supervisor']}):")
        for v in case["visits"]:
            print(f"   {v['type']:10s} {v['date']}  {v['modality']:11s} sup={v['supervisor']}")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
