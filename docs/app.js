"use strict";

// PhD supervisor names are hardcoded here (never read from an input) so
// they can never be mistyped — this must match SUPERVISOR_INDEX in
// v2/helpers.py exactly, since Rule 10 is tied to the specific name
// "Marvin". If the backend's roster ever changes, this list and the
// static blocks in index.html both need updating together.
//
// These short names are what's actually sent to/received from the API —
// never change them to full names, or every request will fail the
// backend's supervisor-roster check. PHD_DISPLAY_NAMES below is a
// display-only layer: use phdDisplayName() anywhere a supervisor name is
// shown to the coordinator, and keep using the raw short name for
// anything sent to the API or matched against API responses.
const PHD_NAMES = ["Walshaw", "Ellis", "Marvin"];

// PhD supervisors with a "Virtual days" list. Marvin is always virtual
// (hard-coded in the backend's Rule 10), so she has no list of her own.
const PHD_VIRTUAL_NAMES = ["Walshaw", "Ellis"];

function phdVirtualList(name) {
  return document.querySelector(`[data-supervisor-virtual="${name}"]`);
}

const PHD_DISPLAY_NAMES = {
  Walshaw: "Patty Walshaw",
  Ellis: "Alissa Ellis",
  Marvin: "Sarah Marvin",
};

function phdDisplayName(name) {
  return PHD_DISPLAY_NAMES[name] || name;
}

// The MD supervisor's name is likewise hardcoded (never read from an
// input) — the MD Supervisor fieldset in index.html is a static heading,
// not an editable field. Unlike the PhD names above, this one IS safe to
// show in full everywhere, including what's sent to the API: the backend
// finds the MD supervisor dynamically by role: "MD", never by matching
// this literal name against anything.
const MD_NAME = "Elizabeth Horstmann";

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

// Versioned so a future schema change can be introduced by bumping the
// suffix — old, incompatible saved blobs are then simply never read
// again rather than needing a migration or crashing restore logic.
const STORAGE_KEY_FORM = "pfs-form-state-v1";
const STORAGE_KEY_RESULT = "pfs-last-result-v1";

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
    scheduleSave();
    return;
  }

  const addFellowBtn = e.target.closest('[data-action="add-fellow"]');
  if (addFellowBtn) {
    addFellowRow();
    scheduleSave();
    return;
  }

  const removeBtn = e.target.closest('[data-action="remove-row"]');
  if (removeBtn) {
    removeBtn.closest(".row").remove();
    scheduleSave();
  }
});

function addFellowRow() {
  const tpl = document.getElementById("tpl-fellow-row");
  fellowsList.appendChild(tpl.content.cloneNode(true));
}

// Research fellows get a required "End date" (their last working day,
// inclusive); it's hidden — and ignored — for full-time fellows. Call
// after anything that sets a row's type programmatically.
function syncResearchEndField(row) {
  row.querySelector(".research-end-field").hidden =
    row.querySelector(".fellow-type").value !== "research";
}

document.body.addEventListener("change", (e) => {
  if (e.target.matches(".fellow-type")) syncResearchEndField(e.target.closest(".fellow-row"));
});

// Restore a saved form (and last schedule, since rerunning the solver can
// yield a different result — see renderResults) if one exists in
// localStorage; otherwise start with one empty fellow row so the form
// isn't empty on load. Wrapped in try/catch so a corrupted or
// schema-incompatible saved blob can never break page load — worst case
// we just fall back to a blank form.
let restoredForm = false;
try {
  const savedFormJson = localStorage.getItem(STORAGE_KEY_FORM);
  if (savedFormJson) {
    restoreFormState(JSON.parse(savedFormJson));
    restoredForm = true;
  }
} catch (e) {
  // fall through to the blank-form default below
}
if (!restoredForm) addFellowRow();

try {
  const savedResultJson = localStorage.getItem(STORAGE_KEY_RESULT);
  if (savedResultJson) {
    const saved = JSON.parse(savedResultJson);
    renderResults(saved.result, saved.hardRuleViolations);
  }
} catch (e) {
  // ignore a corrupted saved result — just don't show anything
}

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

// Holidays use their own template (tpl-holiday-row), which additionally
// carries a cosmetic .notes field — so sample-data-loaded holiday rows
// look consistent with manually-added ones, not a mix of two shapes.
function addFilledHolidayRow(container, start, end, notes) {
  const tpl = document.getElementById("tpl-holiday-row");
  const node = tpl.content.cloneNode(true);
  node.querySelector(".start").value = start;
  node.querySelector(".end").value = end;
  if (notes) node.querySelector(".notes").value = notes;
  container.appendChild(node);
}

