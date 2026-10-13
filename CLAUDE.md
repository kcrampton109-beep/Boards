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
| `cfb.yml` | :07 and :37 hourly; plan step applies ET rules (engine every 30 min Sun/Mon else hourly; snapshots 30 min Fri/Sat) | snapshot, engine, ledger commit, publish, ntfy |
| `cfb-weekly.yml` | Sun 12:45 | forced refresh, report + workbook, ntfy report |
| `nfl.yml` | :17 and :47 | snapshot, engine, ledger commit, publish, ntfy |
| `nfl-weekly.yml` | Tue 13:30 | recap + workbook, ntfy |
| `workbooks.yml` | Mon 13:00 | both workbooks rebuilt from the published boards |
| `net-check.yml` | manual | can GitHub reach every data source? |

Weekly crons are fixed UTC: after Nov 1 (EST) they fire an hour earlier in Eastern time.

## Board rules worth knowing
- **Bets tab** (both pages): the money tiers only. CFB: STRONG, FCS, WEATHER. NFL: STRONG, WEATHER, TEASER legs.
  Change the `BET` list at the top of each page's script (`cfb/board/index.html`, `nfl/page/index.html`) if the owner
  changes what they bet. `UNIT = 10` is the dollar size of a unit.
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
