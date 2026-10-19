"""
Independent hard-rule verifier.

Deliberately imports ONLY helpers.py — never solve.py, never touches a
CpModel/IntVar. This re-derives everything from the plain config dict and
the plain result dict solve() returns, so it can never "trust" the
solver's own bookkeeping. Two real modeling bugs were caught in this
project by manually auditing output rather than trusting the model was
correct — this is that pattern made automatic.

Only hard rules (1-11) are checked here; soft-rule counting is solve()'s
summary's job, not this function's.
"""

import datetime

from .helpers import expand_ranges, build_case_template, expected_case_count


def verify_schedule(config: dict, result: dict) -> list:
    return verify_schedule_with_visits(config, result)[0]


def verify_schedule_with_visits(config: dict, result: dict):
    """(messages, visits): verify_schedule()'s messages, plus every visit
    any of them involves, as {"fellow", "case_index", "type"} dicts
    (deduped, in first-flagged order) — what the Excel export highlights.
    A case-level violation (Rule 9) involves all of that case's visits."""
    violations = []
    flagged = {}  # (fellow, case_index, type) -> None, an ordered set

    def flag_visits(case, *visits):
        for v in visits:
            flagged.setdefault((case["fellow"], case["case_index"], v["type"]), None)

    def flag(message, case=None, *visits):
        violations.append(message)
        if case is not None:
            flag_visits(case, *visits)

    def flag_pair(message, case1, v1, case2, v2):
        flag(message, case1, v1)
        flag_visits(case2, v2)

    if result.get("status") not in ("OPTIMAL", "FEASIBLE"):
        return [f"solver status is {result.get('status')!r}, nothing to verify"], []

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
            flag(f"{fname}: expected {want_n} cases, found {len(got)}")
            continue
        for case in sorted(got, key=lambda c: c["case_index"]):
            want_template, want_tier = build_case_template(case["case_index"], fellow["type"])
            got_types = [v["type"] for v in case["visits"]]
            if got_types != want_template or case["tier"] != want_tier:
                flag(
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
                flag(f"Rule 1: {fname} case {case['case_index']} "
                     f"{v['type']} on {v['date']} is not a Monday", case, v)
            if d in holiday_days:
                flag(f"Rule 2: {fname} case {case['case_index']} "
                     f"{v['type']} on {v['date']} falls on a holiday", case, v)
            if d in fellow_vacation.get(fname, set()):
                flag(f"Rule 3: {fname} case {case['case_index']} "
                     f"{v['type']} on {v['date']} falls on {fname}'s vacation", case, v)
            # Research fellows' (inclusive) end_date — checked directly
            # against the raw config value, not via solve.py's blocked-day
            # expansion of it.
            end_date = fellows.get(fname, {}).get("end_date")
            if end_date and d > parse(end_date):
                flag(f"Rule 3: {fname} case {case['case_index']} "
                     f"{v['type']} on {v['date']} is after {fname}'s "
                     f"research end date ({end_date})", case, v)
            sup = v.get("supervisor")
            if sup and d in supervisor_vacation.get(sup, set()):
                flag(f"Rule 3: {fname} case {case['case_index']} "
                     f"{v['type']} on {v['date']} falls on {sup}'s vacation", case, v)

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
            flag(f"Rule 4: {fname} case {case['case_index']} "
                 f"KSADS dates not strictly increasing: {ksads_sorted} -> {ksads_dates}",
                 case, *(by_type[vt] for vt in ksads_sorted))

        fb_date = parse(by_type["Feedback"]["date"])
        for vt, v in by_type.items():
            if vt != "Feedback" and parse(v["date"]) >= fb_date:
                flag(f"Rule 5: {fname} case {case['case_index']} "
                     f"Feedback ({fb_date}) is not strictly after {vt} ({v['date']})",
                     case, by_type["Feedback"], v)

        if not any(v["modality"] == "in-person" for v in case["visits"]):
            flag(f"Rule 9: {fname} case {case['case_index']} "
                 f"has no in-person appointment", case, *case["visits"])

    # ---- Rule 6: a fellow's cases run strictly sequentially ----
    # One exception: case B's KSADS1 may equal case A's Feedback date, but
    # only when that Feedback's reported time actually starts after the
    # noon KSADS1 ends — the coordinator's 2:15 escape valve. Every other
    # visit must be strictly after, no exceptions.
    for fname, fcases in by_fellow.items():
        fcases_sorted = sorted(fcases, key=lambda c: c["case_index"])
        for a, b in zip(fcases_sorted, fcases_sorted[1:]):
            fb_visit_a = next(v for v in a["visits"] if v["type"] == "Feedback")
            fb_date_a = parse(fb_visit_a["date"])
            for v in b["visits"]:
                v_date = parse(v["date"])
                if v["type"] == "KSADS1":
                    if v_date < fb_date_a:
                        flag_pair(
                            f"Rule 6: {fname} case {b['case_index']} KSADS1 ({v['date']}) "
                            f"is before case {a['case_index']}'s Feedback ({fb_date_a})",
                            b, v, a, fb_visit_a)
                    elif v_date == fb_date_a and _overlaps(v, fb_visit_a):
                        flag_pair(
                            f"Rule 6: {fname} case {b['case_index']} KSADS1 shares a date "
                            f"with case {a['case_index']}'s Feedback ({fb_date_a}) but the "
                            f"Feedback's time ({fb_visit_a.get('time')}) overlaps it",
                            b, v, a, fb_visit_a)
                elif v_date <= fb_date_a:
                    flag_pair(
                        f"Rule 6: {fname} case {b['case_index']} visit {v['type']} "
                        f"({v['date']}) is not after case {a['case_index']}'s "
                        f"Feedback ({fb_date_a})",
                        b, v, a, fb_visit_a)

    # ---- Rule 7: no fellow double-booked same date ----
    # A clash is a same-date pair whose reported times overlap — every
    # KSADS/Med is at noon, except a 4pm-fallback Med, which clashes with
    # nothing at noon. Feedback counts too: the Rule 6 escape valve's 2:15
    # Feedback clears the next case's noon KSADS1, but a noon Feedback
    # beside another of the fellow's visits is a real double-booking.
    for fname, fcases in by_fellow.items():
        seen = {}
        for case in fcases:
            for v in case["visits"]:
                for other_case, other in seen.get(v["date"], []):
                    if _overlaps(v, other):
                        flag_pair(
                            f"Rule 7: {fname} double-booked on {v['date']}: "
                            f"case {other_case['case_index']}/{other['type']} and "
                            f"case {case['case_index']}/{v['type']}",
                            case, v, other_case, other)
                seen.setdefault(v["date"], []).append((case, v))

    # ---- Rule 8: no supervisor double-booked (non-Feedback) same date ----
    # Rebuilt entirely from the output's own reported `supervisor` field per
    # visit — never assumes who was supposed to run a visit.
    # Same time-overlap test as Rule 7, so the MD's noon Med and a 4pm-
    # fallback Med on one date aren't a clash.
    sup_seen = {}
    for case in cases:
        for v in case["visits"]:
            if v["type"] == "Feedback":
                continue
            sup = v.get("supervisor")
            d = v["date"]
            key = (sup, d)
            for other_case, other in sup_seen.get(key, []):
                if _overlaps(v, other):
                    flag_pair(
                        f"Rule 8: {sup} double-booked on {d}: "
                        f"{other_case['fellow']} case {other_case['case_index']}/{other['type']} and "
                        f"{case['fellow']} case {case['case_index']}/{v['type']}",
                        case, v, other_case, other)
            sup_seen.setdefault(key, []).append((case, v))

    # ---- Rule 10 (updated 2026-10-01): modality defaults to in-person;
    # the only telehealth exception is a visit in a case_index==0 case
    # whose supervisor is remote that day — Marvin (always virtual) or any
    # supervisor, PhD or MD, on one of their configured virtual days.
    # Judged per-visit from the output's own reported `supervisor` field
    # (not the case-level primary/secondary_supervisor bookkeeping), so
    # KSADS3's escape-valve reassignment is judged independently of
    # KSADS1/2, exactly matching solve.py's own per-visit constraints.
    # Every later case is always in-person.
    supervisor_virtual = {name: expand_ranges(s.get("virtual_days", []))
                          for name, s in supervisors.items()}
    for case in cases:
        fname = case["fellow"]
        for v in case["visits"]:
            sup = v.get("supervisor")
            remote = sup == "Marvin" or parse(v["date"]) in supervisor_virtual.get(sup, set())
            expected = "telehealth" if case["case_index"] == 0 and remote else "in-person"
            if v["modality"] != expected:
                flag(
                    f"Rule 10: {fname} case {case['case_index']} {v['type']} "
                    f"(supervisor {v.get('supervisor')}) should be {expected}, "
                    f"got {v['modality']}", case, v)

    # ---- Rule 11: MD time slots ----
    # Each visit's reported start time must be a legal one for its type,
    # and no two of the MD's visits on the same date may overlap in time
    # (Med 90 min, Feedback 45 min). Rebuilt from the output's own
    # `supervisor`/`time` fields.
    md_by_date = {}
    for case in cases:
        for v in case["visits"]:
            t = v.get("time")
            allowed = {"Feedback": FEEDBACK_TIMES, "Med": MED_TIMES}.get(v["type"], ("12:00",))
            if t not in allowed:
                flag(
                    f"Rule 11: {case['fellow']} case {case['case_index']} {v['type']} "
                    f"on {v['date']} has invalid start time {t!r} (allowed: {allowed})", case, v)
                continue
            if v.get("supervisor") == md_supervisor:
                md_by_date.setdefault(v["date"], []).append((case, v))
    for d, entries in md_by_date.items():
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                (c1, v1), (c2, v2) = entries[i], entries[j]
                if _overlaps(v1, v2):
                    flag_pair(
                        f"Rule 11: {md_supervisor} double-booked on {d}: "
                        f"{c1['fellow']} case {c1['case_index']}/{v1['type']} at {v1['time']} "
                        f"overlaps {c2['fellow']} case {c2['case_index']}/{v2['type']} at {v2['time']}",
                        c1, v1, c2, v2)

    visits = [{"fellow": f, "case_index": ci, "type": vt} for f, ci, vt in flagged]
    return violations, visits


FEEDBACK_TIMES = ("12:00", "12:45", "14:15")
# 16:00 is the last-resort fallback (see solve.py's solve()) — legal, but
# solve() reports every one in its result's `med_4pm` list.
MED_TIMES = ("12:00", "16:00")


def _minutes(visit):
    """(start, end) in minutes since midnight. Feedback ~45 min, every
    other visit 90 min. Missing/invalid time is treated as noon — Rule 11
    reports the bad time itself separately."""
    try:
        h, m = map(int, visit.get("time", "12:00").split(":"))
    except (AttributeError, ValueError):
        h, m = 12, 0
    start = h * 60 + m
    return start, start + (45 if visit["type"] == "Feedback" else 90)


def _overlaps(v1, v2):
    s1, e1 = _minutes(v1)
    s2, e2 = _minutes(v2)
    return s1 < e2 and s2 < e1
