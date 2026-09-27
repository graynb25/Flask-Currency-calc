/* Currency Converter UI: vanilla JS, no build step.
   Flow: /api/currencies fills the comboboxes and provides the rate table;
   /api/convert returns the authoritative conversion; /api/history draws the chart. */

"use strict";

const $ = (id) => document.getElementById(id);

const el = {
  amount: $("amount"),
  amountSymbol: $("amountSymbol"),
  from: $("fromCur"),
  to: $("toCur"),
  swap: $("swapBtn"),
  result: $("resultValue"),
  resultCode: $("resultCode"),
  rateLine: $("rateLine"),
  copy: $("copyBtn"),
  chips: $("chips"),
  miniGrid: $("miniGrid"),
  statusPill: $("statusPill"),
  statusText: $("statusText"),
  refresh: $("refreshBtn"),
  chartTitle: $("chartTitle"),
  chartCanvas: $("historyChart"),
  chartEmpty: $("chartEmpty"),
  rangePills: $("rangePills"),
  toast: $("toast"),
};

const ZERO_DECIMAL = new Set([
  "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG",
  "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
]);

const POPULAR_PAIRS = [
  ["USD", "EUR"], ["USD", "GBP"], ["USD", "JPY"], ["USD", "COP"],
  ["CAD", "COP"], ["EUR", "GBP"],
];

const MINI_TARGETS = ["EUR", "GBP", "JPY", "CAD", "CHF", "AUD", "COP", "MXN"];

const state = {
  rates: {},          // code -> units per 1 USD
  currencies: [],     // [{code, name, flag}]
  updated: null,
  source: null,
  stale: false,
  from: "USD",
  to: "EUR",
  chartDays: 30,
  chart: null,
  historyKey: null,   // "USD/EUR/30" of the currently drawn chart
  convertTimer: null,
  toastTimer: null,
};

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

/* Escape third-party text (currency names come from a CDN) before innerHTML. */
function esc(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/* ------------------------------------------------------------------ */
/* combobox: type to filter, scroll to pick, full keyboard support     */
/* ------------------------------------------------------------------ */

function createCombobox(root, { onSelect }) {
  root.classList.add("combobox");
  root.innerHTML = `
    <button type="button" class="combo-button" aria-haspopup="listbox" aria-expanded="false">
      <span class="combo-value"></span>
      <svg class="combo-chevron" viewBox="0 0 12 8" aria-hidden="true">
        <path d="M1 1.5 6 6.5 11 1.5" stroke="currentColor" stroke-width="2"
              fill="none" stroke-linecap="round"/>
      </svg>
    </button>
    <div class="combo-panel hidden">
      <input type="text" class="combo-filter" placeholder="Type to search..."
             spellcheck="false" autocomplete="off" aria-label="Search currency">
      <div class="combo-list" role="listbox"></div>
    </div>`;

  const button = root.querySelector(".combo-button");
  const valueEl = root.querySelector(".combo-value");
  const panel = root.querySelector(".combo-panel");
  const filter = root.querySelector(".combo-filter");
  const list = root.querySelector(".combo-list");

  let options = [];     // [{code, name, flag}]
  let value = null;
  let visible = [];     // options matching the current filter
  let activeIndex = -1; // index into visible

  const isOpen = () => !panel.classList.contains("hidden");

  function paint() {
    list.innerHTML = visible.map((o, i) => `
      <div class="combo-option${o.code === value ? " selected" : ""}${i === activeIndex ? " active" : ""}"
           role="option" aria-selected="${o.code === value}" data-code="${esc(o.code)}">
        <span class="flag">${esc(o.flag)}</span><code>${esc(o.code)}</code>
        <span class="opt-name">${esc(o.name)}</span>
      </div>`).join("") || `<div class="combo-empty">No currency matches that.</div>`;
    const active = list.querySelector(".combo-option.active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function renderList() {
    const q = filter.value.trim().toLowerCase();
    visible = options.filter((o) =>
      !q ||
      o.code.toLowerCase().startsWith(q) ||
      o.code.toLowerCase().includes(q) ||
      o.name.toLowerCase().includes(q)
    );
    activeIndex = visible.length ? 0 : -1;
    paint();
  }

  function renderButton() {
    const o = options.find((x) => x.code === value);
    valueEl.innerHTML = o
      ? `<span class="flag">${esc(o.flag)}</span><strong>${esc(o.code)}</strong>
         <span class="combo-name">${esc(o.name)}</span>`
      : `<span class="combo-name">Select currency</span>`;
  }

  function open() {
    panel.classList.remove("hidden");
    button.setAttribute("aria-expanded", "true");
    filter.value = "";
    renderList();
    filter.focus();
  }

  function close() {
    panel.classList.add("hidden");
    button.setAttribute("aria-expanded", "false");
  }

  function choose(code) {
    const changed = code !== value;
    value = code;
    renderButton();
    close();
    if (changed) onSelect(code);
  }

  button.addEventListener("click", () => (isOpen() ? close() : open()));

  filter.addEventListener("input", renderList);

  filter.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!visible.length) return;
      activeIndex = e.key === "ArrowDown"
        ? Math.min(activeIndex + 1, visible.length - 1)
        : Math.max(activeIndex - 1, 0);
      paint();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0 && visible[activeIndex]) choose(visible[activeIndex].code);
    } else if (e.key === "Escape") {
      close();
      button.focus();
    }
  });

  list.addEventListener("click", (e) => {
    const opt = e.target.closest(".combo-option");
    if (opt) choose(opt.dataset.code);
  });

  document.addEventListener("pointerdown", (e) => {
    if (isOpen() && !root.contains(e.target)) close();
  });

  return {
    get value() { return value; },
    set(code) { value = code; renderButton(); },
    setOptions(list_) { options = list_; renderButton(); },
  };
}

