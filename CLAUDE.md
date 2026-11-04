# Boards: CFB + NFL betting models (paper + small real stakes), CBB paper board

Owner bets $10 units at DraftKings / FanDuel / BetMGM on STRONG (+FCS, WEATHER for CFB) plays; every other tier
stays tracked on the boards. Both models' edge is beating OPENING lines (≈50% vs close), so timing and CLV
matter more than win-loss; judge changes by CLV first. Ask the owner before changing tiers, thresholds or stakes.

## Where things run
- **GitHub Actions is the live system** (since 2026-10-13). The laptop copies (C:/Users/kcram/cfb, C:/Users/kcram/nfl)
  are backups; their Windows tasks and Claude sync tasks are being retired.
- Site (GitHub Pages, branch `site`, single force-pushed commit): https://kcrampton109-beep.github.io/Boards/
  (`/cfb/`, `/nfl/`, workbooks `cfb/CFB_Model_2026.xlsx`, `nfl/NFL_weekly.xlsx`).
- Alerts: ntfy topic `boards-pk9lpcuxti4v` (owner's phone). Strategy doc (Claude Docs):
  "College Football and NFL Models: What We Tested and What Works" https://claude.ai/code/artifact/e92915ad-1ea5-45e1-9337-adadb2a3c904

| Workflow | When (cron is UTC) | What |
| --- | --- | --- |
| `cfb.yml` | :07 and :37 hourly + every 15 min Sun 8-14 UTC (openers); plan step: snapshots 30 min Fri/Sat else hourly | snapshot, engine, ledger commit, publish, ntfy |
| `cfb-weekly.yml` | Sun 8:45 AM ET (12:45 UTC EDT / 13:45 EST, gate job picks) | forced refresh, report + workbook, ntfy report |
| `nfl.yml` | :17 and :47 + :02/:32 Tue 18-21 UTC (FanDuel posts next week ~2:40 PM ET Tuesday) | snapshot, engine, ledger commit, publish, ntfy |
| `nfl-weekly.yml` | Tue 9:30 AM ET (13:30 / 14:30 UTC, gate) | recap + workbook, ntfy |
| `workbooks.yml` | Mon 9 AM ET (13:00 / 14:00 UTC, gate) | both workbooks rebuilt from the published boards |
| `cbb.yml` | :12 and :42, but :42 skipped 1-9 PM ET; Oct 25 - Apr 12 only | CBB paper board: fetch, refit, ledger commit, publish (no ntfy) |
| `post.yml` | 9 AM ET daily (13:00 / 14:00 UTC, gate) | social post images + caption → site `/social/`, first image to ntfy |
| `net-check.yml` | manual | can GitHub reach every data source? |

GitHub skips some scheduled runs under load (~1 in 8 in week 1), so gaps of ~1 h happen. Keep Pages publishes under
~10 an hour (GitHub's soft limit for branch-based Pages builds): each engine run publishes once.

When lines post (measured Oct 2026): NFL DraftKings + opener right after Monday Night Football (Tue 4-10 AM ET),
FanDuel Tue ~2:40 PM ET, BetMGM appears in the feed late in the week. CFB: Action Network flips to the new week about
5 AM Sunday; the engine also fetches next week once no games are left to play (Saturday night).

## Board rules worth knowing
- **Bets tab** (both pages) is drawn by the shared `site/bets.js` + `site/bets.css` (published at the site root, loaded as
  `../bets.js`) so both boards look identical; each page only normalises its data and calls `Bets.render`. Money tiers:
  CFB STRONG, FCS, WEATHER; NFL STRONG, WEATHER + 2-team teasers (legs paired in logging order, different games).
  Change the `BET` list at the top of each page's script if the owner changes what they bet; `UNIT = 10` is $ per unit.
  Every bet shows a playable-to number (ledger `limit`, stored since Oct 13; older entries rebuild it from the logged model).
- **Today + Results tabs** (both pages): shared `site/track.js` + `site/track.css`. Each page builds `trackRows` from the
  full ledger (`ledger.json`, published next to `state.json` by `sync_prep.py` / `weekly.py`; state.json only carries recent
  entries) placed on the game's Eastern date (CFB `kickoff`, NFL `kickoff_iso`; NFL teaser legs are paired into 2-team
  teaser rows exactly like the engine's record). Results = tier chips, period tiles, month calendar (green/red by units,
  signed value always printed), by-week table; tap a day/week for its plays. Prefix shared class names (`b-`, `t-`,
  `trk-`): the pages' own `.rec`, `.empty`, `.tag` rules leak into generic names.
- **My Bets** (owner's real bets): "Log bet" on Bets/Today cards opens an in-board panel (`site/logbet.js` + `logbet.css`):
  dropdowns for bet side, line, odds, book, amount (each with "Other…"), saved by creating a GitHub issue through the API
  with the owner's fine-grained token (this repo, Issues read/write) kept in the phone's localStorage (never in the repo).
  The issue body uses the issue-form layout (`.github/ISSUE_TEMPLATE/bet.yml`, still usable by hand). `.github/workflows/mybets.yml`
  acts only on the owner's issues/comments (public repo), runs `tools/log_bet.py` (one file per bet: `<board>/board/mybets/<issue>.json`,
  linked to the ledger entry by `play` id, `game:<id>` when the side differs from the board play), replies, closes the issue and
  republishes `mybets.json`. Edit = PATCH the issue, Remove = a `remove` comment. Saved bets show at once as "pending"
  (localStorage) until `mybets.json` has them. Engines write `board/scores.json` (finals + closing lines); `site/mybets.js`
  grades at the owner's line/odds. Results has a "My bets" chip. Never prefill the GitHub form through the URL: it turns "+" into a space.
- **Wind unders** (both boards): forecast 18+ mph within 24 h of kickoff (owner's call, Oct 19; was 15+ mph up to 2-4 days
  out). CFB logs 15+ mph or 24-48 h as near-miss RESEARCH.
- Pages hide inactive tab panels with `hidden`; keep the `[hidden] { display: none !important }` rule (panel CSS beats it).
- **NFL look-ahead**: books post the following week's lines ~10 days early (DK right after Monday night, FD Tuesday
  afternoon); they move a lot and often aren't on the owner's apps. A next-week game counts only once 3/4 of this week's
  games have started (Sunday evening) AND 2+ of DK/FD/MGM agree within 1.5 pts. Before that its plays show labelled
  "Look-ahead" but are never logged, alerted or put on the Bets tab (open entries on such games are voided).
