"""
Proves verify_schedule() actually catches violations, not just that it
passes on good output: one deliberately-corrupted schedule per hard rule
(1-11), each asserted to trip that specific rule's check, plus one assert
that a genuinely solved schedule passes clean.

Corruptions are applied directly to solve()'s output dict (not by
re-solving with bad input) — that's the point: verify_schedule() must
catch a bad schedule regardless of how it came to be bad, since it isn't
allowed to trust the solver's own bookkeeping.

Runs against the fictional sample_config.json (not real fellow data), so
this suite is self-contained on a fresh clone / in CI.
"""

import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from v2.solve import solve
from v2.verify import verify_schedule


@pytest.fixture(scope="module")
def config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "sample_config.json")
    with open(config_path) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def clean_result(config):
    result = solve(config)
    assert result["status"] in ("OPTIMAL", "FEASIBLE")
    return result


def case_of(result, fellow, case_index):
    return next(c for c in result["cases"]
                if c["fellow"] == fellow and c["case_index"] == case_index)


def visit_of(case, visit_type):
    return next(v for v in case["visits"] if v["type"] == visit_type)


def assert_rule_flagged(violations, rule_number):
    prefix = f"Rule {rule_number}:"
    assert any(v.startswith(prefix) for v in violations), (
        f"expected a violation starting with {prefix!r}, got: {violations}")


def test_clean_schedule_passes(clean_result, config):
    assert verify_schedule(config, clean_result) == []


def test_rule1_monday_only(clean_result, config):
    bad = copy.deepcopy(clean_result)
    case = bad["cases"][0]
    v = case["visits"][0]
    v["date"] = "2026-07-21"  # a Tuesday, not a Monday
    assert_rule_flagged(verify_schedule(config, bad), 1)


def test_rule2_no_holiday(clean_result, config):
    bad = copy.deepcopy(clean_result)
    case = bad["cases"][0]
    v = case["visits"][0]
    v["date"] = "2026-09-07"  # Labor Day, a holiday Monday
    assert_rule_flagged(verify_schedule(config, bad), 2)


def test_rule3_vacation_conflict(clean_result, config):
    bad = copy.deepcopy(clean_result)
    case = case_of(bad, "Fellow A", 0)
    v = case["visits"][0]
    v["date"] = "2026-09-10"  # inside Fellow A's 9/10-9/13 vacation, and a Monday
    assert_rule_flagged(verify_schedule(config, bad), 3)


def test_rule4_ksads_order(clean_result, config):
    bad = copy.deepcopy(clean_result)
    case = case_of(bad, "Fellow A", 0)
    k1 = visit_of(case, "KSADS1")
    k2 = visit_of(case, "KSADS2")
    k1["date"], k2["date"] = k2["date"], k1["date"]  # swap so KSADS1 > KSADS2
    assert_rule_flagged(verify_schedule(config, bad), 4)


def test_rule5_feedback_last(clean_result, config):
    bad = copy.deepcopy(clean_result)
    case = case_of(bad, "Fellow A", 0)
    fb = visit_of(case, "Feedback")
    fb["date"] = "2026-07-20"  # earlier than every other visit in the case
    assert_rule_flagged(verify_schedule(config, bad), 5)


def test_rule6_cases_sequential(clean_result, config):
    bad = copy.deepcopy(clean_result)
    case0 = case_of(bad, "Fellow A", 0)
    case1 = case_of(bad, "Fellow A", 1)
    fb0_date = visit_of(case0, "Feedback")["date"]
    for v in case1["visits"]:
        v["date"] = fb0_date  # case 1's visits no longer after case 0's Feedback
    assert_rule_flagged(verify_schedule(config, bad), 6)


