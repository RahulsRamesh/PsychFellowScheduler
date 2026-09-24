"""
Pure, solver-agnostic helpers shared by solve.py and verify.py.

Deliberately contains NO OR-Tools / CpModel / IntVar references. verify.py
imports only this module (never solve.py) so that "the verifier doesn't
trust the solver's own bookkeeping" is visible from the import graph, not
just a matter of discipline.
"""

import datetime

# Single source of truth for the PhD-supervisor <-> small-int-domain mapping
# used by solve.py's primary_var/secondary_var IntVars. Kept here so both
# solve.py (building the model) and verify.py (decoding solver output
# strings back for comparison) agree on the same names. Hardcoded rather
# than derived from config because Rule 10 is a lab policy tied to this
# specific supervisor (Marvin), not a structural rule that applies
# generically to "whichever supervisor" — the name is load-bearing.
SUPERVISOR_INDEX = {"Walshaw": 0, "Ellis": 1, "Marvin": 2}
INDEX_TO_SUPERVISOR = {v: k for k, v in SUPERVISOR_INDEX.items()}


def daterange_mondays(start: datetime.date, end: datetime.date):
    """All Mondays between start and end, inclusive."""
    d = start
    while d.weekday() != 0:  # 0 = Monday
        d += datetime.timedelta(days=1)
    out = []
    while d <= end:
        out.append(d)
        d += datetime.timedelta(days=7)
    return out


def expand_ranges(ranges):
    """[[start,end], ...] (ISO strings) -> set of date() for every day in range."""
    out = set()
    for r in ranges:
        s = datetime.date.fromisoformat(r[0])
        e = datetime.date.fromisoformat(r[1]) if len(r) > 1 else s
        d = s
        while d <= e:
            out.add(d)
            d += datetime.timedelta(days=1)
    return out


def build_case_template(case_index: int, fellow_type: str):
    """Visit-type list + tier for a case, given its position in the fellow's
    case sequence.

    Rule: full-time fellows have case_index 0-3, tier determined by index
    (0,1 = tier 1 / 5 appts; 2,3 = tier 2 / 4 appts). Research fellows have
    exactly 2 cases (case_index 0-1), both always tier 1.
    """
    if fellow_type == "research":
        tier1 = True
    else:
        tier1 = case_index < 2
    if tier1:
        return ["KSADS1", "KSADS2", "KSADS3", "Med", "Feedback"], 1
    else:
        return ["KSADS1", "KSADS2", "Med", "Feedback"], 2


def expected_case_count(fellow_type: str) -> int:
    """Rule: full-time fellows have 4 cases, research fellows have 2."""
    return 4 if fellow_type == "full-time" else 2