// Shared by loadSampleData() and the "Clear data" button — both replace
// the form's contents wholesale and need to wipe any stale
// validation/result state left over from a prior attempt.
function resetValidationAndResultState() {
  document.querySelectorAll(".invalid").forEach((el) => el.classList.remove("invalid"));
  document.querySelectorAll(".field-error").forEach((el) => { el.textContent = ""; });
  formErrorsBox.hidden = true;
  hideError();
  resultsEl.hidden = true;
  resultsEl.innerHTML = "";
}

function loadSampleData() {
  document.getElementById("clinic_start").value = SAMPLE_DATA.clinic_start;
  document.getElementById("clinic_end").value = SAMPLE_DATA.clinic_end;

  const holidaysListEl = document.getElementById("holidays-list");
  holidaysListEl.innerHTML = "";
  SAMPLE_DATA.holidays.forEach(([s, e, notes]) => addFilledHolidayRow(holidaysListEl, s, e, notes));

  PHD_NAMES.forEach((name) => {
    const container = document.querySelector(`[data-supervisor="${name}"]`);
    container.innerHTML = "";
    (SAMPLE_DATA.supervisor_vacations[name] || []).forEach(([s, e]) => addFilledDateRangeRow(container, s, e));
  });

  const mdVacationsEl = document.getElementById("md-vacations-list");
  mdVacationsEl.innerHTML = "";
  SAMPLE_DATA.md.vacations.forEach(([s, e]) => addFilledDateRangeRow(mdVacationsEl, s, e));

  PHD_VIRTUAL_NAMES.forEach((name) => {
    const container = phdVirtualList(name);
    container.innerHTML = "";
    ((SAMPLE_DATA.supervisor_virtual_days || {})[name] || []).forEach(([s, e]) => addFilledDateRangeRow(container, s, e));
  });
  const mdVirtualEl = document.getElementById("md-virtual-list");
  mdVirtualEl.innerHTML = "";
  (SAMPLE_DATA.md.virtual_days || []).forEach(([s, e]) => addFilledDateRangeRow(mdVirtualEl, s, e));

  fellowsList.innerHTML = "";
  SAMPLE_DATA.fellows.forEach((f) => {
    addFellowRow();
    const row = fellowsList.lastElementChild;
    row.querySelector(".fellow-name").value = f.name;
    row.querySelector(".fellow-type").value = f.type;
    row.querySelector(".fellow-end-date").value = f.end_date || "";
    syncResearchEndField(row);
    const vacationsEl = row.querySelector(".vacation-list");
    f.vacations.forEach(([s, e]) => addFilledDateRangeRow(vacationsEl, s, e));
  });

  resetValidationAndResultState();
  scheduleSave();
}

if (typeof SHOW_LOAD_SAMPLE_BUTTON !== "undefined" && SHOW_LOAD_SAMPLE_BUTTON) {
  document.getElementById("dev-tools").hidden = false;
  document.getElementById("load-sample-btn").addEventListener("click", loadSampleData);
}

// ---------------------------------------------------------------------
// "Clear data" — a real, always-visible coordinator-facing feature (NOT
// gated on SHOW_LOAD_SAMPLE_BUTTON, unlike "Load sample data" above).
// Resets the whole form back to its exact initial page-load state.
// ---------------------------------------------------------------------

function clearData() {
  const confirmed = window.confirm(
    "You are clearing the form fields and the generated schedule. This cannot be reversed so please download the schedule if necessary before confirming."
  );
  if (!confirmed) return;

  restoreFormState({});
  resetValidationAndResultState();
  clearSavedState();
}

document.getElementById("clear-data-btn").addEventListener("click", clearData);

// ---------------------------------------------------------------------
// localStorage persistence — the form and the last generated schedule
// (re-solving can yield a different, equally-valid result, so we persist
// the actual schedule shown rather than just re-deriving it) survive a
// page reload. All reads/writes are wrapped in try/catch: localStorage
// can throw in private browsing or when disabled, and a saved blob can
// be corrupted or from an older schema — persistence is a convenience,
// never something that should be able to break the form itself.
// ---------------------------------------------------------------------

