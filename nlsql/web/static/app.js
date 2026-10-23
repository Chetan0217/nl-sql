// The browser side of nlsql. Plain JavaScript, no framework and nothing loaded from the internet.
// Every button sends JSON to the Python server (app.py) and draws the answer.
// Text from the server is always inserted with text nodes, never as raw HTML,
// so a value in your database can never inject HTML or scripts into the page.

// ================================================================ small helpers

const $ = (id) => document.getElementById(id);

// el("p", {class: "muted"}, "hello", otherElement) -> <p class="muted">hello …</p>
// Keys starting with "on" become event listeners. Children can be text, elements, or arrays of them.
function el(tag, attrs = {}, ...children) {
  const svg = ["svg", "g", "rect", "line", "path", "circle", "text", "polyline", "defs", "linearGradient", "stop"].includes(tag);
  const node = svg ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") svg ? node.setAttribute("class", v) : (node.className = v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// Call the server. Returns parsed JSON, or throws an Error carrying the server's message.
async function api(method, path, body, signal) {
  const res = await fetch(path, {
    method,
    signal,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : res.statusText);
  }
  return data;
}

// localStorage can throw (private windows, blocked storage). Never let that break the page.
const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ } },
};

function toast(msg) {
  const t = el("div", { class: "toast" }, msg);
  $("toasts").append(t);
  setTimeout(() => t.remove(), 2600);
}

async function copy(text, what = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(`${what} to clipboard`); }
  catch { toast("Could not copy: the browser blocked it"); }
}

const isNum = (v) => typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v));
const fmt = (v) => (isNum(v) ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v));
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
const card = (cls, ...kids) => el("div", { class: `card ${cls || ""}` }, ...kids);
const errorCard = (title, msg) => card("bad", el("h3", {}, title), el("p", {}, msg));

// ================================================================ SQL syntax colouring

const KEYWORDS = new Set(("select from where join left right inner outer full cross on and or not in is null as group by order " +
  "having limit offset distinct union all with case when then else end asc desc between like exists insert update delete " +
  "into values set create drop alter table interval over partition count sum avg min max cast coalesce if true false").split(" "));

// Splits SQL into coloured spans. Text is still inserted as text, so it stays safe.
function highlight(sql) {
  const out = [];
  const re = /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*'?)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)(\s*\()?/g;
  let last = 0, m;
  while ((m = re.exec(sql))) {
    if (m.index > last) out.push(sql.slice(last, m.index));
    if (m[1]) out.push(el("span", { class: "tk-cmt" }, m[1]));
    else if (m[2]) out.push(el("span", { class: "tk-str" }, m[2]));
    else if (m[3]) out.push(el("span", { class: "tk-num" }, m[3]));
    else if (KEYWORDS.has(m[4].toLowerCase())) out.push(el("span", { class: "tk-kw" }, m[4]), m[5] || "");
    else if (m[5]) out.push(el("span", { class: "tk-fn" }, m[4]), m[5]);
    else out.push(m[4]);
    last = re.lastIndex;
  }
  out.push(sql.slice(last));
  return el("pre", { class: "sql" }, out);
}

// ================================================================ results: table, chart, big number

function resultView(result) {
  const { columns, rows } = result;
  const chart = chartSpec(result);
  const body = el("div");
  const views = { table: () => tableView(result) };
  if (chart?.kind === "number") views.number = () => bigNumber(result);
  else if (chart) views.chart = () => chartView(result, chart);
  let current = chart?.kind === "number" ? "number" : chart ? "chart" : "table";

  const seg = el("div", { class: "seg" });
  const draw = () => {
    seg.replaceChildren(...Object.keys(views).map((v) =>
      el("button", { class: v === current ? "active" : "", onclick: () => { current = v; draw(); } },
        { table: "Table", chart: "Chart", number: "Answer" }[v])));
    body.replaceChildren(views[current]());
  };
  draw();

  const note = plural(rows.length, "row") + (result.capped ? " (capped at 1,000, there may be more)" : "");
  return card("", el("div", { class: "card-head" },
    el("div", {}, el("h3", { style: "margin:0" }, "Result"), el("span", { class: "meta" }, note)),
    el("div", { class: "row tight" }, Object.keys(views).length > 1 ? seg : null,
      el("button", { class: "small", onclick: () => copy(toCSV(columns, rows), "Rows copied") }, "Copy"),
      el("button", { class: "small", onclick: () => downloadCSV(columns, rows) }, "CSV"))),
    body);
}

