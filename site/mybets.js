/* My Bets, shared by every board: the owner's own bets. The "I took this" button (MyBets.takeUrl) opens a pre-filled
   GitHub issue form (.github/ISSUE_TEMPLATE/bet.yml); .github/workflows/mybets.yml saves it and republishes the board's
   mybets.json. Bets are graded here from the board's scores.json at the owner's own line and odds.
     MyBets.takeUrl(fields)                         link to the pre-filled form
     MyBets.grade(bet, scores)                      {status: open | W | L | P, profit ($), clv (pts), final}
     MyBets.rows(bets, scores, unit, weekOf)        rows for the shared Today / Results tabs (tier "MINE")
     MyBets.render(el, {bets, scores, unit, board}) the My Bets tab (styles: bets.css + track.css) */
(function () {
  const REPO = "https://github.com/kcrampton109-beep/Boards";
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sgn = (v, d = 1) => v == null || isNaN(v) ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d).replace(/\.0+$/, "");
  const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "";
  const usd = v => v == null || isNaN(v) ? "–" : (v < 0 ? "−$" : v > 0 ? "+$" : "$") + Math.abs(v).toFixed(2).replace(/\.00$/, "");
  const odds = v => v == null ? "" : (v > 0 ? "+" : "−") + Math.abs(Math.round(v));
  const line = v => v == null ? "" : Math.abs(v) < 0.25 ? "PK" : sgn(v);
  const payout = o => o > 0 ? o / 100 : 100 / Math.abs(o);
  const etDate = s => { if (!s) return ""; const t = new Date(String(s).replace(" ", "T")); return isNaN(t) ? "" : t.toLocaleDateString("en-CA", { timeZone: "America/New_York" }); };
  const etTxt = s => { if (!s) return ""; const t = new Date(String(s).replace(" ", "T")); return isNaN(t) ? String(s)
    : t.toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " ET"; };

  function takeUrl(f) {
    const q = new URLSearchParams({ template: "bet.yml", title: `Bet (${f.board || ""}): ${f.bet || ""}`.trim() });
    for (const k of ["board", "bet", "line", "odds", "book", "amount", "game", "kickoff", "play"])
      if (f[k] != null && f[k] !== "") q.set(k, String(f[k]));
    return `${REPO}/issues/new?${q}`;
  }

  function legResult(l, s, teaser) {
    const hp = s.home_pts, ap = s.away_pts;
    let r;
    if (l.market === "total") r = (hp + ap - l.line) * (l.side === "over" ? 1 : -1);
    else { const m = l.side === "home" ? hp - ap : ap - hp; r = l.market === "moneyline" ? m : m + l.line + (teaser ? 6 : 0); }
    return r > 0 ? "W" : r < 0 ? "L" : "P";
  }

  function grade(b, scores) {
    if (!b.market) return { status: "open", unlinked: true };
    const teaser = b.market === "teaser";
    const legs = teaser ? (b.legs || []).map(l => Object.assign({ market: "spread" }, l)) : [{ game_id: b.game_id, market: b.market, side: b.side, line: b.line }];
    const res = [], finals = [];
    for (const l of legs) {
      const s = scores[String(l.game_id)];
      if (!s) return { status: "open" };
      res.push(legResult(l, s, teaser));
      finals.push(`${s.away} ${s.away_pts} - ${s.home} ${s.home_pts}`);
    }
    const status = res.includes("L") ? "L" : res.every(r => r === "W") ? "W" : "P";
    const profit = status === "W" ? b.amount * payout(b.odds) : status === "L" ? -b.amount : 0;
    let clv = null;
    const s = scores[String(b.game_id)];
    if (s && b.market === "spread" && s.close_spread != null) clv = b.line - (b.side === "home" ? s.close_spread : -s.close_spread);
    if (s && b.market === "total" && s.close_total != null) clv = b.side === "under" ? b.line - s.close_total : s.close_total - b.line;
    return { status, profit, clv, final: finals.join(" · ") };
  }

  // points better (+) or worse (−) than the line the board logged for the same play
  const vsBoard = b => b.board_line == null || b.line == null || !(b.market === "spread" || b.market === "total") ? null
    : (b.line - b.board_line) * (b.side === "over" ? -1 : 1);
  const price = b => `${b.line != null && b.market !== "teaser" ? (b.market === "total" ? b.line + " " : "") : ""}${odds(b.odds)}`;

  function rows(bets, scores, unit, weekOf) {
    return (bets || []).map(b => {
      const g = grade(b, scores), legs = b.legs || [];
      const ko = b.market === "teaser" && legs.length ? legs.map(l => l.kickoff).sort().pop() : b.kickoff;
      return { id: "mine-" + b.id, date: etDate(ko) || etDate(b.logged), kickoff: etTxt(ko), sortKey: String(ko || b.logged),
               week: weekOf ? weekOf(b) : null, tier: "MINE", label: "My bet", money: false, inAll: false, status: g.status,
               units: g.status === "open" ? null : g.profit / unit, stake: b.amount / unit, clv: g.clv, pick: b.pick, matchup: b.matchup,
               odds: odds(b.odds), app: b.book, final: g.final, logged: etTxt(b.logged) };
    });
  }

  function render(el, d) {
    const bets = (d.bets || []).map(b => Object.assign({ g: grade(b, d.scores || {}) }, b)).sort((a, b) => String(b.logged).localeCompare(String(a.logged)));
    const open = bets.filter(b => b.g.status === "open"), done = bets.filter(b => b.g.status !== "open");
    const w = done.filter(b => b.g.status === "W").length, l = done.filter(b => b.g.status === "L").length, p = done.length - w - l;
    const profit = done.reduce((a, b) => a + b.g.profit, 0), risked = done.filter(b => b.g.status !== "P").reduce((a, b) => a + b.amount, 0);
    const clvs = done.map(b => b.g.clv).filter(v => v != null), vss = bets.map(vsBoard).filter(v => v != null);
    const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
    const atRisk = open.reduce((a, b) => a + b.amount, 0), toWin = open.reduce((a, b) => a + b.amount * payout(b.odds), 0);
    const now = Date.now();
    const card = b => {
      const vs = vsBoard(b), started = b.kickoff && new Date(String(b.kickoff).replace(" ", "T")) < now;
      const boardTxt = b.board_line != null ? `${b.market === "total" ? b.board_line : line(b.board_line)} ${odds(b.board_odds)} ${esc(b.board_book || "")}` +
        (vs == null ? "" : vs === 0 ? " · same number" : ` · you got ${Math.abs(vs)} pt${Math.abs(vs) === 1 ? "" : "s"} <span class="${cls(vs)}">${vs > 0 ? "better" : "worse"}</span>`) : "";
      const status = b.syncing ? { kind: "live", text: "Saving to GitHub: recorded within a couple of minutes" }
        : b.g.unlinked ? { kind: "live", text: "Not a board game: not graded automatically" }
        : started ? { kind: "live", text: "Kicked off: graded when the final is in" } : { kind: "ok", text: "Pending" };
      const legs = b.market === "teaser" ? (b.legs || []).map(x => `${esc(x.pick)} → ${line(x.line + 6)} · ${esc(x.matchup)}`).join("<br>") : "";
      return `<article class="bet ${status.kind}">
        <div class="top"><span class="tag">${b.market === "teaser" ? "2-team teaser" : esc(b.tier === "OTHER" ? "Other bet" : b.tier === "LOOKAHEAD" ? "Look-ahead" : "Board play")}</span><span class="num">$${b.amount} · to win $${(b.amount * payout(b.odds)).toFixed(2)}</span></div>
        <div class="pick">${esc(b.pick)}</div>
        <div class="price num"><b>${price(b)}</b> at <b>${esc(b.book)}</b></div>
        <div class="facts">${[["Game", legs || esc(b.matchup)], ["Kickoff", esc(etTxt(b.market === "teaser" ? (b.legs || []).map(x => x.kickoff).sort().pop() : b.kickoff))],
          ["Board", boardTxt], ["Logged", esc(etTxt(b.logged))]].filter(x => x[1]).map(([k, v]) => `<span>${k}</span><span>${v}</span>`).join("")}</div>
        ${d.editKey ? `<button class="b-take" type="button" data-edit="${esc(d.editKey(b))}">Edit or remove</button>` : ""}
        <div class="now ${status.kind}">${esc(status.text)}</div></article>`;
    };
    el.innerHTML = `<div class="bets">
      <p>Your own bets, logged with the <b>Log bet</b> button on the Bets and Today tabs: pick the line, odds, app and amount you
         actually got and save. Each bet is graded at <b>your</b> line and odds. ${typeof LogBet !== "undefined" && LogBet.connected()
         ? 'This phone is connected to GitHub. <button class="b-link" type="button" data-disconnect="1">Disconnect</button>'
         : "The first time you log a bet, the board walks you through connecting it to GitHub."}</p>
      <div class="bets-sum">
        <div><span class="b-lab">Record</span><span class="b-rec num">${w}-${l}${p ? "-" + p : ""}</span><span class="b-u num">${done.length} settled</span></div>
        <div><span class="b-lab">Won / lost</span><span class="b-rec num ${cls(profit)}">${usd(profit)}</span><span class="b-u num">ROI ${risked ? sgn(100 * profit / risked, 1) + "%" : "–"} on $${risked.toFixed(0)}</span></div>
        <div><span class="b-lab">Open</span><span class="b-rec num">${open.length}</span><span class="b-u num">$${atRisk.toFixed(0)} at risk · to win $${toWin.toFixed(2)}</span></div>
        <div><span class="b-lab">Your line vs the board</span><span class="b-rec num ${cls(avg(vss))}">${avg(vss) == null ? "–" : sgn(avg(vss), 1) + " pts"}</span>
          <span class="b-u num">CLV ${avg(clvs) == null ? "–" : sgn(avg(clvs), 1) + " pts"}</span></div>
      </div>
      <h2>Open<span class="n num">${open.length}</span></h2>
      ${open.length ? `<div class="bets-grid">${open.map(card).join("")}</div>` : `<div class="bets-empty">No open bets logged. Tap <b>I took this</b> on a play to log one.</div>`}
      <h2>Settled<span class="n num">${done.length}</span></h2>
      ${done.length ? `<div class="bets-tbl"><table><thead><tr><th>Result</th><th>Bet</th><th class="r">Price</th><th class="r">Stake</th><th class="r">Won / lost</th><th class="r">CLV</th><th class="r">vs board</th></tr></thead><tbody>
        ${done.map(b => { const vs = vsBoard(b); return `<tr><td><span class="res ${b.g.status}">${b.g.status}</span></td>
          <td class="g"><b>${esc(b.pick)}</b><span class="sub">${esc(b.matchup)} · ${esc(b.g.final)}${d.editKey ? ` · <button class="b-link" type="button" data-edit="${esc(d.editKey(b))}">edit</button>` : ""}</span></td>
          <td class="r num">${price(b)}<span class="sub">${esc(b.book)}</span></td><td class="r num">$${b.amount}</td>
          <td class="r num ${cls(b.g.profit)}">${usd(b.g.profit)}</td><td class="r num ${cls(b.g.clv)}">${b.g.clv == null ? "–" : sgn(b.g.clv)}</td>
          <td class="r num ${cls(vs)}">${vs == null ? "–" : sgn(vs)}</td></tr>`; }).join("")}</tbody></table></div>`
        : `<div class="bets-empty">Nothing settled yet.</div>`}
    </div>`;
  }

  document.addEventListener("click", e => {
    const t = e.target.closest && e.target.closest("[data-disconnect]");
    if (t && typeof LogBet !== "undefined" && confirm("Disconnect this phone from GitHub? You'd paste the key again to log bets.")) {
      LogBet.disconnect(); if (window.__afterLog) window.__afterLog(); }
  });
  window.MyBets = { takeUrl, grade, rows, render };
})();
