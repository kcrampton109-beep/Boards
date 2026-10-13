"""Save a compact snapshot of NFL lines every 30 minutes (Windows Task Scheduler):
  Action Network: every book's spread / total / moneyline + consensus bet % and money % (books 15 consensus,
                  30 open, 68 DraftKings, 69 FanDuel, 75 BetMGM)
  CBS Sports odds page: the open and current line CBS shows (FanDuel) - the user's RLM source
Snapshots are the record of line movement used for RLM flags and closing lines."""
import csv, datetime as dt, gzip, io, pathlib, sys, time
import requests

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "snapshots"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
AN = "https://api.actionnetwork.com/web/v2/scoreboard/nfl"
BOOKS = "15,30,68,69,75"
COLS = ["ts", "game_id", "week", "start", "status", "away", "home", "book", "market", "side", "value", "odds", "tickets_pct",
        "money_pct"]
SEASON = 2026
WEEKS = []   # filled by an_rows: the current week and the next one


def get(url, **kw):
    err = None
    for attempt in range(3):   # a run right after the laptop wakes can beat the network
        try:
            r = requests.get(url, headers=H, timeout=60, **kw)
            r.raise_for_status()
            return r
        except Exception as e:
            err = e
            time.sleep(20)
    raise err


def an_rows(ts):
    games = get(AN, params={"bookIds": BOOKS, "periods": "event"}).json().get("games", [])
    weeks = sorted({g.get("week") for g in games if g.get("week")})
    if weeks:
        nxt = get(AN, params={"bookIds": BOOKS, "periods": "event", "season": SEASON, "week": weeks[-1] + 1}).json().get("games", [])
        games += nxt
        WEEKS[:] = weeks + [weeks[-1] + 1]
    rows = []
    for g in games:
        ab = {t["id"]: t.get("abbr") for t in g.get("teams", [])}
        base = [ts, g["id"], g.get("week"), g.get("start_time"), g.get("status"), ab.get(g.get("away_team_id")),
                ab.get(g.get("home_team_id"))]
        for book, per in (g.get("markets") or {}).items():
            for market, outs in (per.get("event") or {}).items():
                for o in outs:
                    bi = o.get("bet_info") or {}
                    rows.append(base + [book, market, o.get("side"), o.get("value"), o.get("odds"),
                                        (bi.get("tickets") or {}).get("percent"), (bi.get("money") or {}).get("percent")])
    return rows


def cbs_rows(ts):
    import cbs
    rows = []
    for w in WEEKS or [None]:
        url = f"https://www.cbssports.com/nfl/odds/{SEASON}/regular/week-{w}/" if w and w <= 18 else "https://www.cbssports.com/nfl/odds/"
        rows += [[ts] + r for r in cbs.parse(get(url).text)]
    return rows


def main():
    now = dt.datetime.now()
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    day = OUT / now.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    for name, fn, cols in (("an", an_rows, COLS), ("cbs", cbs_rows, ["ts"] + __import__("cbs").COLS)):
        try:
            rows = fn(ts)
        except Exception as e:
            (day / "errors.log").open("a").write(f"{ts}\t{name}\t{e}\n")
            continue
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        w.writerows(rows)
        (day / f"{now:%H%M}_{name}.csv.gz").write_bytes(gzip.compress(buf.getvalue().encode()))


if __name__ == "__main__":
    main()
