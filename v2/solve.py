"""
Psychiatry Fellow Scheduling — Optimization Engine v2
======================================================
v2 of the rule engine (see ../rule_engine.py for the v1 prototype this
builds on). The key difference: **PhD supervisor assignment is now a
decision variable**, not fixed input. That's the actual manual decision
the coordinator makes today, and promoting it to a solver decision is what
makes the supervisor-double-booking rule (#8) achievable as a genuine hard
constraint via the KSADS3 "escape valve" mechanism — in v1 it had to be
softened because supervisors were fixed, historically-assigned input.

Rule numbers below refer to the hard/soft rule list in claude_code_prompt.md.
Hard rules 1-10 must never be violated (solver returns INFEASIBLE rather
than break them). Soft rules 1-4 are penalized in the objective.
"""

import datetime

from ortools.sat.python import cp_model

from .helpers import (
    daterange_mondays,
    expand_ranges,
    build_case_template,
    SUPERVISOR_INDEX,
    INDEX_TO_SUPERVISOR,
)


def reified_eq(model, x, y, name):
    """b <=> (x == y). Used uniformly for both date-equality and
    supervisor-equality checks throughout this file."""
    b = model.NewBoolVar(name)
    model.Add(x == y).OnlyEnforceIf(b)
    model.Add(x != y).OnlyEnforceIf(b.Not())
    return b


