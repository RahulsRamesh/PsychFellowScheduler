// This file holds a visible-by-design value: API_KEY is a shared static
// key, not a secret credential. It ships inside a public GitHub Pages
// site's JS source, so anyone can read it via "view source." It exists
// only to block casual/automated drive-by requests against the open
// Render URL, not a determined actor — see main.py's require_api_key()
// for the accepted tradeoff this implements. Do not treat this as real
// authentication, and do not reuse this key/pattern anywhere real
// security is needed.

const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);

const API_BASE = isLocal
  ? "http://127.0.0.1:8000"
  : "https://psychfellowscheduler.onrender.com";

// TODO: must match the API_KEY env var set on the Render service
// (render.yaml declares it with sync: false, so the value only lives in
// the Render dashboard and here — never committed anywhere else).
const API_KEY = "BxmmGK7AhSsotOQGNoGQiPzsWgd4byURTwX3v_-3HEM";

// Generous headroom over Render free tier's documented ~30-60s cold
// start plus the solver's own 30s worst-case time limit. A slow success
// beats a premature timeout that forces the coordinator to restart the
// same wait.
const SOLVE_TIMEOUT_MS = 100000;
