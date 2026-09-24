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


def solve(config: dict, time_limit_s: int = 30):
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
    for ci, case in enumerate(cases):
        model.Add(sum(modality_var[ci, vt] for vt in case["template"]) >= 1)

    # ---- Rule 6: a fellow's cases run strictly sequentially ----
    # EVERY visit in case N+1 (not just its first) must fall after case N's
    # Feedback — bounding only the first visit lets Med float outside its
    # case's window (this was a real bug in an earlier prototype).
    by_fellow = {}
    for ci, case in enumerate(cases):
        by_fellow.setdefault(case["fellow"], []).append(ci)
    for fellow, case_ids in by_fellow.items():
        case_ids_sorted = sorted(case_ids, key=lambda ci: cases[ci]["case_index"])
        for a, b in zip(case_ids_sorted, case_ids_sorted[1:]):
            for vt in cases[b]["template"]:
                model.Add(date_var[b, vt] > date_var[a, "Feedback"])

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

    # ---- Rule 10: case_index==0 with Marvin as primary supervisor ----
    # Applies ONLY to the case with case_index == 0 for a fellow, and ONLY
    # when THAT case's primary supervisor is Marvin — deliberately not the
    # fellow's chronologically-first Marvin case (v1's approach), which is
    # a different, now-incorrect rule per the current spec. The condition
    # checks primary_var only: if the KSADS3 escape valve later reassigns
    # away from Marvin, KSADS3 still stays telehealth here, since this rule
    # is about the case's designated primary supervisor, not who actually
    # ends up running KSADS3.
    for fellow, case_ids in by_fellow.items():
        case0 = next(ci for ci in case_ids if cases[ci]["case_index"] == 0)
        is_primary_marvin = reified_eq(model, primary_var[case0], SUPERVISOR_INDEX["Marvin"],
                                        f"case0_marvin_{fellow}")
        for vt in cases[case0]["template"]:
            if vt.startswith("KSADS"):
                model.Add(modality_var[case0, vt] == 0).OnlyEnforceIf(is_primary_marvin)  # telehealth
            elif vt == "Med":
                model.Add(modality_var[case0, vt] == 1).OnlyEnforceIf(is_primary_marvin)  # in-person

    # ======================================================================
    # Soft rules — four separately-named penalty lists so the summary can
    # report a per-rule breakdown, not just one aggregate count.
    # ======================================================================

    # ---- Soft 1: KSADS3 supervisor should match the primary supervisor ----
    penalty_ksads3_mismatch = []
    for ci, case in enumerate(cases):
        if ci not in secondary_var:
            continue
        match = reified_eq(model, secondary_var[ci], primary_var[ci], f"ksads3_match_{ci}")
        penalty_ksads3_mismatch.append(match.Not())

    # ---- Soft 2: each full-time fellow should have >=1 case with each PhD
    # supervisor across their cases. Research fellows are structurally
    # unsatisfiable here (only 2 cases for 3 supervisors) and must not be
    # penalized — so they simply produce no bools in this list at all.
    penalty_supervisor_variety = []
    for fellow, case_ids in by_fellow.items():
        if fellows[fellow]["type"] != "full-time":
            continue
        for s_name, s_idx in SUPERVISOR_INDEX.items():
            eqs = [reified_eq(model, primary_var[ci], s_idx, f"cov_{fellow}_{s_name}_{ci}")
                   for ci in case_ids]
            covered = model.NewBoolVar(f"covered_{fellow}_{s_name}")
            model.AddMaxEquality(covered, eqs)
            penalty_supervisor_variety.append(covered.Not())

    # ---- Soft 3: Med position by tier (tier1: not first; tier2: first) ----
    penalty_med_position = []
    for ci, case in enumerate(cases):
        med_after_first = model.NewBoolVar(f"med_after_first_{ci}")
        model.Add(date_var[ci, "Med"] > date_var[ci, "KSADS1"]).OnlyEnforceIf(med_after_first)
        model.Add(date_var[ci, "Med"] < date_var[ci, "KSADS1"]).OnlyEnforceIf(med_after_first.Not())
        if case["tier"] == 1:
            penalty_med_position.append(med_after_first.Not())  # prefer NOT first
        else:
            penalty_med_position.append(med_after_first)  # prefer first

    # ---- Soft 4: ~1 week (7+ days) gap between a fellow's successive cases ----
    penalty_case_gap = []
    for fellow, case_ids in by_fellow.items():
        case_ids_sorted = sorted(case_ids, key=lambda ci: cases[ci]["case_index"])
        for a, b in zip(case_ids_sorted, case_ids_sorted[1:]):
            first_vt_b = cases[b]["template"][0]
            gap = model.NewIntVar(0, 400, f"gap_{a}_{b}")
            model.Add(gap == date_var[b, first_vt_b] - date_var[a, "Feedback"])
            tight = model.NewBoolVar(f"tight_{a}_{b}")
            model.Add(gap < 7).OnlyEnforceIf(tight)
            model.Add(gap >= 7).OnlyEnforceIf(tight.Not())
            penalty_case_gap.append(tight)

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
        return {"status": "INFEASIBLE", "cases": [], "soft_violations": {}, "supervisor_case_load": {}}

    def val(lst):
        return solver.Value(sum(lst)) if lst else 0

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
            visits.append({"type": vt, "date": d.isoformat(), "modality": modality,
                            "supervisor": sup})
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
        "supervisor_case_load": supervisor_case_load,
        "cases": out_cases,
    }
