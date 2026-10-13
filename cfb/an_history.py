"""Download Action Network NCAAF scoreboards (v1: per-book odds rows incl. open, consensus
public%/money%, 1H/quarter lines) for past seasons. Raw responses saved gzipped, never re-fetched."""
import gzip, json, pathlib, sys, time
import requests

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "data" / "an_raw"
OUT.mkdir(parents=True, exist_ok=True)
BOOKS = "15,30,68,69,75,71,79,123"
URL = "https://api.actionnetwork.com/web/v1/scoreboard/ncaaf"
H = {"User-Agent": "Mozilla/5.0"}

seasons = [int(s) for s in sys.argv[1].split(",")] if len(sys.argv) > 1 else list(range(2017, 2027))
for season in seasons:
    for div in ("FBS", "FCS"):
        for week in range(0, 18):
            f = OUT / f"{season}_{div}_w{week:02d}.json.gz"
            if f.exists():
                continue
            for attempt in range(3):
                try:
                    r = requests.get(URL, params={"bookIds": BOOKS, "season": season, "week": week,
                                                  "division": div}, headers=H, timeout=60)
                    r.raise_for_status()
                    break
                except Exception as e:
                    print("retry", season, div, week, e, flush=True)
                    time.sleep(10)
            else:
                continue
            d = r.json()
            g = d.get("games", [])
            n_odds = sum(1 for x in g if x.get("odds"))
            f.write_bytes(gzip.compress(r.content))
            print(season, div, week, "games", len(g), "with odds", n_odds, flush=True)
            time.sleep(1.5)
print("done")