function captureFormState() {
  return {
    clinic_start: document.getElementById("clinic_start").value,
    clinic_end: document.getElementById("clinic_end").value,
    holidays: [...document.querySelectorAll("#holidays-list .date-range-row")].map((row) => [
      row.querySelector(".start").value,
      row.querySelector(".end").value,
      row.querySelector(".notes")?.value || "",
    ]),
    supervisor_vacations: Object.fromEntries(
      PHD_NAMES.map((name) => [name, readDateRanges(document.querySelector(`[data-supervisor="${name}"]`))])
    ),
    md_vacations: readDateRanges(document.getElementById("md-vacations-list")),
    supervisor_virtual_days: Object.fromEntries(
      PHD_VIRTUAL_NAMES.map((name) => [name, readDateRanges(phdVirtualList(name))])
    ),
    md_virtual_days: readDateRanges(document.getElementById("md-virtual-list")),
    fellows: [...fellowsList.querySelectorAll(".fellow-row")].map((row) => ({
      name: row.querySelector(".fellow-name").value,
      type: row.querySelector(".fellow-type").value,
      end_date: row.querySelector(".fellow-end-date").value,
      vacations: readDateRanges(row.querySelector(".vacation-list")),
    })),
  };
}

// Also used by "Clear data" (via restoreFormState({})) so both code paths
// that reset the form share one implementation.
function restoreFormState(saved) {
  document.getElementById("clinic_start").value = saved.clinic_start || "";
  document.getElementById("clinic_end").value = saved.clinic_end || "";

  const holidaysListEl = document.getElementById("holidays-list");
  holidaysListEl.innerHTML = "";
  (saved.holidays || []).forEach(([s, e, notes]) => addFilledHolidayRow(holidaysListEl, s, e, notes));

  PHD_NAMES.forEach((name) => {
    const container = document.querySelector(`[data-supervisor="${name}"]`);
    container.innerHTML = "";
    ((saved.supervisor_vacations || {})[name] || []).forEach(([s, e]) => addFilledDateRangeRow(container, s, e));
  });

  const mdVacationsEl = document.getElementById("md-vacations-list");
  mdVacationsEl.innerHTML = "";
  (saved.md_vacations || []).forEach(([s, e]) => addFilledDateRangeRow(mdVacationsEl, s, e));

  PHD_VIRTUAL_NAMES.forEach((name) => {
    const container = phdVirtualList(name);
    container.innerHTML = "";
    ((saved.supervisor_virtual_days || {})[name] || []).forEach(([s, e]) => addFilledDateRangeRow(container, s, e));
  });
  const mdVirtualEl = document.getElementById("md-virtual-list");
  mdVirtualEl.innerHTML = "";
  (saved.md_virtual_days || []).forEach(([s, e]) => addFilledDateRangeRow(mdVirtualEl, s, e));

  fellowsList.innerHTML = "";
  (saved.fellows || []).forEach((f) => {
    addFellowRow();
    const row = fellowsList.lastElementChild;
    row.querySelector(".fellow-name").value = f.name || "";
    row.querySelector(".fellow-type").value = f.type || "full-time";
    row.querySelector(".fellow-end-date").value = f.end_date || "";
    syncResearchEndField(row);
    (f.vacations || []).forEach(([s, e]) => addFilledDateRangeRow(row.querySelector(".vacation-list"), s, e));
  });
  if (fellowsList.children.length === 0) addFellowRow();
}

let saveTimer = null;
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(STORAGE_KEY_FORM, JSON.stringify(captureFormState()));
    } catch (e) {
      // ignore — see note above
    }
  }, 400);
}

function saveResult(result, hardRuleViolations) {
  try {
    localStorage.setItem(STORAGE_KEY_RESULT, JSON.stringify({ result, hardRuleViolations }));
  } catch (e) {
    // ignore — see note above
  }
}

function clearSavedState() {
  try {
    localStorage.removeItem(STORAGE_KEY_FORM);
    localStorage.removeItem(STORAGE_KEY_RESULT);
  } catch (e) {
    // ignore — see note above
  }
}

// ---------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------