function bigNumber(result) {
  const v = result.rows[0][0];
  return el("div", { style: "padding:12px 4px" }, el("div", { class: "big-number" }, v === null ? "null" : fmt(v)),
    el("div", { class: "muted" }, result.columns[0]));
}

function tableView({ columns, rows }) {
  let sortCol = -1, dir = 1, filter = "";
  const tbody = el("tbody"), thead = el("thead");
  const search = el("input", { class: "search", type: "search", placeholder: "Filter rows…", style: "max-width:260px;margin-bottom:10px" });
  const draw = () => {
    thead.replaceChildren(el("tr", {}, columns.map((c, i) =>
      el("th", { title: "Click to sort", onclick: () => { dir = sortCol === i ? -dir : 1; sortCol = i; draw(); } },
        c, sortCol === i ? el("span", { class: "arrow" }, dir > 0 ? "▲" : "▼") : null))));
    let shown = rows;
    if (filter) shown = shown.filter((r) => r.some((v) => v !== null && String(v).toLowerCase().includes(filter)));
    if (sortCol >= 0) {
      shown = [...shown].sort((a, b) => {
        const x = a[sortCol], y = b[sortCol];
        if (x === null) return 1; if (y === null) return -1;
        return (isNum(x) && isNum(y) ? Number(x) - Number(y) : String(x).localeCompare(String(y))) * dir;
      });
    }
    tbody.replaceChildren(...shown.map((r) => el("tr", {}, r.map((v) =>
      v === null ? el("td", { class: "null" }, "null") : el("td", { class: isNum(v) ? "num" : "" }, isNum(v) ? fmt(v) : v)))));
  };
  search.addEventListener("input", () => { filter = search.value.toLowerCase(); draw(); });
  draw();
  return el("div", {}, rows.length > 8 ? search : null,
    el("div", { class: "table-wrap" }, el("table", {}, thead, tbody)));
}

// Decide whether the rows can be drawn, and how. Returns null when a chart would not help.
function chartSpec({ columns, rows }) {
  if (!rows.length) return null;
  const numeric = columns.map((_, i) => rows.some((r) => r[i] !== null) && rows.every((r) => r[i] === null || isNum(r[i])));
  if (rows.length === 1 && columns.length === 1 && numeric[0]) return { kind: "number" };
  let label = numeric.indexOf(false);
  if (label < 0 && columns.length >= 2) label = 0;
  const series = columns.map((_, i) => i).filter((i) => numeric[i] && i !== label);
  if (label < 0 || !series.length || rows.length < 2 || rows.length > 200) return null;
  const timeLike = /month|date|year|day|week|period|time/i.test(columns[label]) ||
    rows.every((r) => /^\d{4}(-\d{2})?/.test(String(r[label])));
  return { kind: timeLike && rows.length > 2 ? "line" : "bar", label, series };
}

function chartView(result, spec) {
  let s = spec.series[0];
  const holder = el("div", { class: "chart" });
  const draw = () => holder.replaceChildren(spec.kind === "line" ? lineChart(result, spec.label, s) : barChart(result, spec.label, s));
  const picker = spec.series.length > 1
    ? el("label", { class: "inline", style: "margin-bottom:8px" }, "Show",
      el("select", { onchange: (e) => { s = Number(e.target.value); draw(); } },
        spec.series.map((i) => el("option", { value: i }, result.columns[i]))))
    : null;
  draw();
  return el("div", {}, picker, holder);
}

const tip = {
  show(e, text) { const t = $("tooltip"); t.textContent = text; t.classList.remove("hidden"); tip.move(e); },
  move(e) { const t = $("tooltip"); t.style.left = e.clientX + 14 + "px"; t.style.top = e.clientY + 14 + "px"; },
  hide() { $("tooltip").classList.add("hidden"); },
};
const hover = (text) => ({ onmouseenter: (e) => tip.show(e, text), onmousemove: tip.move, onmouseleave: tip.hide });