- **NFL strong spreads: early prices only** (owner's call, Nov 4; the owner bets only Strong): a next-week strong play logs
  and alerts as soon as 2+ of DK/FD/MGM agree within 1.5 pts (no wait for Sunday evening; open strong entries are
  never voided by the look-ahead rule), and a gap counts as strong only at an early-week price (`is_early`: Mon/Tue ET
  or 3.5+ days before kickoff, `RULES["early_days"]`); a gap first seen later logs as a lean, unless that side already
  has a strong entry. Why: live wk 6 plays logged on Tuesday look-ahead lines beat the close by 3.5 pts (all 10), CLV
  fell to +0.4-1.4 once logging waited for Sunday; a week-stale model kept its opener edge (AN 2020-25 3+: 65.5% vs
  64.7%); against the close the same picks were 52-53%.
- **NFL TD props** (Nov 4, owner chose the sportsbook feeds): Action Network's props endpoint has returned empty
  `player_props` since ~Oct 8 2026 and ESPN's propBets carry no odds, so anytime-TD prices come from FanDuel's public
  API (`live.fd_events` + `live.fd_anytime_td`, the read-only JSON its site loads, no login). DraftKings' feed answers
  automated requests with Access Denied (bot protection): don't try to get around it. A TD value play also needs the
  model's chance <= 1.75x the book's (`td_max_ratio`): bigger gaps were role changes the usage shares missed.
- **Logging bets on look-ahead plays** (Oct 28, owner's request): look-ahead cards stay display-only (never logged or
  alerted), but each spread/total look-ahead card on the NFL Plays tab has a "Log bet" button. The bet is saved with
  play `look:<play id>` (`tools/log_bet.py` → tier LOOKAHEAD, "Look-ahead" on My Bets), tied to the game and graded
  from scores.json; `mineOf` also matches `look:<id>`, so the later regular play shows "You bet".
- **NFL book agreement**: a book whose spread is 1.5+ pts off the median of DK/FD/MGM is ignored for prices (Oct 20:
  DK and FD next-week lines up to 5 pts apart).
- **CFB stale lines**: a book 3+ points off consensus is ignored (`RULES["stale_pts"]`), checked through either side of
  the spread; a book whose two spread sides don't mirror is dropped, and a 20+ point model gap is treated as a bad line
  (`max_edge`). Oct 25: BetMGM listed one side of Notre Dame vs Navy and it was logged as Notre Dame +30.5 (voided).
- **CFB strong tier** (owner's call, Oct 26): FBS gap 7+ in weeks 1-6, 5+ from week 7 (`strong_fbs_late`, `late_from_week`),
  and only at early-week prices (`is_early`: Sunday/Monday or 3.5+ days before kickoff); a later gap logs as a lean.
  FCS-vs-FCS (5+) has no timing rule: its lines post midweek.
- **Graduation** (owner's call, Nov 4): CFB FCS graduated to a proven real-money tier (66 plays, 44-22, CLV +1.5, beat close
  70%). 1 unit stays $10 and FCS stays 1u on every play (2021-25: FCS gaps 7+ won 58.8%, 5-7 won 57.5%, so no edge sizing).
  FBS Strong stays on trial at $10 until 20 plays under the 5-point rule are graded (CLV +0.5, beat close 55%); NFL Strong
  until 40 plays. `tools/checkin.py` scorecards track both; raise graduation as the owner's decision, never change stakes.
- **CBB paper board** (built Oct 28 2026, `/cbb/`): paper only, flat 1u, no alerts, no Bets/My Bets tab. Model = blend of
  a results ridge and a market-ratings ridge (fit to earlier games' closing lines), `cbb/research/REPORT.md`. Tiers
  (`RULES`): spreads 3-4 / 4+, totals 5-6 / 6+, November games in their own tier; spread gaps 12+ not logged. ESPN
  supplies the game list, neutral flag and conferences (Action's conference labels are current, not historical).
  `board/seen_pending.json` + `first_seen.jsonl` record each book's first number vs Action's opener (Openers tab): the
  backtest's CLV was from that opener, so this decides whether the edge is bettable. Money tiers are the owner's call.
  Season rollover (July): the hist cache key is per season, so the first run re-runs `cbb/bootstrap.py` (adds last season).
  ESPN can swap home/away after a game is listed (neutral sites): spreads are graded by the backed team (`team_eid`, inferred
  from the pick for entries before Nov 4) and every settled play is re-graded each run; first-seen spreads are reoriented.
  Past days are re-fetched until `day_final` (`data/season/final_days.json`). `track.js` takes optional `scope` / `tier`.
- **CBB rules since Nov 4 2026** (owner's call, `cbb/research/EDGE.md`): blend margins x `margin_scale` (1.063, ridge
  shrinkage; `bootstrap.py` refits it into hist.json) and totals minus the model's average gap to opening totals over 7 days
  (`total_level`). November main tiers: NOV_FAV (favourite, spread gap 4+) and NOV_OVER (over, total gap 5+); everything
  else in November is NOV research (also holds the Nov 1-4 plays from the old model). Dec on: SPREAD 4+ / SPREAD_LEAN 3-4,
  TOTAL 6+ / TOTAL_LEAN 5-6. Owner won't bet CBB until the main plays show positive ROI.

## Social post (faceless picks account, started Nov 4 2026)
- Owner is building an audience for a possible paid early-alert tier next season: free game-day posts now, no selling yet.
- `social/daily_post.py` (run by `post.yml` from the boards as published): on a day with money-tier plays (CFB STRONG/FCS/
  WEATHER, NFL STRONG/WEATHER; no teasers) it shows each play's logged line vs the current consensus line, so the free
  post shows edge already captured and never an early number; otherwise the previous day's results. 1080-wide PNGs
  (up to 4, balanced), caption under 280 chars, phone page `/social/index.html` (long-press save, copy caption).
- `BRAND` ("Opener Lab"), `HANDLE`, `LINK` at the top of the script are placeholders until the owner names the account.
  Posting to X is manual (owner). Push runs publish but never send the phone alert.

## Data (public repo: no third-party data committed)
- Committed: code, `*/board/` ledgers + state the engine needs between runs, `cfb/data/calls.log`.
- Actions cache: `cfb-raw-v1` / `nfl-raw-v1` (history, downloaded by `*/bootstrap.py` on a miss; CFB ≈106 CFBD
  calls of 1,000/month), `*-work-*` (regenerated tables), `*-snap-*` (line snapshots). CBB: `cbb-hist-<season>`
  (past seasons' scoreboards + derived prior, ~35 min to rebuild), `cbb-season-<season>-*` (this season's files, saved daily).
- Secret `CFBD_KEY` (CollegeFootballData). NFL needs no key.

## Making changes
1. `git pull --rebase` first: the bot commits board updates every run.
2. Edit, commit (end message with the Co-Authored-By line), push. Pushes touching code paths trigger a run at once.
3. Run logs are owner-only. Read results without auth: `GET /repos/kcrampton109-beep/Boards/actions/runs`, then
   `/actions/runs/<id>/jobs` (step results) and `/check-runs/<job id>/annotations` (engine errors are emitted as
   `::error` annotations).
4. Each board has its own concurrency group (`cfb`, `nfl`, `workbooks`). Never share one group across boards:
   GitHub cancels a pending run when another queues in the same group.
5. `tools/publish_site.sh` must keep other boards' folders (it archives the whole `site` tree and pushes with a lease).

## Weekly check-in
Claude scheduled task `boards-weekly-checkin` (Mondays) runs `python tools/checkin.py`, sends the summary + both
workbooks, and updates the strategy doc's live-results tables and change log.