def test_rule6_late_feedback_escape_valve_allowed(clean_result, config):
    # A same-day tie with the Feedback at 2:15 (after the noon KSADS1
    # ends) is NOT a Rule 6 violation — this is the coordinator's
    # real-world escape valve, not a bug. Scoped to Rule 6: moving the
    # Feedback to 2:15 can incidentally collide with another Feedback
    # already at 2:15 that day (a Rule 11 matter, not this test's).
    bad = copy.deepcopy(clean_result)
    case0 = case_of(bad, "Fellow A", 0)
    case1 = case_of(bad, "Fellow A", 1)
    fb0 = visit_of(case0, "Feedback")
    ksads1_1 = visit_of(case1, "KSADS1")
    ksads1_1["date"] = fb0["date"]
    fb0["time"] = "14:15"
    assert not [v for v in verify_schedule(config, bad) if v.startswith("Rule 6:")]


@pytest.mark.parametrize("fb_time", ["12:00", "12:45"])
def test_rule6_ksads1_tied_but_feedback_overlaps(clean_result, config, fb_time):
    # Same-day tie with the Feedback at 12:00 or 12:45 overlaps the noon
    # KSADS1 (12:00-1:30) — still a Rule 6 violation.
    bad = copy.deepcopy(clean_result)
    case0 = case_of(bad, "Fellow A", 0)
    case1 = case_of(bad, "Fellow A", 1)
    fb0 = visit_of(case0, "Feedback")
    ksads1_1 = visit_of(case1, "KSADS1")
    ksads1_1["date"] = fb0["date"]
    fb0["time"] = fb_time
    assert_rule_flagged(verify_schedule(config, bad), 6)


def test_rule7_fellow_double_booked(clean_result, config):
    bad = copy.deepcopy(clean_result)
    case0 = case_of(bad, "Fellow A", 2)
    case1 = case_of(bad, "Fellow A", 3)
    d = visit_of(case0, "KSADS1")["date"]
    visit_of(case1, "KSADS1")["date"] = d  # same fellow, two non-Feedback visits, same date
    assert_rule_flagged(verify_schedule(config, bad), 7)


def test_rule8_supervisor_double_booked(clean_result, config):
    bad = copy.deepcopy(clean_result)
    # Pick two KSADS visits from different fellows' cases, force same date
    # and same reported supervisor.
    case_a = case_of(bad, "Fellow A", 1)
    case_b = case_of(bad, "Fellow B", 1)
    va = visit_of(case_a, "KSADS1")
    vb = visit_of(case_b, "KSADS1")
    vb["supervisor"] = va["supervisor"]
    vb["date"] = va["date"]
    assert_rule_flagged(verify_schedule(config, bad), 8)


def test_rule9_at_least_one_in_person(clean_result, config):
    bad = copy.deepcopy(clean_result)
    case = case_of(bad, "Fellow A", 0)
    for v in case["visits"]:
        v["modality"] = "telehealth"
    assert_rule_flagged(verify_schedule(config, bad), 9)


def test_rule10_marvin_case0(clean_result, config):
    bad = copy.deepcopy(clean_result)
    # Find a case_index==0 case with Marvin as primary supervisor — which
    # fellow gets that case can vary run to run (solver tie-breaking isn't
    # pinned), so search by supervisor rather than assume a fellow name.
    case = next(c for c in bad["cases"]
                if c["case_index"] == 0 and c["primary_supervisor"] == "Marvin")
    visit_of(case, "KSADS1")["modality"] = "in-person"
    assert_rule_flagged(verify_schedule(config, bad), 10)


def test_rule10_default_inperson_outside_exception(clean_result, config):
    # Modality defaults to in-person everywhere; case_index != 0 can never
    # be telehealth regardless of who supervises it.
    bad = copy.deepcopy(clean_result)
    case = case_of(bad, "Fellow A", 2)  # tier-2 case, case_index != 0
    visit_of(case, "KSADS1")["modality"] = "telehealth"
    assert_rule_flagged(verify_schedule(config, bad), 10)