// Horizontal bars: one per row, longest = largest value. Shows at most 40 rows.
function barChart({ columns, rows }, li, si) {
  const data = rows.slice(0, 40).map((r) => ({ label: r[li] === null ? "null" : String(r[li]), v: Number(r[si] ?? 0) }));
  const W = 760, labelW = 190, valueW = 90, rowH = 28, top = 8;
  const max = Math.max(...data.map((d) => Math.abs(d.v)), 1);
  const H = top + data.length * rowH + 8;
  const barW = (v) => Math.max(2, (Math.abs(v) / max) * (W - labelW - valueW));
  const cut = (s) => (s.length > 26 ? s.slice(0, 25) + "…" : s);
  return el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${columns[si]} by ${columns[li]}` },
    el("defs", {}, el("linearGradient", { id: "bar-grad", x1: "0", x2: "1" },
      el("stop", { offset: "0", "stop-color": "var(--bar)" }), el("stop", { offset: "1", "stop-color": "var(--bar2)" }))),
    data.map((d, i) => {
      const y = top + i * rowH;
      return el("g", hover(`${d.label}: ${fmt(d.v)}`),
        el("text", { class: "lbl", x: labelW - 10, y: y + 18, "text-anchor": "end" }, cut(d.label)),
        el("rect", { class: "barrect", x: labelW, y: y + 5, width: barW(d.v), height: rowH - 10, rx: 4, fill: "url(#bar-grad)" }),
        el("text", { x: labelW + barW(d.v) + 8, y: y + 18 }, fmt(d.v)));
    }),
    rows.length > 40 ? el("text", { x: labelW, y: H - 2 }, `first 40 of ${rows.length} rows`) : null);
}

// A line over time with dots you can hover, and a soft area under it.
function lineChart({ columns, rows }, li, si) {
  const data = rows.map((r) => ({ label: String(r[li]), v: Number(r[si] ?? 0) }));
  const W = 760, H = 300, L = 70, R = 44, T = 16, B = 44;
  const vals = data.map((d) => d.v);
  const step = niceStep((Math.max(...vals, 1) - Math.min(0, ...vals)) / 4);
  const lo = Math.floor(Math.min(0, ...vals) / step) * step, hi = Math.ceil(Math.max(...vals, 1) / step) * step;
  const x = (i) => L + (data.length === 1 ? 0 : (i / (data.length - 1)) * (W - L - R));
  const y = (v) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
  const pts = data.map((d, i) => `${x(i)},${y(d.v)}`).join(" ");
  const ticks = Array.from({ length: Math.round((hi - lo) / step) + 1 }, (_, k) => lo + k * step);
  const every = Math.ceil(data.length / 8);
  return el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${columns[si]} over ${columns[li]}` },
    el("defs", {}, el("linearGradient", { id: "area-grad", x1: "0", y1: "0", x2: "0", y2: "1" },
      el("stop", { offset: "0", "stop-color": "var(--bar)", "stop-opacity": ".35" }),
      el("stop", { offset: "1", "stop-color": "var(--bar)", "stop-opacity": "0" }))),
    ticks.map((t) => el("g", {}, el("line", { class: "axis", x1: L, x2: W - R, y1: y(t), y2: y(t) }),
      el("text", { x: L - 8, y: y(t) + 4, "text-anchor": "end" }, fmt(t)))),
    el("path", { d: `M${x(0)},${y(lo)} L${pts.replaceAll(" ", " L")} L${x(data.length - 1)},${y(lo)} Z`, fill: "url(#area-grad)" }),
    el("polyline", { points: pts, fill: "none", stroke: "var(--bar)", "stroke-width": 2.5, "stroke-linejoin": "round" }),
    data.map((d, i) => el("g", {},
      el("circle", { cx: x(i), cy: y(d.v), r: 4, fill: "var(--card)", stroke: "var(--bar)", "stroke-width": 2, ...hover(`${d.label}: ${fmt(d.v)}`) }),
      (i % every === 0 && data.length - 1 - i >= every / 2) || i === data.length - 1
        ? el("text", { x: x(i), y: H - B + 20, "text-anchor": i === 0 ? "start" : i === data.length - 1 ? "end" : "middle" }, d.label.slice(0, 10))
        : null)));
}

// A round step for axis ticks: 1, 2 or 5 times a power of ten.
function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw || 1)), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

