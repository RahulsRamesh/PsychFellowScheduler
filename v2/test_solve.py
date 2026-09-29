"""
Independent cross-check of solve()'s soft_violation_details against the
actual case data in its own result — in the spirit of verify.py's
philosophy (see its module docstring): never trust the solver's own
bookkeeping. Each check here re-derives the fact a detail entry claims to
be true directly from result["cases"], rather than trusting the flag that
produced the entry.

Runs against the fictional sample_config.json (not real fellow data), so
this suite is self-contained on a fresh clone / in CI.
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from v2.solve import solve, _assign_feedback_times


@pytest.fixture(scope="module")
def config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "sample_config.json")
    with open(config_path) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def result(config):
    r = solve(config)
    assert r["status"] in ("OPTIMAL", "FEASIBLE")
    return r


def case_of(result, fellow, case_index):
    return next(c for c in result["cases"]
                if c["fellow"] == fellow and c["case_index"] == case_index)


def visit_of(case, visit_type):
    return next(v for v in case["visits"] if v["type"] == visit_type)


# ---- 6: sanity check nothing is broken ----
def test_solve_returns_optimal_or_feasible(result):
    assert result["status"] in ("OPTIMAL", "FEASIBLE")


# ---- 1: same 6 keys in soft_violations and soft_violation_details ----
def test_details_keys_match_violations_keys(result):
    assert set(result["soft_violation_details"].keys()) == set(result["soft_violations"].keys())
    assert set(result["soft_violation_details"].keys()) == {
        "supervisor_variety (soft #2)",
        "ksads3_mismatch (soft #1)",
        "med_position (soft #3)",
        "case_gap (soft #4)",
        "feedback_215 (soft #5)",
        "feedback_triple (soft #6)",
    }


# ---- 2: every case-scoped entry corresponds to a real case ----
def test_case_scoped_entries_reference_real_cases(result):
    real_cases = {(c["fellow"], c["case_index"]) for c in result["cases"]}
    case_scoped_keys = [
        "ksads3_mismatch (soft #1)",
        "med_position (soft #3)",
        "case_gap (soft #4)",
        "feedback_215 (soft #5)",
        "feedback_triple (soft #6)",
    ]
    for key in case_scoped_keys:
        for entry in result["soft_violation_details"][key]:
            assert set(entry.keys()) == {"fellow", "case_index"}
            assert (entry["fellow"], entry["case_index"]) in real_cases, (
                f"{key}: entry {entry} does not correspond to a real case")


# ---- 3: ksads3_mismatch entries independently confirmed via case data ----
def test_ksads3_mismatch_entries_are_genuinely_mismatched(result):
    details = result["soft_violation_details"]["ksads3_mismatch (soft #1)"]
    # Every entry must be a tier-1 case (only tier-1 cases have a KSADS3 /
    # secondary_supervisor at all).
    for entry in details:
        case = case_of(result, entry["fellow"], entry["case_index"])
        assert case["tier"] == 1, (
            f"ksads3_mismatch entry {entry} refers to a non-tier-1 case, "
            f"which has no secondary supervisor to mismatch")
        assert case["secondary_supervisor"] != case["primary_supervisor"], (
            f"ksads3_mismatch entry {entry} claims a mismatch, but case data "
            f"shows primary={case['primary_supervisor']!r} == "
            f"secondary={case['secondary_supervisor']!r}")

    # And the converse: every tier-1 case that actually mismatches must be
    # present in the details list (no false negatives).
    flagged = {(e["fellow"], e["case_index"]) for e in details}
    for case in result["cases"]:
        if case["tier"] == 1 and case["secondary_supervisor"] != case["primary_supervisor"]:
            assert (case["fellow"], case["case_index"]) in flagged, (
                f"case {case['fellow']}/{case['case_index']} mismatches "
                f"(primary={case['primary_supervisor']!r}, "
                f"secondary={case['secondary_supervisor']!r}) but is missing "
                f"from ksads3_mismatch details")


# ---- 4: med_position entries independently recomputed from visit dates ----
def _med_violates_position_rule(case):
    """tier 1: Med should NOT be the case's earliest-dated visit.
    tier 2: Med SHOULD be the case's earliest-dated visit.
    Recomputed directly from visits[].date, independent of any solver bool."""
    dates = {v["type"]: datetime.date.fromisoformat(v["date"]) for v in case["visits"]}
    med_date = dates["Med"]
    earliest_date = min(dates.values())
    med_is_earliest = (med_date == earliest_date)
    if case["tier"] == 1:
        return med_is_earliest  # violation if Med IS earliest
    else:
        return not med_is_earliest  # violation if Med is NOT earliest


def test_med_position_entries_independently_recomputed(result):
    details = result["soft_violation_details"]["med_position (soft #3)"]
    for entry in details:
        case = case_of(result, entry["fellow"], entry["case_index"])
        assert _med_violates_position_rule(case), (
            f"med_position entry {entry} claims a violation, but recomputing "
            f"directly from visit dates shows no violation for case "
            f"(tier {case['tier']}): {case['visits']}")

    # Converse: every case that actually violates the rule must be flagged.
    flagged = {(e["fellow"], e["case_index"]) for e in details}
    for case in result["cases"]:
        if _med_violates_position_rule(case):
            assert (case["fellow"], case["case_index"]) in flagged, (
                f"case {case['fellow']}/{case['case_index']} violates the "
                f"Med-position rule by date recomputation but is missing "
                f"from med_position details")


# ---- 5: supervisor_variety entries independently confirmed ----
def test_supervisor_variety_entries_are_full_time_and_genuinely_missing_a_supervisor(result):
    details = result["soft_violation_details"]["supervisor_variety (soft #2)"]
    assert len(details) == len(set(details)), "supervisor_variety details must be deduped"

    by_fellow = {}
    for case in result["cases"]:
        by_fellow.setdefault(case["fellow"], []).append(case)

    all_phd_supervisors = {"Walshaw", "Ellis", "Marvin"}

    for fellow in details:
        fellow_cases = by_fellow[fellow]
        # (a) full-time == 4 cases
        assert len(fellow_cases) == 4, (
            f"supervisor_variety flags {fellow!r}, but they have "
            f"{len(fellow_cases)} cases, not 4 (not full-time)")
        # (b) genuinely missing at least one of Walshaw/Ellis/Marvin as
        # primary_supervisor across their cases
        covered = {c["primary_supervisor"] for c in fellow_cases}
        missing = all_phd_supervisors - covered
        assert missing, (
            f"supervisor_variety flags {fellow!r}, but their cases' "
            f"primary_supervisor values {covered} cover all of "
            f"{all_phd_supervisors} — nothing is actually missing")


# ---- 4 (case_gap): sanity on shape + documented count/len mismatch ----
def test_case_gap_details_shape_and_expected_mismatch(result):
    details = result["soft_violation_details"]["case_gap (soft #4)"]
    count = result["soft_violations"]["case_gap (soft #4)"]
    assert len(details) == len(set((e["fellow"], e["case_index"]) for e in details)), (
        "case_gap details must be deduped by (fellow, case_index)")
    # This is a sanity check on the documented, expected relationship, not
    # an equality: len(details) counts *cases* touched by at least one
    # tight transition, while `count` counts tight *transitions*. They are
    # different units and need not match (see comment in solve.py).
    if count == 0:
        assert details == []
    else:
        assert len(details) > 0


# ---- 4 (case_gap): "tight" now means exactly the late-Feedback escape
# valve (Rule 6) was used — independently recompute each transition's
# tie status directly from visit dates, not from any solver bool ----
def test_case_gap_matches_late_feedback_escape_valve(result):
    details = result["soft_violation_details"]["case_gap (soft #4)"]
    flagged = {(e["fellow"], e["case_index"]) for e in details}

    by_fellow = {}
    for case in result["cases"]:
        by_fellow.setdefault(case["fellow"], []).append(case)

    for fellow, fcases in by_fellow.items():
        fcases_sorted = sorted(fcases, key=lambda c: c["case_index"])
        for a, b in zip(fcases_sorted, fcases_sorted[1:]):
            fb_a = next(v for v in a["visits"] if v["type"] == "Feedback")
            ksads1_b = next(v for v in b["visits"] if v["type"] == "KSADS1")
            is_tied = fb_a["date"] == ksads1_b["date"]

            # A tied Feedback must be at 2:15 (after the noon KSADS1 ends),
            # regardless of what soft_violation_details says.
            if is_tied:
                assert fb_a["time"] == "14:15", (
                    f"{fellow} case {a['case_index']}: Feedback on {fb_a['date']} is "
                    f"tied with the next KSADS1 but is at {fb_a['time']}, not 2:15")

            if is_tied:
                assert (fellow, a["case_index"]) in flagged, (
                    f"{fellow} case {a['case_index']}'s Feedback is genuinely tied "
                    f"with case {b['case_index']}'s KSADS1 but is missing from "
                    f"case_gap details")
                assert (fellow, b["case_index"]) in flagged, (
                    f"{fellow} case {b['case_index']}'s KSADS1 is genuinely tied "
                    f"with case {a['case_index']}'s Feedback but is missing from "
                    f"case_gap details")


@pytest.mark.parametrize("fb_cases,late,has_med,expected", [
    ([0], set(), False, {0: "12:00"}),                                   # lone Feedback
    ([0], {0}, False, {0: "14:15"}),                                     # lone late Feedback
    ([0], set(), True, {0: "14:15"}),                                    # Med + Feedback
    ([0], {0}, True, {0: "14:15"}),                                      # Med + late Feedback
    ([0, 1], set(), False, {0: "12:00", 1: "14:15"}),                    # 2 Feedbacks: never 12:45
    ([0, 1], {0}, False, {1: "12:00", 0: "14:15"}),                      # late one takes 2:15
    ([0, 1, 2], set(), False, {0: "12:00", 1: "12:45", 2: "14:15"}),     # triple day
    ([0, 1, 2], {0}, False, {1: "12:00", 2: "12:45", 0: "14:15"}),       # triple, late takes 2:15
])
def test_assign_feedback_times(fb_cases, late, has_med, expected):
    is_late = {ci: ci in late for ci in fb_cases}
    assert _assign_feedback_times(fb_cases, is_late, has_med) == expected


def _next_case_ksads1_dates(result):
    """(fellow, case_index) -> date of that fellow's NEXT case's KSADS1."""
    by_fellow = {}
    for case in result["cases"]:
        by_fellow.setdefault(case["fellow"], []).append(case)
    out = {}
    for fellow, fcases in by_fellow.items():
        fcases_sorted = sorted(fcases, key=lambda c: c["case_index"])
        for a, b in zip(fcases_sorted, fcases_sorted[1:]):
            out[fellow, a["case_index"]] = visit_of(b, "KSADS1")["date"]
    return out


# ---- 5 (feedback_215): exactly the 2:15 Feedbacks that aren't the
# Rule 6 escape valve (which case_gap already counts) ----
def test_feedback_215_matches_times(result):
    details = result["soft_violation_details"]["feedback_215 (soft #5)"]
    count = result["soft_violations"]["feedback_215 (soft #5)"]
    next_k1 = _next_case_ksads1_dates(result)
    expected = set()
    for case in result["cases"]:
        fb = visit_of(case, "Feedback")
        key = (case["fellow"], case["case_index"])
        if fb["time"] == "14:15" and next_k1.get(key) != fb["date"]:
            expected.add(key)
    assert {(e["fellow"], e["case_index"]) for e in details} == expected
    assert count == len(expected)


# ---- 6 (feedback_triple): count == number of days with 3 Feedbacks ----
def test_feedback_triple_matches_times(result):
    count = result["soft_violations"]["feedback_triple (soft #6)"]
    per_day = {}
    for case in result["cases"]:
        fb = visit_of(case, "Feedback")
        per_day.setdefault(fb["date"], []).append(fb["time"])
    triple_days = [d for d, ts in per_day.items() if len(ts) == 3]
    assert count == len(triple_days)
    assert len(result["soft_violation_details"]["feedback_triple (soft #6)"]) == 3 * count
