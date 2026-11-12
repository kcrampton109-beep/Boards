"""Download daily D1 men's college basketball scoreboards for past seasons, raw and gzipped, never re-fetched.

  Action Network     per-book odds rows (15 consensus, 30 opener, 68 DK, 69 FD, 75 BetMGM, 71/79 other books);
                     for past dates the sportsbook rows hold the closing line. Scores in boxscore.
  ESPN               neutral-site flag, venue, conference-game flag, tournament notes (Action has no neutral flag).

Action's v1 scoreboard (what the history was first saved from) has answered 404 for every sport since Nov 10 2026,
so Action is read from v2 and converted to the v1 layout (an_v2_as_v1): files saved before and after look the same to
build.py and the engine.

Usage: python cbb/research/fetch.py [first_season[,last_season]]   seasons named by start year (2024 = 2024-25)
"""
import datetime as dt, gzip, json, pathlib, sys, threading, time
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
H = {"User-Agent": "Mozilla/5.0"}
SRC = {
    "an": ("https://api.actionnetwork.com/web/v2/scoreboard/ncaab",
           lambda d: {"bookIds": "15,30,68,69,71,75,79", "periods": "event", "date": d, "division": "D1"}),
    "espn": ("https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
             lambda d: {"dates": d, "groups": 50, "limit": 500}),
}


def _pct(o, key):
    """Ticket / money share from a v2 outcome; 0 and 100 are what Action shows before any bets are counted on that
    market, so they are treated as missing."""
    v = ((o.get("bet_info") or {}).get(key) or {}).get("percent")
    return None if v in (0, 100) else v


def an_v2_as_v1(content):
    """v2 scoreboard JSON -> the v1 layout: one 'odds' row per book (type 'game') with spread_home/away, their odds,
    total, over/under odds, moneylines and the consensus ticket / money shares."""
    out = []
    for g in json.loads(content).get("games", []):
        odds = []
        for b, per in (g.get("markets") or {}).items():
            ev = (per or {}).get("event") or {}
            row = dict(book_id=int(b), type="game")
            for o in ev.get("spread") or []:
                s = o.get("side")
                if s in ("home", "away"):
                    row[f"spread_{s}"], row[f"spread_{s}_line"] = o.get("value"), o.get("odds")
                    row[f"spread_{s}_public"], row[f"spread_{s}_money"] = _pct(o, "tickets"), _pct(o, "money")
            for o in ev.get("total") or []:
                s = o.get("side")
                if s in ("over", "under"):
                    row["total"], row[s] = o.get("value"), o.get("odds")
                    row[f"total_{s}_public"], row[f"total_{s}_money"] = _pct(o, "tickets"), _pct(o, "money")
            for o in ev.get("moneyline") or []:
                if o.get("side") in ("home", "away"):
                    row[f"ml_{o['side']}"] = o.get("odds")
            if b == "15":
                row["num_bets"] = g.get("num_bets")
            if len(row) > 2:
                odds.append(row)
        keep = ("id", "status", "type", "start_time", "home_team_id", "away_team_id", "winning_team_id", "teams",
                "boxscore", "num_bets")
        out.append(dict({k: g.get(k) for k in keep}, odds=odds))
    return json.dumps({"games": out, "source": "action v2"}).encode()


CONVERT = {"an": an_v2_as_v1}


def get(src, d, timeout=60):
    url, params = SRC[src]
    r = requests.get(url, params=params(d), headers=H, timeout=timeout)
    r.raise_for_status()
    return CONVERT.get(src, lambda c: c)(r.content)


def days(season):
    d, end = dt.date(season, 11, 1), min(dt.date(season + 1, 4, 10), dt.date.today() - dt.timedelta(days=1))
    while d <= end:
        yield d.strftime("%Y%m%d")
        d += dt.timedelta(days=1)


def pull(src, seasons, pause=1.2):
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
                    content = get(src, d)
                    break
                except Exception as e:
                    print(src, "retry", d, e, flush=True)
                    time.sleep(10 * (attempt + 1))
            else:
                continue
            f.write_bytes(gzip.compress(content))
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
