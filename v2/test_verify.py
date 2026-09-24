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
