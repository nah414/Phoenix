"use strict";
// Phoenix Cognition control panel — vanilla JS, no build step.
// All dynamic content is assigned via textContent / element properties
// (never innerHTML), so attacker-controlled paths can't inject markup.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---- UI token (sent as X-Phoenix-UI-Token) --------------------------------
// The daemon admits /v1/cognition/* only with its PHOENIX_UI_TOKEN. Two sources:
//  1. The desktop shortcut starts the daemon with a fresh per-launch token and
//     opens /cognition#token=<token>. A URL fragment is never sent to a server.
//     It is read once, kept for this tab session only (sessionStorage, never
//     localStorage), and stripped from the address bar via history.replaceState.
//  2. A token typed into Connection (the phone-over-Tailscale flow, where the
//     daemon's PHOENIX_UI_TOKEN is set by hand). Remembered on this device.
// A per-launch token, when present, wins for this tab.
const TOKEN_KEY = "phx.token";

function readStore(store, key) {
  try { return store.getItem(key) || ""; } catch { return ""; }
}

function writeStore(store, key, value) {
  try {
    if (value) store.setItem(key, value); else store.removeItem(key);
  } catch { /* storage unavailable (private mode, blocked site data) */ }
}

function takeTokenFromFragment() {
  const hash = window.location.hash.replace(/^#/, "");
  if (!hash) return "";
  let token = "";
  let found = false;
  const rest = [];
  for (const part of hash.split("&")) {
    if (part.startsWith("token=")) {
      found = true;
      try { token = decodeURIComponent(part.slice("token=".length)).trim(); } catch { token = ""; }
    } else if (part) {
      rest.push(part);
    }
  }
  if (!found) return "";
  const clean = window.location.pathname + window.location.search + (rest.length ? `#${rest.join("&")}` : "");
  try { history.replaceState(history.state, "", clean); } catch { /* keep the token anyway */ }
  return token;
}

const launchToken = takeTokenFromFragment();
if (launchToken) writeStore(sessionStorage, TOKEN_KEY, launchToken);

const baseEl = $("#base");
const tokenEl = $("#token");
baseEl.value = readStore(localStorage, "phx.base");
tokenEl.value = readStore(sessionStorage, TOKEN_KEY) || readStore(localStorage, TOKEN_KEY);
baseEl.addEventListener("change", () => writeStore(localStorage, "phx.base", baseEl.value.trim()));
tokenEl.addEventListener("change", () => {
  // A hand-entered token replaces this tab's per-launch token.
  writeStore(sessionStorage, TOKEN_KEY, "");
  writeStore(localStorage, TOKEN_KEY, tokenEl.value.trim());
});

function apiUrl(path) {
  const base = (baseEl.value || "").trim().replace(/\/$/, "");
  return base + path;
}

async function api(path, { method = "GET", body = null } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = (tokenEl.value || "").trim();
  if (token) headers["X-Phoenix-UI-Token"] = token;
  const res = await fetch(apiUrl(path), {
    method,
    headers,
    body: body ? JSON.stringify(body) : null,
  });
  let data;
  try { data = await res.json(); } catch { data = { detail: res.statusText }; }
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function render(card, summary, data, kind = "ok") {
  const out = $(".out", card);
  out.className = "out " + kind;
  out.textContent = (summary ? summary + "\n\n" : "") + JSON.stringify(data, null, 2);
}

function busy(btn, on) {
  btn.disabled = on;
  btn.dataset.label = btn.dataset.label || btn.textContent;
  btn.textContent = on ? "…working" : btn.dataset.label;
}

async function loadCorpora() {
  try {
    const { dir, files } = await api("/v1/cognition/corpora");
    // Where file paths live: relative paths resolve inside this directory, and
    // when the daemon has PHOENIX_CORPUS_DIR set, paths outside it are refused.
    const dirEl = $("#corpus-dir");
    if (dirEl) dirEl.textContent = dir ? `Files: relative paths resolve inside ${dir}` : "";
    const opts = files.map((f) => {
      const opt = document.createElement("option");
      opt.value = f;
      return opt;
    });
    $("#corpora").replaceChildren(...opts);
  } catch { /* corpora list is best-effort */ }
}

const ACTIONS = {
  async audit(card) {
    const corpus = $(".corpus", card).value.trim();
    const d = await api("/v1/cognition/audit", { method: "POST", body: { corpus } });
    render(card, d.ready ? "READY ✓" : `NOT READY — under: ${d.under_floor.join(", ") || "—"}`, d,
      d.ready ? "ok" : "warn");
  },

  async evaluate(card) {
    const corpus = $(".corpus", card).value.trim();
    const model = $(".model", card).value.trim();
    const stub = $(".stub", card).checked;
    const confusion = $(".confusion", card).checked;
    const body = { corpus, stub, confusion };
    if (!stub && model) body.model = model;
    const d = await api("/v1/cognition/evaluate", { method: "POST", body });
    render(card, `GATE ${d.gate_passed ? "PASS ✓" : "FAIL ✗"} · macro-F1 ${d.macro_f1.toFixed(4)}`,
      d, d.gate_passed ? "ok" : "warn");
  },

  async adapt(card) {
    const dataset = $(".dataset", card).value;
    const path = $(".path", card).value.trim();
    const out = $(".out-path", card).value.trim();
    const d = await api("/v1/cognition/adapt", { method: "POST", body: { dataset, path, out } });
    render(card, `${d.emitted} pairs → ${d.out} (${d.skipped} skipped)`, d);
    loadCorpora();
  },

  async train(card) {
    const corpus = $(".corpus", card).value.trim();
    const out = $(".out-path", card).value.trim();
    const version = $(".version", card).value.trim() || "gbm-v1.0.0";
    const { job_id } = await api("/v1/cognition/train", { method: "POST", body: { corpus, out, version } });
    render(card, `job ${job_id} running…`, { job_id, status: "running" });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const j = await api(`/v1/cognition/jobs/${job_id}`);
      if (j.status === "running") {
        render(card, `job ${job_id} running…`, j);
        continue;
      }
      render(card, j.status === "done" ? `trained ${j.trained_examples} → ${j.model}` : `error: ${j.error}`,
        j, j.status === "done" ? "ok" : "err");
      break;
    }
  },
};

$$("button[data-act]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const card = btn.closest(".card");
    busy(btn, true);
    try {
      await ACTIONS[btn.dataset.act](card);
    } catch (e) {
      render(card, "error", { detail: String(e.message || e) }, "err");
    } finally {
      busy(btn, false);
    }
  });
});

loadCorpora();
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/cognition/static/sw.js").catch(() => {});
}
