const CURRENCY = document.body.dataset.currency || "ZAR";
const money = new Intl.NumberFormat("en-ZA", { style: "currency", currency: CURRENCY, maximumFractionDigits: 0 });
const money2 = new Intl.NumberFormat("en-ZA", { style: "currency", currency: CURRENCY, minimumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-ZA", { maximumFractionDigits: 4 });
const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(d)}%`);
const signed = (v, fmt) => `${v > 0 ? "+" : ""}${fmt(v)}`;
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const charts = {};
let freq = "W";

async function api(path, options) {
  const res = await fetch(`/api${path}`, options);
  if (res.status === 204) return null;
  const body = await res.json();
  if (!res.ok) {
    const d = body.detail;
    throw new Error(Array.isArray(d?.errors) ? d.errors.join("\n") : typeof d === "string" ? d
      : Array.isArray(d) ? d.map((e) => e.msg.replace("Value error, ", "")).join("\n") : "Request failed");
  }
  return body;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) node.append(c instanceof Node ? c : document.createTextNode(c ?? ""));
  return node;
}

function renderTable(table, headers, rows) {
  table.replaceChildren(
    el("thead", {}, el("tr", {}, headers.map(([label, num]) => el("th", num ? { class: "num" } : {}, label)))),
    el("tbody", {}, rows),
  );
}

// ------------------------------------------------------------------ KPIs
function renderKpis(s) {
  document.getElementById("asof").textContent = s.as_of ? `Valued at close on ${s.as_of}` : "No prices yet";
  const gainPct = s.net_contributions ? s.total_gain / s.net_contributions : 0;
  const cards = [
    ["Portfolio value", money.format(s.total_value), `${money.format(s.cash)} in cash`, "hero"],
    ["Total gain", signed(s.total_gain, money.format), `${signed(gainPct, pct)} on ${money.format(s.net_contributions)} invested`],
    ["Annual return (TWR)", pct(s.twr_annualised ?? s.twr), `${pct(s.twr)} since inception`],
    ["Money-weighted (XIRR)", pct(s.xirr), "your personal annual return"],
    ["Volatility", pct(s.volatility), "annualised"],
    ["Sharpe ratio", s.sharpe === null ? "–" : s.sharpe.toFixed(2), "vs. risk-free rate"],
    ["Max drawdown", pct(s.max_drawdown), "worst peak-to-trough"],
    ["Income & costs", money.format(s.dividends), `dividends · ${money.format(s.fees)} fees`],
  ];
  document.getElementById("kpis").replaceChildren(...cards.map(([label, value, sub, extra]) =>
    el("div", { class: `kpi ${extra || ""}` },
      el("div", { class: "label" }, label),
      el("div", { class: `value ${extra ? "" : label === "Total gain" ? cls(s.total_gain) : ""}` }, value),
      el("div", { class: "sub" }, sub))));
}

// ------------------------------------------------------------------ charts
function baseOptions(extra = {}) {
  const grid = css("--line");
  const ink = css("--muted");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: ink, boxWidth: 12 } } },
    scales: {
      x: { ticks: { color: ink, maxTicksLimit: 10, autoSkip: true, maxRotation: 0 }, grid: { display: false } },
      y: { ticks: { color: ink }, grid: { color: grid } },
    },
    ...extra,
  };
}

function draw(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}

async function renderTimeseries() {
  const ts = await api(`/timeseries?freq=${freq}`);
  const labels = ts.map((p) => p.date);
  const accent = css("--accent");
  draw("valueChart", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Portfolio value", data: ts.map((p) => p.total_value), borderColor: accent,
          backgroundColor: accent + "22", fill: true, pointRadius: 0, borderWidth: 2, tension: 0.15 },
        { label: "Net invested", data: ts.map((p) => p.contributions), borderColor: css("--muted"),
          borderDash: [5, 4], pointRadius: 0, borderWidth: 1.5, stepped: true },
      ],
    },
    options: baseOptions({
      plugins: {
        legend: { labels: { color: css("--muted"), boxWidth: 12 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money.format(c.parsed.y)}` } },
      },
      scales: {
        x: { ticks: { color: css("--muted"), maxTicksLimit: window.innerWidth < 600 ? 4 : 10, maxRotation: 0 }, grid: { display: false } },
        y: { ticks: { color: css("--muted"), callback: (v) => money.format(v) }, grid: { color: css("--line") } },
      },
    }),
  });
  const neg = css("--neg");
  draw("ddChart", {
    type: "line",
    data: { labels, datasets: [{ label: "Drawdown", data: ts.map((p) => p.drawdown * 100), borderColor: neg,
      backgroundColor: neg + "30", fill: true, pointRadius: 0, borderWidth: 1.5 }] },
    options: baseOptions({
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.parsed.y.toFixed(1)}%` } } },
      scales: {
        x: { ticks: { color: css("--muted"), maxTicksLimit: window.innerWidth < 600 ? 3 : 6, maxRotation: 0 }, grid: { display: false } },
        y: { max: 0, ticks: { color: css("--muted"), callback: (v) => `${v}%` }, grid: { color: css("--line") } },
      },
    }),
  });
}

async function renderAllocation() {
  const alloc = await api("/allocation");
  const palette = [css("--accent"), "#6d8b74", "#b7791f", "#7c6fb0", "#c2576b", "#94a3b8"];
  draw("allocChart", {
    type: "doughnut",
    data: { labels: alloc.map((a) => a.asset_class),
      datasets: [{ data: alloc.map((a) => a.value), backgroundColor: palette, borderColor: css("--card"), borderWidth: 3 }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "62%",
      plugins: {
        legend: { position: "right", labels: { color: css("--ink"), boxWidth: 12,
          generateLabels: (chart) => chart.data.labels.map((l, i) => ({
            text: `${l}  ${pct(alloc[i].weight)}`, fillStyle: palette[i], strokeStyle: palette[i],
            fontColor: css("--ink"), index: i })) } },
        tooltip: { callbacks: { label: (c) => `${c.label}: ${money.format(c.parsed)} (${pct(alloc[c.dataIndex].weight)})` } },
      },
    },
  });
}

// ------------------------------------------------------------------ tables
async function renderHoldings() {
  const rows = await api("/holdings");
  renderTable(document.getElementById("holdings"),
    [["Fund"], ["Class"], ["Units", 1], ["Avg cost", 1], ["Price", 1], ["Value", 1], ["Gain", 1], ["Return", 1], ["Weight"]],
    rows.map((h) => el("tr", {},
      el("td", {}, el("strong", {}, h.symbol), el("div", { class: "muted small" }, h.name)),
      el("td", {}, el("span", { class: "tag" }, h.asset_class)),
      el("td", { class: "num" }, num.format(h.quantity)),
      el("td", { class: "num" }, money2.format(h.avg_cost)),
      el("td", { class: "num" }, money2.format(h.price)),
      el("td", { class: "num" }, money.format(h.market_value)),
      el("td", { class: `num ${cls(h.unrealised_pnl)}` }, signed(h.unrealised_pnl, money.format)),
      el("td", { class: `num ${cls(h.unrealised_pct)}` }, signed(h.unrealised_pct, pct)),
      el("td", {}, el("div", { class: "bar", title: pct(h.weight) }, el("span", { style: `width:${(h.weight * 100).toFixed(1)}%` }))),
    )));
}

async function renderMonthly() {
  const data = await api("/monthly-returns");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const years = [...new Set(data.map((d) => d.year))].sort();
  const lookup = new Map(data.map((d) => [`${d.year}-${d.month}`, d.return]));
  const shade = (r) => {
    const a = Math.min(Math.abs(r) / 0.06, 1) * 0.75 + 0.08;
    return r >= 0 ? `background: rgba(21,128,61,${a}); color:${a > 0.5 ? "#fff" : "inherit"}`
      : `background: rgba(185,28,28,${a}); color:${a > 0.5 ? "#fff" : "inherit"}`;
  };
  renderTable(document.getElementById("monthly"), [["Year"], ...months.map((m) => [m]), ["Year"]],
    years.map((y) => {
      const vals = months.map((_m, i) => lookup.get(`${y}-${i + 1}`));
      const yearly = vals.filter((v) => v !== undefined).reduce((acc, v) => acc * (1 + v), 1) - 1;
      return el("tr", {},
        el("td", { class: "year" }, String(y)),
        vals.map((v) => (v === undefined ? el("td", {}, "") : el("td", { style: shade(v) }, pct(v)))),
        el("td", { style: shade(yearly / 4), class: "year" }, pct(yearly)));
    }));
}

async function renderTransactions() {
  const txs = await api("/transactions?limit=200");
  renderTable(document.getElementById("txs"),
    [["Date"], ["Type"], ["Fund"], ["Detail", 1], ["Cash", 1], [""]],
    txs.map((t) => {
      const trade = t.type === "BUY" || t.type === "SELL";
      const cash = trade ? (t.type === "BUY" ? -1 : 1) * t.quantity * t.price - t.fees
        : ["WITHDRAWAL", "FEE"].includes(t.type) ? -t.amount : t.amount;
      return el("tr", {},
        el("td", {}, t.date),
        el("td", {}, el("span", { class: "tag" }, t.type)),
        el("td", {}, t.symbol || ""),
        el("td", { class: "num muted" }, trade ? `${num.format(t.quantity)} @ ${money2.format(t.price)}` : t.note || ""),
        el("td", { class: `num ${cls(cash)}` }, signed(cash, money2.format)),
        el("td", {}, el("button", { class: "link", title: "Delete", "aria-label": `Delete transaction ${t.id}`,
          onclick: () => deleteTx(t.id) }, "✕")));
    }));
}

async function deleteTx(id) {
  if (!confirm("Delete this transaction?")) return;
  try {
    await api(`/transactions/${id}`, { method: "DELETE" });
    await refresh();
  } catch (e) {
    alert(e.message);
  }
}

async function loadSymbols() {
  const instruments = await api("/instruments");
  const select = document.getElementById("symbolSelect");
  select.replaceChildren(el("option", { value: "" }, "—"),
    ...instruments.map((i) => el("option", { value: i.symbol }, `${i.symbol} · ${i.name}`)));
}

// ------------------------------------------------------------------ forms
function setMsg(id, text, ok) {
  const m = document.getElementById(id);
  m.textContent = text;
  m.className = `msg ${ok ? "ok" : "err"}`;
}

document.getElementById("txForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const payload = { date: f.date, type: f.type, symbol: f.symbol || null };
  for (const k of ["quantity", "price", "amount", "fees"]) payload[k] = f[k] ? Number(f[k]) : 0;
  try {
    await api("/transactions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    setMsg("txMsg", "Saved.", true);
    e.target.reset();
    await refresh();
  } catch (err) {
    setMsg("txMsg", err.message, false);
  }
});

document.getElementById("importForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const body = new FormData();
  body.append("file", form.get("file"));
  try {
    const res = await api(`/import/${form.get("kind")}`, { method: "POST", body });
    setMsg("importMsg", `Imported ${res.imported} rows.`, true);
    e.target.reset();
    await loadSymbols();
    await refresh();
  } catch (err) {
    setMsg("importMsg", err.message, false);
  }
});

document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", async () => {
  document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
  freq = b.dataset.freq;
  await renderTimeseries();
}));

async function refresh() {
  const summary = await api("/summary");
  renderKpis(summary);
  await Promise.all([renderTimeseries(), renderAllocation(), renderHoldings(), renderMonthly(), renderTransactions()]);
}

document.querySelector('#txForm [name="date"]').valueAsDate = new Date();
loadSymbols();
refresh().catch((e) => { document.getElementById("asof").textContent = `Could not load portfolio: ${e.message}`; });