def test_rule10_ksads3_judged_independently(clean_result, config):
    # KSADS3 has its own supervisor (the escape valve) and must be judged
    # on its own — telehealth only if ITS supervisor is Marvin, regardless
    # of who supervises KSADS1/2 in the same case_index==0 case.
    bad = copy.deepcopy(clean_result)
    case = case_of(bad, "Fellow A", 0)  # case_index 0, always tier 1 (has KSADS3)
    k1, k2, k3 = (visit_of(case, vt) for vt in ("KSADS1", "KSADS2", "KSADS3"))

    k1["supervisor"] = "Walshaw"
    k2["supervisor"] = "Walshaw"
    k1["modality"] = "in-person"   # correct: non-Marvin supervisor -> in-person
    k2["modality"] = "in-person"   # correct
    k3["supervisor"] = "Marvin"
    k3["modality"] = "in-person"   # wrong: Marvin + case_index 0 -> should be telehealth

    violations = verify_schedule(config, bad)
    assert_rule_flagged(violations, 10)
    # Scope to Rule 10 specifically — reassigning KSADS1/2's supervisor
    # to Walshaw for this test can incidentally land on one of her real
    # vacation days (an unrelated Rule 3/8 violation), which isn't what
    # this test is about.
    rule10_violations = [v for v in violations if v.startswith("Rule 10:")]
    assert any("KSADS3" in v for v in rule10_violations), (
        f"expected a KSADS3-specific Rule 10 violation: {rule10_violations}")
    assert not any("KSADS1" in v or "KSADS2" in v for v in rule10_violations), (
        f"KSADS1/2 are correctly in-person (non-Marvin supervisor) and must not "
        f"trigger Rule 10 just because KSADS3 in the same case involves Marvin: "
        f"{rule10_violations}")


def _md_visits(result):
    return [(c, v) for c in result["cases"] for v in c["visits"]
            if v["type"] in ("Med", "Feedback")]


def test_rule11_med_and_feedback_overlap(clean_result, config):
    # A Feedback at 12:45 on another case's Med day overlaps the Med
    # (12:00-1:30) — only 2:15 is allowed alongside a Med.
    bad = copy.deepcopy(clean_result)
    med = visit_of(case_of(bad, "Fellow A", 0), "Med")
    fb = visit_of(case_of(bad, "Fellow B", 0), "Feedback")
    fb["date"], fb["time"] = med["date"], "12:45"
    assert_rule_flagged(verify_schedule(config, bad), 11)


def test_rule11_med_and_feedback_at_215_ok(clean_result, config):
    bad = copy.deepcopy(clean_result)
    med = visit_of(case_of(bad, "Fellow A", 0), "Med")
    fb = visit_of(case_of(bad, "Fellow B", 0), "Feedback")
    fb["date"], fb["time"] = med["date"], "14:15"
    # Scoped to Rule 11 against this specific pair: the move itself may
    # incidentally break unrelated rules (e.g. Fellow B's case order).
    rule11 = [v for v in verify_schedule(config, bad)
              if v.startswith("Rule 11:") and "Fellow B case 0/Feedback" in v
              and "Fellow A case 0/Med" in v]
    assert not rule11


def test_rule11_two_feedbacks_same_slot(clean_result, config):
    bad = copy.deepcopy(clean_result)
    fb_a = visit_of(case_of(bad, "Fellow A", 0), "Feedback")
    fb_b = visit_of(case_of(bad, "Fellow B", 0), "Feedback")
    fb_b["date"], fb_b["time"] = fb_a["date"], fb_a["time"]
    assert_rule_flagged(verify_schedule(config, bad), 11)


def test_rule11_invalid_time(clean_result, config):
    bad = copy.deepcopy(clean_result)
    visit_of(case_of(bad, "Fellow A", 0), "Med")["time"] = "14:15"  # Med is noon-only
    assert_rule_flagged(verify_schedule(config, bad), 11)


def test_solver_feedback_times_follow_preferences(clean_result):
    # Independent check of the solver's slot choices: 12:45 only ever
    # appears on a 3-Feedback day (2 Feedbacks go 12:00 + 2:15).
    by_date = {}
    for c, v in _md_visits(clean_result):
        by_date.setdefault(v["date"], []).append(v)
    for d, vs in by_date.items():
        fbs = [v for v in vs if v["type"] == "Feedback"]
        for v in fbs:
            if v["time"] == "12:45":
                assert len(fbs) == 3, f"12:45 Feedback on {d} without a 3-Feedback day: {vs}"