def solve(config: dict, time_limit_s: int = 45):
    # config may still carry a v1-era `case_supervisors` key (e.g. if the
    # caller reuses config_from_real_data.json as-is) — it's unused input
    # now that supervisor is solved for, not fixed. Ignore it rather than
    # erroring, so existing config files don't need hand-editing.

    clinic_start = datetime.date.fromisoformat(config["clinic_start"])
    clinic_end = datetime.date.fromisoformat(config["clinic_end"])
    holiday_days = expand_ranges(config["holidays"])

    # Rules 1 & 2: Monday-only, no holidays — baked directly into the
    # candidate date list itself, so every date_var's domain automatically
    # satisfies both regardless of which values get excluded later.
    all_mondays = [d for d in daterange_mondays(clinic_start, clinic_end)
                   if d not in holiday_days]
    monday_idx = {d: i for i, d in enumerate(all_mondays)}

    fellows = {f["name"]: f for f in config["fellows"]}
    supervisors = {s["name"]: s for s in config["supervisors"]}
    fellow_vacation = {name: expand_ranges(f["vacations"]) for name, f in fellows.items()}
    supervisor_vacation = {name: expand_ranges(s["vacations"]) for name, s in supervisors.items()}

    md_supervisor = next(s["name"] for s in config["supervisors"] if s["role"] == "MD")
    phd_supervisors = [s["name"] for s in config["supervisors"] if s["role"] == "PhD"]
    if set(phd_supervisors) != set(SUPERVISOR_INDEX):
        raise ValueError(
            f"Config PhD supervisors {sorted(phd_supervisors)} don't match "
            f"the expected {sorted(SUPERVISOR_INDEX)} — SUPERVISOR_INDEX in "
            f"helpers.py needs updating if the supervisor roster changed."
        )

    # Build cases directly from fellows (case_index 0-3 full-time, 0-1
    # research) — no longer sourced from a fixed case_supervisors input.
    cases = []
    for fellow in config["fellows"]:
        fname = fellow["name"]
        ftype = fellow["type"]
        n_cases = 4 if ftype == "full-time" else 2
        for case_index in range(n_cases):
            template, tier = build_case_template(case_index, ftype)
            cases.append({
                "fellow": fname,
                "case_index": case_index,
                "template": template,
                "tier": tier,
            })

    model = cp_model.CpModel()

    def allowed_indices(fellow, extra_vacation_days=None):
        f_bad = fellow_vacation[fellow]
        extra_bad = extra_vacation_days or set()
        return [monday_idx[d] for d in all_mondays if d not in f_bad and d not in extra_bad]

    date_var = {}       # (case_id, visit_type) -> IntVar (index into all_mondays)
    modality_var = {}   # (case_id, visit_type) -> BoolVar (True = in-person)
    primary_var = {}     # case_id -> IntVar in {0,1,2}: KSADS1/KSADS2 supervisor
    secondary_var = {}   # case_id -> IntVar in {0,1,2}: KSADS3 supervisor (tier-1 only)
    case_fellow_allowed = {}  # case_id -> list of Monday indices allowed for the fellow alone

    for ci, case in enumerate(cases):
        fellow = case["fellow"]
        f_allowed = allowed_indices(fellow)
        case_fellow_allowed[ci] = f_allowed
        # Rule 3 (fellow half) + Rule 3 (MD half, since Med/Feedback's
        # supervisor is fixed) can both be baked directly into the domain.
        fmd_allowed = allowed_indices(fellow, supervisor_vacation[md_supervisor])

        for vt in case["template"]:
            if vt in ("Med", "Feedback"):
                allowed = fmd_allowed
            else:  # KSADS1/2/3 — PhD supervisor unresolved yet, see below
                allowed = f_allowed
            if not allowed:
                raise ValueError(
                    f"No feasible Mondays at all for {fellow} (case {ci}, "
                    f"visit {vt}) — vacations+holidays block everything."
                )
            dv = model.NewIntVarFromDomain(cp_model.Domain.FromValues(allowed),
                                            f"date_{ci}_{vt}")
            date_var[ci, vt] = dv
            modality_var[ci, vt] = model.NewBoolVar(f"inperson_{ci}_{vt}")

        primary_var[ci] = model.NewIntVar(0, 2, f"primary_{ci}")
        if case["tier"] == 1:
            secondary_var[ci] = model.NewIntVar(0, 2, f"secondary_{ci}")

    # ---- Rule 3 (PhD half): conditional supervisor-vacation exclusion ----
    # Can't bake a PhD supervisor's vacation into the date domain the way
    # v1 did, because which PhD supervisor runs a given KSADS visit is now
    # itself a decision variable. Instead: for each case and each candidate
    # supervisor s, reify "is this case's [primary|secondary] supervisor
    # s?" once, then forbid that supervisor's vacation dates conditionally.
    for ci, case in enumerate(cases):
        f_allowed = case_fellow_allowed[ci]
        for s_name, s_idx in SUPERVISOR_INDEX.items():
            forbidden_idx = [idx for idx in f_allowed
                              if all_mondays[idx] in supervisor_vacation[s_name]]
            if not forbidden_idx:
                continue
            is_primary_s = reified_eq(model, primary_var[ci], s_idx,
                                       f"is_primary_{s_name}_{ci}")
            for vt in ("KSADS1", "KSADS2"):
                for idx in forbidden_idx:
                    model.Add(date_var[ci, vt] != idx).OnlyEnforceIf(is_primary_s)
            if ci in secondary_var:
                is_secondary_s = reified_eq(model, secondary_var[ci], s_idx,
                                             f"is_secondary_{s_name}_{ci}")
                for idx in forbidden_idx:
                    model.Add(date_var[ci, "KSADS3"] != idx).OnlyEnforceIf(is_secondary_s)

    # ---- Rule 4: KSADS visits within a case occur in increasing order ----
    for ci, case in enumerate(cases):
        ksads = [vt for vt in case["template"] if vt.startswith("KSADS")]
        for a, b in zip(ksads, ksads[1:]):
            model.Add(date_var[ci, a] < date_var[ci, b])

    # ---- Rule 5: Feedback is always the last appointment in its case ----
    for ci, case in enumerate(cases):
        for vt in case["template"]:
            if vt != "Feedback":
                model.Add(date_var[ci, "Feedback"] > date_var[ci, vt])

    # All-different dates within a case (needed so Rule 8's pairwise check
    # below only has to consider cross-case pairs).
    for ci, case in enumerate(cases):
        all_vt = case["template"]
        for i in range(len(all_vt)):
            for j in range(i + 1, len(all_vt)):
                model.Add(date_var[ci, all_vt[i]] != date_var[ci, all_vt[j]])

    # ---- Rule 9: at least one in-person appointment per case ----
    # Now trivially satisfied by construction — see Rule 10 below, which
    # forces Med in-person in every case unconditionally — but kept as an
    # explicit, independent check of the literal rule rather than relying
    # on that as an implicit side effect.
    for ci, case in enumerate(cases):
        model.Add(sum(modality_var[ci, vt] for vt in case["template"]) >= 1)

    # ---- Rule 6: a fellow's cases run strictly sequentially ----
    # EVERY visit in case N+1 (not just its first) must fall after case N's
    # Feedback — bounding only the first visit lets Med float outside its
    # case's window (this was a real bug in an earlier prototype).
    #
    # One coordinator-specified exception: case N+1's KSADS1 may land on
    # the SAME calendar day as case N's Feedback — the "late Feedback"
    # escape valve. In reality this works because Feedback's time-of-day
    # is flexible (12:00/12:45 normally) and can be pushed to 2:15, so it
    # still real-world-follows the noon KSADS1 even though the date
    # matches. We don't model time-of-day at all otherwise; `late_feedback_var`
    # is just a boolean marking whether this specific escape valve got
    # used. This is safe to allow unconditionally (not gated behind a
    # separate "permission" variable) because it's defined as an exact
    # reified equality below, and rules 7/8 (fellow/supervisor
    # double-booking) already exempt Feedback from same-day conflict
    # checks against other visit types — so a same-day KSADS1/Feedback
    # pair was never actually forbidden by anything else. No other visit
    # type in case N+1 gets this relaxation — only KSADS1.
    by_fellow = {}
    for ci, case in enumerate(cases):
        by_fellow.setdefault(case["fellow"], []).append(ci)

    late_feedback_var = {}  # case_id (the earlier case, "a") -> BoolVar
    for fellow, case_ids in by_fellow.items():
        case_ids_sorted = sorted(case_ids, key=lambda ci: cases[ci]["case_index"])
        for a, b in zip(case_ids_sorted, case_ids_sorted[1:]):
            for vt in cases[b]["template"]:
                if vt == "KSADS1":
                    model.Add(date_var[b, vt] >= date_var[a, "Feedback"])
                else:
                    model.Add(date_var[b, vt] > date_var[a, "Feedback"])
            late_feedback_var[a] = reified_eq(model, date_var[b, "KSADS1"], date_var[a, "Feedback"],
                                               f"late_feedback_{a}")

    # ---- Rule 7: no fellow double-booked (non-Feedback) same date ----
    fellow_nonfb_slots = []  # (fellow, case_id, visit_type)
    for ci, case in enumerate(cases):
        for vt in case["template"]:
            if vt != "Feedback":
                fellow_nonfb_slots.append((case["fellow"], ci, vt))

    for i in range(len(fellow_nonfb_slots)):
        f1, ci1, vt1 = fellow_nonfb_slots[i]
        for j in range(i + 1, len(fellow_nonfb_slots)):
            f2, ci2, vt2 = fellow_nonfb_slots[j]
            if f1 == f2 and ci1 != ci2:
                b = reified_eq(model, date_var[ci1, vt1], date_var[ci2, vt2],
                                f"feq_{ci1}{vt1}_{ci2}{vt2}")
                model.Add(b == 0)

    # ---- Rule 8: no supervisor double-booked (non-Feedback) same date ----
    # Confirmed a true HARD rule (a supervisor can't be in two places at
    # once) — unlike v1, which had to soften this because supervisors were
    # fixed input and hard-enforcing it was infeasible against the real
    # historical assignments. Now that supervisor choice is free, the
    # KSADS3 escape valve (soft rule 1) gives the solver room to satisfy
    # this hard.
    #
    # MD half: the single MD supervisor (identified by role, not name) is
    # still fixed for Med, so this is grouped by literal name exactly like
    # v1's pattern.
    md_slots = [(ci, vt) for ci, case in enumerate(cases)
                for vt in case["template"] if vt == "Med"]
    for i in range(len(md_slots)):
        ci1, vt1 = md_slots[i]
        for j in range(i + 1, len(md_slots)):
            ci2, vt2 = md_slots[j]
            b = reified_eq(model, date_var[ci1, vt1], date_var[ci2, vt2],
                            f"mdeq_{ci1}{vt1}_{ci2}{vt2}")
            model.Add(b == 0)

    # PhD half (KSADS1/2/3): supervisor is a variable now (primary_var for
    # KSADS1/KSADS2, secondary_var for KSADS3), so "same supervisor, same
    # date" is checked via a second reified equality on the supervisor
    # variables themselves, not by pre-grouping on a fixed name.
    ksads_slots = []  # (case_id, visit_type, date_var, supervisor_var)
    for ci, case in enumerate(cases):
        for vt in case["template"]:
            if vt == "KSADS1" or vt == "KSADS2":
                ksads_slots.append((ci, vt, date_var[ci, vt], primary_var[ci]))
            elif vt == "KSADS3":
                ksads_slots.append((ci, vt, date_var[ci, vt], secondary_var[ci]))

    for i in range(len(ksads_slots)):
        ci1, vt1, d1, s1 = ksads_slots[i]
        for j in range(i + 1, len(ksads_slots)):
            ci2, vt2, d2, s2 = ksads_slots[j]
            if ci1 == ci2:
                continue  # already all-different within case
            date_eq = reified_eq(model, d1, d2, f"pdeq_{ci1}{vt1}_{ci2}{vt2}")
            sup_eq = reified_eq(model, s1, s2, f"pseq_{ci1}{vt1}_{ci2}{vt2}")
            model.Add(date_eq + sup_eq <= 1)

    # ---- Rule 10 (updated 2026-09-27): modality defaults to in-person;
    # the only telehealth exception is a visit in a case_index==0 case
    # that Marvin actually supervises ----
    # Coordinator's reasoning: a fellow only shadows (doesn't lead) their
    # supervisor during their very first case, and Marvin supervises fully
    # virtually — so telehealth requires BOTH case_index==0 AND Marvin
    # being the supervisor who actually runs that specific visit. This is
    # judged per-visit, not per-case: KSADS1/KSADS2 share one supervisor
    # (primary_var) and so share one modality decision, but KSADS3 has its
    # own supervisor (secondary_var, the KSADS3 escape valve — soft rule
    # 1) and is judged independently. It's entirely possible for KSADS1/2
    # to be telehealth (primary is Marvin) while KSADS3 is in-person
    # (reassigned away from Marvin), or the reverse. Med/Feedback are
    # always MD-supervised, so they're forced in-person unconditionally,
    # in every case — not just case_index==0 — with no exception ever.
    for ci, case in enumerate(cases):
        for vt in case["template"]:
            if vt in ("Med", "Feedback"):
                model.Add(modality_var[ci, vt] == 1)

        if case["case_index"] != 0:
            # The shadow-vs-lead exception only ever applies to a
            # fellow's very first case — every other case is always
            # in-person for KSADS visits too.
            for vt in case["template"]:
                if vt.startswith("KSADS"):
                    model.Add(modality_var[ci, vt] == 1)
            continue

        is_primary_marvin = reified_eq(model, primary_var[ci], SUPERVISOR_INDEX["Marvin"],
                                        f"marvin_primary_{ci}")
        for vt in ("KSADS1", "KSADS2"):
            model.Add(modality_var[ci, vt] == 0).OnlyEnforceIf(is_primary_marvin)       # telehealth
            model.Add(modality_var[ci, vt] == 1).OnlyEnforceIf(is_primary_marvin.Not())  # default in-person

        if ci in secondary_var:
            is_secondary_marvin = reified_eq(model, secondary_var[ci], SUPERVISOR_INDEX["Marvin"],
                                              f"marvin_secondary_{ci}")
            model.Add(modality_var[ci, "KSADS3"] == 0).OnlyEnforceIf(is_secondary_marvin)       # telehealth
            model.Add(modality_var[ci, "KSADS3"] == 1).OnlyEnforceIf(is_secondary_marvin.Not())  # default in-person

    # ======================================================================
    # Soft rules — four separately-named penalty lists so the summary can
    # report a per-rule breakdown, not just one aggregate count.
    # ======================================================================

    # ---- Soft 1: KSADS3 supervisor should match the primary supervisor ----
    # ksads3_match_info runs parallel to penalty_ksads3_mismatch, recording
    # which (case, fellow) each `match` bool belongs to, so that after
    # solving we can report *which* cases mismatched, not just the count.
    penalty_ksads3_mismatch = []
    ksads3_match_info = []  # (match_bool, fellow, case_index)
    for ci, case in enumerate(cases):
        if ci not in secondary_var:
            continue
        match = reified_eq(model, secondary_var[ci], primary_var[ci], f"ksads3_match_{ci}")
        penalty_ksads3_mismatch.append(match.Not())
        ksads3_match_info.append((match, case["fellow"], case["case_index"]))

    # ---- Soft 2: each full-time fellow should have >=1 case with each PhD
    # supervisor across their cases. Research fellows are structurally
    # unsatisfiable here (only 2 cases for 3 supervisors) and must not be
    # penalized — so they simply produce no bools in this list at all.
    # supervisor_variety_info runs parallel to penalty_supervisor_variety,
    # recording which fellow each `covered` bool belongs to (the specific
    # missing supervisor isn't reported — just the fellow's name).
    penalty_supervisor_variety = []
    supervisor_variety_info = []  # (covered_bool, fellow)
    for fellow, case_ids in by_fellow.items():
        if fellows[fellow]["type"] != "full-time":
            continue
        for s_name, s_idx in SUPERVISOR_INDEX.items():
            eqs = [reified_eq(model, primary_var[ci], s_idx, f"cov_{fellow}_{s_name}_{ci}")
                   for ci in case_ids]
            covered = model.NewBoolVar(f"covered_{fellow}_{s_name}")
            model.AddMaxEquality(covered, eqs)
            penalty_supervisor_variety.append(covered.Not())
            supervisor_variety_info.append((covered, fellow))

    # ---- Soft 3: Med position by tier (tier1: not first; tier2: first) ----
    # med_position_info runs parallel to penalty_med_position, recording
    # which (case, fellow) each violation bool belongs to.
    penalty_med_position = []
    med_position_info = []  # (violation_bool, fellow, case_index)
    for ci, case in enumerate(cases):
        med_after_first = model.NewBoolVar(f"med_after_first_{ci}")
        model.Add(date_var[ci, "Med"] > date_var[ci, "KSADS1"]).OnlyEnforceIf(med_after_first)
        model.Add(date_var[ci, "Med"] < date_var[ci, "KSADS1"]).OnlyEnforceIf(med_after_first.Not())
        if case["tier"] == 1:
            violation = med_after_first.Not()  # prefer NOT first
        else:
            violation = med_after_first  # prefer first
        penalty_med_position.append(violation)
        med_position_info.append((violation, case["fellow"], case["case_index"]))

    # ---- Soft 4: a fellow's next case shouldn't need the "late Feedback"
    # escape valve (Rule 6 above) — i.e., shouldn't have to start the very
    # same day as the previous case's Feedback ----
    # Every date is a Monday (Rule 1), and case N+1 must start on or after
    # case N's Feedback (Rule 6) — so the real calendar gap between them is
    # always either exactly 0 (a same-day tie, only possible via the late-
    # Feedback escape valve) or at least 7 days (any two *different*
    # Mondays are always >=7 real days apart, holidays only ever widen
    # that, never shrink it). There's no way to land "1-6 days apart," so
    # `late_feedback_var[a]` — already an exact reified equality — is both
    # the complete definition of this rule's violation AND automatically
    # correct regardless of how many holiday Mondays fall between the two
    # dates; no separate day-counting is needed.
    penalty_case_gap = []
    case_gap_info = []  # (late_bool, fellow, case_index_a, case_index_b)
    for fellow, case_ids in by_fellow.items():
        case_ids_sorted = sorted(case_ids, key=lambda ci: cases[ci]["case_index"])
        for a, b in zip(case_ids_sorted, case_ids_sorted[1:]):
            penalty_case_gap.append(late_feedback_var[a])
            case_gap_info.append((late_feedback_var[a], fellow, cases[a]["case_index"], cases[b]["case_index"]))

    # Supervisor-related soft rules (1, 2) weighted noticeably higher than
    # positional ones (3, 4) — supervisor continuity matters more to the
    # coordinator than visit ordering. (Rule 8 — supervisor conflict — is
    # NOT part of this objective at all now; it's a hard forbid above.)
    W_SUP, W_POS = 10, 1
    model.Minimize(
        W_SUP * (sum(penalty_supervisor_variety) + sum(penalty_ksads3_mismatch))
        + W_POS * (sum(penalty_med_position) + sum(penalty_case_gap))
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": "INFEASIBLE", "cases": [], "soft_violations": {},
                "soft_violation_details": {}, "supervisor_case_load": {}}

    def val(lst):
        return solver.Value(sum(lst)) if lst else 0

    # ---- soft_violation_details: which specific (fellow, case) each soft
    # rule's count is made of, independently re-derived from the same bools
    # `val()` sums above — a frontend pairs "count" and "details" by the
    # identical string keys used in soft_violations.
    ksads3_mismatch_details = [
        {"fellow": fellow, "case_index": case_index}
        for match, fellow, case_index in ksads3_match_info
        if solver.Value(match) == 0
    ]

    med_position_details = [
        {"fellow": fellow, "case_index": case_index}
        for violation, fellow, case_index in med_position_info
        if solver.Value(violation) == 1
    ]

    # case_gap is inherently about a *transition* between two consecutive
    # cases, not a single case — each tight transition implicates both the
    # earlier case (whose Feedback started the gap) and the later case
    # (whose first visit ended it). A case can show up via two different
    # tight transitions (incoming and outgoing), so this list is deduped by
    # (fellow, case_index). NOTE: because of this, len(case_gap_details)
    # will generally NOT equal soft_violations["case_gap (soft #4)"] — the
    # count is a count of tight *transitions*, this list is a set of
    # *cases* touched by at least one tight transition. That mismatch is
    # expected and correct; do not "fix" it into a spurious equality.
    case_gap_details = []
    _case_gap_seen = set()
    for late, fellow, case_index_a, case_index_b in case_gap_info:
        if solver.Value(late) == 1:
            for case_index in (case_index_a, case_index_b):
                key = (fellow, case_index)
                if key not in _case_gap_seen:
                    _case_gap_seen.add(key)
                    case_gap_details.append({"fellow": fellow, "case_index": case_index})

    # supervisor_variety: deduped to unique fellow names, not (fellow,
    # supervisor) pairs — a fellow missing 2 of the 3 PhD supervisors
    # contributes 2 to soft_violations' raw count but only 1 entry here.
    # That mismatch is expected and correct; do not "fix" it into a
    # spurious equality.
    supervisor_variety_details = []
    _supervisor_variety_seen = set()
    for covered, fellow in supervisor_variety_info:
        if solver.Value(covered.Not()) == 1 and fellow not in _supervisor_variety_seen:
            _supervisor_variety_seen.add(fellow)
            supervisor_variety_details.append(fellow)

    out_cases = []
    for ci, case in enumerate(cases):
        primary_name = INDEX_TO_SUPERVISOR[solver.Value(primary_var[ci])]
        secondary_name = (INDEX_TO_SUPERVISOR[solver.Value(secondary_var[ci])]
                           if ci in secondary_var else None)
        visits = []
        for vt in case["template"]:
            d = all_mondays[solver.Value(date_var[ci, vt])]
            modality = "in-person" if solver.Value(modality_var[ci, vt]) else "telehealth"
            if vt in ("Med", "Feedback"):
                sup = md_supervisor
            elif vt == "KSADS3":
                sup = secondary_name
            else:  # KSADS1, KSADS2
                sup = primary_name
            # "late" only ever applies to Feedback (the 2:15 escape-valve
            # slot, Rule 6) — always False elsewhere. A fellow's last case
            # has no following case, so it has no entry in
            # late_feedback_var at all; that's correctly just False too.
            late = vt == "Feedback" and ci in late_feedback_var and bool(solver.Value(late_feedback_var[ci]))
            visits.append({"type": vt, "date": d.isoformat(), "modality": modality,
                            "supervisor": sup, "late": late})
        out_cases.append({
            "fellow": case["fellow"],
            "case_index": case["case_index"],
            "tier": case["tier"],
            "primary_supervisor": primary_name,
            "secondary_supervisor": secondary_name,
            "visits": visits,
        })

    supervisor_case_load = {s_name: 0 for s_name in SUPERVISOR_INDEX}
    for ci in range(len(cases)):
        supervisor_case_load[INDEX_TO_SUPERVISOR[solver.Value(primary_var[ci])]] += 1

    return {
        "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
        "soft_violations": {
            "supervisor_variety (soft #2)": val(penalty_supervisor_variety),
            "ksads3_mismatch (soft #1)": val(penalty_ksads3_mismatch),
            "med_position (soft #3)": val(penalty_med_position),
            "case_gap (soft #4)": val(penalty_case_gap),
        },
        "soft_violation_details": {
            "supervisor_variety (soft #2)": supervisor_variety_details,
            "ksads3_mismatch (soft #1)": ksads3_mismatch_details,
            "med_position (soft #3)": med_position_details,
            "case_gap (soft #4)": case_gap_details,
        },
        "supervisor_case_load": supervisor_case_load,
        "cases": out_cases,
    }
