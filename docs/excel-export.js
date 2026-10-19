"use strict";

// Excel export of a solved schedule, laid out like the coordinator's
// original "Assessment Blocks" workbook: one column pair per fellow (form
// order), one row of blocks per case round, each block bordered with
// holiday line / patient name / fellow + supervisor / visits / vacations.
//
// Built in the browser with ExcelJS (loaded on demand — see
// loadExcelJS()), so the backend is never involved. buildScheduleWorkbook
// takes ExcelJS as a parameter and touches no DOM, so it also runs under
// Node for testing.

const EXCELJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/exceljs/4.4.0/exceljs.min.js";
const EXCELJS_SRI = "sha512-dlPw+ytv/6JyepmelABrgeYgHI0O+frEwgfnPdXDTOIZz+eDgfW07QXG02/O8COfivBdGNINy+Vex+lYmJ5rxw==";

const XL_VISIT_LABELS = {
  KSADS1: "Assessment Visit 1",
  KSADS2: "Assessment Visit 2",
  KSADS3: "Assessment Visit 3",
  Med: "Medication Visit",
  Feedback: "Feedback",
};
const XL_FEEDBACK_TIMES = { "12:00": "12PM", "12:45": "12:45PM", "14:15": "2:15PM" };

const XL_RED = "FFFF0000";
const XL_PURPLE = "FF7030A0";
const XL_BLUE = "FF00B0F0"; // the MD's visits (Med, Feedback)
const XL_BLACK = "FF000000";
const XL_YELLOW = "FFFFFF00"; // fill for a visit that breaks a hard rule

// "Jennifer Tu" -> "JT"
function initials(name) {
  return name.trim().split(/\s+/).map((w) => w[0].toUpperCase()).join("");
}

// ISO "YYYY-MM-DD" -> "M/D" / "M/D/YYYY"; plain string reshuffle, no
// timezone involved.
function shortDate(iso) {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
}
function longDate(iso) {
  return `${shortDate(iso)}/${iso.slice(0, 4)}`;
}

// Every Monday (ISO) from start to end inclusive. UTC throughout so DST
// never shifts a day.
function mondaysInRange(start, end) {
  const out = [];
  const d = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  while (d.getUTCDay() !== 1) d.setUTCDate(d.getUTCDate() + 1);
  for (; d <= last; d.setUTCDate(d.getUTCDate() + 7)) out.push(d.toISOString().slice(0, 10));
  return out;
}

// Clinic Mondays falling inside any of `ranges` ([[start, end], ...]) —
// appointments are Monday-only, so only those dates matter to the
// coordinator (e.g. a Thu-Sun vacation is omitted entirely).
function mondaysIn(ranges, config) {
  const clinic = mondaysInRange(config.clinic_start, config.clinic_end);
  return clinic.filter((m) => (ranges || []).some(([s, e]) => s <= m && m <= (e || s)));
}

function dateList(ranges, config) {
  return mondaysIn(ranges, config).map(shortDate).join(", ");
}

// "JT: 10/5, 11/9" / "AE: 9/14 (virtual: 8/24)" / "SM: none"
function availabilityText(label, vacations, virtualDays, config) {
  const vac = dateList(vacations, config) || "none";
  const virt = dateList(virtualDays, config);
  return `${label}: ${vac}${virt ? ` (virtual: ${virt})` : ""}`;
}

// The visit's date cell: date, then the Feedback time, a telehealth note
// (in-person is the default and goes unmarked), and the supervisor's
// initials when a KSADS3 is handed to someone other than the primary.
function visitDateText(v, c, phdFullName) {
  let text = longDate(v.date);
  if (v.type === "Feedback") text += ` @${XL_FEEDBACK_TIMES[v.time] || v.time || "12PM"}`;
  if (v.type === "Med" && v.time === "16:00") text += " @4PM";
  if (v.modality === "telehealth") text += " telehealth";
  if (v.type === "KSADS3" && v.supervisor !== c.primary_supervisor) {
    text += ` (${initials(phdFullName(v.supervisor))})`;
  }
  return text;
}