const fromBox = createCombobox(el.from, { onSelect: (code) => { state.from = code; onPairChange(); } });
const toBox = createCombobox(el.to, { onSelect: (code) => { state.to = code; onPairChange(); } });

/* ------------------------------------------------------------------ */
/* formatting                                                          */
/* ------------------------------------------------------------------ */

function decimalsFor(code) {
  return ZERO_DECIMAL.has(code) ? 0 : 2;
}

function fmtMoney(code, value) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimalsFor(code),
    maximumFractionDigits: decimalsFor(code),
  }).format(value);
}

/* Symbol prefix for a currency: $ for USD/COP, ¥ for JPY, € for EUR...
   Falls back to the code itself when a currency has no narrow symbol. */
function currencySymbol(code) {
  try {
    const parts = new Intl.NumberFormat("en-US", {
      style: "currency", currency: code, currencyDisplay: "narrowSymbol",
    }).formatToParts(1);
    return (parts.find((p) => p.type === "currency") || {}).value || code;
  } catch {
    return code;
  }
}

function fmtCurrency(code, value) {
  try {
    // A sub-cent value (24.99 COP -> $0.0077) is meaningless at two decimals;
    // widen the fraction until the digits actually say something.
    if (value > 0 && value < 0.01) {
      return new Intl.NumberFormat("en-US", {
        style: "currency", currency: code, currencyDisplay: "narrowSymbol",
        maximumFractionDigits: 8,
      }).format(value);
    }
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: code, currencyDisplay: "narrowSymbol",
      minimumFractionDigits: decimalsFor(code),
      maximumFractionDigits: decimalsFor(code),
    }).format(value);
  } catch {
    return `${currencySymbol(code)}${fmtMoney(code, value)}`;
  }
}

function fmtRate(value) {
  if (Math.abs(value) >= 1) return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
  return value.toPrecision(6);
}

