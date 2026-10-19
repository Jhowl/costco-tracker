"use strict";

const $app = document.getElementById("app");
const $person = document.getElementById("person");
const state = { people: [], meta: null, person: "" };
try { state.person = localStorage.getItem("person") || ""; } catch (_) { }

// ---------------------------------------------------------------- helpers
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = v => v == null ? "–" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = d => d ? new Date(d.slice(0, 10) + "T12:00").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "–";
const person = id => state.people.find(p => p.id === id) || { name: "?", color: "#888" };
const who = id => `<span class="dot" style="background:${person(id).color}"></span>${esc(person(id).name)}`;
const pname = p => esc(p.name || p.receipt_name);
const ret = r => r ? `<span class="st-${r.state}">${esc(r.label)}</span>` : "";

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { }
    throw new Error(msg);
  }
  return res.json();
}
function withPerson(path, extra = {}) {
  const u = new URL(path, location.origin);
  if (state.person) u.searchParams.set("person", state.person);
  for (const [k, v] of Object.entries(extra)) if (v != null && v !== "") u.searchParams.set(k, v);
  return u.pathname + u.search;
}
function route() {
  const [path, query] = location.hash.slice(1).split("?");
  return { path: path || "/", params: new URLSearchParams(query || "") };
}
function go(path, params) {
  const q = new URLSearchParams(Object.entries(params || {}).filter(([, v]) => v != null && v !== "")).toString();
  location.hash = path + (q ? "?" + q : "");
}

// ---------------------------------------------------------------- charts (inline SVG)
function monthBars(rows) {
  const months = [...new Set(rows.map(r => r.month))].slice(-36);
  if (!months.length) return `<p class="muted">No receipts yet.</p>`;
  const per = months.map(m => rows.filter(r => r.month === m));
  const max = Math.max(...per.map(rs => rs.reduce((s, r) => s + Math.max(r.spent, 0), 0)), 1);
  const W = 760, H = 220, pad = 40, bw = (W - pad) / months.length;
  let out = `<svg viewBox="0 0 ${W} ${H + 24}" width="100%" role="img" aria-label="Spending per month">`;
  for (let i = 0; i <= 4; i++) {
    const y = H - (H - 10) * i / 4;
    out += `<line class="gridline" x1="${pad}" x2="${W}" y1="${y}" y2="${y}"/><text x="${pad - 4}" y="${y + 4}" text-anchor="end">${max * i / 4 >= 1000 ? "$" + (max * i / 4000).toFixed(1) + "K" : "$" + Math.round(max * i / 4)}</text>`;
  }
  per.forEach((rs, i) => {
    let y = H;
    for (const r of rs) {
      const h = (H - 10) * Math.max(r.spent, 0) / max;
      y -= h;
      out += `<rect x="${pad + i * bw + 2}" y="${y}" width="${Math.max(bw - 4, 2)}" height="${h}" rx="2" fill="${person(r.person_id).color}"><title>${months[i]} · ${esc(person(r.person_id).name)}: ${money(r.spent)} (${r.receipts} receipts)</title></rect>`;
    }
    if (months.length <= 12 || i % 3 === 0) out += `<text x="${pad + i * bw + bw / 2}" y="${H + 16}" text-anchor="middle">${months[i].slice(2)}</text>`;
  });
  return out + "</svg>";
}

