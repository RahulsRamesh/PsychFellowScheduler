"use strict";

// PhD supervisor names are hardcoded here (never read from an input) so
// they can never be mistyped — this must match SUPERVISOR_INDEX in
// v2/helpers.py exactly, since Rule 10 is tied to the specific name
// "Marvin". If the backend's roster ever changes, this list and the
// static blocks in index.html both need updating together.
const PHD_NAMES = ["Walshaw", "Ellis", "Marvin"];

const form = document.getElementById("schedule-form");
const formErrorsBox = document.getElementById("form-errors");
const formErrorsList = document.getElementById("form-errors-list");
const fellowsList = document.getElementById("fellows-list");
const submitBtn = document.getElementById("submit-btn");
const loadingEl = document.getElementById("loading");
const loadingMessageEl = document.getElementById("loading-message");
const errorBanner = document.getElementById("error-banner");
const errorMessageEl = document.getElementById("error-message");
const retryBtn = document.getElementById("retry-btn");
const resultsEl = document.getElementById("results");

let serverLikelyWarm = false;
let pendingRetryConfig = null;

// ---------------------------------------------------------------------
// Wake the Render free-tier instance the moment the page loads, well
// before the coordinator finishes the form — the single highest-leverage
// thing we can do about the ~30-60s cold start. No custom headers, so no
// CORS preflight.
// ---------------------------------------------------------------------
fetch(`${API_BASE}/health`).then((r) => { if (r.ok) serverLikelyWarm = true; }).catch(() => {});

// ---------------------------------------------------------------------
// Dynamic row add/remove — one delegated listener handles every
// "add a date-range row" / "remove this row" button on the page,
// including ones inside dynamically-added fellow blocks.
// ---------------------------------------------------------------------
document.body.addEventListener("click", (e) => {
  const addRowBtn = e.target.closest('[data-action="add-row"]');
  if (addRowBtn) {
    const tpl = document.querySelector(addRowBtn.dataset.template);
    const scope = addRowBtn.closest(".row") || document;
    const target = scope.querySelector(addRowBtn.dataset.target) || document.querySelector(addRowBtn.dataset.target);
    target.appendChild(tpl.content.cloneNode(true));
    return;
  }

  const addFellowBtn = e.target.closest('[data-action="add-fellow"]');
  if (addFellowBtn) {
    addFellowRow();
    return;
  }

  const removeBtn = e.target.closest('[data-action="remove-row"]');
  if (removeBtn) {
    removeBtn.closest(".row").remove();
  }
});

function addFellowRow() {
  const tpl = document.getElementById("tpl-fellow-row");
  fellowsList.appendChild(tpl.content.cloneNode(true));
}

// Start with one empty fellow row so the form isn't empty on load.
addFellowRow();

// ---------------------------------------------------------------------
// Dev-only "Load sample data" button — gated on config.js's
// SHOW_LOAD_SAMPLE_BUTTON. Testing convenience, not coordinator-facing.
// ---------------------------------------------------------------------

function addFilledDateRangeRow(container, start, end) {
  const tpl = document.getElementById("tpl-daterange-row");
  const node = tpl.content.cloneNode(true);
  node.querySelector(".start").value = start;
  node.querySelector(".end").value = end;
  container.appendChild(node);
}

function loadSampleData() {
  document.getElementById("clinic_start").value = SAMPLE_DATA.clinic_start;
  document.getElementById("clinic_end").value = SAMPLE_DATA.clinic_end;

  const holidaysListEl = document.getElementById("holidays-list");
  holidaysListEl.innerHTML = "";
  SAMPLE_DATA.holidays.forEach(([s, e]) => addFilledDateRangeRow(holidaysListEl, s, e));

  PHD_NAMES.forEach((name) => {
    const container = document.querySelector(`[data-supervisor="${name}"]`);
    container.innerHTML = "";
    (SAMPLE_DATA.supervisor_vacations[name] || []).forEach(([s, e]) => addFilledDateRangeRow(container, s, e));
  });

  document.getElementById("md-name").value = SAMPLE_DATA.md.name;
  const mdVacationsEl = document.getElementById("md-vacations-list");
  mdVacationsEl.innerHTML = "";
  SAMPLE_DATA.md.vacations.forEach(([s, e]) => addFilledDateRangeRow(mdVacationsEl, s, e));

  fellowsList.innerHTML = "";
  SAMPLE_DATA.fellows.forEach((f) => {
    addFellowRow();
    const row = fellowsList.lastElementChild;
    row.querySelector(".fellow-name").value = f.name;
    row.querySelector(".fellow-type").value = f.type;
    const vacationsEl = row.querySelector(".vacation-list");
    f.vacations.forEach(([s, e]) => addFilledDateRangeRow(vacationsEl, s, e));
  });

  // Reset any stale validation/result state left over from a prior attempt.
  document.querySelectorAll(".invalid").forEach((el) => el.classList.remove("invalid"));
  document.querySelectorAll(".field-error").forEach((el) => { el.textContent = ""; });
  formErrorsBox.hidden = true;
  hideError();
  resultsEl.hidden = true;
}