/* Grouped, high-precision formatting for values placed into the amount box.
   15 significant digits preserves essentially the full float64 value, so a
   carried amount is exactly what was converted: no clipping of sub-cent
   values like 0.0076722, no rounding of large ones. */
function fmtAmountInput(value) {
  return new Intl.NumberFormat("en-US", { maximumSignificantDigits: 15 }).format(value);
}

/* Animated count-up so the big result "lands" instead of just swapping text. */
function animateCurrency(elm, target, code) {
  const from = parseFloat(elm.dataset.value ?? target);
  elm.dataset.value = target;
  if (reducedMotion.matches || !Number.isFinite(from) || from === target) {
    elm.textContent = fmtCurrency(code, target);
    return;
  }
  const start = performance.now();
  const duration = 480;
  function frame(now) {
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
    elm.textContent = fmtCurrency(code, from + (target - from) * eased);
    if (t < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------------------ */
/* API helpers                                                         */
/* ------------------------------------------------------------------ */

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status})`);
  return body;
}

/* ------------------------------------------------------------------ */
/* toast                                                               */
/* ------------------------------------------------------------------ */

function toast(message, kind = "error") {
  el.toast.textContent = message;
  el.toast.className = `toast ${kind}`;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => el.toast.classList.add("hidden"), 3200);
}

/* ------------------------------------------------------------------ */
/* status pill                                                         */
/* ------------------------------------------------------------------ */

function setStatus() {
  let label = "Rates unavailable";
  if (state.updated) {
    const when = new Date(state.updated);
    const stamp = isNaN(when)
      ? state.updated
      : new Intl.DateTimeFormat("en-GB", {
          day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
          timeZone: "UTC",
        }).format(when) + " UTC";
    label = state.stale ? `Cached (offline) · ${stamp}` : `Live · updated ${stamp}`;
  }
  el.statusText.textContent = label;
  el.statusPill.title = `Source: ${state.source ?? "unknown"}`;
  el.statusPill.classList.toggle("stale", state.stale);
  el.statusPill.classList.toggle("error", !state.updated);
}

/* ------------------------------------------------------------------ */
/* chips, mini cards                                                   */
/* ------------------------------------------------------------------ */

function populateChips() {
  el.chips.innerHTML = "";
  for (const [from, to] of POPULAR_PAIRS) {
    if (!state.rates[from] || !state.rates[to]) continue;
    const btn = document.createElement("button");
    btn.className = "chip";
    btn.dataset.pair = `${from}/${to}`;
    btn.textContent = `${from} → ${to}`;
    btn.addEventListener("click", () => {
      state.from = from;
      state.to = to;
      fromBox.set(from);
      toBox.set(to);
      onPairChange();
    });
    el.chips.appendChild(btn);
  }
  markActiveChip();
}

function markActiveChip() {
  const pair = `${state.from}/${state.to}`;
  for (const chip of el.chips.children) {
    chip.classList.toggle("active", chip.dataset.pair === pair);
  }
}

/* Instant side conversions, computed from the rate table we already have. */
function renderMiniCards() {
  const from = state.from;
  const targets = MINI_TARGETS.filter(
    (c) => c !== from && c !== state.to && state.rates[c]
  ).slice(0, 4);
  const amount = amountValue();
  const byCode = Object.fromEntries(state.currencies.map((c) => [c.code, c]));

  el.miniGrid.innerHTML = targets
    .map((code) => {
      const rate = state.rates[code] / state.rates[from];
      const c = byCode[code] ?? { flag: "🏳️", name: code };
      return `
        <div class="mini-card">
          <div class="mini-top"><span class="flag">${esc(c.flag)}</span><code>${esc(code)}</code></div>
          <div class="mini-value">${fmtCurrency(code, amount * rate)}</div>
          <div class="mini-name">${esc(c.name)}</div>
        </div>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ */
/* conversion                                                          */
/* ------------------------------------------------------------------ */

function amountValue(raw) {
  const parsed = parseFloat((raw ?? el.amount.value).replace(/[,\s]/g, ""));
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function updateAmountSymbol() {
  el.amountSymbol.textContent = currencySymbol(state.from);
}

async function convert() {
  const amount = amountValue();
  if (amount === null) {
    el.result.textContent = "…";
    delete el.result.dataset.value;  // don't let a stale value get carried by swap
    el.resultCode.textContent = "";
    el.rateLine.textContent = "Enter a valid amount.";
    return;
  }
  try {
    const data = await api(
      `/api/convert?amount=${encodeURIComponent(amount)}&from=${state.from}&to=${state.to}`
    );
    const target = Object.keys(data.results)[0];
    animateCurrency(el.result, data.results[target], target);
    el.resultCode.textContent = target;
    const rate = data.rates[target];
    el.rateLine.innerHTML =
      `<strong>1 ${data.from} = ${fmtRate(rate)} ${target}</strong>` +
      ` · 1 ${target} = ${fmtRate(1 / rate)} ${data.from}` +
      ` · data from ${data.updated}`;
  } catch (err) {
    toast(err.message);
  }
}

/* ------------------------------------------------------------------ */
/* history chart                                                       */
/* ------------------------------------------------------------------ */

async function loadHistory() {
  const from = state.from;
  const to = state.to;
  const days = state.chartDays;
  const key = `${from}/${to}/${days}`;
  state.historyKey = key;
  el.chartTitle.textContent = `${from} → ${to} · ${days}-day history`;

  if (typeof Chart === "undefined") {
    el.chartCanvas.classList.add("hidden");
    el.chartEmpty.classList.remove("hidden");
    el.chartEmpty.textContent = "Chart library could not load (no internet?).";
    return;
  }

  if (!state.chart) {  // nothing drawn yet: hint while the first fetch runs
    el.chartEmpty.textContent = "Loading history...";
    el.chartEmpty.classList.remove("hidden");
  }

  try {
    const data = await api(`/api/history?from=${from}&to=${to}&days=${days}`);
    if (state.historyKey !== key) return; // user moved on while we waited
    if (!data.points.length) {
      el.chartCanvas.classList.add("hidden");
      el.chartEmpty.classList.remove("hidden");
      el.chartEmpty.textContent =
        `No historical data is available for ${from} → ${to} right now. ` +
        `The converter above still works for all 160+ currencies.`;
      if (state.chart) { state.chart.destroy(); state.chart = null; }
      return;
    }

    el.chartEmpty.classList.add("hidden");
    el.chartCanvas.classList.remove("hidden");
    drawChart(data.points, `${from} → ${to}`);
  } catch (err) {
    el.chartEmpty.textContent = `History unavailable: ${err.message}`;
    el.chartCanvas.classList.add("hidden");
    el.chartEmpty.classList.remove("hidden");
  }
}

function drawChart(points, label) {
  const ctx = el.chartCanvas.getContext("2d");
  const lineGrad = ctx.createLinearGradient(0, 0, el.chartCanvas.width || 600, 0);
  lineGrad.addColorStop(0, "#22d3ee");
  lineGrad.addColorStop(1, "#7c5cff");
  const fillGrad = ctx.createLinearGradient(0, 0, 0, 260);
  fillGrad.addColorStop(0, "rgba(124, 92, 255, 0.30)");
  fillGrad.addColorStop(1, "rgba(124, 92, 255, 0)");

  const labels = points.map((p) => p.date);
  const values = points.map((p) => p.rate);
  const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });

  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label,
        data: values,
        borderColor: lineGrad,
        backgroundColor: fillGrad,
        fill: true,
        tension: 0.35,
        borderWidth: 2.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: "#22d3ee",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "rgba(13, 18, 32, 0.95)",
          borderColor: "rgba(255,255,255,0.15)",
          borderWidth: 1,
          titleColor: "#f2f5fa",
          bodyColor: "#9aa7bd",
          padding: 10,
          displayColors: false,
          callbacks: {
            title: (items) => dayFmt.format(new Date(items[0].label + "T00:00:00")),
            label: (item) => `1 ${label.split(" ")[0]} = ${fmtRate(item.parsed.y)} ${label.split(" ")[2]}`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: "rgba(255,255,255,0.05)" },
          ticks: {
            color: "#6b7893",
            maxTicksLimit: 8,
            callback: (_v, i) => dayFmt.format(new Date(labels[i] + "T00:00:00")),
          },
        },
        y: {
          grid: { color: "rgba(255,255,255,0.05)" },
          ticks: { color: "#6b7893", maxTicksLimit: 6, callback: (v) => fmtRate(v) },
        },
      },
    },
  });
}

