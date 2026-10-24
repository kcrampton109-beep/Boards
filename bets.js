/* Shared Bets tab for every board, so CFB and NFL look and read the same (styles: site/bets.css).
   Each page turns its own data into this shape and calls Bets.render(element, data):
     data.unit      dollars per unit
     data.intro     one sentence: which plays are on this tab
     data.summary   [{label, rec: "8-4", units}]                       season record per money tier
     data.open      [{label, pick, odds, app, stake, matchup, kickoff, logged, playTo, now: {kind: ok|gone|live, text}, sortKey}]
     data.pairs     [{legs: [{pick, matchup, kickoff}], stake, price, playTo, logged, now}]   2-team teasers to bet together
     data.settled   [{status, label, pick, matchup, kickoff, odds, app, stake, units, clv, final, logged}]
     data.emptyOpen text shown when nothing is open */
(function () {
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sgn = (v, d = 1) => v == null || isNaN(v) ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d).replace(/\.0+$/, "");
  const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "";

  function render(el, d) {
    const unit = d.unit || 10;
    const usd = u => u == null || isNaN(u) ? "–" : (u < 0 ? "−$" : "$") + Math.abs(u * unit).toFixed(2).replace(/\.00$/, "");
    const units = u => `${+(+u).toFixed(2)}u`;
    const stake = u => `${units(u)} · ${usd(u)}`;
    const facts = rows => `<div class="facts">${rows.filter(r => r[1]).map(([k, v]) => `<span>${k}</span><span>${v}</span>`).join("")}</div>`;
    const now = n => n ? `<div class="now ${n.kind}">${esc(n.text)}</div>` : "";
    // "Log bet" (x.take: key of a play the page registered for the in-board panel, site/logbet.js) and the owner's
    // logged bets on the play (x.mine: short descriptions)
    const took = x => (x.mine && x.mine.length ? `<div class="b-mine">✓ You bet ${x.mine.map(esc).join("; ")}</div>` : "")
      + (x.take ? `<button class="b-take" type="button" data-take="${esc(x.take)}">${x.mine && x.mine.length ? "Log another bet on this" : "Log bet"}</button>` : "");

    const betCard = b => `<article class="bet ${b.now ? b.now.kind : ""}">
        <div class="top"><span class="tag">${esc(b.label)}</span><span class="num">${stake(b.stake)}</span></div>
        <div class="pick">${esc(b.pick)}</div>
        <div class="price num"><b>${esc(b.odds)}</b> at <b>${esc(b.app)}</b></div>
        ${facts([["Game", esc(b.matchup)], ["Kickoff", esc(b.kickoff)], ["Playable to", `<span class="play-to">${esc(b.playTo)}</span>`],
                 ["Logged", esc(b.logged || "new: logs on the next check")]])}
        ${took(b)}${now(b.now)}</article>`;
    const pairCard = p => `<article class="bet ${p.now ? p.now.kind : ""}">
        <div class="top"><span class="tag">2-team teaser</span><span class="num">${stake(p.stake)}</span></div>
        <div class="legs">${p.legs.map(l => `<span>${esc(l.pick)}</span>`).join("")}</div>
        <div class="price num"><b>${esc(p.price)}</b></div>
        ${facts([["Games", p.legs.map(l => `${esc(l.matchup)} · ${esc(l.kickoff)}`).join("<br>")], ["Playable to", `<span class="play-to">${esc(p.playTo)}</span>`],
                 ["Logged", esc(p.logged || "new")]])}
        ${took(p)}${now(p.now)}</article>`;

    const open = (d.open || []).slice().sort((a, b) => String(a.sortKey || "").localeCompare(String(b.sortKey || "")));
    const gone = open.filter(b => b.now && b.now.kind === "gone"), active = open.filter(b => !(b.now && b.now.kind === "gone"));
    const pairs = d.pairs || [];
    const settled = d.settled || [];
    el.innerHTML = `<div class="bets">
      <p>${d.intro} Dollars use $${unit} a unit. Bet at the price shown or better and never past the <b>playable to</b> number.</p>
      <div class="bets-sum">${(d.summary || []).map(s => `<div><span class="b-lab">${esc(s.label)}</span><span class="b-rec num">${esc(s.rec)}</span>
        <span class="b-u num ${cls(s.units)}">${sgn(s.units || 0, 2)}u · ${usd(s.units || 0)}</span></div>`).join("")}</div>
      <h2>Open bets<span class="n num">${active.length + pairs.length}</span></h2>
      ${active.length || pairs.length ? `<div class="bets-grid">${active.map(betCard).join("")}${pairs.map(pairCard).join("")}</div>`
        : `<div class="bets-empty">${esc(d.emptyOpen || "No open bets right now.")}</div>`}
      ${gone.length ? `<h2>No longer plays<span class="n num">${gone.length}</span></h2>
      <p>Logged earlier, but the line has since moved past the playable number. Skip these unless you already bet them; they are still graded.</p>
      <div class="bets-grid">${gone.map(betCard).join("")}</div>` : ""}
      <h2>Settled<span class="n num">${settled.length}</span></h2>
      ${settled.length ? `<div class="bets-tbl"><table><thead><tr><th>Result</th><th>Bet</th><th class="r">Price</th><th class="r">Stake</th><th class="r">Won / lost</th><th class="r">CLV</th></tr></thead><tbody>
        ${settled.map(r => `<tr><td><span class="res ${esc(r.status)}">${esc(r.status)}</span></td>
          <td class="g"><b>${esc(r.pick)}</b> <span class="sub">${esc(r.label)} · ${esc(r.matchup)} · ${esc(r.kickoff)}${r.final ? " · " + esc(r.final) : ""}</span></td>
          <td class="r num">${esc(r.odds)} <span class="sub">${esc(r.app)}</span></td><td class="r num">${units(r.stake)}</td>
          <td class="r num ${cls(r.units)}">${usd(r.units)} <span class="sub">${sgn(r.units, 2)}u</span></td>
          <td class="r num ${cls(r.clv)}">${r.clv == null ? "–" : sgn(r.clv)}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="bets-empty">Nothing settled yet.</div>`}
    </div>`;
  }
  window.Bets = { render };
})();