function priceChart(purchases) {
  const pts = purchases.filter(p => p.quantity > 0 && p.unit_price);
  if (pts.length < 2) return "";
  const t = p => new Date(p.purchase_date + "T12:00").getTime();
  const xs = pts.map(t), ys = pts.flatMap(p => [p.unit_price, p.paid_unit || p.unit_price]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
  const step = Math.max(...ys) > 20 ? 5 : Math.max(...ys) > 4 ? 1 : 0.5;
  const y0 = Math.max(0, Math.floor(Math.min(...ys) / step) * step - (Math.min(...ys) % step === 0 ? step : 0));
  const y1 = Math.ceil(Math.max(...ys) / step) * step + (Math.max(...ys) % step === 0 ? step : 0);
  const r = pts.length > 30 ? 3.5 : 5;
  const W = 760, H = 200, pad = 46;
  const X = v => pad + (W - pad - 10) * (x1 === x0 ? 0.5 : (v - x0) / (x1 - x0));
  const Y = v => H - 10 - (H - 20) * (y1 === y0 ? 0.5 : (v - y0) / (y1 - y0));
  let out = `<svg viewBox="0 0 ${W} ${H + 20}" width="100%" role="img" aria-label="Price history">`;
  const tick = (y1 - y0) / step > 6 ? step * Math.ceil((y1 - y0) / step / 5) : step;
  for (let v = y0; v <= y1 + 1e-9; v += tick) {
    out += `<line class="gridline" x1="${pad}" x2="${W}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${pad - 4}" y="${Y(v) + 4}" text-anchor="end">$${v.toFixed(2)}</text>`;
  }
  out += `<polyline fill="none" stroke="var(--accent2)" stroke-width="2" points="${pts.map(p => `${X(t(p))},${Y(p.unit_price)}`).join(" ")}"/>`;
  for (const p of pts) {
    if (p.paid_unit != null && p.paid_unit < p.unit_price - 0.005)
      out += `<circle cx="${X(t(p))}" cy="${Y(p.paid_unit)}" r="4" fill="none" stroke="var(--ok)" stroke-width="2"><title>${p.purchase_date}: paid ${money(p.paid_unit)} after instant savings</title></circle>`;
    out += `<circle cx="${X(t(p))}" cy="${Y(p.unit_price)}" r="${r}" fill="${person(p.person_id).color}" stroke="var(--card)" stroke-width="1.5" data-tip="${esc(`<b>${fmtDate(p.purchase_date)}</b><br>${person(p.person_id).name} · ${p.quantity}×<br>Price ${money(p.unit_price)}${p.discount ? `<br>Paid ${money(p.paid_unit)} after savings` : ""}`)}"></circle>`;
  }
  out += `<text x="${pad}" y="${H + 16}">${fmtDate(pts[0].purchase_date)}</text><text x="${W}" y="${H + 16}" text-anchor="end">${fmtDate(pts[pts.length - 1].purchase_date)}</text>`;
  return out + `</svg><p class="small muted">Line = shelf price per unit. <span style="color:var(--ok)">○</span> = what you paid after instant savings. Dot colour = who bought it.</p>`;
}

function bars(rows, label, value, fmt = money, onclick) {
  const max = Math.max(...rows.map(value), 1);
  return `<div class="bars">${rows.map((r, i) => `<div class="b ${onclick ? "click" : ""}" data-i="${i}">
    <span class="lbl" title="${esc(label(r))}">${esc(label(r))}</span>
    <div><div class="t" style="width:${Math.max(100 * value(r) / max, 1)}%"></div></div>
    <span class="num">${fmt(value(r))}</span></div>`).join("")}</div>`;
}

// ---------------------------------------------------------------- pages
const pages = {};

// ---------------------------------------------------------------- deltas & tooltips
const pctChange = (cur, prev) => prev ? 100 * (cur - prev) / Math.abs(prev) : null;
// direction arrow + text so meaning never rides on colour alone; upGood decides the colour
function delta(cur, prev, label, upGood = false, fmt = null) {
  const pct = pctChange(cur, prev);
  if (pct == null || !isFinite(pct)) return `<span class="delta flat">no data ${esc(label)}</span>`;
  if (Math.abs(pct) < 0.5) return `<span class="delta flat">● same as ${esc(label)}</span>`;
  const up = pct > 0, good = up === upGood;
  return `<span class="delta ${good ? "good" : "bad"}">${up ? "▲" : "▼"} ${Math.abs(pct).toFixed(1)}%</span> <span class="muted">vs ${fmt ? fmt(prev) : money(prev)} ${esc(label)}</span>`;
}
const $tip = Object.assign(document.createElement("div"), { className: "tip hidden" });
document.body.appendChild($tip);
document.addEventListener("mousemove", e => {
  const t = e.target.closest && e.target.closest("[data-tip]");
  if (!t) { $tip.classList.add("hidden"); return; }
  $tip.innerHTML = t.dataset.tip;
  $tip.classList.remove("hidden");
  const w = $tip.offsetWidth;
  $tip.style.left = Math.min(e.clientX + 14, innerWidth - w - 8) + "px";
  $tip.style.top = (e.clientY + 16) + "px";
});

function yoyChart(monthly, year, todayMonth) {
  const max = Math.max(...monthly.map(m => Math.max(m.this_year, m.last_year)), 1);
  const nice = Math.pow(10, Math.floor(Math.log10(max)));
  const top = Math.ceil(max / nice) * nice;
  const W = 760, H = 230, pad = 52, gw = (W - pad) / 12, bw = Math.min(16, gw / 2 - 3);
  const Y = v => H - (H - 12) * Math.max(v, 0) / top;
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let out = `<svg viewBox="0 0 ${W} ${H + 26}" width="100%" role="img" aria-label="Spending per month, ${year} vs ${year - 1}">`;
  for (let i = 0; i <= 4; i++) {
    const v = top * i / 4;
    out += `<line class="gridline" x1="${pad}" x2="${W}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${pad - 6}" y="${Y(v) + 4}" text-anchor="end">${v >= 1000 ? "$" + (v / 1000).toFixed(v % 1000 ? 1 : 0) + "K" : "$" + Math.round(v)}</text>`;
  }
  const bar = (x, v, cls) => v > 0 ? `<path class="${cls}" d="M${x},${H} V${Y(v) + 4} q0,-4 4,-4 h${bw - 8} q4,0 4,4 V${H} Z"/>` : "";
  monthly.forEach((m, i) => {
    const x0 = pad + i * gw + gw / 2 - bw - 1;
    const future = i + 1 > todayMonth;
    const tip = `<b>${names[i]}</b><br>${year}: ${future ? "–" : money(m.this_year) + ` · ${m.this_trips} trips`}<br>${year - 1}: ${money(m.last_year)} · ${m.last_trips} trips`;
    out += `<g data-tip="${esc(tip)}"><rect x="${pad + i * gw}" y="0" width="${gw}" height="${H}" fill="transparent"/>${bar(x0, m.last_year, "s-last")}${bar(x0 + bw + 2, m.this_year, "s-this")}</g>`;
    out += `<text x="${pad + i * gw + gw / 2}" y="${H + 18}" text-anchor="middle" ${i + 1 === todayMonth ? 'class="strong"' : ""}>${names[i]}</text>`;
  });
  return out + `</svg>`;
}

function meter(ytd, lastYtd, lastFull, projection, year) {
  const top = Math.max(lastFull, projection, ytd, 1);
  const p = v => Math.min(100, 100 * v / top);
  return `<div class="meter" aria-hidden="true">
      <div class="fill" style="width:${p(ytd)}%"></div>
      <div class="pace" style="left:${p(projection)}%" data-tip="On pace for ${money(projection)} in ${year}"></div>
      <div class="mark" style="left:${p(lastYtd)}%" data-tip="${year - 1} by this date: ${money(lastYtd)}"></div>
      <div class="end" style="left:${p(lastFull)}%" data-tip="Full ${year - 1}: ${money(lastFull)}"></div>
    </div>
    <div class="row small muted meter-legend"><span><i class="k fill"></i>${year} so far</span><span><i class="k mark"></i>${year - 1} by today</span><span><i class="k end"></i>full ${year - 1}</span><span><i class="k pace"></i>pace</span></div>`;
}


function overviewSection(h) {
  const a = h.all_time, maxYear = Math.max(...h.years.map(r => r.spent), 1);
  const tile = (label, value, sub) => `<div class="stat"><div class="l">${label}</div><div class="v">${value}</div><div class="small muted">${sub || ""}</div></div>`;
  const yearRows = h.years.slice().reverse().map((r, i, arr) => {
    const prev = arr[i + 1];
    const note = r.year === String(h.year) ? `so far (to ${fmtDate(h.today)})` : r.partial ? `from ${fmtDate(r.first)}` : "";
    const change = prev && !r.partial && !prev.partial ? delta(r.spent, prev.spent, prev.year) : `<span class="muted small">${note}</span>`;
    return `<tr><td><b>${r.year}</b></td>
      <td><div class="row" style="flex-wrap:nowrap;gap:8px"><div style="width:90px"><div class="t s-this" style="height:10px;border-radius:0 4px 4px 0;width:${Math.max(100 * r.spent / maxYear, 2)}%"></div></div><span class="num">${money(r.spent)}</span></div></td>
      <td class="num">${r.trips}</td><td class="num hide-sm">${money(r.avg_trip)}</td><td class="num hide-sm">${money(r.saved)}</td><td class="small">${change}</td></tr>`;
  }).join("");
  const maxCat = Math.max(...h.all_time_categories.map(c => c.spent), 1);
  return `
    <h2 class="section">All-time overview <span class="muted small">since ${fmtDate(h.first_date)}</span></h2>
    <div class="stats">
      ${tile("Total spent", money(a.spent), `${a.receipts} receipts`)}
      ${tile("Trips to Costco", a.trips, `about every ${Math.max(1, Math.round((new Date(h.today) - new Date(h.first_date)) / 864e5 / Math.max(a.trips, 1)))} days`)}
      ${tile("Average per trip", money(a.avg_trip), "")}
      ${tile("Average per month", money(a.per_month), "")}
      ${tile("Instant savings", money(a.saved), `${(100 * a.saved / Math.max(a.spent + a.saved, 1)).toFixed(1)}% off`)}
    </div>
    <div class="grid g2">
      <div class="card"><h2>By year</h2><div class="scroll"><table>
        <tr><th>Year</th><th>Spent</th><th class="num">Trips</th><th class="num hide-sm">Per trip</th><th class="num hide-sm">Saved</th><th>Change</th></tr>${yearRows}</table></div></div>
      <div class="card"><div class="row end"><h2>Spending per month</h2><div class="small">${state.people.filter(p => h.all_time_monthly.some(m => m.person_id === p.id)).map(p => who(p.id)).join(" &nbsp; ")}</div></div>
        ${monthBars(h.all_time_monthly)}</div>
    </div>
    <div class="grid g2">
      ${listCard("Where the money goes (all time)", `<div class="bars">${h.all_time_categories.map(c => `<div class="b click" data-cat="${esc(c.category)}">
          <span class="lbl">${esc(c.category)}</span><div><div class="t s-this" style="width:${Math.max(100 * c.spent / maxCat, 0.5)}%"></div></div><span class="num">${money(c.spent)}</span></div>`).join("")}</div>`)}
      ${listCard("Most bought (all time)", `<table><tr><th>Product</th><th class="num">Times</th><th class="num">Spent</th><th class="hide-sm">Last</th></tr>${h.all_time_top.map(p => `<tr class="click" data-pid="${p.id}"><td>${pname(p)}</td><td class="num">${p.times}</td><td class="num">${money(p.spent)}</td><td class="hide-sm">${fmtDate(p.last)}</td></tr>`).join("")}</table>`)}
    </div>
    <h2 class="section">This year in detail</h2>`;
}

const listCard = (title, body) => `<div class="card"><h2>${title}</h2>${body}</div>`;

pages["/"] = async () => {
  const h = await api(withPerson("/api/home"));
  const P = h.periods, y = h.year;
  const todayLabel = new Date(h.today + "T12:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const monthName = new Date(h.today + "T12:00").toLocaleDateString(undefined, { month: "long" });
  if (!h.last_date) {
    $app.innerHTML = `<div class="card"><h1>Welcome</h1><p>No receipts yet. <a href="#/import">Import your Costco receipts</a> to get started.</p></div>`;
    return;
  }
  const tile = (label, value, sub) => `<div class="stat"><div class="l">${label}</div><div class="v">${value}</div><div class="small">${sub || ""}</div></div>`;
  const maxCat = Math.max(...h.categories.map(c => Math.max(c.spent, c.last_spent)), 1);
  const maxPerson = Math.max(...h.people.map(p => Math.max(p.spent, p.last_spent)), 1);
  $app.innerHTML = `
    <section class="card hero">
      <div class="l">Spent in ${y} so far</div>
      <div class="hero-v">${money(P.ytd.spent)}</div>
      <div class="hero-d">${delta(P.ytd.spent, P.last_ytd.spent, `by ${todayLabel}, ${y - 1}`)}</div>
      <div class="small muted">Full ${y - 1}: <b>${money(P.last_year.spent)}</b> · on pace for <b>${money(h.projection)}</b> in ${y}
        (${h.projection >= P.last_year.spent ? "+" : "−"}${money(Math.abs(h.projection - P.last_year.spent))})</div>
      ${meter(P.ytd.spent, P.last_ytd.spent, P.last_year.spent, h.projection, y)}
    </section>
    <div class="stats">
      ${tile(`${monthName} so far`, money(P.mtd.spent), delta(P.mtd.spent, P.last_year_mtd.spent, `${monthName} ${y - 1}`))}
      ${tile(`Trips in ${y}`, P.ytd.trips, delta(P.ytd.trips, P.last_ytd.trips, `${y - 1}`, true, v => v))}
      ${tile("Average per trip", money(P.ytd.avg_trip), delta(P.ytd.avg_trip, P.last_ytd.avg_trip, `${y - 1}`))}
      ${tile(`Instant savings ${y}`, money(P.ytd.saved), delta(P.ytd.saved, P.last_ytd.saved, `${y - 1}`, true))}
      ${tile("Last visit", h.days_since_visit === 0 ? "Today" : `${h.days_since_visit} days ago`, `<span class="muted">${fmtDate(h.last_date)}${P.ytd.trips ? ` · usually every ${Math.round(h.day_of_year / P.ytd.trips)} days` : ""}</span>`)}
      ${tile(`Gas in ${y}`, h.gas.gallons ? `${h.gas.gallons} gal` : "–", h.gas.gallons ? `<span class="muted">${money(h.gas.spent)} · ${h.gas.fills} fills · avg $${h.gas.avg_price.toFixed(3)}/gal</span>` : "")}
    </div>
    <div class="card">
      <div class="row end"><h2>Month by month: ${y} vs ${y - 1}</h2>
        <div class="row small"><span><i class="k s-last"></i>${y - 1}</span><span><i class="k s-this"></i>${y}</span></div></div>
      ${yoyChart(h.monthly, y, +h.today.slice(5, 7))}
    </div>
    ${overviewSection(h)}
    <div class="grid g2">
      ${listCard("🛒 Probably running low", h.restock.length ? `<table>${h.restock.map(r => `<tr class="click" data-pid="${r.product_id}">
          <td>${pname(r)}<div class="small muted">usually every ${r.every_days} days · bought ${r.times}×</div></td>
          <td class="num">${r.days_since} d ago<div class="small ${r.overdue_days > 0 ? "st-open" : "muted"}">${r.overdue_days > 0 ? `${r.overdue_days} d overdue` : "due now"}</div></td>
          <td class="num hide-sm">${money(r.last_price)}</td></tr>`).join("")}</table>` : `<p class="muted">Nothing due — or not enough history yet.</p>`)}
      ${listCard("💲 Recent price changes", h.price_changes.length ? `<table>${h.price_changes.map(c => `<tr class="click" data-pid="${c.product_id}">
          <td>${pname(c)}<div class="small muted">${fmtDate(c.old_date)} → ${fmtDate(c.date)}</div></td>
          <td class="num">${money(c.old)} → <b>${money(c.new)}</b></td>
          <td class="num"><span class="delta ${c.pct > 0 ? "bad" : "good"}">${c.pct > 0 ? "▲" : "▼"} ${Math.abs(c.pct)}%</span></td></tr>`).join("")}</table>` : `<p class="muted">No shelf-price changes in the last 120 days.</p>`)}
    </div>
    <div class="grid g2">
      ${listCard(`Categories in ${y} <span class="muted small">vs same period ${y - 1}</span>`, `<div class="bars">${h.categories.filter(c => c.spent > 0).slice(0, 12).map(c => `
          <div class="b click" data-cat="${esc(c.category)}" data-tip="<b>${esc(c.category)}</b><br>${y}: ${money(c.spent)}<br>${y - 1} by ${todayLabel}: ${money(c.last_spent)}">
            <span class="lbl">${esc(c.category)}</span>
            <div class="pair"><div class="t s-this" style="width:${Math.max(100 * c.spent / maxCat, 0.5)}%"></div><div class="t s-last thin" style="width:${Math.max(100 * c.last_spent / maxCat, 0.5)}%"></div></div>
            <span class="num">${money(c.spent)}<div class="small">${c.last_spent ? `<span class="delta ${c.spent > c.last_spent ? "bad" : "good"}">${c.spent > c.last_spent ? "▲" : "▼"} ${Math.abs(pctChange(c.spent, c.last_spent)).toFixed(0)}%</span>` : `<span class="muted">new</span>`}</div></span>
          </div>`).join("")}</div>`)}
      <div>
        ${listCard(`Who spends in ${y}`, h.people.map(p => `<div class="bars"><div class="b">
            <span class="lbl">${who(p.person_id)}</span>
            <div class="pair"><div class="t" style="background:${person(p.person_id).color};width:${Math.max(100 * p.spent / maxPerson, 0.5)}%"></div><div class="t s-last thin" style="width:${Math.max(100 * p.last_spent / maxPerson, 0.5)}%"></div></div>
            <span class="num">${money(p.spent)}<div class="small muted">${p.trips} trips</div></span></div></div>`).join("") + `<p class="small muted">Thin grey bar = same period ${y - 1}.</p>`)}
        ${listCard("↩️ Return windows open", h.returns_open.length ? `<table>${h.returns_open.map(r => `<tr class="click" data-pid="${r.product_id}">
            <td>${pname(r)}<div class="small muted">${fmtDate(r.purchase_date)} · ${money(r.paid)}</div></td><td class="num">${ret(r.return)}</td></tr>`).join("")}</table>
            ${h.returns_open_count > 5 ? `<a href="#/returns">All ${h.returns_open_count} →</a>` : ""}` : `<p class="muted">No 90-day return windows open. Most other items can be returned any time — <a href="#/returns">see Returns</a>.</p>`)}
      </div>
    </div>
    <div class="grid g2">
      ${listCard(`Most bought in ${y}`, `<table><tr><th>Product</th><th class="num">Trips</th><th class="num">Spent</th></tr>${h.top_this_year.map(t => `<tr class="click" data-pid="${t.product_id}"><td>${pname(t)}</td><td class="num">${t.trips}</td><td class="num">${money(t.spent)}</td></tr>`).join("")}</table>`)}
      ${listCard(`Biggest items in ${y}`, `<table>${h.biggest_this_year.map(b => `<tr class="click" data-pid="${b.product_id}"><td>${pname(b)}<div class="small muted">${fmtDate(b.purchase_date)} · ${who(b.person_id)}</div></td><td class="num">${money(b.paid)}</td></tr>`).join("")}</table>`)}
    </div>
    ${listCard("Recent receipts", `<table>${h.recent_receipts.map(r => `<tr class="click" data-rid="${r.id}"><td>${fmtDate(r.purchase_date)}</td><td>${who(r.person_id)}</td>
        <td class="hide-sm">${esc(r.warehouse || "")}${r.kind !== "warehouse" ? ` <span class="chip">${esc(r.kind)}</span>` : ""}${r.is_refund ? ` <span class="chip">refund</span>` : ""}</td>
        <td class="num">${r.lines} items</td><td class="num"><b>${money(r.total)}</b></td></tr>`).join("")}</table>
        <p class="small muted">Data from ${fmtDate(h.first_date)} to ${fmtDate(h.last_date)}.</p>`)}`;
  bindProductLinks($app);
  $app.querySelectorAll("tr[data-rid]").forEach(tr => tr.onclick = () => go("/receipt/" + tr.dataset.rid));
  $app.querySelectorAll("[data-cat]").forEach(b => b.onclick = () => go("/products", { cat: b.dataset.cat }));
};

pages["/calendar"] = async ({ params }) => {
  const now = new Date();
  const month = params.get("m") || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const product = params.get("product");
  const [y, m] = month.split("-").map(Number);
  const [days, yearDays, prod] = await Promise.all([
    api(withPerson("/api/calendar", { month, product })),
    api(withPerson("/api/calendar", { month: String(y), product })),
    product ? api(withPerson("/api/products/" + product)) : null,
  ]);
  const shift = k => { const d = new Date(y, m - 1 + k, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; };
  const byDate = {};
  for (const d of days) (byDate[d.date] = byDate[d.date] || []).push(d);
  const first = new Date(y, m - 1, 1), start = new Date(first);
  start.setDate(1 - first.getDay());
  const todayIso = new Date().toLocaleDateString("en-CA");
  let cells = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(d => `<div class="h">${d}</div>`).join("");
  for (let i = 0; i < 42; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const iso = d.toLocaleDateString("en-CA");
    const ev = byDate[iso] || [];
    cells += `<div class="d ${d.getMonth() !== m - 1 ? "out" : ""} ${ev.length ? "has" : ""} ${iso === todayIso ? "today" : ""}" data-date="${iso}">
      <span class="n">${d.getDate()}</span>
      ${ev.map(e => `<div class="e" style="background:${person(e.person_id).color}" title="${esc(person(e.person_id).name)}">${money(e.total)}${product ? ` · ${e.items}×` : ""}</div>`).join("")}</div>`;
  }
  // year strip: one square per week, darker = more visits
  const weeks = new Array(53).fill(0);
  const jan1 = new Date(y, 0, 1);
  for (const d of yearDays) weeks[Math.min(52, Math.floor((new Date(d.date + "T12:00") - jan1) / 6048e5))] += d.receipts;
  const wmax = Math.max(...weeks, 1);
  const monthTotal = days.reduce((s, d) => s + d.total, 0);
  const yearTotal = yearDays.reduce((s, d) => s + d.total, 0);
  const visits = new Set(days.map(d => d.date)).size, yVisits = new Set(yearDays.map(d => d.date)).size;
  $app.innerHTML = `
    <div class="row end"><h1>${new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" })}</h1>
      <div class="row"><button class="ghost" data-m="${shift(-12)}">« Year</button><button class="ghost" data-m="${shift(-1)}">‹ Prev</button>
      <button class="ghost" data-m="">Today</button><button class="ghost" data-m="${shift(1)}">Next ›</button><button class="ghost" data-m="${shift(12)}">Year »</button></div></div>
    ${prod ? `<div class="card row end"><div>Only days you bought <a href="#/product/${prod.id}"><b>${pname(prod)}</b></a> · bought ${prod.times}× in total${prod.avg_days_between ? `, about every ${prod.avg_days_between} days` : ""}</div>
      <button class="ghost" id="clear">Show all purchases</button></div>` : ""}
    <div class="stats">
      <div class="stat"><div class="v">${visits}</div><div class="l">${prod ? "Days bought" : "Visits"} this month</div></div>
      <div class="stat"><div class="v">${money(monthTotal)}</div><div class="l">This month</div></div>
      <div class="stat"><div class="v">${yVisits}</div><div class="l">${prod ? "Days bought" : "Visits"} in ${y}</div></div>
      <div class="stat"><div class="v">${money(yearTotal)}</div><div class="l">Spent in ${y}</div></div>
    </div>
    <div class="card"><div class="small muted" style="margin-bottom:6px">${y} by week</div>
      <div class="year">${weeks.map((w, i) => `<div title="Week ${i + 1}: ${w} receipt(s)" style="${w ? `background:var(--accent2);opacity:${0.25 + 0.75 * w / wmax}` : ""}"></div>`).join("")}</div></div>
    <div class="cal">${cells}</div>
    <div id="day"></div>
    <div id="period"></div>`;
  $app.querySelectorAll("button[data-m]").forEach(b => b.onclick = () => go("/calendar", { m: b.dataset.m, product }));
  const clear = document.getElementById("clear");
  if (clear) clear.onclick = () => go("/calendar", { m: month });
  $app.querySelectorAll(".d.has").forEach(c => c.onclick = () => {
    $app.querySelectorAll(".d.sel").forEach(x => x.classList.remove("sel"));
    c.classList.add("sel");
    showDay(c.dataset.date);
  });
  renderPeriods(y, m);
};

async function showDay(date) {
  const $day = document.getElementById("day");
  $day.innerHTML = `<div class="card"><span class="spinner"></span></div>`;
  const list = await api(withPerson("/api/receipts", { start: date, end: date }));
  const full = await Promise.all(list.map(r => api("/api/receipts/" + r.id)));
  $day.innerHTML = `<h2 style="margin-top:20px">${fmtDate(date)}</h2>` + full.map(receiptCard).join("");
  bindProductLinks($day);
  $day.scrollIntoView({ behavior: "smooth" });
}

function receiptCard(r) {
  return `<div class="card">
    <div class="row end"><div>${who(r.person_id)} · <b>${esc(r.warehouse || "Costco")}</b> · ${esc(r.purchased_at.replace("T", " "))}
      ${r.kind !== "warehouse" ? `<span class="chip">${esc(r.kind)}</span>` : ""}${r.is_refund ? ` <span class="chip st-expired">refund</span>` : ""}</div>
      <div><b>${money(r.total)}</b> <span class="muted small">tax ${money(r.taxes)}${r.instant_savings ? ` · saved ${money(r.instant_savings)}` : ""}</span></div></div>
    <div class="scroll"><table>
      <tr><th>Item</th><th class="hide-sm">Category</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Savings</th><th class="num">Paid</th><th class="hide-sm">Return</th></tr>
      ${r.items.map(i => `<tr class="click" data-pid="${i.product_id}"><td>${pname(i)}<div class="small muted">${esc(i.item_number)} · ${esc(i.receipt_name)}</div></td>
        <td class="hide-sm">${esc(i.category || "")}</td><td class="num">${i.quantity}</td><td class="num">${money(i.unit_price)}</td>
        <td class="num">${i.discount ? money(i.discount) : ""}</td><td class="num">${money(i.amount + i.discount)}</td><td class="hide-sm small">${ret(i.return)}</td></tr>`).join("")}
    </table></div>
    <div class="small muted">Receipt ${esc(r.barcode)} · <a href="#/receipt/${r.id}">open</a></div></div>`;
}
function bindProductLinks(el) {
  el.querySelectorAll("tr[data-pid]").forEach(tr => tr.onclick = () => go("/product/" + tr.dataset.pid));
}

pages["/products"] = async ({ params }) => {
  const q = params.get("q") || "", cat = params.get("cat") || "", sort = params.get("sort") || "last";
  const cats = state.meta.categories;
  $app.innerHTML = `
    <h1>Products</h1>
    <div class="card row">
      <input id="q" type="search" placeholder="Search: eggs, kirkland water, 1234567…" value="${esc(q)}" style="flex:1;min-width:200px" autofocus>
      <select id="cat"><option value="">All categories</option>${["Not categorised", ...cats].map(c => `<option ${c === cat ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <select id="sort">${[["last", "Last bought"], ["times", "Most times"], ["spent", "Most spent"], ["name", "Name"]].map(([v, l]) => `<option value="${v}" ${v === sort ? "selected" : ""}>${l}</option>`).join("")}</select>
    </div>
    <div class="card scroll" id="list"><span class="spinner"></span></div>`;
  const load = async () => {
    const rows = await api(withPerson("/api/products", { q: $("q").value, category: $("cat").value, sort: $("sort").value }));
    document.getElementById("list").innerHTML = `<div class="small muted" style="margin-bottom:6px">${rows.length} products${$("q").value ? " (fuzzy match)" : ""}</div>
      <table><tr><th>Product</th><th class="hide-sm">Category</th><th class="num">Times</th><th class="num">Last price</th><th class="num hide-sm">Min – max</th><th class="num hide-sm">Spent</th><th>Last bought</th><th class="hide-sm">Return</th></tr>
      ${rows.map(p => `<tr class="click" data-pid="${p.id}"><td>${pname(p)}<div class="small muted">${esc(p.item_number)} · ${esc(p.receipt_name)} ${p.buyers.map(b => `<span class="dot" title="${esc(person(b).name)}" style="background:${person(b).color}"></span>`).join("")}</div></td>
        <td class="hide-sm">${esc(p.category || "–")}</td><td class="num">${p.times}${p.returns ? ` <span class="small st-expired" title="returned">↩${p.returns}</span>` : ""}</td>
        <td class="num">${money(p.last_price)}</td><td class="num hide-sm">${p.min_price === p.max_price ? money(p.min_price) : `${money(p.min_price)} – ${money(p.max_price)}`}</td>
        <td class="num hide-sm">${money(p.spent)}</td><td>${fmtDate(p.last)}</td><td class="hide-sm small">${ret(p.return)}</td></tr>`).join("")}</table>`;
    bindProductLinks(document.getElementById("list"));
  };
  const $ = id => document.getElementById(id);
  let timer;
  $("q").oninput = () => { clearTimeout(timer); timer = setTimeout(() => { history.replaceState(null, "", `#/products?q=${encodeURIComponent($("q").value)}&cat=${encodeURIComponent($("cat").value)}&sort=${$("sort").value}`); load(); }, 250); };
  $("cat").onchange = $("sort").onchange = () => go("/products", { q: $("q").value, cat: $("cat").value, sort: $("sort").value });
  await load();
};

pages["/product"] = async ({ id }) => {
  const p = await api(withPerson("/api/products/" + id));
  const s = p.stats || {}, d = p.receipt_details || {};
  const bought = p.purchases.filter(x => x.quantity > 0);
  const last = bought[bought.length - 1];
  const tile = (label, value, sub) => `<div class="stat"><div class="l">${label}</div><div class="v">${value}</div><div class="small muted">${sub || ""}</div></div>`;
  const qty = v => (Math.round(v * 100) / 100).toLocaleString();
  const maxYear = Math.max(...(s.by_year || []).map(r => r.spent), 1);
  const due = s.due_in_days == null ? "" : s.due_in_days < 0 ? `<span class="st-open">${-s.due_in_days} days overdue</span>` : s.due_in_days === 0 ? `<span class="st-open">due today</span>` : `in ${s.due_in_days} days`;
  $app.innerHTML = `
    <div class="small"><a href="#/products">← Products</a></div>
    <h1>${pname(p)}</h1>
    <div class="row small muted" style="margin:-10px 0 14px">
      <span>Item #<b>${esc(p.item_number)}</b></span>
      <span>· on receipt <code>${esc(p.receipt_name)}</code>${d.description2 ? ` <code>${esc(d.description2)}</code>` : ""}</span>
      ${p.brand ? `<span>· ${esc(p.brand)}</span>` : ""}
      <span class="chip">${esc(p.category || "Not categorised")}</span>
      ${d.department != null ? `<span class="chip" title="Costco department number">Dept ${esc(d.department)}</span>` : ""}
      ${d.taxable != null ? `<span class="chip">${d.taxable ? "Taxable" : "Not taxed"}</span>` : ""}
      ${p.ai_status === "manual" ? `<span class="chip">edited</span>` : p.ai_status === "done" ? `<span class="chip">AI</span>` : `<span class="chip">${esc(p.ai_status)}</span>`}
      ${/^\d+$/.test(p.item_number) ? `<a href="https://www.costco.com/CatalogSearch?dept=All&keyword=${encodeURIComponent(p.item_number)}" target="_blank" rel="noopener">Find on costco.com ↗</a>` : ""}
    </div>
    ${bought.length ? `
    <div class="stats">
      ${tile("Times bought", `${p.times}×`, `${qty(s.total_quantity)} units · ${s.avg_quantity_per_trip} per trip`)}
      ${tile("Last price", money(s.last_price), s.price_change_pct != null ? (Math.abs(s.price_change_pct) < 0.5 ? `same as first purchase` : `<span class="delta ${s.price_change_pct > 0 ? "bad" : "good"}">${s.price_change_pct > 0 ? "▲" : "▼"} ${Math.abs(s.price_change_pct)}%</span> since first (${money(s.first_price)})`) : fmtDate(s.last_date))}
      ${tile("Price range", s.min_price === s.max_price ? money(s.min_price) : `<span class="v-sm">${money(s.min_price)}–${money(s.max_price)}</span>`, `average ${money(s.avg_price)}`)}
      ${tile("Best price paid", money(s.best_paid_unit), `${fmtDate(s.best_paid_date)}${s.best_paid_unit < s.min_price ? " · with instant savings" : ""}`)}
      ${tile("Total spent", money(s.total_spent), s.total_saved > 0 ? `saved ${money(s.total_saved)} · on sale ${s.times_on_sale}×` : "never on sale")}
      ${tile("Buy rhythm", p.avg_days_between ? `every ${p.avg_days_between} d` : "–", s.next_expected ? `next ~${fmtDate(s.next_expected)} · ${due}` : `bought ${s.days_since_last} days ago`)}
      ${tile("First bought", `<span class="v-sm">${fmtDate(s.first_date)}</span>`, `last ${fmtDate(s.last_date)} · ${s.days_since_last} days ago`)}
      ${s.returns ? tile("Returned", `${s.returns}×`, "refund lines") : ""}
    </div>` : ""}
    <div class="grid g2">
      <div class="card"><h2>Can I return it?</h2>
        ${last ? `<p style="font-size:16px;margin:0 0 6px">${ret(last.return)} <span class="muted small">(last purchase, ${fmtDate(last.purchase_date)})</span></p>` : ""}
        <p class="small">${esc(p.return_note || "")}</p><p class="small muted">${esc(p.policy || "Not categorised yet.")}</p></div>
      <div class="card"><h2>By year</h2>
        <table><tr><th>Year</th><th class="num">Trips</th><th class="num">Units</th><th>Spent</th></tr>
        ${(s.by_year || []).slice().reverse().map(r => `<tr><td>${r.year}</td><td class="num">${r.times}</td><td class="num">${qty(r.quantity)}</td>
          <td><div class="row" style="flex-wrap:nowrap"><div class="t s-this" style="height:10px;border-radius:0 4px 4px 0;width:${Math.max(70 * r.spent / maxYear, 1)}%"></div><span class="num small">${money(r.spent)}</span></div></td></tr>`).join("")}</table>
        ${(s.by_person || []).length > 1 || state.people.length > 1 ? `<div class="small" style="margin-top:10px">${(s.by_person || []).map(b => `${who(b.person_id)} ${b.times}× · ${money(b.spent)}`).join(" &nbsp; ")}</div>` : ""}
        ${(s.warehouses || []).length ? `<div class="small muted" style="margin-top:6px">Bought at ${s.warehouses.map(([w, n]) => `${esc(w)} (${n})`).join(", ")}</div>` : ""}
      </div>
    </div>
    ${bought.length > 1 ? `<div class="card"><h2>Price history</h2>${priceChart(p.purchases)}</div>` : ""}
    <div class="card scroll"><div class="row end"><h2>Purchases</h2><a href="#/calendar?m=${(last || p.purchases[0] || { purchase_date: "" }).purchase_date.slice(0, 7)}&product=${p.id}">📅 See on calendar</a></div>
      <table><tr><th>Date</th><th>Who</th><th class="hide-sm">Warehouse</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Savings</th><th class="num">Paid</th><th class="num hide-sm">Gap</th><th>Return</th></tr>
      ${p.purchases.map((x, i, arr) => {
        const prev = arr.slice(0, i).reverse().find(q => q.quantity > 0 && q.purchase_date < x.purchase_date);
        const gap = prev && x.quantity > 0 ? Math.round((new Date(x.purchase_date) - new Date(prev.purchase_date)) / 864e5) : null;
        return { x, gap };
      }).reverse().map(({ x, gap }) => `<tr class="click" data-rid="${x.receipt_id}"><td>${fmtDate(x.purchase_date)}</td><td>${who(x.person_id)}</td><td class="hide-sm">${esc(x.warehouse || "")}</td>
        <td class="num">${x.quantity}</td><td class="num">${money(x.unit_price)}</td><td class="num">${x.discount ? money(x.discount) : ""}</td><td class="num">${money(x.paid)}</td>
        <td class="num hide-sm muted">${gap != null ? gap + " d" : ""}</td>
        <td class="small">${x.quantity < 0 ? `<span class="st-returned">Refund</span>` : ret(x.return)}</td></tr>`).join("")}</table></div>
    ${p.siblings.length ? `<div class="card"><h2>Same product, other item numbers</h2>${p.siblings.map(s => `<a href="#/product/${s.id}">${esc(s.name || s.receipt_name)} (#${esc(s.item_number)})</a>`).join("<br>")}</div>` : ""}
    <details class="card"><summary>Edit name / category / return rule</summary>
      <div class="row" style="margin-top:10px">
        <input id="e-name" value="${esc(p.name || "")}" placeholder="Name" style="flex:1;min-width:200px">
        <select id="e-cat">${state.meta.categories.map(c => `<option ${c === p.category ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <select id="e-ret">${Object.keys(state.meta.return_policy).map(c => `<option ${c === p.return_class ? "selected" : ""}>${c}</option>`).join("")}</select>
        <button id="e-save">Save</button></div></details>`;
  $app.querySelectorAll("tr[data-rid]").forEach(tr => tr.onclick = () => go("/receipt/" + tr.dataset.rid));
  document.getElementById("e-save").onclick = async () => {
    await api("/api/products/" + p.id, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: document.getElementById("e-name").value, category: document.getElementById("e-cat").value, return_class: document.getElementById("e-ret").value }) });
    render();
  };
};


// ---------------------------------------------------------------- single-series bars with tooltips
function seriesBars(points, { label, value, tip, highlight, height = 180, labelEvery = 1 }) {
  if (!points.length) return "";
  const max = Math.max(...points.map(value), 0.01);
  const nice = Math.pow(10, Math.floor(Math.log10(max)));
  const top = Math.ceil(max / nice) * nice;
  const W = 760, H = height, pad = 52, bw = (W - pad) / points.length;
  const Y = v => H - (H - 12) * Math.max(v, 0) / top;
  const fmtAxis = v => v >= 1000 ? "$" + (v / 1000).toFixed(v % 1000 ? 1 : 0) + "K" : "$" + Math.round(v);
  let out = `<svg viewBox="0 0 ${W} ${H + 24}" width="100%" role="img">`;
  for (let i = 0; i <= 4; i++) {
    const v = top * i / 4;
    out += `<line class="gridline" x1="${pad}" x2="${W}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${pad - 6}" y="${Y(v) + 4}" text-anchor="end">${tip ? fmtAxis(v) : Math.round(v)}</text>`;
  }
  points.forEach((p, i) => {
    const v = Math.max(value(p), 0), w = Math.max(Math.min(bw - 3, 28), 2), x = pad + i * bw + (bw - w) / 2;
    const h = H - Y(v);
    const r = Math.min(4, h, w / 2);
    out += `<g data-tip="${esc(tip ? tip(p) : `${label(p)}: ${value(p)}`)}"><rect x="${pad + i * bw}" y="0" width="${bw}" height="${H}" fill="transparent"/>`;
    if (v > 0) out += `<path class="${highlight && highlight(p) ? "s-this" : "s-last"}" d="M${x},${H} V${Y(v) + r} q0,-${r} ${r},-${r} h${w - 2 * r} q${r},0 ${r},${r} V${H} Z"/>`;
    out += `</g>`;
    if (i % labelEvery === 0) out += `<text x="${pad + i * bw + bw / 2}" y="${H + 17}" text-anchor="middle">${esc(label(p))}</text>`;
  });
  return out + "</svg>";
}

const changeBadge = (cur, prev) => {
  if (!prev) return cur ? `<span class="muted small">new</span>` : "";
  const pct = pctChange(cur, prev);
  if (Math.abs(pct) < 0.5) return `<span class="muted small">● same</span>`;
  return `<span class="delta ${pct > 0 ? "bad" : "good"}">${pct > 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(0)}%</span>`;
};
const catLink = c => `#/category?c=${encodeURIComponent(c)}`;

// ---------------------------------------------------------------- categories
pages["/categories"] = async () => {
  const d = await api(withPerson("/api/categories"));
  const y = d.year, max = Math.max(...d.categories.map(c => c.spent), 1);
  const ytdTotal = d.categories.reduce((s, c) => s + c.ytd, 0), lastYtdTotal = d.categories.reduce((s, c) => s + c.last_ytd, 0);
  const growing = d.categories.filter(c => c.last_ytd > 20 && c.ytd > 0).map(c => ({ ...c, pct: pctChange(c.ytd, c.last_ytd) })).sort((a, b) => b.pct - a.pct);
  const years = d.years;
  const heat = v => v > 0 ? `background:color-mix(in srgb, var(--series-this) ${Math.round(8 + 60 * Math.sqrt(v / max))}%, transparent)` : "";
  $app.innerHTML = `
    <h1>Categories</h1>
    <div class="stats">
      <div class="stat"><div class="l">Categories</div><div class="v">${d.categories.length}</div><div class="small muted">item totals, before tax</div></div>
      <div class="stat"><div class="l">All time</div><div class="v">${money(d.total)}</div><div class="small muted">since ${esc(years[0] || "–")}</div></div>
      <div class="stat"><div class="l">${y} so far</div><div class="v">${money(ytdTotal)}</div><div class="small">${delta(ytdTotal, lastYtdTotal, `same period ${y - 1}`)}</div></div>
      <div class="stat"><div class="l">Biggest category</div><div class="v v-sm">${esc(d.categories[0] ? d.categories[0].category : "–")}</div><div class="small muted">${d.categories[0] ? `${d.categories[0].share}% of spending` : ""}</div></div>
      ${growing.length ? `<div class="stat"><div class="l">Growing fastest in ${y}</div><div class="v v-sm">${esc(growing[0].category)}</div><div class="small">${changeBadge(growing[0].ytd, growing[0].last_ytd)} <span class="muted">vs ${y - 1}</span></div></div>
      <div class="stat"><div class="l">Shrinking most in ${y}</div><div class="v v-sm">${esc(growing[growing.length - 1].category)}</div><div class="small">${changeBadge(growing[growing.length - 1].ytd, growing[growing.length - 1].last_ytd)} <span class="muted">vs ${y - 1}</span></div></div>` : ""}
    </div>
    <div class="card scroll"><table>
      <tr><th>Category</th><th>All time</th><th class="num">Share</th><th class="num">${y} so far</th><th class="num">vs ${y - 1}</th><th class="num hide-sm">Products</th><th class="num hide-sm">Trips</th><th class="num hide-sm">Per trip</th><th class="hide-sm">Top product</th><th class="hide-sm">Last</th></tr>
      ${d.categories.map(c => `<tr class="click" data-cat="${esc(c.category)}">
        <td><b>${esc(c.category)}</b></td>
        <td><div class="row" style="flex-wrap:nowrap;gap:8px"><div style="width:110px"><div class="t s-this" style="height:10px;border-radius:0 4px 4px 0;width:${Math.max(100 * c.spent / max, 1)}%"></div></div><span class="num">${money(c.spent)}</span></div></td>
        <td class="num">${c.share}%</td><td class="num">${money(c.ytd)}</td><td class="num">${changeBadge(c.ytd, c.last_ytd)}</td>
        <td class="num hide-sm">${c.products}</td><td class="num hide-sm">${c.trips}</td><td class="num hide-sm">${money(c.per_trip)}</td>
        <td class="hide-sm small">${c.top_product ? `${esc(c.top_product.name)} <span class="muted">${money(c.top_product.spent)}</span>` : ""}</td>
        <td class="hide-sm small">${fmtDate(c.last)}</td></tr>`).join("")}
    </table><p class="small muted">"vs ${y - 1}" compares ${y} so far with the same dates in ${y - 1}. Spending is item totals after instant savings, before tax.</p></div>
    <div class="card scroll"><h2>Spending by category and year</h2><table class="heat">
      <tr><th>Category</th>${years.map(yr => `<th class="num">${yr}${yr === String(y) ? " (so far)" : ""}</th>`).join("")}</tr>
      ${d.categories.map(c => `<tr class="click" data-cat="${esc(c.category)}"><td>${esc(c.category)}</td>${years.map(yr => { const v = c.by_year[yr] || 0; return `<td class="num" style="${heat(v)}">${v ? money(v) : "–"}</td>`; }).join("")}</tr>`).join("")}
      <tr><td><b>Total</b></td>${years.map(yr => `<td class="num"><b>${money(d.year_totals[yr])}</b></td>`).join("")}</tr>
    </table></div>`;
  $app.querySelectorAll("[data-cat]").forEach(tr => tr.onclick = () => location.hash = catLink(tr.dataset.cat));
};

pages["/category"] = async ({ params }) => {
  const name = params.get("c") || "";
  const d = await api(withPerson("/api/category", { name }));
  const y = d.year, months = d.monthly.slice(-36);
  const tile = (label, value, sub) => `<div class="stat"><div class="l">${label}</div><div class="v">${value}</div><div class="small muted">${sub || ""}</div></div>`;
  const shortMonth = m => new Date(m + "-15").toLocaleDateString(undefined, { month: "short" }) + (m.endsWith("-01") ? " " + m.slice(2, 4) : "");
  $app.innerHTML = `
    <div class="small"><a href="#/categories">← Categories</a></div>
    <h1>${esc(name)}</h1>
    <div class="stats">
      ${tile("All time", money(d.spent), `${money(d.per_month)} per month`)}
      ${`<div class="stat"><div class="l">${y} so far</div><div class="v">${money(d.ytd)}</div><div class="small">${delta(d.ytd, d.last_ytd, `same period ${y - 1}`)}</div></div>`}
      ${tile("Trips with this category", d.trips, `${money(d.per_trip)} per trip`)}
      ${tile("Units bought", d.units, `${d.products.length} different products`)}
      ${tile("First / last", `<span class="v-sm">${fmtDate(d.last)}</span>`, `since ${fmtDate(d.first)}`)}
    </div>
    <div class="card"><h2>Per month</h2>
      ${seriesBars(months, { label: m => shortMonth(m.month), value: m => m.spent, tip: m => `<b>${m.month}</b><br>${money(m.spent)}`, highlight: m => m.month.startsWith(String(y)), labelEvery: months.length > 18 ? 3 : 1 })}
      <div class="row small"><span><i class="k s-this"></i>${y}</span><span><i class="k s-last"></i>earlier</span></div></div>
    <div class="grid g2">
      <div class="card"><h2>By year</h2><table><tr><th>Year</th><th class="num">Spent</th><th class="num">Trips</th><th class="num">Units</th><th class="num">Share of all spending</th></tr>
        ${d.by_year.slice().reverse().map(r => `<tr><td>${r.year}</td><td class="num">${money(r.spent)}</td><td class="num">${r.trips}</td><td class="num">${r.units}</td><td class="num">${r.share != null ? r.share + "%" : "–"}</td></tr>`).join("")}</table>
        ${d.people.length ? `<p class="small">${d.people.map(p => `${who(p.person_id)} ${money(p.spent)} · ${p.trips} trips`).join(" &nbsp; ")}</p>` : ""}</div>
      ${listCard("Price changes (last 12 months)", d.price_changes.length ? `<table>${d.price_changes.map(c => `<tr class="click" data-pid="${c.product_id}"><td>${pname(c)}<div class="small muted">${fmtDate(c.old_date)} → ${fmtDate(c.date)}</div></td><td class="num">${money(c.old)} → <b>${money(c.new)}</b></td><td class="num">${changeBadge(c.new, c.old)}</td></tr>`).join("")}</table>` : `<p class="muted">No price changes in the last 12 months.</p>`)}
    </div>
    <div class="card scroll"><h2>Products</h2><table>
      <tr><th>Product</th><th class="num">Times</th><th class="num">Spent</th><th class="num">Last price</th><th class="num hide-sm">Min – max</th><th>Last bought</th><th class="hide-sm">Return</th></tr>
      ${d.products.map(p => `<tr class="click" data-pid="${p.id}"><td>${pname(p)}<div class="small muted">${esc(p.item_number)} · ${esc(p.receipt_name)}</div></td>
        <td class="num">${p.times}</td><td class="num">${money(p.spent)}</td><td class="num">${money(p.last_price)}</td>
        <td class="num hide-sm">${p.min_price === p.max_price ? money(p.min_price) : `${money(p.min_price)} – ${money(p.max_price)}`}</td>
        <td>${fmtDate(p.last)}</td><td class="hide-sm small">${ret(p.return)}</td></tr>`).join("")}</table></div>`;
  bindProductLinks($app);
};

// ---------------------------------------------------------------- calendar: month / year / all-time panels
async function renderPeriods(y, m) {
  const $p = document.getElementById("period");
  if (!$p) return;
  $p.innerHTML = `<div class="card"><span class="spinner"></span></div>`;
  const pad = n => String(n).padStart(2, "0");
  const today = new Date(), tIso = today.toLocaleDateString("en-CA");
  const lastDay = new Date(y, m, 0).getDate();
  const isCurMonth = y === today.getFullYear() && m === today.getMonth() + 1;
  const mEnd = isCurMonth ? tIso : `${y}-${pad(m)}-${pad(lastDay)}`;
  const lyEnd = isCurMonth ? `${y - 1}-${pad(m)}-${pad(Math.min(today.getDate(), new Date(y - 1, m, 0).getDate()))}` : `${y - 1}-${pad(m)}-${pad(new Date(y - 1, m, 0).getDate())}`;
  const isCurYear = y === today.getFullYear();
  const yEnd = isCurYear ? tIso : `${y}-12-31`;
  const lyYearEnd = isCurYear ? `${y - 1}-${tIso.slice(5)}` : `${y - 1}-12-31`;
  const [month, year, all] = await Promise.all([
    api(withPerson("/api/period", { start: `${y}-${pad(m)}-01`, end: mEnd, vs_start: `${y - 1}-${pad(m)}-01`, vs_end: lyEnd })),
    api(withPerson("/api/period", { start: `${y}-01-01`, end: yEnd, vs_start: `${y - 1}-01-01`, vs_end: lyYearEnd.replace("-02-29", "-02-28") })),
    api(withPerson("/api/period", { start: "2000-01-01", end: tIso })),
  ]);
  const monthName = new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const vsMonth = new Date(y - 1, m - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" }) + (isCurMonth ? " (same days)" : "");
  $p.innerHTML =
    periodSection(monthName, month, vsMonth, "month") +
    periodSection(`${y}${isCurYear ? " so far" : ""}`, year, isCurYear ? `same period ${y - 1}` : String(y - 1), "year") +
    periodSection("All time", all, null, "all");
  bindProductLinks($p);
  $p.querySelectorAll("tr[data-rid]").forEach(tr => tr.onclick = () => go("/receipt/" + tr.dataset.rid));
  $p.querySelectorAll("[data-cat]").forEach(b => b.onclick = () => location.hash = catLink(b.dataset.cat));
}

function periodSection(title, d, vsLabel, kind) {
  const t = d.totals, v = d.vs;
  const tile = (label, value, sub) => `<div class="stat"><div class="l">${label}</div><div class="v">${value}</div><div class="small">${sub || ""}</div></div>`;
  const vsd = (cur, prev, upGood, fmt) => v ? delta(cur, prev, vsLabel, upGood, fmt) : "";
  if (!t.receipts) return `<h2 class="section">${esc(title)}</h2><div class="card muted">No purchases.</div>`;
  const maxCat = Math.max(...d.categories.map(c => c.spent), 1);
  const maxWd = Math.max(...d.weekdays.map(w => w.trips), 1);
  const range = kind === "all" ? "" : `${fmtDate(d.start)} – ${fmtDate(d.end)}`;
  return `
    <h2 class="section">${esc(title)} <span class="muted small">${range}</span></h2>
    <div class="stats">
      ${tile("Spent", money(t.spent), vsd(t.spent, v && v.spent))}
      ${tile("Trips", t.trips, vsd(t.trips, v && v.trips, true, x => x))}
      ${tile("Average per trip", money(t.avg_trip), vsd(t.avg_trip, v && v.avg_trip))}
      ${tile("Items", d.lines, `<span class="muted">${d.units} units · ${d.distinct_products} products</span>`)}
      ${tile("Instant savings", money(t.saved), vsd(t.saved, v && v.saved, true))}
      ${kind !== "all" ? tile("New products", d.new_count, `<span class="muted">first time bought</span>`) : ""}
      ${d.gas.fills ? tile("Gas", `${d.gas.gallons} gal`, `<span class="muted">${money(d.gas.spent)} · ${d.gas.fills} fills</span>`) : ""}
      ${d.refunds.count ? tile("Returns", d.refunds.count, `<span class="muted">${money(d.refunds.total)} refunded</span>`) : ""}
    </div>
    <div class="grid g2">
      ${listCard("Categories", `<div class="bars">${d.categories.slice(0, 10).map(c => `<div class="b click" data-cat="${esc(c.category)}" data-tip="<b>${esc(c.category)}</b><br>${money(c.spent)} · ${c.share}% of items${v ? `<br>${esc(vsLabel)}: ${money(c.vs_spent || 0)}` : ""}">
          <span class="lbl">${esc(c.category)}</span><div><div class="t s-this" style="width:${Math.max(100 * c.spent / maxCat, 0.5)}%"></div></div>
          <span class="num">${money(c.spent)}${v ? `<div class="small">${changeBadge(c.spent, c.vs_spent)}</div>` : `<div class="small muted">${c.share}%</div>`}</span></div>`).join("")}</div>`)}
      ${listCard("Most bought", `<table><tr><th>Product</th><th class="num">Trips</th><th class="num">Units</th><th class="num">Spent</th></tr>${d.top.by_trips.map(p => `<tr class="click" data-pid="${p.product_id}"><td>${pname(p)}</td><td class="num">${p.trips}</td><td class="num">${p.units}</td><td class="num">${money(p.spent)}</td></tr>`).join("")}</table>`)}
    </div>
    <div class="grid g2">
      ${kind !== "all"
        ? listCard(`New this ${kind === "month" ? "month" : "year"} <span class="muted small">${d.new_count} products</span>`, d.new_products.length ? `<table>${d.new_products.map(n => `<tr class="click" data-pid="${n.product_id}"><td>${pname(n)}<div class="small muted">${esc(n.category)} · ${fmtDate(n.date)}</div></td><td class="num">${money(n.paid)}</td></tr>`).join("")}</table>` : `<p class="muted">Nothing new — only things you've bought before.</p>`)
        : listCard("Biggest spend", `<table>${d.top.by_spent.map(p => `<tr class="click" data-pid="${p.product_id}"><td>${pname(p)}<div class="small muted">${p.trips} trips</div></td><td class="num">${money(p.spent)}</td></tr>`).join("")}</table>`)}
      ${listCard("Price changes", d.price_changes.length ? `<table>${d.price_changes.map(c => `<tr class="click" data-pid="${c.product_id}"><td>${pname(c)}<div class="small muted">${fmtDate(c.old_date)} → ${fmtDate(c.date)}</div></td><td class="num">${money(c.old)} → <b>${money(c.new)}</b></td><td class="num">${changeBadge(c.new, c.old)}</td></tr>`).join("")}</table>` : `<p class="muted">No shelf-price changes.</p>`)}
    </div>
    <div class="grid g2">
      ${listCard("Biggest receipts", `<table>${d.biggest_receipts.map(r => `<tr class="click" data-rid="${r.id}"><td>${fmtDate(r.purchase_date)} · ${who(r.person_id)}<div class="small muted">${esc(r.warehouse || "")} · ${r.lines} items</div></td><td class="num"><b>${money(r.total)}</b></td></tr>`).join("")}</table>`)}
      ${listCard("When &amp; where", `<div class="bars">${d.weekdays.map(w => `<div class="b"><span class="lbl">${w.day}</span><div><div class="t s-this" style="width:${Math.max(100 * w.trips / maxWd, w.trips ? 2 : 0)}%"></div></div><span class="num">${w.trips} ${w.trips === 1 ? "trip" : "trips"}</span></div>`).join("")}</div>
        <p class="small muted">${d.warehouses.map(([w, n]) => `${esc(w)} ${n}×`).join(" · ")}</p>
        ${d.people.length > 1 ? `<p class="small">${d.people.map(p => `${who(p.person_id)} ${money(p.spent)} · ${p.trips} trips`).join(" &nbsp; ")}</p>` : ""}`)}
    </div>`;
}

pages["/receipts"] = async () => {
  const rows = await api(withPerson("/api/receipts", { limit: 1000 }));
  $app.innerHTML = `<h1>Receipts</h1><div class="card scroll"><table>
    <tr><th>Date</th><th>Who</th><th class="hide-sm">Warehouse</th><th>Type</th><th class="num">Lines</th><th class="num hide-sm">Saved</th><th class="num">Total</th></tr>
    ${rows.map(r => `<tr class="click" data-rid="${r.id}"><td>${esc(r.purchased_at.replace("T", " "))}</td><td>${who(r.person_id)}</td><td class="hide-sm">${esc(r.warehouse || "")}</td>
      <td>${esc(r.kind)}${r.is_refund ? " · refund" : ""}${r.source === "photo" ? " · 📷" : ""}</td><td class="num">${r.lines}</td><td class="num hide-sm">${r.instant_savings ? money(r.instant_savings) : ""}</td><td class="num">${money(r.total)}</td></tr>`).join("")}
  </table>${rows.length ? "" : `<p class="muted">No receipts yet — go to <a href="#/import">Import</a>.</p>`}</div>`;
  $app.querySelectorAll("tr[data-rid]").forEach(tr => tr.onclick = () => go("/receipt/" + tr.dataset.rid));
};

pages["/receipt"] = async ({ id }) => {
  const r = await api("/api/receipts/" + id);
  $app.innerHTML = `<div class="small"><a href="#/receipts">← Receipts</a></div><h1>${fmtDate(r.purchase_date)}</h1>${receiptCard(r)}
    <button class="ghost" id="del">Delete this receipt</button>`;
  bindProductLinks($app);
  document.getElementById("del").onclick = async e => {
    if (e.target.dataset.armed !== "1") { e.target.dataset.armed = "1"; e.target.textContent = "Click again to delete"; return; }
    await api("/api/receipts/" + r.id, { method: "DELETE" });
    go("/receipts");
  };
};

pages["/returns"] = async () => {
  const r = await api(withPerson("/api/returns"));
  const table = (rows, empty) => rows.length ? `<div class="scroll"><table><tr><th>Product</th><th>Bought</th><th>Who</th><th class="num">Paid</th><th>Status</th></tr>
    ${rows.map(x => `<tr class="click" data-pid="${x.product_id}"><td>${pname(x)}<div class="small muted">${esc(x.category || "")}${x.return_note ? " · " + esc(x.return_note) : ""}</div></td>
      <td>${fmtDate(x.purchase_date)}</td><td>${who(x.person_id)}</td><td class="num">${money(x.paid)}</td><td>${ret(x.return)}</td></tr>`).join("")}</table></div>` : `<p class="muted">${empty}</p>`;
  $app.innerHTML = `<h1>Returns</h1>
    <div class="card"><h2>⏳ Time-limited, still open (90-day rule)</h2>${table(r.open, "Nothing with a running 90-day window.")}</div>
    <div class="card"><h2>⚠️ Special conditions</h2>${table(r.limited, "None.")}</div>
    <div class="card"><h2>Window closed in the last 60 days</h2>${table(r.expired, "None.")}</div>
    ${r.uncategorised ? `<p class="muted">${r.uncategorised} recent purchases aren't categorised yet — see Import → AI.</p>` : ""}
    <div class="card"><h2>Costco return policy (summary)</h2>
      ${Object.entries(r.policy).map(([k, v]) => `<p><span class="st-${{ standard: "anytime", electronics_90: "open", limited: "check", non_returnable: "no" }[k]}"><b>${k}</b></span> — ${esc(v)}</p>`).join("")}
      <p class="small muted">Everything else (food included) can be returned any time. Look up a specific product in <a href="#/products">Products</a>. The warehouse membership counter has the final word.</p></div>`;
  bindProductLinks($app);
};

pages["/ask"] = async () => {
  const examples = ["How many times did we buy eggs this year and what did we pay?", "Can I still return the TV we bought?",
    "What do we spend the most on?", "Who goes to Costco more, and how much does each of us spend per month?",
    "Has the price of Kirkland paper towels gone up?", "What did we buy last time?"];
  $app.innerHTML = `<h1>Ask AI</h1>
    <div class="card"><textarea id="qq" rows="3" placeholder="Ask anything about your Costco purchases…"></textarea>
      <div class="row end" style="margin-top:8px"><div class="small muted">Answered by your local model with your data${state.person ? ` (only ${esc(person(+state.person).name)})` : ""}.</div><button id="askb">Ask</button></div>
      <div class="row small" style="margin-top:10px">${examples.map(e => `<a href="#" class="chip ex">${esc(e)}</a>`).join("")}</div></div>
    <div id="ans"></div>`;
  const ask = async () => {
    const q = document.getElementById("qq").value.trim();
    if (!q) return;
    const $ans = document.getElementById("ans"), b = document.getElementById("askb");
    b.disabled = true;
    $ans.innerHTML = `<div class="card"><span class="spinner"></span> Thinking… (the first question after idle loads the model, ~30 s)</div>`;
    try {
      const r = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, person: state.person ? +state.person : null }) });
      $ans.innerHTML = `<div class="card"><div class="answer">${esc(r.answer)}</div>${r.products_used.length ? `<p class="small muted">Products looked at: ${r.products_used.map(esc).join(", ")}</p>` : ""}</div>`;
    } catch (e) { $ans.innerHTML = `<div class="card st-expired">${esc(e.message)}</div>`; }
    b.disabled = false;
  };
  document.getElementById("askb").onclick = ask;
  document.getElementById("qq").onkeydown = e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask(); };
  $app.querySelectorAll(".ex").forEach(a => a.onclick = e => { e.preventDefault(); document.getElementById("qq").value = a.textContent; ask(); });
};

pages["/import"] = async () => {
  const [ai, health] = await Promise.all([api("/api/ai/status"), api("/api/health/llm")]);
  const opts = state.people.map(p => `<option value="${p.id}" ${String(p.id) === state.person ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  $app.innerHTML = `<h1>Import</h1>
    <div class="card"><h2>1 · Download your receipts from costco.com</h2>
      <ol>
        <li>On your computer, sign in at <a href="https://www.costco.com" target="_blank" rel="noopener">costco.com</a> → Account → <b>Orders &amp; Purchases</b> and open the <b>Warehouse</b> tab.</li>
        <li>Open the browser console (<code>F12</code> or <code>⌘⌥J</code>). Chrome may ask you to type <code>allow pasting</code> first.</li>
        <li><button class="ghost" id="copy">Copy the export script</button> and paste it into the console, press Enter. A <code>costco-receipts-….json</code> file downloads.</li>
        <li>Your wife does the same signed in with <b>her</b> costco.com account.</li>
      </ol>
      <p class="small muted">The script only reads your receipts from costco.com with your own session. Re-run it any time: receipts already imported are skipped.</p></div>
    <div class="card"><h2>2 · Upload</h2>
      <div class="row">Whose receipts? <select id="who">${opts}</select></div>
      <div class="grid g2" style="margin-top:12px">
        <div class="drop"><b>Costco JSON export</b><br><input type="file" id="json" accept=".json,application/json" multiple><br><span class="small muted">from the script above</span></div>
        <div class="drop"><b>Photo of a paper receipt</b><br><input type="file" id="photo" accept="image/*" multiple><br><span class="small muted">read by ${esc(health.model)} — takes ~1 min each</span></div>
      </div>
      <div id="result" style="margin-top:12px"></div></div>
    <div class="card"><h2>3 · AI categories &amp; return rules</h2>
      <p>Ollama: ${health.reachable ? `<span class="st-anytime">reachable</span> · ${esc(health.url)} · model ${esc(health.model)} ${health.model_present ? "✓" : `<span class="st-expired">not installed</span>`}` : `<span class="st-expired">not reachable (${esc(health.url)})</span>`}</p>
      <p>Products: ${Object.entries(ai.counts).map(([k, v]) => `<span class="chip">${esc(k)}: ${v}</span>`).join(" ")} ${ai.running ? `<span class="spinner"></span> running` : ""}</p>
      ${ai.last_error ? `<p class="small st-expired">${esc(ai.last_error)}</p>` : ""}
      <div class="row"><button id="ai-go">Categorise new</button><button class="ghost" id="ai-retry">Retry failed</button><button class="ghost" id="ai-redo">Redo all (keeps your edits)</button></div></div>
    <div class="card"><h2>People</h2>
      ${state.people.map(p => `<div class="row" style="margin-bottom:6px"><span class="dot" style="background:${p.color}"></span><input data-pid="${p.id}" class="pname" value="${esc(p.name)}"><span class="small muted">${p.receipts} receipts${p.first ? ` · ${fmtDate(p.first)} → ${fmtDate(p.last)}` : ""}</span></div>`).join("")}
      <div class="row"><input id="newp" placeholder="Add person"><button class="ghost" id="addp">Add</button></div></div>`;
  const $r = document.getElementById("result");
  document.getElementById("copy").onclick = async e => {
    const code = await (await fetch("/costco-export.js")).text();
    try { await navigator.clipboard.writeText(code); e.target.textContent = "Copied ✓"; }
    catch (_) { $r.innerHTML = `<p>Copy it from here:</p><pre>${esc(code)}</pre>`; }
  };
  const upload = async (kind, files) => {
    for (const f of files) {
      const fd = new FormData();
      fd.append("person_id", document.getElementById("who").value);
      fd.append("file", f);
      const uid = "up-" + Math.random().toString(36).slice(2);
      $r.insertAdjacentHTML("beforeend", `<p id="${uid}"><span class="spinner"></span> ${esc(f.name)}…</p>`);
      const line = document.getElementById(uid);
      try {
        const s = await api("/api/import/" + kind, { method: "POST", body: fd });
        line.innerHTML = `✅ ${esc(f.name)}: ${s.imported} new receipts, ${s.duplicates} already imported, ${s.items} items, ${s.new_products} new products${s.errors.length ? `<br><span class="st-expired small">${s.errors.map(esc).join("<br>")}</span>` : ""}`;
      } catch (e) { line.innerHTML = `❌ ${esc(f.name)}: ${esc(e.message)}`; }
    }
    await loadPeople();
    pollAI();
  };
  document.getElementById("json").onchange = e => upload("json", e.target.files);
  document.getElementById("photo").onchange = e => upload("photo", e.target.files);
  const aiCall = q => async () => { await api("/api/ai/categorize" + q, { method: "POST" }); pollAI(); render(); };
  document.getElementById("ai-go").onclick = aiCall("");
  document.getElementById("ai-retry").onclick = aiCall("?retry_failed=true");
  document.getElementById("ai-redo").onclick = aiCall("?redo_all=true");
  $app.querySelectorAll(".pname").forEach(i => i.onchange = async () => {
    await api("/api/people/" + i.dataset.pid, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: i.value }) });
    await loadPeople();
  });
  document.getElementById("addp").onclick = async () => {
    const name = document.getElementById("newp").value.trim();
    if (!name) return;
    await api("/api/people", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    await loadPeople(); render();
  };
};

// ---------------------------------------------------------------- shell
async function loadPeople() {
  state.people = await api("/api/people");
  $person.innerHTML = `<option value="">Everyone</option>` + state.people.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  $person.value = state.person;
}

let pollTimer;
async function pollAI() {
  clearTimeout(pollTimer);
  const s = await api("/api/ai/status").catch(() => null);
  const $b = document.getElementById("ai-banner");
  if (s && s.running) {
    $b.innerHTML = `<span class="spinner"></span> AI is categorising products… ${s.counts.pending || 0} left (pages refresh as you browse)`;
    $b.classList.remove("hidden");
    pollTimer = setTimeout(pollAI, 5000);
  } else {
    $b.classList.add("hidden");
  }
}

async function render() {
  const { path, params } = route();
  const [, base, id] = path.match(/^(\/[^/]*)\/?(\d+)?$/) || [];
  const page = pages[id ? base : path] || pages["/"];
  document.querySelectorAll("#nav a").forEach(a => {
    const href = a.getAttribute("href").slice(1);
    a.classList.toggle("active", href === "/" ? path === "/" : path.startsWith(href) || (href === "/products" && base === "/product") || (href === "/categories" && path === "/category") || (href === "/receipts" && base === "/receipt"));
  });
  try { await page({ params, id }); }
  catch (e) { $app.innerHTML = `<div class="card st-expired">Error: ${esc(e.message)}</div>`; }
}

$person.onchange = () => {
  state.person = $person.value;
  try { localStorage.setItem("person", state.person); } catch (_) { }
  render();
};
window.addEventListener("hashchange", render);

(async () => {
  [state.meta] = await Promise.all([api("/api/meta"), loadPeople()]);
  if (state.person && !state.people.some(p => String(p.id) === state.person)) { state.person = ""; $person.value = ""; }
  render();
  pollAI();
})();