if (typeof SHOW_LOAD_SAMPLE_BUTTON !== "undefined" && SHOW_LOAD_SAMPLE_BUTTON) {
  document.getElementById("dev-tools").hidden = false;
  document.getElementById("load-sample-btn").addEventListener("click", loadSampleData);
}

// ---------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------

// clinic_start/clinic_end share one error element, and md-name's error
// element is a sibling outside its <label> — neither is reachable via a
// generic closest(".row") lookup the way repeatable row fields are, so
// they're mapped explicitly rather than guessed from DOM structure.
const EXPLICIT_ERROR_TARGETS = {
  clinic_start: "clinic-dates-error",
  clinic_end: "clinic-dates-error",
  "md-name": "md-name-error",
};

function errorElementFor(el) {
  const explicitId = EXPLICIT_ERROR_TARGETS[el.id];
  if (explicitId) return document.getElementById(explicitId);
  return el.closest(".row")?.querySelector(".field-error");
}

function clearFieldError(el) {
  el.classList.remove("invalid");
  const small = errorElementFor(el);
  if (small) small.textContent = "";
}

function setFieldError(el, message) {
  el.classList.add("invalid");
  const small = errorElementFor(el);
  if (small) small.textContent = message;
}

// Clear a field's error as soon as the coordinator edits it, rather than
// only on re-submit.
document.body.addEventListener("input", (e) => {
  if (e.target.matches("input, select")) clearFieldError(e.target);
});

function mondaysBetween(start, end) {
  const out = [];
  const d = new Date(start + "T00:00:00");
  const endDate = new Date(end + "T00:00:00");
  while (d.getDay() !== 1) d.setDate(d.getDate() + 1);
  while (d <= endDate) {
    out.push(new Date(d));
    d.setDate(d.getDate() + 7);
  }
  return out;
}

function dateInRanges(date, ranges) {
  return ranges.some(([s, e]) => date >= new Date(s + "T00:00:00") && date <= new Date(e + "T00:00:00"));
}

function validateDateRangeRows(container, errors) {
  container.querySelectorAll(".date-range-row").forEach((row) => {
    const startEl = row.querySelector(".start");
    const endEl = row.querySelector(".end");
    if (!startEl.value || !endEl.value) {
      const el = !startEl.value ? startEl : endEl;
      setFieldError(el, "Both dates are required.");
      errors.push({ message: "A date range is missing a start or end date.", element: el });
      return;
    }
    if (endEl.value < startEl.value) {
      setFieldError(endEl, "End date must be on or after the start date.");
      errors.push({ message: "A date range ends before it starts.", element: endEl });
    }
  });
}

