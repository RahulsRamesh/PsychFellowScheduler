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
  "case_gap (soft #4)": "Gap between cases",
};

function statTile(label, value, max) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  const div = document.createElement("div");
  div.className = "stat-tile";
  div.innerHTML = `
    <div class="stat-label">${label}</div>
    <div class="stat-value">${value}</div>
    <div class="stat-bar-track"><div class="stat-bar-fill" style="width:${pct}%"></div></div>
  `;
  return div;
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

  const softValues = Object.values(result.soft_violations);
  const softMax = Math.max(1, ...softValues);
  const softGrid = document.createElement("div");
  softGrid.className = "stat-grid";
  Object.entries(result.soft_violations).forEach(([key, value]) => {
    softGrid.appendChild(statTile(SOFT_RULE_LABELS[key] || key, value, softMax));
  });
  resultsEl.appendChild(softGrid);

  const loadValues = Object.values(result.supervisor_case_load);
  const loadMax = Math.max(1, ...loadValues);
  const loadGrid = document.createElement("div");
  loadGrid.className = "stat-grid";
  Object.entries(result.supervisor_case_load).forEach(([name, count]) => {
    loadGrid.appendChild(statTile(name, count, loadMax));
  });
  resultsEl.appendChild(loadGrid);

  const byFellow = new Map();
  result.cases.forEach((c) => {
    if (!byFellow.has(c.fellow)) byFellow.set(c.fellow, []);
    byFellow.get(c.fellow).push(c);
  });

  byFellow.forEach((cases, fellow) => {
    const details = document.createElement("details");
    details.className = "fellow-block";
    details.open = true;

    const summary = document.createElement("summary");
    summary.textContent = fellow;
    details.appendChild(summary);

    cases
      .sort((a, b) => a.case_index - b.case_index)
      .forEach((c) => {
        const caseBlock = document.createElement("div");
        caseBlock.className = "case-block";

        const h4 = document.createElement("h4");
        const supText = c.secondary_supervisor && c.secondary_supervisor !== c.primary_supervisor
          ? `${c.primary_supervisor} (KSADS3: ${c.secondary_supervisor})`
          : c.primary_supervisor;
        h4.textContent = `Case ${c.case_index} — tier ${c.tier} — ${supText}`;
        caseBlock.appendChild(h4);

        const table = document.createElement("table");
        table.className = "visit-table";
        table.innerHTML = `
          <thead><tr><th>Type</th><th>Date</th><th>Modality</th><th>Supervisor</th></tr></thead>
          <tbody>
            ${c.visits.map((v) => `<tr><td>${v.type}</td><td>${v.date}</td><td>${v.modality}</td><td>${v.supervisor}</td></tr>`).join("")}
          </tbody>
        `;
        caseBlock.appendChild(table);
        details.appendChild(caseBlock);
      });

    resultsEl.appendChild(details);
  });
}