// clinic_start/clinic_end share one error element, which isn't
// reachable via a generic closest(".row") lookup the way repeatable row
// fields are, so it's mapped explicitly rather than guessed from DOM
// structure.
const EXPLICIT_ERROR_TARGETS = {
  clinic_start: "clinic-dates-error",
  clinic_end: "clinic-dates-error",
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
  if (e.target.matches("input, select")) {
    clearFieldError(e.target);
    scheduleSave();
  }
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

  validateDateRangeRows(document.getElementById("md-vacations-list"), errors);
  PHD_VIRTUAL_NAMES.forEach((name) => validateDateRangeRows(phdVirtualList(name), errors));
  validateDateRangeRows(document.getElementById("md-virtual-list"), errors);

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
    if (row.querySelector(".fellow-type").value === "research") {
      const endEl = row.querySelector(".fellow-end-date");
      const who = name || "A research fellow";
      if (!endEl.value) {
        setFieldError(endEl, "Research fellows need an end date.");
        errors.push({ message: `${who} is missing a research end date.`, element: endEl });
      } else if ((clinicStartEl.value && endEl.value < clinicStartEl.value) ||
                 (clinicEndEl.value && endEl.value > clinicEndEl.value)) {
        setFieldError(endEl, "End date must be within the clinic dates.");
        errors.push({ message: `${who}'s end date must be within the clinic start and end dates.`, element: endEl });
      }
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
      errors.push({ message: "Every Monday in the clinic window falls on a holiday — no appointments can be scheduled.", element: clinicEndEl });
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
    virtual_days: PHD_VIRTUAL_NAMES.includes(name) ? readDateRanges(phdVirtualList(name)) : [],
  }));
  supervisors.push({
    name: MD_NAME,
    role: "MD",
    vacations: readDateRanges(document.getElementById("md-vacations-list")),
    virtual_days: readDateRanges(document.getElementById("md-virtual-list")),
  });

  const fellows = [...fellowsList.querySelectorAll(".fellow-row")].map((row) => {
    const fellow = {
      name: row.querySelector(".fellow-name").value.trim(),
      type: row.querySelector(".fellow-type").value,
      vacations: readDateRanges(row.querySelector(".vacation-list")),
    };
    if (fellow.type === "research") fellow.end_date = row.querySelector(".fellow-end-date").value;
    return fellow;
  });

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
  showLoading("Calculating schedule...");

  const wakingTimer = setTimeout(() => {
    if (!serverLikelyWarm) {
      showLoading("Waking up the server – this can take up to a minute. Please don't close this tab.");
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
      showError("This tool is temporarily unable to authenticate with the server. Please contact Rahul.");
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

    if (body.result.status === "TIMEOUT") {
      pendingRetryConfig = config;
      showError("The solver ran out of time before finding a schedule. This doesn't mean your inputs are impossible, please try again. If this issue persists, try relaxing some of the date constraints.", { retry: true });
      return;
    }

    renderResults(body.result, body.hard_rule_violations);
    saveResult(body.result, body.hard_rule_violations);
  } catch (err) {
    if (err.name === "AbortError") {
      pendingRetryConfig = config;
      showError("The server took too long to respond — it may still be waking up. Please wait a moment and try again.", { retry: true });
    } else {
      pendingRetryConfig = config;
      showError("Could not reach the server. Check your internet connection and try again.", { retry: true });
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

// The solver scores case overlap (soft #4) and 2:15 Feedback (soft #5)
// separately, but both just mean "a Feedback got pushed later than noon",
// so they're shown as one display-only tile whose count is every Feedback
// with a start time after 12:00 (2:15, and 12:45 on a 3-Feedback day).
const MODIFIED_FEEDBACK_KEY = "modified_feedback_time";
const MERGED_INTO_MODIFIED_FEEDBACK = ["case_gap (soft #4)", "feedback_215 (soft #5)"];

// Still penalized by the solver, just not shown as a tile: a 3-Feedback
// day's 12:45 and 2:15 Feedbacks already show up in the Modified Feedback
// time tile.
const HIDDEN_SOFT_RULES = ["feedback_triple (soft #6)"];

// Results saved before visits carried a `time` field only had a `late`
// flag (the 2:15 case-overlap Feedback) — fall back to it for those.
function isFeedbackPushedLater(v) {
  return v.type === "Feedback" && (v.time ? v.time !== "12:00" : Boolean(v.late));
}

// [{ key, value, entries }] in the solver's order, with #4/#5 replaced by
// the merged tile at #4's position. `entries` is what click-to-highlight
// targets (same shape as soft_violation_details).
function softTiles(result) {
  const details = result.soft_violation_details || {};
  const pushed = result.cases
    .filter((c) => c.visits.some(isFeedbackPushedLater))
    .map((c) => ({ fellow: c.fellow, case_index: c.case_index }));
  const tiles = [];
  Object.entries(result.soft_violations).forEach(([key, value]) => {
    if (key === MERGED_INTO_MODIFIED_FEEDBACK[0]) {
      tiles.push({ key: MODIFIED_FEEDBACK_KEY, value: pushed.length, entries: pushed });
    } else if (!MERGED_INTO_MODIFIED_FEEDBACK.includes(key) && !HIDDEN_SOFT_RULES.includes(key)) {
      tiles.push({ key, value, entries: details[key] || [] });
    }
  });
  return tiles;
}

const SOFT_RULE_LABELS = {
  "supervisor_variety (soft #2)": "Supervisor case distribution",
  "ksads3_mismatch (soft #1)": "KSADS3 supervisor mismatch",
  "med_position (soft #3)": "Med visit position",
  [MODIFIED_FEEDBACK_KEY]: "Modified Feedback time",
};

const SOFT_RULE_TOOLTIPS = {
  "supervisor_variety (soft #2)": "Measures how many full-time fellows failed to get at least one case with each of the 3 PhD supervisors across their 4 cases. Research fellows are excluded since they only have 2 cases.",
  "ksads3_mismatch (soft #1)": "Measures how many instances KSADS3 is supervised by someone different than the KSADS1/2 supervisor.",
  "med_position (soft #3)": "Measures how many cases broke the preferred Med-visit timing: cases 1/2 shouldn't start with Med, cases 3/4 should.",
  [MODIFIED_FEEDBACK_KEY]: "Measures how many Feedbacks were pushed to a later time to accommodate same-day appointments.",
};

function supervisorLoadTooltip(name) {
  return `Number of cases where ${phdDisplayName(name)} is the primary KSADS1/2 supervisor, across the whole schedule — optimized for a balanced workload.`;
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
    ? `${phdDisplayName(c.primary_supervisor)} (KSADS3: ${phdDisplayName(c.secondary_supervisor)})`
    : phdDisplayName(c.primary_supervisor);
}

// Both maps below are display-only — the raw values ("Med", "in-person",
// "telehealth") are what the backend actually sends/expects, and stay
// unchanged everywhere except this one rendering step.
const VISIT_TYPE_LABELS = { Med: "Med Visit" };
const MODALITY_LABELS = { "in-person": "In-person", "telehealth": "Telehealth" };

// Every Feedback gets a start-time badge (see solve.py Rule 11). 12pm is
// the normal time, so its badge is neutral; 12:45 and 2:15 mean the
// Feedback was pushed later and keep the amber warning style. Other visit
// types are always at noon and get no badge.
const TIME_BADGES = {
  "12:00": { text: "12pm", title: "Feedback is at the standard 12pm time.", neutral: true },
  "12:45": { text: "12:45pm", title: "Appointment is moved to 12:45pm to accommodate multiple same-day appointments." },
  "14:15": { text: "2:15pm", title: "Appointment is pushed back to accommodate a same day appointment." },
};

// Results saved before visits carried a `time` field only had a `late`
// flag (2:15) — treat any other legacy Feedback as noon.
function timeBadge(v) {
  if (v.type !== "Feedback") return "";
  const b = TIME_BADGES[v.time || (v.late ? "14:15" : "12:00")];
  if (!b) return "";
  return ` <span class="late-badge${b.neutral ? " neutral" : ""}" title="${b.title}">${b.text}</span>`;
}

// Backend dates are ISO "YYYY-MM-DD"; the schedule shows "MM/DD/YYYY".
// Plain string reshuffle — no Date parsing, so no timezone shifts.
function formatDate(iso) {
  const [y, m, d] = iso.split("-");
  return `${m}/${d}/${y}`;
}

// Visits are always shown chronologically. Sorting happens on the ISO
// strings (before formatting), where a plain string sort is already
// chronological. All visits in a case have distinct dates (hard rule), so
// there's never a tie to break.
function visitTable(visits) {
  const ordered = [...visits].sort((a, b) => a.date.localeCompare(b.date));
  const table = document.createElement("table");
  table.className = "visit-table";
  table.innerHTML = `
    <thead><tr><th>Type</th><th>Date</th><th>Modality</th><th>Supervisor</th></tr></thead>
    <tbody>
      ${ordered.map((v) => `<tr><td>${VISIT_TYPE_LABELS[v.type] || v.type}</td><td>${formatDate(v.date)}${timeBadge(v)}</td><td>${MODALITY_LABELS[v.modality] || v.modality}</td><td>${phdDisplayName(v.supervisor)}</td></tr>`).join("")}
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
function caseBlockFor(c, headingPrefixNodes) {
  const caseBlock = document.createElement("div");
  caseBlock.className = "case-block";
  caseBlock.dataset.fellow = c.fellow;
  caseBlock.dataset.caseIndex = String(c.case_index);
  const h4 = document.createElement("h4");
  h4.append(...headingPrefixNodes);
  caseBlock.appendChild(h4);
  caseBlock.appendChild(visitTable(c.visits));
  return caseBlock;
}

function fellowLabel(tagName, fellow) {
  const el = document.createElement(tagName);
  el.className = "fellow-label";
  el.dataset.fellow = fellow;
  el.textContent = fellow;
  return el;
}

function renderSchedule(container, cases, groupBy) {
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
        details.appendChild(caseBlockFor(c, [fellowLabel("span", c.fellow), ` — Supervisor: ${supervisorText(c)}`]));
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
        details.appendChild(caseBlockFor(c, [`Case ${c.case_index + 1} — Supervisor: ${supervisorText(c)}`]));
      });

    container.appendChild(details);
  });
}

function renderResults(result, hardRuleViolations) {
  resultsEl.innerHTML = "";
  resultsEl.hidden = false;

  const statusClass = result.status === "OPTIMAL" ? "optimal" : "feasible";
  const statusLabel = result.status === "OPTIMAL" ? "Optimal schedule found" : "Feasible schedule found (close to optimal - adjusting dates may allow for a more optimal schedule)";
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
    verifyLine.innerHTML = "If you are seeing this, please report it to Rahul:<br>" +
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
  function targetForSoftRule(key, entries) {
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

  const softGridLabel = document.createElement("p");
  softGridLabel.className = "toggle-label";
  softGridLabel.textContent = "Soft preference scores (lower is better but a schedule can still be fully valid with some being non-zero. Click a tile to highlight the cases that contributed to that score.)";
  resultsEl.appendChild(softGridLabel);

  const tiles = softTiles(result);
  const softMax = Math.max(1, ...tiles.map((t) => t.value));
  const softGrid = document.createElement("div");
  softGrid.className = "stat-grid";
  tiles.forEach(({ key, value, entries }) => {
    const tile = statTile(SOFT_RULE_LABELS[key] || key, value, softMax, SOFT_RULE_TOOLTIPS[key]);
    tile.dataset.tile = key;
    softGrid.appendChild(wireTile(tile, () => targetForSoftRule(key, entries)));
  });
  resultsEl.appendChild(softGrid);

  const loadValues = Object.values(result.supervisor_case_load);
  const loadMax = Math.max(1, ...loadValues);
  const loadGrid = document.createElement("div");
  loadGrid.className = "stat-grid";
  Object.entries(result.supervisor_case_load).forEach(([name, count]) => {
    // name stays the raw short form for matching (dataset.tile,
    // targetForSupervisor) — only the visible label/tooltip use the full
    // display name.
    const tile = statTile(phdDisplayName(name), count, loadMax, supervisorLoadTooltip(name));
    tile.dataset.tile = name;
    loadGrid.appendChild(wireTile(tile, () => targetForSupervisor(name)));
  });
  resultsEl.appendChild(loadGrid);

  // Group-by toggle (fellow vs case #): a purely client-side re-render of
  // the already-fetched result.cases — no re-solve needed. Always resets
  // to "by fellow" for a fresh result. Visits within each case are always
  // shown chronologically (see visitTable).
  let currentGroupBy = "fellow";

  function rerenderSchedule() {
    renderSchedule(scheduleContainer, result.cases, currentGroupBy);
    applyHighlights();
  }

  const toggleLabel = document.createElement("p");
  toggleLabel.className = "toggle-label";
  toggleLabel.textContent = "Sort cases display";
  resultsEl.appendChild(toggleLabel);

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

  const scheduleContainer = document.createElement("div");
  resultsEl.appendChild(scheduleContainer);

  function setGroupBy(groupBy) {
    currentGroupBy = groupBy;
    byFellowBtn.classList.toggle("active", groupBy === "fellow");
    byCaseBtn.classList.toggle("active", groupBy === "case");
    rerenderSchedule();
  }

  byFellowBtn.addEventListener("click", () => setGroupBy("fellow"));
  byCaseBtn.addEventListener("click", () => setGroupBy("case"));

  byFellowBtn.classList.add("active");
  rerenderSchedule();
}
