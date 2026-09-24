"""
Independent hard-rule verifier.

Deliberately imports ONLY helpers.py — never solve.py, never touches a
CpModel/IntVar. This re-derives everything from the plain config dict and
the plain result dict solve() returns, so it can never "trust" the
solver's own bookkeeping. Two real modeling bugs were caught in this
project by manually auditing output rather than trusting the model was
correct — this is that pattern made automatic.

Only hard rules (1-10) are checked here; soft-rule counting is solve()'s
summary's job, not this function's.
"""

import datetime

from .helpers import expand_ranges, build_case_template, expected_case_count


def verify_schedule(config: dict, result: dict) -> list:
    violations = []

    if result.get("status") not in ("OPTIMAL", "FEASIBLE"):
        return [f"solver status is {result.get('status')!r}, nothing to verify"]

    holiday_days = expand_ranges(config["holidays"])
    fellows = {f["name"]: f for f in config["fellows"]}
    supervisors = {s["name"]: s for s in config["supervisors"]}
    fellow_vacation = {name: expand_ranges(f["vacations"]) for name, f in fellows.items()}
    supervisor_vacation = {name: expand_ranges(s["vacations"]) for name, s in supervisors.items()}
    md_supervisor = next(s["name"] for s in config["supervisors"] if s["role"] == "MD")

    cases = result["cases"]

    # ---- Rule shape check: right number/shape of cases per fellow ----
    by_fellow = {}
    for case in cases:
        by_fellow.setdefault(case["fellow"], []).append(case)
    for fname, fellow in fellows.items():
        got = by_fellow.get(fname, [])
        want_n = expected_case_count(fellow["type"])
        if len(got) != want_n:
            violations.append(
                f"{fname}: expected {want_n} cases, found {len(got)}")
            continue
        for case in sorted(got, key=lambda c: c["case_index"]):
            want_template, want_tier = build_case_template(case["case_index"], fellow["type"])
            got_types = [v["type"] for v in case["visits"]]
            if got_types != want_template or case["tier"] != want_tier:
                violations.append(
                    f"{fname} case {case['case_index']}: expected template "
                    f"{want_template} (tier {want_tier}), got {got_types} (tier {case['tier']})")

    def parse(d):
        return datetime.date.fromisoformat(d)

    # ---- Rules 1, 2, 3: Monday-only, no holiday, no vacation conflict ----
    for case in cases:
        fname = case["fellow"]
        for v in case["visits"]:
            d = parse(v["date"])
            if d.weekday() != 0:
                violations.append(f"Rule 1: {fname} case {case['case_index']} "
                                   f"{v['type']} on {v['date']} is not a Monday")
            if d in holiday_days:
                violations.append(f"Rule 2: {fname} case {case['case_index']} "
                                   f"{v['type']} on {v['date']} falls on a holiday")
            if d in fellow_vacation.get(fname, set()):
                violations.append(f"Rule 3: {fname} case {case['case_index']} "
                                   f"{v['type']} on {v['date']} falls on {fname}'s vacation")
            sup = v.get("supervisor")
            if sup and d in supervisor_vacation.get(sup, set()):
                violations.append(f"Rule 3: {fname} case {case['case_index']} "
                                   f"{v['type']} on {v['date']} falls on {sup}'s vacation")

    # ---- Rule 4: KSADS visits within a case in increasing order ----
    # ---- Rule 5: Feedback strictly last ----
    # ---- Rule 9: >=1 in-person visit per case ----
    for case in cases:
        fname = case["fellow"]
        by_type = {v["type"]: v for v in case["visits"]}
        ksads_types = [vt for vt in by_type if vt.startswith("KSADS")]
        ksads_sorted = sorted(ksads_types)  # KSADS1, KSADS2, KSADS3 sorts correctly lexically
        ksads_dates = [parse(by_type[vt]["date"]) for vt in ksads_sorted]
        if ksads_dates != sorted(ksads_dates) or len(set(ksads_dates)) != len(ksads_dates):
            violations.append(f"Rule 4: {fname} case {case['case_index']} "
                               f"KSADS dates not strictly increasing: {ksads_sorted} -> {ksads_dates}")

        fb_date = parse(by_type["Feedback"]["date"])
        for vt, v in by_type.items():
            if vt != "Feedback" and parse(v["date"]) >= fb_date:
                violations.append(f"Rule 5: {fname} case {case['case_index']} "
                                   f"Feedback ({fb_date}) is not strictly after {vt} ({v['date']})")

        if not any(v["modality"] == "in-person" for v in case["visits"]):
            violations.append(f"Rule 9: {fname} case {case['case_index']} "
                               f"has no in-person appointment")

    # ---- Rule 6: a fellow's cases run strictly sequentially ----
    for fname, fcases in by_fellow.items():
        fcases_sorted = sorted(fcases, key=lambda c: c["case_index"])
        for a, b in zip(fcases_sorted, fcases_sorted[1:]):
            fb_date_a = parse(next(v for v in a["visits"] if v["type"] == "Feedback")["date"])
            for v in b["visits"]:
                if parse(v["date"]) <= fb_date_a:
                    violations.append(
                        f"Rule 6: {fname} case {b['case_index']} visit {v['type']} "
                        f"({v['date']}) is not after case {a['case_index']}'s "
                        f"Feedback ({fb_date_a})")

    # ---- Rule 7: no fellow double-booked (non-Feedback) same date ----
    for fname, fcases in by_fellow.items():
        seen = {}
        for case in fcases:
            for v in case["visits"]:
                if v["type"] == "Feedback":
                    continue
                d = v["date"]
                if d in seen:
                    violations.append(
                        f"Rule 7: {fname} double-booked on {d}: "
                        f"case {seen[d][0]}/{seen[d][1]} and case {case['case_index']}/{v['type']}")
                else:
                    seen[d] = (case["case_index"], v["type"])

    # ---- Rule 8: no supervisor double-booked (non-Feedback) same date ----
    # Rebuilt entirely from the output's own reported `supervisor` field per
    # visit — never assumes who was supposed to run a visit.
    sup_seen = {}
    for case in cases:
        for v in case["visits"]:
            if v["type"] == "Feedback":
                continue
            sup = v.get("supervisor")
            d = v["date"]
            key = (sup, d)
            if key in sup_seen:
                other = sup_seen[key]
                violations.append(
                    f"Rule 8: {sup} double-booked on {d}: "
                    f"{other[0]} case {other[1]}/{other[2]} and "
                    f"{case['fellow']} case {case['case_index']}/{v['type']}")
            else:
                sup_seen[key] = (case["fellow"], case["case_index"], v["type"])

    # ---- Rule 10: case_index==0 with Marvin as primary supervisor ----
    for case in cases:
        if case["case_index"] != 0:
            continue
        if case.get("primary_supervisor") != "Marvin":
            continue
        fname = case["fellow"]
        for v in case["visits"]:
            if v["type"].startswith("KSADS") and v["modality"] != "telehealth":
                violations.append(
                    f"Rule 10: {fname} case 0 (Marvin) {v['type']} should be "
                    f"telehealth, got {v['modality']}")
            if v["type"] == "Med" and v["modality"] != "in-person":
                violations.append(
                    f"Rule 10: {fname} case 0 (Marvin) Med should be "
                    f"in-person, got {v['modality']}")

    return violations
