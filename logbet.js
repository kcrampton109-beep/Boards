/* Log a bet right on the board (shared by every board; styles: logbet.css).
   A panel with a dropdown per detail (bet, line, odds, sportsbook, amount) saves the bet by opening a GitHub issue through
   the GitHub API with the owner's own token (fine-grained, this repo only, Issues read/write), kept in this browser only.
   .github/workflows/mybets.yml records the issue (tools/log_bet.py) and republishes mybets.json; until then the bet is kept
   here as "pending" so it shows at once. Edit = update the issue; Remove = a "remove" comment.
     LogBet.open(play, onSaved)   play: {board, play, game_id, matchup, away, home, kickoff, market, side, line, odds, book,
                                         amount, teaser?: [{pick, matchup}]}
     LogBet.edit(bet, onSaved)    a bet from mybets.json (or a pending one)
     LogBet.pending(board)        bets saved here that mybets.json doesn't have yet */
(function () {
  const REPO = "kcrampton109-beep/Boards", API = `https://api.github.com/repos/${REPO}`;
  const TOKEN_KEY = "boards-gh-token", PEND_KEY = "boards-pending-bets";
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } },
  };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtLine = v => v === 0 ? "PK" : (v > 0 ? "+" : "") + v;
  const fmtOdds = v => (v > 0 ? "+" : "") + v;
  const BOOKS = ["DraftKings", "FanDuel", "BetMGM"];
  const BOOKN = { DK: "DraftKings", FD: "FanDuel", MGM: "BetMGM" };
  const ODDS = [-200, -190, -180, -175, -170, -165, -160, -155, -150, -145, -140, -135, -130, -125, -122, -120, -118, -115, -112,
                -110, -108, -105, -102, -100, 100, 102, 105, 108, 110, 115, 120, 125, 130, 135, 140, 145, 150, 160, 170, 175, 180, 190, 200];
  const AMOUNTS = [5, 10, 15, 20, 25, 30, 40, 50, 75, 100];
  const TYPES = [["spread", "away"], ["spread", "home"], ["total", "over"], ["total", "under"], ["moneyline", "away"], ["moneyline", "home"]];
  const TYPE_LABEL = { "spread|away": "Away team spread", "spread|home": "Home team spread", "total|over": "Over", "total|under": "Under",
                       "moneyline|away": "Away team moneyline", "moneyline|home": "Home team moneyline" };

  // ------------------------------------------------------------ GitHub
  const token = () => store.get(TOKEN_KEY, "");
  async function gh(method, path, body) {
    const r = await fetch(API + path, { method, headers: { Authorization: `Bearer ${token()}`, Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) {
      let msg = ""; try { msg = (await r.json()).message || ""; } catch (e) {}
      throw new Error(r.status === 401 ? "GitHub didn't accept the key (expired or pasted wrong). Connect again."
        : r.status === 403 || r.status === 404 ? "The key can't create issues on Boards: give it Issues read and write on that repository."
        : `GitHub said ${r.status} ${msg}`);
    }
    return r.status === 204 ? null : r.json();
  }
  // same layout as the "Log a bet" issue form, so tools/log_bet.py reads it unchanged
  function body(f) {
    const rows = [["Board", f.board], ["Bet", f.pick], ["Line you got", f.line == null ? "" : String(f.line)], ["Odds you got", String(f.odds)],
                  ["Sportsbook", f.book], ["Amount bet ($)", String(f.amount)], ["Game", f.matchup], ["Kickoff", f.kickoff],
                  ["Board play (leave as is)", f.play], ["Bet type (only for a bet that is not a board play)", f.type || "From the board play"],
                  ["Notes", f.notes]];
    return rows.map(([k, v]) => `### ${k}\n\n${v == null || v === "" ? "_No response_" : v}`).join("\n\n");
  }

  // ------------------------------------------------------------ pending (saved here, not yet in mybets.json)
  const pendingAll = () => store.get(PEND_KEY, []);
  function pending(board, published) {
    const have = new Set((published || []).map(b => b.id));
    const keep = pendingAll().filter(b => !have.has(b.id) && Date.now() - b.saved < 2 * 864e5);   // drop recorded / 2-day-old
    store.set(PEND_KEY, keep);
    return keep.filter(b => b.board === board).map(b => Object.assign({ syncing: true }, b));
  }
  const addPending = b => store.set(PEND_KEY, pendingAll().filter(x => x.id !== b.id).concat([Object.assign({ saved: Date.now() }, b)]));
  const dropPending = id => store.set(PEND_KEY, pendingAll().filter(x => x.id !== id));

  // ------------------------------------------------------------ panel
  let root = null;
  function shell(title, inner) {
    close();
    root = document.createElement("div");
    root.className = "lb-overlay";
    root.innerHTML = `<div class="lb-sheet" role="dialog" aria-modal="true" aria-labelledby="lb-title">
      <div class="lb-head"><h2 id="lb-title">${esc(title)}</h2><button class="lb-x" type="button" aria-label="Close">×</button></div>
      <div class="lb-body">${inner}</div></div>`;
    document.body.appendChild(root);
    document.body.classList.add("lb-open");
    root.addEventListener("click", e => { if (e.target === root) close(); });
    root.querySelector(".lb-x").onclick = close;
    document.addEventListener("keydown", onKey);
    setTimeout(() => { const f = root && root.querySelector("select, input, button.lb-primary"); if (f) f.focus(); }, 30);
  }
  const onKey = e => { if (e.key === "Escape") close(); };
  function close() {
    if (root) root.remove();
    root = null;
    document.body.classList.remove("lb-open");
    document.removeEventListener("keydown", onKey);
  }

  // a dropdown with an "Other…" choice that reveals a box to type any value
  function pick(id, label, values, current, fmt, inputmode) {
    const vals = values.slice();
    if (current != null && current !== "" && !vals.some(v => v === current)) vals.push(current);
    if (typeof current === "number") vals.sort((a, b) => a - b);
    return `<label class="lb-field"><span>${label}</span><select id="lb-${id}">${vals.map(v => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(fmt ? fmt(v) : v)}</option>`).join("")}
      <option value="__other">Other…</option></select>
      <input id="lb-${id}-other" class="lb-other" ${inputmode ? `inputmode="${inputmode}"` : ""} placeholder="Type it" hidden></label>`;
  }
  const valueOf = id => { const s = root.querySelector(`#lb-${id}`); if (!s) return null;
    return s.value === "__other" ? root.querySelector(`#lb-${id}-other`).value.trim() : s.value; };

  function connectView(then) {
    shell("Connect the board to GitHub", `
      <p>One time on this phone: the board saves your bets to your GitHub repo, so it needs a key that can only log bets there.</p>
      <ol class="lb-steps">
        <li>Open <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">GitHub → new fine-grained token</a> (signed in).</li>
        <li><b>Token name</b>: Boards bets. <b>Expiration</b>: the longest offered.</li>
        <li><b>Repository access</b>: <i>Only select repositories</i> → <b>Boards</b>.</li>
        <li><b>Permissions</b> → Repository permissions → <b>Issues</b>: <i>Read and write</i>. Nothing else.</li>
        <li><b>Generate token</b>, copy it, paste it below.</li>
      </ol>
      <label class="lb-field"><span>Key</span><input id="lb-token" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="github_pat_…"></label>
      <p class="lb-note">It stays in this browser on this phone; it is never put on the board or sent anywhere except GitHub.</p>
      <div class="lb-err" id="lb-err" hidden></div>
      <div class="lb-actions"><button class="lb-primary" id="lb-save-token" type="button">Connect</button></div>`);
    root.querySelector("#lb-save-token").onclick = async () => {
      const t = root.querySelector("#lb-token").value.trim(), err = root.querySelector("#lb-err");
      if (!/^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$/.test(t)) { err.hidden = false; err.textContent = "That doesn't look like a GitHub key (it starts with github_pat_)."; return; }
      store.set(TOKEN_KEY, t);
      try { await gh("GET", "/issues?per_page=1"); }
      catch (e) { store.set(TOKEN_KEY, ""); err.hidden = false; err.textContent = e.message; return; }
      then();
    };
  }

  // the bet panel: new (from a play) or edit (an existing bet)
  function betView(p, existing, onSaved) {
    if (!token()) return connectView(() => betView(p, existing, onSaved));
    const teaser = p.market === "teaser";
    const type = `${p.market}|${p.side}`;
    const amount = Math.round(+(p.amount || 10));
    const lineVals = []; if (!teaser && p.line != null) for (let x = -10; x <= 10; x++) lineVals.push(Math.round((p.line + x * 0.5) * 2) / 2);
    const typeOpts = teaser ? [] : TYPES.map(([m, s]) => [`${m}|${s}`, m === "total" ? (s === "over" ? "Over" : "Under")
      : `${s === "away" ? p.away || "Away team" : p.home || "Home team"} ${m === "spread" ? "spread" : "moneyline"}`]);
    shell(existing ? "Edit your bet" : "Log your bet", `
      <div class="lb-play"><b>${esc(teaser ? p.teaser.map(l => l.pick).join(" + ") : p.pickLabel || "")}</b>
        <span>${esc(p.matchup || "")}${p.kickoffTxt ? " · " + esc(p.kickoffTxt) : ""}</span>
        ${p.boardTxt ? `<span>Board: ${esc(p.boardTxt)}</span>` : ""}</div>
      ${teaser ? `<p class="lb-note">2-team 6-point teaser: both legs as one bet.</p>`
        : `<label class="lb-field"><span>Bet</span><select id="lb-type">${typeOpts.map(([v, l]) => `<option value="${v}"${v === type ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
           <div id="lb-line-wrap">${pick("line", "Line you got", lineVals, p.line, fmtLine, "decimal")}</div>`}
      ${pick("odds", "Odds you got", ODDS, p.odds, fmtOdds, "numeric")}
      ${pick("book", "Sportsbook", BOOKS, BOOKN[p.book] || p.book || "DraftKings")}
      ${pick("amount", "Amount ($)", AMOUNTS, amount, v => "$" + v, "decimal")}
      <label class="lb-field"><span>Notes (optional)</span><input id="lb-notes" value="${esc(existing ? existing.notes || "" : "")}"></label>
      <div class="lb-err" id="lb-err" hidden></div>
      <div class="lb-actions">
        ${existing ? '<button class="lb-danger" id="lb-remove" type="button">Remove bet</button>' : ""}
        <button class="lb-primary" id="lb-save" type="button">${existing ? "Save changes" : "Save bet"}</button></div>`);
    root.querySelectorAll("select").forEach(s => s.addEventListener("change", () => {
      const o = root.querySelector(`#${s.id}-other`); if (o) { o.hidden = s.value !== "__other"; if (!o.hidden) o.focus(); } }));
    const ts = root.querySelector("#lb-type");
    if (ts) ts.addEventListener("change", () => { root.querySelector("#lb-line-wrap").hidden = ts.value.startsWith("moneyline"); });
    if (ts && type.startsWith("moneyline")) root.querySelector("#lb-line-wrap").hidden = true;
    const err = m => { const e = root.querySelector("#lb-err"); e.hidden = !m; e.textContent = m || ""; };

    root.querySelector("#lb-save").onclick = async () => {
      const [m, s] = teaser ? ["teaser", null] : ts.value.split("|");
      const line = teaser || m === "moneyline" ? null : parseFloat(String(valueOf("line")).replace("PK", "0").replace("−", "-"));
      const odds = parseInt(String(valueOf("odds")).replace("−", "-").replace("+", ""), 10);
      const amt = parseFloat(String(valueOf("amount")).replace("$", ""));
      const book = valueOf("book") || "";
      if (!teaser && m !== "moneyline" && isNaN(line)) return err("Pick or type the line you got.");
      if (isNaN(odds) || Math.abs(odds) < 100) return err("Odds should look like -110 or +105.");
      if (!(amt > 0)) return err("Pick or type the amount.");
      if (!book) return err("Pick the sportsbook.");
      const same = !teaser && m === p.market && s === p.side;
      const team = s === "home" ? p.home : p.away;
      const pickTxt = teaser ? p.teaser.map(l => l.pick).join(" + ") : m === "total" ? `${s === "over" ? "Over" : "Under"} ${line}`
        : m === "moneyline" ? `${team} ML` : `${team} ${fmtLine(line)}`;
      const f = { board: p.board, pick: pickTxt, line, odds, book, amount: amt, matchup: p.matchup, kickoff: p.kickoff,
                  play: teaser || same ? p.play : `game:${p.game_id}`, type: teaser || same ? "" : TYPE_LABEL[`${m}|${s}`],
                  notes: root.querySelector("#lb-notes").value.trim() };
      const btn = root.querySelector("#lb-save"); btn.disabled = true; btn.textContent = "Saving…"; err("");
      try {
        const issue = existing
          ? await gh("PATCH", `/issues/${existing.id}`, { title: `Bet (${p.board}): ${pickTxt}`, body: body(f) })
          : await gh("POST", "/issues", { title: `Bet (${p.board}): ${pickTxt}`, body: body(f) });
        addPending({ id: issue.number, issue: issue.html_url, board: p.board, pick: pickTxt, line, odds, book, amount: amt, play: f.play,
                     market: teaser ? "teaser" : m, side: teaser ? null : s, game_id: p.game_id, matchup: p.matchup, kickoff: p.kickoff,
                     legs: teaser ? p.teaser : undefined, board_line: same ? p.boardLine : null, notes: f.notes,
                     logged: existing ? existing.logged : new Date().toISOString() });
        root.querySelector(".lb-body").innerHTML = `<p class="lb-ok">✓ Saved: <b>${esc(pickTxt)}</b> ${esc(fmtOdds(odds))} at ${esc(book)}, $${amt}.</p>
          <p class="lb-note">It's on My Bets now; GitHub records it within a minute or two (shown as "saving" until then).</p>
          <div class="lb-actions"><button class="lb-primary" id="lb-done" type="button">Done</button></div>`;
        root.querySelector("#lb-done").onclick = () => { close(); onSaved && onSaved(); };
        root.querySelector("#lb-done").focus();
      } catch (e) { btn.disabled = false; btn.textContent = existing ? "Save changes" : "Save bet"; err(e.message); }
    };
    const rm = root.querySelector("#lb-remove");
    if (rm) rm.onclick = async () => {
      if (!confirm("Remove this bet from My Bets?")) return;
      rm.disabled = true; rm.textContent = "Removing…";
      try {
        await gh("POST", `/issues/${existing.id}/comments`, { body: "remove" });
        dropPending(existing.id);
        store.set("boards-removed-bets", store.get("boards-removed-bets", []).concat([existing.id]));
        close(); onSaved && onSaved();
      } catch (e) { rm.disabled = false; rm.textContent = "Remove bet"; err(e.message); }
    };
  }

  function edit(b, onSaved) {
    const [away, home] = String(b.matchup || "").split(/ @ | vs /);
    betView({ board: b.board, play: b.play, game_id: b.game_id, matchup: b.matchup, away, home, kickoff: b.kickoff, kickoffTxt: "",
              market: b.market || "spread", side: b.side || "away", line: b.line, odds: b.odds, book: b.book, amount: b.amount,
              teaser: b.legs, pickLabel: b.pick, boardLine: b.board_line,
              boardTxt: b.board_line != null ? `${fmtLine(b.board_line)}` : "" }, b, onSaved);
  }
  const removed = () => new Set(store.get("boards-removed-bets", []));

  // every board card's button carries data-take (a play registered in window.__takes) or data-edit (window.__edits)
  document.addEventListener("click", e => {
    const t = e.target.closest && e.target.closest("[data-take], [data-edit]");
    if (!t) return;
    e.preventDefault();
    if (t.dataset.take && window.__takes && window.__takes[t.dataset.take]) betView(window.__takes[t.dataset.take], null, window.__afterLog);
    if (t.dataset.edit && window.__edits && window.__edits[t.dataset.edit]) edit(window.__edits[t.dataset.edit], window.__afterLog);
  });

  window.LogBet = { open: (p, onSaved) => betView(p, null, onSaved), edit, pending, removed, connected: () => !!token(),
                    disconnect: () => store.set(TOKEN_KEY, "") };
})();