/* ------------------------------------------------------------------ */
/* persistence: remember the last pair/amount across reloads           */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = "converter.lastState";

function saveState() {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ from: state.from, to: state.to, amount: el.amount.value })
    );
  } catch { /* private mode etc. */ }
}

function restoreState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    if (saved.from && state.rates[saved.from]) state.from = saved.from;
    if (saved.to && state.rates[saved.to]) state.to = saved.to;
    if (saved.amount && amountValue(saved.amount) !== null) el.amount.value = saved.amount;
  } catch { /* corrupt storage: keep defaults */ }
}

/* ------------------------------------------------------------------ */
/* events                                                              */
/* ------------------------------------------------------------------ */

function onPairChange() {
  markActiveChip();
  updateAmountSymbol();
  saveState();
  convert();
  renderMiniCards();
  loadHistory();
}

async function loadCurrencies() {
  const data = await api("/api/currencies");
  state.rates = data.rates;
  state.currencies = data.currencies;
  state.updated = data.updated;
  state.source = data.source;
  state.stale = data.stale;
  setStatus();

  fromBox.setOptions(state.currencies);
  toBox.setOptions(state.currencies);
  state.from = "USD";
  state.to = "EUR";
  restoreState();       // override defaults with the user's last pair/amount
  fromBox.set(state.from);
  toBox.set(state.to);

  populateChips();
  onPairChange();
}