function toCSV(columns, rows) {
  const esc = (v) => (v === null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  return [columns, ...rows].map((r) => r.map(esc).join(",")).join("\n");
}

function downloadFile(name, text, type) {
  const a = el("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
const downloadCSV = (columns, rows) => downloadFile("nlsql-result.csv", toCSV(columns, rows), "text/csv");

// ================================================================ tabs, theme, sidebar, keyboard

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${name}`));
  if (name === "history") renderHistory();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

const THEMES = ["dark", "light", "auto"];
function applyTheme(t) {
  if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  $("theme-btn").textContent = { auto: "◐", light: "☀", dark: "☾" }[t];
  $("theme-btn").title = `Theme: ${t === "auto" ? "follow Windows" : t}. Click to change.`;
}
let theme = store.get("nlsql-theme-mode", "dark"); // dark unless the person picked something else
applyTheme(theme);
$("theme-btn").addEventListener("click", () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % 3];
  applyTheme(theme);
  store.set("nlsql-theme-mode", theme);
  try { localStorage.setItem("nlsql-theme", theme); } catch { /* ignore */ }
});

function toggleSidebar() {
  const layout = document.querySelector(".layout");
  layout.classList.toggle(window.innerWidth <= 900 ? "show-side" : "no-side");
}
$("sidebar-btn").addEventListener("click", toggleSidebar);

document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  const tab = { 1: "ask", 2: "explain", 3: "history", 4: "rules" }[e.key];
  if (tab) showTab(tab);
  else if (e.key === "/") { e.preventDefault(); showTab("ask"); $("question").focus(); }
  else if (e.key === "s") toggleSidebar();
});

// Remember which box was used last, so clicking a schema name inserts it there.
let lastBox = $("question");
for (const id of ["question", "sql-in", "rules-text"]) $(id).addEventListener("focus", (e) => (lastBox = e.target));

function insertAtCursor(box, text) {
  const s = box.selectionStart ?? box.value.length, e = box.selectionEnd ?? s;
  const before = box.value.slice(0, s), pad = before && !/\s$/.test(before) ? " " : "";
  box.value = before + pad + text + box.value.slice(e);
  box.focus();
  box.selectionStart = box.selectionEnd = s + pad.length + text.length;
}

// ================================================================ status pills

async function loadStatus() {
  try {
    const s = await api("GET", "/api/status");
    const llmOk = s.llm.reachable !== false && s.llm.model_installed !== false;
    let llm = s.llm.model;
    if (s.llm.reachable === false) llm += ": Ollama not running";
    else if (s.llm.model_installed === false) llm += ": model not pulled";
    const pill = (ok, text, title) => el("span", { class: "pill", title }, el("span", { class: `dot ${ok ? "ok" : "bad"}` }), text);
    $("status").replaceChildren(
      pill(true, `${s.dialect} · ${s.tables.length} tables`, s.database),
      pill(llmOk, llm, llmOk ? "The local model is ready" : "Open Ollama from the Start menu"),
      pill(s.rules > 0, plural(s.rules, "rule"), "Business rules sent with every question"));
    if (!$("dialect").options.length) {
      for (const d of await api("GET", "/api/dialects")) $("dialect").append(el("option", { value: d }, d));
      $("dialect").value = s.dialect;
    }
  } catch (e) {
    $("status").replaceChildren(el("span", { class: "pill" }, el("span", { class: "dot bad" }), "Server not reachable"));
  }
}

// ================================================================ schema sidebar

let schemaTables = [];
async function loadSchema(reload = false) {
  const r = await api(reload ? "POST" : "GET", reload ? "/api/schema/reload" : "/api/schema");
  schemaTables = r.tables;
  drawSchema();
  if (reload) { toast("Schema reloaded from the database"); loadStatus(); }
}

function drawSchema() {
  const q = $("schema-filter").value.trim().toLowerCase();
  const tree = $("schema-tree");
  const items = schemaTables.map((t) => {
    const cols = q && !t.name.toLowerCase().includes(q) ? t.columns.filter((c) => c.name.toLowerCase().includes(q)) : t.columns;
    if (q && !cols.length) return null;
    return el("details", { open: q ? "" : null },
      el("summary", {}, t.name, el("span", { class: "ncols" }, cols.length),
        el("button", { class: "link-btn", title: "Insert table name", onclick: (e) => { e.preventDefault(); insertAtCursor(lastBox, t.name); } }, "+")),
      t.comment ? el("div", { class: "tcomment" }, t.comment) : null,
      cols.map((c) => [
        el("div", { class: "col", title: "Click to insert", onclick: () => insertAtCursor(lastBox, c.name) },
          c.name, c.pk ? el("span", { class: "pk" }, "KEY") : null, el("span", { class: "type" }, c.type.toLowerCase())),
        c.samples?.length ? el("div", { class: "samples" }, "e.g. " + c.samples.join(", ")) : null,
      ]));
  }).filter(Boolean);
  tree.replaceChildren(...(items.length ? items : [el("p", { class: "hint" }, "No match.")]));
}
$("schema-filter").addEventListener("input", drawSchema);
$("schema-reload").addEventListener("click", () => loadSchema(true).catch((e) => toast(e.message)));

// ================================================================ waiting animation

// Shows a spinner and a live seconds counter. Returns a function that stops it.
function waiting(out, title, cancel) {
  const t0 = Date.now();
  const timer = el("span", { class: "timer" }, "0s");
  const hint = el("div", { class: "meta" }, "The local model writes the SQL, the checks run, then it puts the answer into words.");
  out.replaceChildren(card("", el("div", { class: "waiting" }, el("div", { class: "orb" }),
    el("div", { style: "flex:1" }, el("div", {}, el("b", {}, title), "  ", timer), hint),
    cancel ? el("button", { class: "small", onclick: cancel }, "Cancel") : null),
  el("div", { class: "skeleton", style: "width:80%" }), el("div", { class: "skeleton", style: "width:55%" })));
  const id = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    timer.textContent = `${s}s`;
    if (s === 15) hint.textContent = "The first question after starting Ollama is slower while the model loads.";
    if (s === 45) hint.textContent = "Still working. If a check failed, the model is retrying with the exact error (up to 3 tries).";
  }, 250);
  return () => clearInterval(id);
}

// ================================================================ Ask: question -> SQL

const EXAMPLES = [
  "Which product category earned the most in 2025?",
  "Monthly revenue in 2025",
  "Top 10 customers by total spend, with their city",
  "Which payment method is used most for online orders?",
  "Products with the highest return rate",
  "How many orders were cancelled?",
];
$("examples").append(...EXAMPLES.map((q) =>
  el("button", { class: "chip", onclick: () => { $("question").value = q; ask(); } }, q)));

$("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || !e.shiftKey)) { e.preventDefault(); ask(); }
});
$("ask-btn").addEventListener("click", () => ask());

let asking = null;
async function ask() {
  const question = $("question").value.trim();
  if (!question) { $("question").focus(); toast("Type a question first"); return; }
  if (asking) return;
  const out = $("ask-out"), ctrl = new AbortController();
  asking = ctrl;
  $("ask-btn").disabled = true;
  const stop = waiting(out, "Translating…", () => ctrl.abort());
  try {
    const r = await api("POST", "/api/ask", { question, run: $("run").checked, check_meaning: $("check-meaning").checked, answer: $("answer-words").checked }, ctrl.signal);
    stop();
    if (r.ok) r.historyId = addHistory(question, r);
    out.replaceChildren(...renderAnswer(r, question));
  } catch (e) {
    stop();
    out.replaceChildren(e.name === "AbortError"
      ? card("warn", el("h3", {}, "Cancelled"), el("p", {}, "The page stopped waiting. The model may finish in the background; its answer is dropped."))
      : errorCard("Error", e.message));
  } finally {
    asking = null;
    $("ask-btn").disabled = false;
  }
}

function renderAnswer(r, question) {
  if (r.clarification) {
    return [card("warn", el("h3", {}, "The model needs more detail"), el("p", { style: "font-size:16px" }, r.clarification),
      el("p", { class: "meta" }, "Add that detail to your question and ask again."))];
  }
  if (!r.ok) {
    return [card("bad", el("h3", {}, `No valid query after ${r.attempts} tries`),
      el("p", {}, "Every attempt was rejected by the safety or database checks. Try rewording the question:"),
      el("ul", {}, r.errors.slice(-3).map((x) => el("li", {}, x))))];
  }
  const parts = [];
  const resultBox = el("div", { class: "out", style: "margin:0" });

  // --- the answer in plain words, first, because it is what the person asked for
  const answerBox = el("div", { class: "answer-slot" });
  if (r.answer) answerBox.append(answerCard(r.answer));
  parts.push(answerBox);

  // --- SQL card: view / edit / copy / run / explain / thumbs
  let sql = r.sql, editing = false;
  const view = el("div");
  const editBtn = el("button", { class: "small" }, "Edit");
  const drawSQL = () => {
    if (editing) {
      const ta = el("textarea", { class: "code", rows: Math.min(18, sql.split("\n").length + 2) });
      ta.value = sql;
      ta.addEventListener("input", () => (sql = ta.value));
      ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); runInto(sql, resultBox); } });
      view.replaceChildren(ta);
      editBtn.textContent = "Done";
    } else {
      view.replaceChildren(highlight(sql));
      editBtn.textContent = "Edit";
    }
  };
  editBtn.addEventListener("click", () => { editing = !editing; drawSQL(); });
  drawSQL();

  const badges = r.fromHistory
    ? [el("span", { class: "badge info" }, "From history: re-run without the model")]
    : [el("span", { class: "badge ok" }, "✓ Safety checks"), el("span", { class: "badge ok" }, "✓ Database EXPLAIN"),
      el("span", { class: "badge info" }, `${plural(r.attempts, "attempt")} · ${r.seconds}s`)];

  parts.push(card("ok",
    el("div", { class: "card-head" }, el("div", { class: "row tight" }, el("h3", { style: "margin:0" }, "SQL"), badges),
      el("div", { class: "row tight" },
        verdictButtons(r.historyId),
        editBtn,
        el("button", { class: "small", onclick: () => copy(sql, "SQL copied") }, "Copy"),
        el("button", { class: "small", onclick: () => runInto(sql, resultBox) }, "Run ▶"),
        el("button", { class: "small", onclick: () => { $("sql-in").value = sql; showTab("explain"); explain(); } }, "Explain"))),
    view));

  // --- assumptions + meaning side by side
  const side = [];
  if (r.assumptions?.length) {
    side.push(card("", el("h3", {}, "Assumptions"), el("ul", {}, r.assumptions.map((a) => el("li", {}, a))),
      el("p", { class: "meta" }, "Check these. A wrong assumption gives a wrong number from a perfectly valid query.")));
  }
  if (r.meaning) {
    side.push(card("", el("h3", {}, "What this SQL actually answers"), el("p", { style: "font-size:15.5px" }, r.meaning),
      el("p", { class: "meta" }, "Written by a separate model call that only saw the SQL. If it doesn't match your question, the SQL is wrong.")));
  }
  if (side.length) parts.push(side.length === 2 ? el("div", { class: "grid2" }, side) : side[0]);

  if (r.run_error) resultBox.append(errorCard("Database error", r.run_error));
  if (r.result) resultBox.append(resultView(r.result));
  parts.push(resultBox);
  return parts;
}

// Runs SQL into `out`. Returns the result, or null when it failed.
async function runInto(sql, out) {
  const stop = waiting(out, "Running…");
  try {
    const r = await api("POST", "/api/run", { sql });
    stop();
    out.replaceChildren(resultView(r.result));
    return r.result;
  } catch (e) {
    stop();
    out.replaceChildren(errorCard("Could not run", e.message));
    return null;
  }
}

// The plain-language answer. Numbers the model wrote that are not in the rows get flagged.
function answerCard(a) {
  if (!a.text) {
    return card("warn", el("h3", {}, "No answer in words"),
      el("p", { class: "meta" }, `The model could not describe the result (${a.error || "no text"}). The rows below are still correct.`));
  }
  const checked = a.unverified.length
    ? el("span", { class: "badge warn", title: "These numbers are not in the result. The model may have calculated or invented them." },
      `⚠ Check: ${a.unverified.join(", ")}`)
    : el("span", { class: "badge ok", title: "Every number in this sentence appears in the result rows" }, "✓ Numbers match the result");
  return card("answer", el("div", { class: "card-head" }, el("h3", { style: "margin:0" }, "Answer"),
    el("div", { class: "row tight" }, checked,
      el("button", { class: "small", onclick: () => copy(a.text, "Answer copied") }, "Copy"))),
  el("p", { class: "answer-text" }, a.text));
}

// Asks the model to describe rows the page already has (used for history re-runs).
async function describeInto(slot, question, sql, result) {
  if (!result || !$("answer-words").checked) return;
  slot.replaceChildren(card("", el("div", { class: "waiting" }, el("div", { class: "orb" }), el("span", { class: "meta" }, "Writing the answer in words…"))));
  try {
    const a = await api("POST", "/api/answer", { question, sql, columns: result.columns, rows: result.rows.slice(0, 50) });
    slot.replaceChildren(answerCard(a));
  } catch (e) {
    slot.replaceChildren(answerCard({ text: null, error: e.message, unverified: [] }));
  }
}

// ================================================================ History (saved in this browser only)

const HKEY = "nlsql-history";
const getHistory = () => store.get(HKEY, []);
const saveHistory = (h) => { store.set(HKEY, h.slice(0, 200)); $("history-count").textContent = Math.min(h.length, 200); };

function addHistory(question, r) {
  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  saveHistory([{ id, question, sql: r.sql, assumptions: r.assumptions || [], at: new Date().toISOString(),
    rows: r.result?.row_count ?? null, verdict: null }, ...getHistory()]);
  return id;
}

function setVerdict(id, verdict) {
  saveHistory(getHistory().map((h) => (h.id === id ? { ...h, verdict: h.verdict === verdict ? null : verdict } : h)));
}

// Thumbs up / down. They mark an answer right or wrong; the export becomes an accuracy test set.
function verdictButtons(id) {
  if (!id) return null;
  const wrap = el("span", { class: "row tight" });
  const draw = () => {
    const v = getHistory().find((h) => h.id === id)?.verdict;
    wrap.replaceChildren(
      el("button", { class: `small ${v === "right" ? "on" : ""}`, title: "This answer is right", onclick: () => { setVerdict(id, "right"); draw(); } }, "👍"),
      el("button", { class: `small ${v === "wrong" ? "on" : ""}`, title: "This answer is wrong", onclick: () => { setVerdict(id, "wrong"); draw(); } }, "👎"));
  };
  draw();
  return wrap;
}

function renderHistory() {
  const q = $("history-filter").value.trim().toLowerCase();
  const all = getHistory();
  const items = all.filter((h) => !q || h.question.toLowerCase().includes(q) || h.sql.toLowerCase().includes(q));
  if (!items.length) {
    $("history-list").replaceChildren(el("div", { class: "empty" },
      all.length ? "Nothing matches that search." : "No questions yet. Ask one and it appears here."));
    return;
  }
  $("history-list").replaceChildren(...items.map((h) => el("div", { class: "hist" },
    el("div", {}, el("div", { class: "q" }, h.question),
      el("div", { class: "when" }, new Date(h.at).toLocaleString(), h.rows !== null ? ` · ${plural(h.rows, "row")}` : "",
        h.verdict ? ` · marked ${h.verdict}` : "")),
    el("div", { class: "acts" },
      verdictButtons(h.id),
      el("button", { class: "small", title: "Run the saved SQL again (no model, instant)", onclick: () => rerun(h) }, "Re-run ▶"),
      el("button", { class: "small", title: "Ask the model again", onclick: () => { $("question").value = h.question; showTab("ask"); ask(); } }, "Ask again"),
      el("button", { class: "small ghost danger", title: "Delete", onclick: () => { saveHistory(getHistory().filter((x) => x.id !== h.id)); renderHistory(); } }, "✕")),
    highlight(h.sql))));
}

function rerun(h) {
  showTab("ask");
  $("question").value = h.question;
  const r = { ok: true, sql: h.sql, assumptions: h.assumptions, fromHistory: true, historyId: h.id };
  const out = $("ask-out");
  out.replaceChildren(...renderAnswer(r, h.question));
  runInto(h.sql, out.lastElementChild).then((res) => describeInto(out.querySelector(".answer-slot"), h.question, h.sql, res));
}

$("history-filter").addEventListener("input", renderHistory);
$("history-export").addEventListener("click", () =>
  downloadFile("nlsql-history.json", JSON.stringify(getHistory(), null, 2), "application/json"));
$("history-clear").addEventListener("click", () => {
  if (!getHistory().length) return;
  const keep = getHistory();
  saveHistory([]);
  renderHistory();
  const undo = el("button", { class: "small", style: "margin-left:10px", onclick: () => { saveHistory(keep); renderHistory(); } }, "Undo");
  const t = el("div", { class: "toast" }, "History cleared", undo);
  $("toasts").append(t);
  setTimeout(() => t.remove(), 6000);
});
$("history-count").textContent = getHistory().length;

// ================================================================ Explain: SQL -> plain language

$("explain-btn").addEventListener("click", () => explain());
$("sql-in").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); explain(); } });
$("run-sql-btn").addEventListener("click", () => {
  if (!$("sql-in").value.trim()) return toast("Paste a SQL query first");
  runInto($("sql-in").value, $("explain-out"));
});
$("format-btn").addEventListener("click", async () => {
  try {
    const r = await api("POST", "/api/format", { sql: $("sql-in").value, dialect: $("dialect").value });
    $("sql-in").value = r.sql;
  } catch (e) { toast(e.message); }
});

async function explain() {
  const sql = $("sql-in").value.trim();
  if (!sql) { $("sql-in").focus(); toast("Paste a SQL query first"); return; }
  const out = $("explain-out");
  $("explain-btn").disabled = true;
  const stop = waiting(out, "Explaining…");
  try {
    const r = await api("POST", "/api/explain", { sql, dialect: $("dialect").value });
    stop();
    const f = r.facts;
    const factChips = [
      ["Tables", f.tables?.join(", ")], ["Joins", f.joins?.length], ["Filter", f.where], ["Grouped by", f.group_by?.join(", ")],
      ["Sorted by", f.order_by?.join(", ")], ["Limit", f.limit], ["Outputs", f.outputs?.join(", ")],
    ].filter(([, v]) => v).map(([k, v]) => el("span", { class: "fact" }, el("b", {}, k), String(v)));
    out.replaceChildren(...[
      card("ok", el("div", { class: "card-head" }, el("h3", { style: "margin:0" }, "In plain language"),
        el("div", { class: "row tight" },
          el("span", { class: `badge ${r.safe_to_run ? "ok" : "bad"}` }, r.safe_to_run ? "✓ Safe to run here" : "✕ Would be blocked here"),
          el("span", { class: "badge info" }, `${r.seconds}s`))),
      el("p", { style: "font-size:17px;margin:0" }, r.summary)),
      r.warnings.length ? card("warn", el("h3", {}, `⚠ ${plural(r.warnings.length, "warning")}`),
        el("ul", {}, r.warnings.map((w) => el("li", {}, w)))) : null,
      el("div", { class: "grid2" },
        card("", el("h3", {}, "Step by step"), el("ol", { class: "steps" }, r.steps.map((s) => el("li", {}, s)))),
        card("", el("h3", {}, "Facts the parser found (not the model)"), el("div", { class: "facts" }, factChips),
          el("p", { class: "meta" }, "These come from reading the SQL's structure with code, so they can be trusted more than the summary."))),
    ].filter(Boolean));
  } catch (e) {
    stop();
    out.replaceChildren(errorCard("Could not explain", e.message));
  } finally {
    $("explain-btn").disabled = false;
  }
}

// ================================================================ Business rules

async function loadRules() {
  const r = await api("GET", "/api/rules");
  $("rules-text").value = r.text;
  $("rules-msg").textContent = `${plural(r.rules.length, "rule")} · ${r.path}`;
}

$("rules-save").addEventListener("click", async () => {
  try {
    const r = await api("PUT", "/api/rules", { text: $("rules-text").value });
    $("rules-msg").textContent = `${plural(r.rules.length, "rule")} · ${r.path}`;
    toast(`Saved. ${plural(r.rules.length, "rule")} now apply to every question.`);
    loadStatus();
  } catch (e) {
    toast("Could not save: " + e.message);
  }
});

// ================================================================ start

loadStatus();
setInterval(loadStatus, 30000); // notice when Ollama is opened or closed
loadSchema().catch((e) => $("schema-tree").replaceChildren(el("p", { class: "hint" }, e.message)));
loadRules().catch(() => {});
$("question").focus();
