"""
Proves verify_schedule() actually catches violations, not just that it
passes on good output: one deliberately-corrupted schedule per hard rule
(1-10), each asserted to trip that specific rule's check, plus one assert
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
    # A legitimate same-day tie (Feedback marked late) is NOT a violation —
    # this is the coordinator's real-world escape valve, not a bug.
    bad = copy.deepcopy(clean_result)
    case0 = case_of(bad, "Fellow A", 0)
    case1 = case_of(bad, "Fellow A", 1)
    fb0 = visit_of(case0, "Feedback")
    ksads1_1 = visit_of(case1, "KSADS1")
    ksads1_1["date"] = fb0["date"]
    fb0["late"] = True
    assert verify_schedule(config, bad) == []


def test_rule6_ksads1_tied_but_feedback_not_marked_late(clean_result, config):
    # Same-day tie without the "late" flag is still a Rule 6 violation —
    # the exception only exists when the escape valve was actually used.
    bad = copy.deepcopy(clean_result)
    case0 = case_of(bad, "Fellow A", 0)
    case1 = case_of(bad, "Fellow A", 1)
    fb0 = visit_of(case0, "Feedback")
    ksads1_1 = visit_of(case1, "KSADS1")
    ksads1_1["date"] = fb0["date"]
    fb0["late"] = False
    assert_rule_flagged(verify_schedule(config, bad), 6)


def test_rule6_late_flag_without_actual_tie_is_inconsistent(clean_result, config):
    # "late" claims the escape valve was used, but the dates don't
    # actually match — inconsistent solver bookkeeping, not a real tie.
    # Force the mismatch explicitly (don't assume the baseline solve
    # didn't already legitimately tie this transition on its own).
    bad = copy.deepcopy(clean_result)
    case0 = case_of(bad, "Fellow A", 0)
    case1 = case_of(bad, "Fellow A", 1)
    fb0 = visit_of(case0, "Feedback")
    ksads1_1 = visit_of(case1, "KSADS1")
    fb0["late"] = True
    ksads1_1["date"] = "2099-01-05"  # deliberately does not match fb0's date
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