function validate() {
  const errors = [];

  const clinicStartEl = document.getElementById("clinic_start");
  const clinicEndEl = document.getElementById("clinic_end");
  if (!clinicStartEl.value || !clinicEndEl.value) {
    const el = !clinicStartEl.value ? clinicStartEl : clinicEndEl;
    setFieldError(el, "Required.");
    errors.push({ message: "Clinic start/end date is required.", element: el });
  } else if (clinicEndEl.value <= clinicStartEl.value) {
    setFieldError(clinicEndEl, "End date must be after the start date.");
    errors.push({ message: "Clinic end date must be after the start date.", element: clinicEndEl });
  }

  validateDateRangeRows(document.getElementById("holidays-list"), errors);
  PHD_NAMES.forEach((name) => {
    validateDateRangeRows(document.querySelector(`[data-supervisor="${name}"]`), errors);
  });

  const mdNameEl = document.getElementById("md-name");
  if (!mdNameEl.value.trim()) {
    setFieldError(mdNameEl, "MD supervisor name is required.");
    errors.push({ message: "MD supervisor name is required.", element: mdNameEl });
  }
  validateDateRangeRows(document.getElementById("md-vacations-list"), errors);

  const fellowRows = [...fellowsList.querySelectorAll(".fellow-row")];
  if (fellowRows.length === 0) {
    errors.push({ message: "At least one fellow is required.", element: document.querySelector('[data-action="add-fellow"]') });
  }
  const seenNames = new Set();
  fellowRows.forEach((row) => {
    const nameEl = row.querySelector(".fellow-name");
    const name = nameEl.value.trim();
    if (!name) {
      setFieldError(nameEl, "Fellow name is required.");
      errors.push({ message: "A fellow is missing a name.", element: nameEl });
    } else if (seenNames.has(name)) {
      setFieldError(nameEl, "Fellow names must be unique.");
      errors.push({ message: `Duplicate fellow name: ${name}`, element: nameEl });
    } else {
      seenNames.add(name);
    }
    validateDateRangeRows(row.querySelector(".vacation-list"), errors);
  });

  // Cheap sanity check mirroring backend Rules 1/2 — at least one
  // non-holiday Monday must exist in the clinic window at all. Per-
  // fellow/supervisor vacation-driven infeasibility is NOT duplicated
  // here; that needs the full solver domain logic and is what the
  // backend's 422 response is for.
  if (clinicStartEl.value && clinicEndEl.value && clinicEndEl.value > clinicStartEl.value) {
    const holidayRanges = [...document.querySelectorAll("#holidays-list .date-range-row")]
      .map((row) => [row.querySelector(".start").value, row.querySelector(".end").value])
      .filter(([s, e]) => s && e);
    const mondays = mondaysBetween(clinicStartEl.value, clinicEndEl.value);
    const hasFreeMonday = mondays.some((d) => !dateInRanges(d, holidayRanges));
    if (!hasFreeMonday) {
      errors.push({ message: "Every Monday in the clinic window falls on a holiday — no appointments could ever be scheduled.", element: clinicEndEl });
    }
  }

  return errors;
}

