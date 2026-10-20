# Boards: CFB + NFL betting models (paper + small real stakes)

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
- **NFL look-ahead**: a next-week game counts once 2 of DK/FD/MGM have posted it (DraftKings posts look-ahead lines
  days early). Before that its plays are shown labelled "Look-ahead" but never logged, alerted or put on the Bets tab.
- **CFB stale lines**: a book 3+ points off consensus is ignored (`RULES["stale_pts"]`).

## Data (public repo: no third-party data committed)
- Committed: code, `*/board/` ledgers + state the engine needs between runs, `cfb/data/calls.log`.
- Actions cache: `cfb-raw-v1` / `nfl-raw-v1` (history, downloaded by `*/bootstrap.py` on a miss; CFB ≈106 CFBD
  calls of 1,000/month), `*-work-*` (regenerated tables), `*-snap-*` (line snapshots).
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
