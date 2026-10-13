"""NFL cloud setup: download the model's history, or build the play-by-play tables from it.

  python bootstrap.py          download anything missing: nflverse schedule + play-by-play 2006-2026 + 2026 roster
                               (public GitHub releases), Action Network scoreboards 2017-2026, SportsbookReview
                               archive pages 2007-21 (opening/closing lines). Kept in the Actions cache, not the repo.
  python bootstrap.py tables   build data/tg, qb, rz.parquet from all play-by-play (the engine's daily refresh only
                               rebuilds the current season and splices it in, so the full tables must exist first)."""
import sys
import pull_data

RAW = pull_data.RAW

if sys.argv[1:] == ["tables"]:
    import build
    build.main("pbp")
    sys.exit(0)

pull_data.games(False)
pull_data.pbp(False)
pull_data.get(f"{pull_data.NV}/rosters/roster_2026.parquet", RAW / "roster_2026.parquet")
pull_data.an(False)
sbr = RAW / "sbr"
sbr.mkdir(exist_ok=True)
for y in range(2007, 2022):
    pull_data.get(f"https://www.sportsbookreviewsonline.com/scoresoddsarchives/nfl-odds-{y}-{str(y + 1)[2:]}/",
                  sbr / f"nfl_{y}.html")
missing = [y for y in range(2006, 2027) if not (RAW / f"pbp_{y}.parquet").exists()] + \
          [y for y in range(2007, 2022) if not (sbr / f"nfl_{y}.html").exists()]
if missing:
    sys.exit(f"bootstrap: still missing {missing}")
print("bootstrap: history complete")
