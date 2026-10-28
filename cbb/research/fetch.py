"""Download daily D1 men's college basketball scoreboards for past seasons, raw and gzipped, never re-fetched.

  Action Network v1  per-book odds rows (15 consensus, 30 opener, 68 DK, 69 FD, 75 BetMGM, 71/79 other books);
                     for past dates the sportsbook rows hold the closing line. Scores in boxscore.
  ESPN               neutral-site flag, venue, conference-game flag, tournament notes (Action has no neutral flag).

Usage: python cbb/research/fetch.py [first_season[,last_season]]   seasons named by start year (2024 = 2024-25)
"""
import datetime as dt, gzip, pathlib, sys, threading, time
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
H = {"User-Agent": "Mozilla/5.0"}
SRC = {
    "an": ("https://api.actionnetwork.com/web/v1/scoreboard/ncaab",
           lambda d: {"bookIds": "15,30,68,69,71,75,79", "date": d, "division": "D1"}),
    "espn": ("https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
             lambda d: {"dates": d, "groups": 50, "limit": 500}),
}


def days(season):
    d, end = dt.date(season, 11, 1), min(dt.date(season + 1, 4, 10), dt.date.today() - dt.timedelta(days=1))
    while d <= end:
        yield d.strftime("%Y%m%d")
        d += dt.timedelta(days=1)


def pull(src, seasons, pause=1.2):
    url, params = SRC[src]
    out = ROOT / "data" / f"{src}_raw"
    out.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        n = 0
        for d in days(season):
            f = out / f"{d}.json.gz"
            if f.exists():
                continue
            for attempt in range(4):
                try:
                    r = requests.get(url, params=params(d), headers=H, timeout=60)
                    r.raise_for_status()
                    break
                except Exception as e:
                    print(src, "retry", d, e, flush=True)
                    time.sleep(10 * (attempt + 1))
            else:
                continue
            f.write_bytes(gzip.compress(r.content))
            n += 1
            time.sleep(pause)
        print(src, season, "fetched", n, "days", flush=True)


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else [2021, 2025]
    seasons = range(a[0], (a[1] if len(a) > 1 else a[0]) + 1)
    th = [threading.Thread(target=pull, args=(s, seasons)) for s in SRC]
    [t.start() for t in th]
    [t.join() for t in th]
    print("done")