function showFormErrors(errors) {
  if (errors.length === 0) {
    formErrorsBox.hidden = true;
    formErrorsList.innerHTML = "";
    return;
  }
  formErrorsList.innerHTML = "";
  errors.forEach(({ message, element }) => {
    const li = document.createElement("li");
    li.textContent = message;
    li.addEventListener("click", () => {
      element?.scrollIntoView({ behavior: "smooth", block: "center" });
      element?.focus();
    });
    formErrorsList.appendChild(li);
  });
  formErrorsBox.hidden = false;
  formErrorsBox.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------------
// Config assembly — walk the DOM directly into the shape solve() wants.
// ---------------------------------------------------------------------

function readDateRanges(container) {
  return [...container.querySelectorAll(".date-range-row")].map((row) => [
    row.querySelector(".start").value,
    row.querySelector(".end").value,
  ]);
}

function assembleConfig() {
  const holidays = readDateRanges(document.getElementById("holidays-list"));

  const supervisors = PHD_NAMES.map((name) => ({
    name,
    role: "PhD",
    vacations: readDateRanges(document.querySelector(`[data-supervisor="${name}"]`)),
  }));
  supervisors.push({
    name: document.getElementById("md-name").value.trim(),
    role: "MD",
    vacations: readDateRanges(document.getElementById("md-vacations-list")),
  });

  const fellows = [...fellowsList.querySelectorAll(".fellow-row")].map((row) => ({
    name: row.querySelector(".fellow-name").value.trim(),
    type: row.querySelector(".fellow-type").value,
    vacations: readDateRanges(row.querySelector(".vacation-list")),
  }));

  return {
    clinic_start: document.getElementById("clinic_start").value,
    clinic_end: document.getElementById("clinic_end").value,
    holidays,
    supervisors,
    fellows,
  };
}

// ---------------------------------------------------------------------
// Solve interaction
// ---------------------------------------------------------------------

function showLoading(message) {
  loadingEl.hidden = false;
  loadingMessageEl.textContent = message;
}

function hideLoading() {
  loadingEl.hidden = true;
}

function showError(message, { retry } = {}) {
  errorMessageEl.textContent = message;
  errorBanner.hidden = false;
  retryBtn.hidden = !retry;
  errorBanner.scrollIntoView({ behavior: "smooth", block: "start" });
}

function hideError() {
  errorBanner.hidden = true;
  retryBtn.hidden = true;
}

async function runSolve(config) {
  hideError();
  resultsEl.hidden = true;
  submitBtn.disabled = true;
  showLoading("Contacting scheduler…");

  const wakingTimer = setTimeout(() => {
    if (!serverLikelyWarm) {
      showLoading("Waking up the scheduling server — this can take up to a minute if it's been idle. Please don't close this tab.");
    }
  }, 4000);

  const controller = new AbortController();
  const timeoutTimer = setTimeout(() => controller.abort(), SOLVE_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE}/solve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify(config),
      signal: controller.signal,
    });

    if (response.status === 401) {
      showError("This tool is temporarily unable to authenticate with the scheduling server. This is not something you can fix — please contact the maintainer.");
      return;
    }

    if (response.status === 422) {
      const body = await response.json().catch(() => ({}));
      showError(`The server rejected this schedule request: ${body.detail || "invalid input."}`);
      return;
    }

    if (!response.ok) {
      showError(`Something went wrong on the server (HTTP ${response.status}). Please try again, and contact support if it persists.`);
      return;
    }

    const body = await response.json();
    serverLikelyWarm = true;

    if (body.result.status === "INFEASIBLE") {
      showError("No valid schedule could be found with these inputs. This usually means vacation or holiday dates leave too little room for the hard scheduling rules to be satisfied. Try adjusting vacation dates and solving again.");
      return;
    }

    renderResults(body.result, body.hard_rule_violations);
  } catch (err) {
    if (err.name === "AbortError") {
      pendingRetryConfig = config;
      showError("The server took too long to respond — it may still be waking up. Please wait a moment and try again.", { retry: true });
    } else {
      pendingRetryConfig = config;
      showError("Could not reach the scheduling server. Check your internet connection and try again.", { retry: true });
    }
  } finally {
    clearTimeout(wakingTimer);
    clearTimeout(timeoutTimer);
    hideLoading();
    submitBtn.disabled = false;
  }
}

retryBtn.addEventListener("click", () => {
  if (pendingRetryConfig) runSolve(pendingRetryConfig);
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const errors = validate();
  showFormErrors(errors);
  if (errors.length > 0) return;
  hideError();
  showFormErrors([]);
  runSolve(assembleConfig());
});

// ---------------------------------------------------------------------
// Results rendering
// ---------------------------------------------------------------------

const SOFT_RULE_LABELS = {
  "supervisor_variety (soft #2)": "Supervisor variety",
  "ksads3_mismatch (soft #1)": "KSADS3 supervisor mismatch",
  "med_position (soft #3)": "Med visit position",
  "case_gap (soft #4)": "Late Feedback escape valve",
};

const SOFT_RULE_TOOLTIPS = {
  "supervisor_variety (soft #2)": "How many full-time fellows failed to get at least one case with each of the 3 PhD supervisors across their 4 cases. Research fellows are excluded — structurally impossible with only 2 cases.",
  "ksads3_mismatch (soft #1)": "How many tier-1 cases had KSADS3 supervised by someone different from KSADS1/2's supervisor (the 'escape valve,' used to avoid a double-booking).",
  "med_position (soft #3)": "How many cases broke the preferred Med-visit timing: tier-1 cases shouldn't start with Med, tier-2 cases should.",
  "case_gap (soft #4)": "How many times the next case had to start on the SAME day as the previous case's Feedback, using the 'late Feedback' escape valve (Feedback pushed to 2:15 so it still follows that day's KSADS1 in real time). Every other transition is always naturally ≥7 days apart on its own.",
};

function supervisorLoadTooltip(name) {
  return `Number of cases where ${name} is the primary KSADS1/2 supervisor, across the whole schedule — a sanity check for balanced workload.`;
}

