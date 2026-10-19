/* Shared Today + Results tabs for every board, so CFB and NFL look and work the same (styles: track.css + bets.css).
   Each page turns its full ledger into rows and calls Track.renderToday / Track.renderResults:
     row = {id, date: "YYYY-MM-DD" (Eastern game date), kickoff, sortKey, week, tier, label, money (bet tier?),
            inAll (false = left out of "All plays", e.g. teaser legs that are counted as 2-team teasers),
            status: "W" | "L" | "P" | "open", units, stake, clv, pick, matchup, odds, app, final, logged,
            playTo, now: {kind: ok | gone | live, text}}
   Today:   {rows, unit, today}
   Results: {rows, unit, today, week (the board's current football week), tiers: [[key, label], ...]}
            tier key "money" = the Bets-tab plays, "all" = everything, else a row.tier value. */
(function () {
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sgn = (v, d = 1) => v == null || isNaN(v) ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d).replace(/\.0+$/, "");
  const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "";
  const GRADED = new Set(["W", "L", "P"]);
  const shift = (d, n) => { const t = new Date(d + "T12:00:00Z"); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
  const dayName = (d, opt) => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", Object.assign({ timeZone: "UTC" }, opt));
  const monthName = m => dayName(m + "-15", { month: "long", year: "numeric" });

  function stats(rows) {
    const g = rows.filter(r => GRADED.has(r.status));
    const w = g.filter(r => r.status === "W").length, l = g.filter(r => r.status === "L").length, p = g.length - w - l;
    const units = g.reduce((a, r) => a + (+r.units || 0), 0);
    const risk = g.filter(r => r.status !== "P").reduce((a, r) => a + (+r.stake || 0), 0);
    const clv = g.map(r => r.clv).filter(v => v != null && !isNaN(v));
    return { n: g.length, w, l, p, units, roi: risk ? units / risk : null,
             clv: clv.length ? clv.reduce((a, b) => a + b, 0) / clv.length : null,
             beat: clv.length ? clv.filter(v => v > 0).length / clv.length : null };
  }
  const rec = s => `${s.w}-${s.l}${s.p ? "-" + s.p : ""}`;
  const winPct = s => s.w + s.l ? (100 * s.w / (s.w + s.l)).toFixed(1) + "%" : "–";
  const money = unit => u => u == null || isNaN(u) ? "–" : (u < 0 ? "−$" : u > 0 ? "+$" : "$") + Math.abs(u * unit).toFixed(2).replace(/\.00$/, "");
  const units = u => `${+(+u).toFixed(2)}u`;
  const bySort = (a, b) => String(a.sortKey || "").localeCompare(String(b.sortKey || ""));

  // ---------------------------------------------------------------- Today: every play on the day's games
  function renderToday(el, d) {
    const usd = money(d.unit || 10);
    let day = d.today, scope = "money";
    const card = r => {
      const graded = GRADED.has(r.status);
      const result = r.status === "W" ? { kind: "ok", text: `Won ${usd(r.units)} (${sgn(r.units, 2)}u)` }
        : r.status === "L" ? { kind: "gone", text: `Lost ${usd(r.units)} (${sgn(r.units, 2)}u)` }
        : r.status === "P" ? { kind: "live", text: "Push: stake back" } : r.now;
      const facts = [["Game", esc(r.matchup)], ["Kickoff", esc(r.kickoff)],
                     graded ? ["Final", esc(r.final)] : ["Playable to", r.playTo ? `<span class="play-to">${esc(r.playTo)}</span>` : ""],
                     graded && r.clv != null ? ["CLV", `<span class="${cls(r.clv)}">${sgn(r.clv)} pts</span>`] : ["", ""],
                     ["Logged", esc(r.logged)]].filter(x => x[1]);
      return `<article class="bet ${graded ? (r.status === "W" ? "won" : r.status === "L" ? "lost" : "live") : (r.now ? r.now.kind : "")}">
        <div class="top"><span class="tag">${esc(r.label)}</span><span class="num">${units(r.stake || 0)} · ${usd(r.stake || 0).replace("+", "")}</span></div>
        <div class="pick">${esc(r.pick)}</div>
        <div class="price num"><b>${esc(r.odds)}</b> at <b>${esc(r.app)}</b></div>
        <div class="facts">${facts.map(([k, v]) => `<span>${k}</span><span>${v}</span>`).join("")}</div>
        ${result ? `<div class="now ${result.kind}">${esc(result.text)}</div>` : ""}</article>`;
    };
    const draw = () => {
      const rows = d.rows.filter(r => r.date === day && (scope === "all" || r.money)).sort(bySort);
      const s = stats(rows), open = rows.filter(r => r.status === "open").length;
      el.innerHTML = `<div class="trk">
        <div class="trk-nav">
          <button class="trk-arrow" data-d="-1" aria-label="Previous day">‹</button>
          <h2>${dayName(day, { weekday: "long", month: "long", day: "numeric" })}${day === d.today ? ' <span class="trk-badge">Today</span>' : ""}</h2>
          <button class="trk-arrow" data-d="1" aria-label="Next day">›</button>
          ${day !== d.today ? '<button class="trk-chip" data-d="0">Back to today</button>' : ""}
        </div>
        <div class="trk-chips" role="group" aria-label="Which plays">
          <button class="trk-chip" data-s="money" aria-pressed="${scope === "money"}">Money plays</button>
          <button class="trk-chip" data-s="all" aria-pressed="${scope === "all"}">All plays</button>
        </div>
        <p class="trk-line num">${rows.length ? `${rows.length} play${rows.length === 1 ? "" : "s"} · ${open} still open · settled ${rec(s)} · <span class="${cls(s.units)}">${sgn(s.units, 2)}u (${usd(s.units)})</span>` : ""}</p>
        ${rows.length ? `<div class="bets-grid">${rows.map(card).join("")}</div>`
          : `<div class="bets-empty">No ${scope === "money" ? "money " : ""}plays on games ${day === d.today ? "today" : "this day"}.</div>`}
      </div>`;
      el.querySelectorAll("[data-d]").forEach(b => b.onclick = () => { const n = +b.dataset.d; day = n ? shift(day, n) : d.today; draw(); });
      el.querySelectorAll("[data-s]").forEach(b => b.onclick = () => { scope = b.dataset.s; draw(); });
    };
    draw();
  }

  // ---------------------------------------------------------------- Results: today / week / month / season, calendar, weeks
  function renderResults(el, d) {
    const usd = money(d.unit || 10);
    let tier = "money", month = d.today.slice(0, 7), pick = null;   // pick = {kind: "day" | "week", key}
    const pool = () => d.rows.filter(r => GRADED.has(r.status) &&
      (tier === "money" ? r.money : tier === "all" ? r.inAll !== false : r.tier === tier));
    const tile = (label, rows, sub) => { const s = stats(rows); return `<div class="trk-tile"><span class="t-lab">${label}</span>
        <span class="t-rec num">${rec(s)}</span><span class="t-sub num">${winPct(s)} · <span class="${cls(s.units)}">${sgn(s.units, 2)}u · ${usd(s.units)}</span></span>
        <span class="t-sub num">${sub || ""}ROI ${s.roi == null ? "–" : sgn(s.roi * 100, 1) + "%"} · CLV ${s.clv == null ? "–" : sgn(s.clv) + " pts"}</span></div>`; };
    const list = rows => rows.length ? `<div class="bets-tbl"><table><thead><tr><th>Result</th><th>Bet</th><th class="r">Price</th><th class="r">Stake</th><th class="r">Won / lost</th><th class="r">CLV</th></tr></thead><tbody>
        ${rows.slice().sort(bySort).map(r => `<tr><td><span class="res ${esc(r.status)}">${esc(r.status)}</span></td>
          <td class="g"><b>${esc(r.pick)}</b><span class="sub">${esc(r.label)} · ${esc(r.matchup)} · ${esc(r.kickoff)}${r.final ? " · " + esc(r.final) : ""}</span></td>
          <td class="r num">${esc(r.odds)}<span class="sub">${esc(r.app)}</span></td><td class="r num">${units(r.stake || 0)}</td>
          <td class="r num ${cls(r.units)}">${usd(r.units)}<span class="sub">${sgn(r.units, 2)}u</span></td>
          <td class="r num ${cls(r.clv)}">${r.clv == null ? "–" : sgn(r.clv)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="bets-empty">No settled plays.</div>`;

    const draw = () => {
      const rows = pool();
      const today = d.today, wk = d.week;
      // calendar for the month
      const first = month + "-01", lead = new Date(first + "T12:00:00Z").getUTCDay();
      const days = []; for (let x = first; x.slice(0, 7) === month; x = shift(x, 1)) days.push(x);
      const byDay = {}; rows.forEach(r => (byDay[r.date] = byDay[r.date] || []).push(r));
      const mRows = rows.filter(r => r.date.slice(0, 7) === month);
      const maxAbs = Math.max(1, ...days.map(x => Math.abs(stats(byDay[x] || []).units)));
      const cell = x => { const rs = byDay[x] || []; if (!rs.length) return `<div class="trk-day none${x === today ? " now" : ""}"><span class="dn">${+x.slice(8)}</span></div>`;
        const s = stats(rs), a = Math.min(1, Math.abs(s.units) / maxAbs), mix = Math.round(14 + 46 * a);
        const bg = s.units > 0 ? `color-mix(in srgb, var(--win) ${mix}%, var(--card))` : s.units < 0 ? `color-mix(in srgb, var(--loss) ${mix}%, var(--card))` : "var(--soft)";
        return `<button class="trk-day${x === today ? " now" : ""}${pick && pick.kind === "day" && pick.key === x ? " sel" : ""}" data-day="${x}" style="background:${bg}"
          aria-label="${dayName(x, { month: "short", day: "numeric" })}: ${rec(s)}, ${sgn(s.units, 2)} units" title="${rec(s)} · ${sgn(s.units, 2)}u · ${usd(s.units)}">
          <span class="dn">${+x.slice(8)}</span><span class="du num">${sgn(s.units, 1)}u</span><span class="dr num">${rec(s)}</span></button>`; };
      // weeks (football weeks), newest first
      const weeks = [...new Set(rows.map(r => r.week))].filter(w => w != null).sort((a, b) => b - a);
      const wkRow = w => { const rs = rows.filter(r => r.week === w), s = stats(rs), ds = rs.map(r => r.date).sort();
        const span = ds.length ? `${dayName(ds[0], { month: "short", day: "numeric" })} – ${dayName(ds[ds.length - 1], { month: "short", day: "numeric" })}` : "";
        return `<tr class="trk-wk${pick && pick.kind === "week" && pick.key === w ? " sel" : ""}" data-week="${w}" tabindex="0"><td><b>Week ${w}</b><span class="sub">${span}</span></td>
          <td class="r num">${rec(s)}</td><td class="r num">${winPct(s)}</td><td class="r num ${cls(s.units)}">${sgn(s.units, 2)}u</td><td class="r num ${cls(s.units)}">${usd(s.units)}</td>
          <td class="r num">${s.roi == null ? "–" : sgn(s.roi * 100, 1) + "%"}</td><td class="r num ${cls(s.clv)}">${s.clv == null ? "–" : sgn(s.clv)}</td></tr>`; };
      const detailRows = !pick ? [] : pick.kind === "day" ? (byDay[pick.key] || []) : rows.filter(r => r.week === pick.key);
      const detailTitle = !pick ? "" : pick.kind === "day" ? dayName(pick.key, { weekday: "long", month: "long", day: "numeric" }) : `Week ${pick.key}`;
      const ds = stats(detailRows), ms = stats(mRows);
      el.innerHTML = `<div class="trk">
        <div class="trk-chips" role="group" aria-label="Which plays">${d.tiers.map(([k, lab]) => `<button class="trk-chip" data-t="${k}" aria-pressed="${tier === k}">${esc(lab)}</button>`).join("")}</div>
        <div class="trk-tiles">
          ${tile("Today", rows.filter(r => r.date === today))}
          ${tile(`This week · Wk ${wk}`, rows.filter(r => r.week === wk))}
          ${tile(`Last week · Wk ${wk - 1}`, rows.filter(r => r.week === wk - 1))}
          ${tile(monthName(today.slice(0, 7)), rows.filter(r => r.date.slice(0, 7) === today.slice(0, 7)))}
          ${tile("Season", rows)}
        </div>
        <div class="trk-cal">
          <div class="trk-nav trk-month"><button class="trk-arrow" data-m="-1" aria-label="Previous month">‹</button>
            <h2>${monthName(month)}</h2>
            <button class="trk-arrow" data-m="1" aria-label="Next month">›</button></div>
          <p class="trk-line num">${monthName(month).split(" ")[0]}: ${rec(ms)} · ${winPct(ms)} · <span class="${cls(ms.units)}">${sgn(ms.units, 2)}u · ${usd(ms.units)}</span></p>
          <div class="trk-grid">${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(w => `<span class="trk-wd">${w}</span>`).join("")}
            ${'<div class="trk-day blank"></div>'.repeat(lead)}${days.map(cell).join("")}</div>
          <p class="trk-key"><span class="sw win"></span> winning day <span class="sw loss"></span> losing day · darker = bigger day · tap a day for its plays</p>
        </div>
        <div id="trk-detail">${pick ? `<h2>${esc(detailTitle)} <span class="trk-msum num">${rec(ds)} · <span class="${cls(ds.units)}">${sgn(ds.units, 2)}u · ${usd(ds.units)}</span></span>
            <button class="trk-chip" data-close="1">Close</button></h2>${list(detailRows)}` : ""}</div>
        <h2>By week</h2>
        ${weeks.length ? `<div class="bets-tbl"><table><thead><tr><th>Week</th><th class="r">Record</th><th class="r">Win %</th><th class="r">Units</th><th class="r">$</th><th class="r">ROI</th><th class="r">CLV</th></tr></thead>
          <tbody>${weeks.map(wkRow).join("")}</tbody></table></div><p class="trk-key">Tap a week for its plays.</p>` : '<div class="bets-empty">Nothing settled yet.</div>'}
      </div>`;
      el.querySelectorAll("[data-t]").forEach(b => b.onclick = () => { tier = b.dataset.t; pick = null; draw(); });
      el.querySelectorAll("[data-m]").forEach(b => b.onclick = () => { const t = new Date(month + "-15T12:00:00Z"); t.setUTCMonth(t.getUTCMonth() + +b.dataset.m); month = t.toISOString().slice(0, 7); draw(); });
      const open = p => { pick = p; draw(); el.querySelector("#trk-detail").scrollIntoView({ behavior: "smooth", block: "start" }); };
      el.querySelectorAll("[data-day]").forEach(b => b.onclick = () => open({ kind: "day", key: b.dataset.day }));
      el.querySelectorAll("[data-week]").forEach(r => { r.onclick = () => open({ kind: "week", key: +r.dataset.week }); r.onkeydown = e => { if (e.key === "Enter") r.onclick(); }; });
      el.querySelectorAll("[data-close]").forEach(b => b.onclick = () => { pick = null; draw(); });
    };
    draw();
  }

  window.Track = { renderToday, renderResults };
})();