el.amount.addEventListener("input", () => {
  clearTimeout(state.convertTimer);
  state.convertTimer = setTimeout(() => {
    saveState();
    convert();
    renderMiniCards();
  }, 250);
});

el.swap.addEventListener("click", () => {
  // Carry the converted value over so the swap shows the same money from
  // the other side: 100 USD = 325,714.80 COP becomes 325,714.80 COP = 100 USD.
  const carried = parseFloat(el.result.dataset.value);
  [state.from, state.to] = [state.to, state.from];
  fromBox.set(state.from);
  toBox.set(state.to);
  if (Number.isFinite(carried)) {
    el.amount.value = fmtAmountInput(carried);
  }
  el.swap.classList.toggle("flipped");
  onPairChange();
});

el.copy.addEventListener("click", async () => {
  const text = `${el.result.textContent} ${el.resultCode.textContent}`;
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard", "ok");
  } catch {
    toast("Couldn't access the clipboard", "error");
  }
});

el.refresh.addEventListener("click", refreshRates);

el.rangePills.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-days]");
  if (!btn) return;
  for (const b of el.rangePills.children) b.classList.toggle("active", b === btn);
  state.chartDays = Number(btn.dataset.days);
  loadHistory();
});

async function refreshRates() {
  el.refresh.classList.add("spinning");
  try {
    const data = await api("/api/refresh", { method: "POST" });
    await loadCurrencies();
    toast(
      data.stale
        ? `Couldn't reach the API; showing cached rates from ${data.updated}`
        : `Rates refreshed: ${data.updated}`,
      data.stale ? "error" : "ok"
    );
  } catch (err) {
    toast(err.message);
  } finally {
    el.refresh.classList.remove("spinning");
  }
}

loadCurrencies().catch((err) => {
  setStatus();
  toast(err.message);
});