// Tiles are clickable (see renderResults) — a div with role="button"
// rather than a <button>, since the tile's block-level children aren't
// valid inside a real <button>.
function statTile(label, value, max, tooltip) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  const div = document.createElement("div");
  div.className = "stat-tile";
  div.setAttribute("role", "button");
  div.setAttribute("tabindex", "0");
  div.setAttribute("aria-pressed", "false");
  div.innerHTML = `
    <span class="stat-info" aria-hidden="true">ⓘ</span>
    <div class="stat-label">${label}</div>
    <div class="stat-value">${value}</div>
    <div class="stat-bar-track"><div class="stat-bar-fill" style="width:${pct}%"></div></div>
  `;
  if (tooltip) {
    div.querySelector(".stat-info").title = tooltip;
    div.setAttribute("aria-description", tooltip);
  }
  return div;
}

function supervisorText(c) {
  return c.secondary_supervisor && c.secondary_supervisor !== c.primary_supervisor
    ? `${c.primary_supervisor} (KSADS3: ${c.secondary_supervisor})`
    : c.primary_supervisor;
}

// visitOrder "standard" keeps the template order (KSADS1, KSADS2, KSADS3,
// Med, Feedback); "date" sorts chronologically instead. Dates are ISO
// strings (YYYY-MM-DD), so a plain string sort is already chronological —
// no Date parsing needed. All visits in a case have distinct dates (hard
// rule), so there's never a tie to break.
function visitTable(visits, visitOrder) {
  const ordered = visitOrder === "date"
    ? [...visits].sort((a, b) => a.date.localeCompare(b.date))
    : visits;
  const table = document.createElement("table");
  table.className = "visit-table";
  table.innerHTML = `
    <thead><tr><th>Type</th><th>Date</th><th>Modality</th><th>Supervisor</th></tr></thead>
    <tbody>
      ${ordered.map((v) => `<tr><td>${v.type}</td><td>${v.date}${v.late ? ' <span class="late-badge" title="Late Feedback escape valve: pushed to 2:15 so it still follows this day’s KSADS1 in real time">2:15pm</span>' : ""}</td><td>${v.modality}</td><td>${v.supervisor}</td></tr>`).join("")}
    </tbody>
  `;
  return table;
}

// Groups cases either by fellow (each fellow's cases in case-# order) or
// by case #, i.e. "round" (all fellows' case 0s together, then all case
// 1s, etc. — mirrors the coordinator's original Excel layout). Research
// fellows only have cases 0-1, so case-2/3 groups naturally contain only
// full-time fellows — that's correct, not a bug.
//
// Every case block is stamped with data-fellow/data-case-index, and every
// fellow-name label (the by-fellow <summary>, or the name <span> inside a
// by-case# <h4>) gets class "fellow-label" + data-fellow, so stat-tile
// highlighting can target elements identically in either grouping.
function caseBlockFor(c, headingPrefixNodes, visitOrder) {
  const caseBlock = document.createElement("div");
  caseBlock.className = "case-block";
  caseBlock.dataset.fellow = c.fellow;
  caseBlock.dataset.caseIndex = String(c.case_index);
  const h4 = document.createElement("h4");
  h4.append(...headingPrefixNodes);
  caseBlock.appendChild(h4);
  caseBlock.appendChild(visitTable(c.visits, visitOrder));
  return caseBlock;
}

function fellowLabel(tagName, fellow) {
  const el = document.createElement(tagName);
  el.className = "fellow-label";
  el.dataset.fellow = fellow;
  el.textContent = fellow;
  return el;
}

