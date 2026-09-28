// Data for the dev-only "Load sample data" button — mirrors the repo's
// sample_config.json (fictional fellow names, real supervisor names).
// Not deployed logic; just fill-the-form convenience. See config.js's
// SHOW_LOAD_SAMPLE_BUTTON to toggle the button on/off.
const SAMPLE_DATA = {
  clinic_start: "2026-07-20",
  clinic_end: "2026-12-21",
  // Third element (optional) is the cosmetic notes text for that row.
  holidays: [
    ["2026-09-07", "2026-09-07", "Labor Day"],
    ["2026-10-26", "2026-10-31", "AACAP conference"],
  ],
  supervisor_vacations: {
    Walshaw: [
      ["2026-07-06", "2026-07-10"],
      ["2026-07-20", "2026-07-24"],
      ["2026-11-23", "2026-11-25"],
      ["2026-12-21", "2026-12-21"],
    ],
    Ellis: [
      ["2026-07-20", "2026-07-20"],
      ["2026-09-14", "2026-09-14"],
      ["2026-11-23", "2026-11-23"],
      ["2026-12-21", "2026-12-21"],
    ],
    Marvin: [
      ["2026-12-14", "2026-12-14"],
    ],
  },
  md: {
    name: "Horstmann",
    vacations: [
      ["2026-11-23", "2026-11-23"],
      ["2026-08-03", "2026-08-03"],
    ],
  },
  fellows: [
    {
      name: "Fellow A",
      type: "full-time",
      vacations: [
        ["2026-09-10", "2026-09-13"],
        ["2026-10-05", "2026-10-09"],
        ["2026-11-07", "2026-11-11"],
      ],
    },
    {
      name: "Fellow B",
      type: "full-time",
      vacations: [
        ["2026-10-12", "2026-10-12"],
        ["2026-11-23", "2026-11-23"],
        ["2026-12-21", "2026-12-21"],
      ],
    },
    {
      name: "Fellow C",
      type: "full-time",
      vacations: [
        ["2026-10-05", "2026-10-05"],
        ["2026-10-26", "2026-10-26"],
        ["2026-11-23", "2026-11-23"],
      ],
    },
    {
      name: "Research Fellow D",
      type: "research",
      vacations: [
        ["2026-08-31", "2026-08-31"],
      ],
    },
  ],
};