// "Assessment Blocks Jul-Dec 2026.xlsx", matching the original's name.
function scheduleFilename(config) {
  const month = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  const y1 = config.clinic_start.slice(0, 4);
  const y2 = config.clinic_end.slice(0, 4);
  const span = y1 === y2
    ? `${month(config.clinic_start)}-${month(config.clinic_end)} ${y1}`
    : `${month(config.clinic_start)} ${y1}-${month(config.clinic_end)} ${y2}`;
  return `Assessment Blocks ${span}.xlsx`;
}

// result: solve() output; config: the exact config that was solved;
// phdFullName: short PhD name ("Walshaw") -> full display name.
function buildScheduleWorkbook(ExcelJS, result, config, phdFullName) {
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet("Sheet1", {
    pageSetup: { orientation: "landscape", fitToPage: true, fitToWidth: 1, fitToHeight: 0 },
  });

  const md = config.supervisors.find((s) => s.role === "MD");
  const phdByName = Object.fromEntries(config.supervisors.filter((s) => s.role === "PhD").map((s) => [s.name, s]));
  const fellowByName = Object.fromEntries(config.fellows.map((f) => [f.name, f]));
  const fellowOrder = config.fellows.map((f) => f.name).filter((n) => result.cases.some((c) => c.fellow === n));
  const caseAt = new Map(result.cases.map((c) => [`${c.fellow}\u0000${c.case_index}`, c]));
  const rounds = [...new Set(result.cases.map((c) => c.case_index))].sort((a, b) => a - b);

  // Visits breaking a hard rule — every visit the verifier flagged, plus
  // any last-resort 4pm Med — get a yellow row so they're easy to spot.
  // Results saved before this existed carry neither list.
  const visitKey = (fellow, caseIndex, type) => `${fellow}\u0000${caseIndex}\u0000${type}`;
  const broken = new Set([
    ...(result.hard_rule_violation_visits || []).map((v) => visitKey(v.fellow, v.case_index, v.type)),
    ...(result.med_4pm || []).map((m) => visitKey(m.fellow, m.case_index, "Med")),
  ]);
  const yellowFill = { type: "pattern", pattern: "solid", fgColor: { argb: XL_YELLOW } };

  const holidayText = `Holiday: ${dateList(config.holidays, config) || "none"}`;
  const mdText = availabilityText(initials(md.name), md.vacations, md.virtual_days, config);

  // Columns: [label, date, spacer] per fellow.
  fellowOrder.forEach((_, i) => {
    ws.getColumn(3 * i + 1).width = 28;
    ws.getColumn(3 * i + 2).width = 30;
    ws.getColumn(3 * i + 3).width = 10;
  });

  const thin = { style: "thin", color: { argb: XL_BLACK } };
  const medium = { style: "medium", color: { argb: XL_BLACK } };
  const font = (extra) => ({ name: "Calibri", size: 11, color: { argb: XL_BLACK }, ...extra });

  // Draws one block with its top-left at (top, left); returns its height.
  function drawBlock(top, left, c) {
    const fellow = fellowByName[c.fellow];
    const visits = [...c.visits].sort((a, b) => a.date.localeCompare(b.date));
    const height = visits.length + 5; // holiday, patient, fellow, visits..., blank, vacation
    const bottom = top + height - 1;
    const L = left;
    const R = left + 1;

    for (let r = top; r <= bottom; r++) {
      for (const col of [L, R]) {
        const cell = ws.getCell(r, col);
        cell.border = {
          left: col === L ? medium : thin,
          right: col === R ? medium : thin,
          top: r === top ? medium : thin,
          bottom: r === bottom ? medium : thin,
        };
        cell.alignment = { wrapText: true, vertical: "middle" };
        cell.font = font();
      }
    }

    const holiday = ws.getCell(top, L);
    ws.mergeCells(top, L, top, R);
    holiday.value = holidayText;
    holiday.font = font({ bold: true, italic: true, color: { argb: XL_RED } });
    holiday.alignment = { horizontal: "center", vertical: "middle", wrapText: true };

    const patient = ws.getCell(top + 1, L);
    patient.value = "Patient Name: ";
    patient.font = font({ size: 12, bold: true, italic: true });
    ws.getCell(top + 1, R).font = font({ size: 12, bold: true, italic: true });

    const secondary = c.secondary_supervisor && c.secondary_supervisor !== c.primary_supervisor
      ? c.secondary_supervisor : null;
    ws.getCell(top + 2, L).value = c.fellow;
    ws.getCell(top + 2, L).font = font({ size: 10.5, italic: true });
    ws.getCell(top + 2, R).value = `Supervisor: ${c.primary_supervisor}${secondary ? `/${secondary}` : ""}`;
    ws.getCell(top + 2, R).font = font({ size: 10.5, color: { argb: XL_PURPLE } });

    visits.forEach((v, i) => {
      const r = top + 3 + i;
      const color = v.type === "Med" || v.type === "Feedback" ? XL_BLUE : XL_BLACK;
      ws.getCell(r, L).value = XL_VISIT_LABELS[v.type] || v.type;
      ws.getCell(r, L).font = font({ color: { argb: color } });
      const dateCell = ws.getCell(r, R);
      dateCell.value = visitDateText(v, c, phdFullName);
      dateCell.font = font({ color: { argb: color } });
      dateCell.alignment = { horizontal: "right", vertical: "middle", wrapText: true };
      if (broken.has(visitKey(c.fellow, c.case_index, v.type))) {
        ws.getCell(r, L).fill = yellowFill;
        dateCell.fill = yellowFill;
      }
    });

    const phdText = [c.primary_supervisor, secondary].filter(Boolean).map((name) => {
      const s = phdByName[name] || { vacations: [], virtual_days: [] };
      return availabilityText(initials(phdFullName(name)), s.vacations, s.virtual_days, config);
    });
    const vacation = ws.getCell(bottom, L);
    ws.mergeCells(bottom, L, bottom, R);
    vacation.value = `Vacation- ${[availabilityText(initials(fellow.name), fellow.vacations, [], config), ...phdText, mdText].join("; ")}`;
    vacation.font = font({ bold: true, italic: true, color: { argb: XL_RED } });
    vacation.alignment = { horizontal: "center", vertical: "middle", wrapText: true };
    // Excel never auto-fits a merged cell's height, so size it from the
    // text: ~55 characters per line across the two merged columns.
    const lines = Math.ceil(vacation.value.length / 55);
    ws.getRow(bottom).height = Math.max(ws.getRow(bottom).height || 0, 15 * lines + 3);

    return height;
  }

  // Block rows: every block in a round shares a top row; the round is as
  // tall as its tallest block (tier-1 cases have 5 visits, tier-2 have
  // 4), then two blank rows before the next round.
  let top = 1;
  rounds.forEach((round) => {
    let roundHeight = 0;
    fellowOrder.forEach((name, i) => {
      const c = caseAt.get(`${name}\u0000${round}`);
      if (c) roundHeight = Math.max(roundHeight, drawBlock(top, 3 * i + 1, c));
    });
    ws.getRow(top + 1).height = 17;
    for (let r = top + 2; r < top + roundHeight - 1; r++) ws.getRow(r).height = 16;
    top += roundHeight + 2;
  });

  return wb;
}

// Loads ExcelJS from the CDN the first time it's needed (it's ~1 MB, and
// most visits never export).
let excelJSPromise = null;
function loadExcelJS() {
  if (window.ExcelJS) return Promise.resolve(window.ExcelJS);
  excelJSPromise ||= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = EXCELJS_URL;
    script.integrity = EXCELJS_SRI;
    script.crossOrigin = "anonymous";
    script.onload = () => resolve(window.ExcelJS);
    script.onerror = () => {
      excelJSPromise = null;
      reject(new Error("Could not load the Excel library."));
    };
    document.head.appendChild(script);
  });
  return excelJSPromise;
}

async function downloadScheduleExcel(result, config, phdFullName) {
  const ExcelJS = await loadExcelJS();
  const wb = buildScheduleWorkbook(ExcelJS, result, config, phdFullName);
  const buffer = await wb.xlsx.writeBuffer();
  const blob = new Blob([buffer], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = scheduleFilename(config);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

if (typeof module !== "undefined") {
  module.exports = { buildScheduleWorkbook, scheduleFilename, initials, mondaysIn };
}