function renderSchedule(container, cases, groupBy, visitOrder) {
  container.innerHTML = "";

  if (groupBy === "case") {
    const byCaseIndex = new Map();
    cases.forEach((c) => {
      if (!byCaseIndex.has(c.case_index)) byCaseIndex.set(c.case_index, []);
      byCaseIndex.get(c.case_index).push(c);
    });

    [...byCaseIndex.keys()].sort((a, b) => a - b).forEach((caseIndex) => {
      const group = byCaseIndex.get(caseIndex).sort((a, b) => a.fellow.localeCompare(b.fellow));
      const details = document.createElement("details");
      details.className = "fellow-block";
      details.open = true;

      const summary = document.createElement("summary");
      summary.textContent = `Case ${caseIndex + 1}`;
      details.appendChild(summary);

      group.forEach((c) => {
        details.appendChild(caseBlockFor(c, [fellowLabel("span", c.fellow), ` — Supervisor: ${supervisorText(c)}`], visitOrder));
      });

      container.appendChild(details);
    });
    return;
  }

  // groupBy === "fellow" (default)
  const byFellow = new Map();
  cases.forEach((c) => {
    if (!byFellow.has(c.fellow)) byFellow.set(c.fellow, []);
    byFellow.get(c.fellow).push(c);
  });

  byFellow.forEach((fellowCases, fellow) => {
    const details = document.createElement("details");
    details.className = "fellow-block";
    details.open = true;

    details.appendChild(fellowLabel("summary", fellow));

    fellowCases
      .sort((a, b) => a.case_index - b.case_index)
      .forEach((c) => {
        details.appendChild(caseBlockFor(c, [`Case ${c.case_index + 1} — Supervisor: ${supervisorText(c)}`], visitOrder));
      });

    container.appendChild(details);
  });
}

function renderResults(result, hardRuleViolations) {
  resultsEl.innerHTML = "";
  resultsEl.hidden = false;

  const statusClass = result.status === "OPTIMAL" ? "optimal" : "feasible";
  const statusLabel = result.status === "OPTIMAL" ? "Optimal schedule found" : "Feasible schedule found (not proven optimal)";
  const statusBadge = document.createElement("span");
  statusBadge.className = `status-badge ${statusClass}`;
  statusBadge.textContent = statusLabel;
  resultsEl.appendChild(statusBadge);

  const verifyLine = document.createElement("p");
  if (hardRuleViolations.length === 0) {
    verifyLine.className = "verify-line ok";
    verifyLine.textContent = "✓ All hard scheduling rules verified.";
  } else {
    verifyLine.className = "verify-line bad";
    verifyLine.innerHTML = "This should not happen — please report this:<br>" +
      hardRuleViolations.map((v) => `• ${v}`).join("<br>");
  }
  resultsEl.appendChild(verifyLine);

  // Click-to-highlight: at most one tile is active at a time. The active
  // tile's highlight target lives here (not in the DOM) so it survives the
  // schedule re-render that the group-by toggle triggers. A fresh
  // renderResults() call starts with nothing active.
  //   target = { fellowNames: Set<string> }            — name-only highlight
  //          | { caseKeys: Set<"fellow\u0000index"> }  — full case-block highlight
  let activeTile = null;
  let activeTarget = null;
  const caseKey = (fellow, caseIndex) => `${fellow}\u0000${caseIndex}`;
  const softDetails = result.soft_violation_details || {};

  function targetForSoftRule(key) {
    const entries = softDetails[key] || [];
    if (key.startsWith("supervisor_variety")) {
      return { fellowNames: new Set(entries) };
    }
    return { caseKeys: new Set(entries.map((e) => caseKey(e.fellow, e.case_index))) };
  }

  function targetForSupervisor(name) {
    // Primary only, matching supervisor_case_load's own definition (and
    // this tile's displayed number) exactly — including secondary/KSADS3
    // hand-offs here would highlight more cases than the tile's count,
    // which is confusing rather than more informative.
    return {
      caseKeys: new Set(
        result.cases
          .filter((c) => c.primary_supervisor === name)
          .map((c) => caseKey(c.fellow, c.case_index)),
      ),
    };
  }

  function applyHighlights() {
    scheduleContainer.querySelectorAll(".highlighted").forEach((el) => el.classList.remove("highlighted"));
    if (!activeTarget) return;
    if (activeTarget.fellowNames) {
      scheduleContainer.querySelectorAll(".fellow-label[data-fellow]").forEach((el) => {
        if (activeTarget.fellowNames.has(el.dataset.fellow)) el.classList.add("highlighted");
      });
    } else {
      scheduleContainer.querySelectorAll(".case-block[data-fellow]").forEach((el) => {
        if (activeTarget.caseKeys.has(caseKey(el.dataset.fellow, el.dataset.caseIndex))) el.classList.add("highlighted");
      });
    }
  }

  function wireTile(tile, makeTarget) {
    const toggle = () => {
      if (activeTile) {
        activeTile.classList.remove("active");
        activeTile.setAttribute("aria-pressed", "false");
      }
      if (activeTile === tile) {
        activeTile = null;
        activeTarget = null;
      } else {
        activeTile = tile;
        activeTarget = makeTarget();
        tile.classList.add("active");
        tile.setAttribute("aria-pressed", "true");
      }
      applyHighlights();
    };
    tile.addEventListener("click", toggle);
    tile.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggle();
      }
    });
    return tile;
  }

  const softValues = Object.values(result.soft_violations);
  const softMax = Math.max(1, ...softValues);
  const softGrid = document.createElement("div");
  softGrid.className = "stat-grid";
  Object.entries(result.soft_violations).forEach(([key, value]) => {
    const tile = statTile(SOFT_RULE_LABELS[key] || key, value, softMax, SOFT_RULE_TOOLTIPS[key]);
    tile.dataset.tile = key;
    softGrid.appendChild(wireTile(tile, () => targetForSoftRule(key)));
  });
  resultsEl.appendChild(softGrid);

  const loadValues = Object.values(result.supervisor_case_load);
  const loadMax = Math.max(1, ...loadValues);
  const loadGrid = document.createElement("div");
  loadGrid.className = "stat-grid";
  Object.entries(result.supervisor_case_load).forEach(([name, count]) => {
    const tile = statTile(name, count, loadMax, supervisorLoadTooltip(name));
    tile.dataset.tile = name;
    loadGrid.appendChild(wireTile(tile, () => targetForSupervisor(name)));
  });
  resultsEl.appendChild(loadGrid);

  // Two independent toggles: group-by (fellow vs case #) and visit order
  // within each case's table (standard template order vs chronological).
  // Both are purely client-side re-renders of the already-fetched
  // result.cases — no re-solve needed. Both always reset to their default
  // for a fresh result, regardless of what was selected last time.
  let currentGroupBy = "fellow";
  let currentVisitOrder = "standard";

  function rerenderSchedule() {
    renderSchedule(scheduleContainer, result.cases, currentGroupBy, currentVisitOrder);
    applyHighlights();
  }

  const toggleRow = document.createElement("div");
  toggleRow.className = "group-toggle";
  const byFellowBtn = document.createElement("button");
  byFellowBtn.type = "button";
  byFellowBtn.textContent = "By fellow";
  const byCaseBtn = document.createElement("button");
  byCaseBtn.type = "button";
  byCaseBtn.textContent = "By case #";
  toggleRow.append(byFellowBtn, byCaseBtn);
  resultsEl.appendChild(toggleRow);

  const orderToggleRow = document.createElement("div");
  orderToggleRow.className = "group-toggle";
  const standardOrderBtn = document.createElement("button");
  standardOrderBtn.type = "button";
  standardOrderBtn.textContent = "Standard order";
  const byDateBtn = document.createElement("button");
  byDateBtn.type = "button";
  byDateBtn.textContent = "By date";
  orderToggleRow.append(standardOrderBtn, byDateBtn);
  resultsEl.appendChild(orderToggleRow);

  const scheduleContainer = document.createElement("div");
  resultsEl.appendChild(scheduleContainer);

  function setGroupBy(groupBy) {
    currentGroupBy = groupBy;
    byFellowBtn.classList.toggle("active", groupBy === "fellow");
    byCaseBtn.classList.toggle("active", groupBy === "case");
    rerenderSchedule();
  }

  function setVisitOrder(order) {
    currentVisitOrder = order;
    standardOrderBtn.classList.toggle("active", order === "standard");
    byDateBtn.classList.toggle("active", order === "date");
    rerenderSchedule();
  }

  byFellowBtn.addEventListener("click", () => setGroupBy("fellow"));
  byCaseBtn.addEventListener("click", () => setGroupBy("case"));
  standardOrderBtn.addEventListener("click", () => setVisitOrder("standard"));
  byDateBtn.addEventListener("click", () => setVisitOrder("date"));

  byFellowBtn.classList.add("active");
  standardOrderBtn.classList.add("active");
  rerenderSchedule();
}
