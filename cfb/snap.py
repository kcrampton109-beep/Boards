"""Save a compact snapshot of every book's NCAAF lines + bet%/money% splits (Action Network).
Runs from Windows Task Scheduler every 30 min; Sun-Thu it only keeps the top-of-hour run."""
import csv, datetime as dt, gzip, io, pathlib, sys, time
import requests

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "snapshots"
URL = "https://api.actionnetwork.com/web/v2/scoreboard/ncaaf"
BOOKS = "15,30,68,69,75,71,79,123"
COLS = ["ts", "div", "game_id", "start", "status", "away_id", "home_id", "away", "home", "book",
        "period", "market", "side", "team_id", "value", "odds", "tickets_pct", "money_pct"]

now = dt.datetime.now()
if now.weekday() not in (4, 5) and now.minute >= 30 and "--force" not in sys.argv:
    sys.exit(0)
ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
day = OUT / now.strftime("%Y-%m-%d")
day.mkdir(parents=True, exist_ok=True)
for div in ("FBS", "FCS"):
    games, err = None, None
    for attempt in range(3):  # a run that fires right after the laptop wakes can beat the network
        try:
            r = requests.get(URL, params={"bookIds": BOOKS, "periods": "event,firsthalf", "division": div},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            r.raise_for_status()
            games = r.json().get("games", [])
            break
        except Exception as e:
            err = e
            time.sleep(30)
    if games is None:
        (day / "errors.log").open("a").write(f"{ts}\t{div}\t{err}\n")
        continue
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLS)
    for g in games:
        names = {t["id"]: t.get("full_name") for t in g.get("teams", [])}
        base = [ts, div, g["id"], g.get("start_time"), g.get("status"), g.get("away_team_id"),
                g.get("home_team_id"), names.get(g.get("away_team_id")), names.get(g.get("home_team_id"))]
        for book, per_map in (g.get("markets") or {}).items():
            for period, mk in per_map.items():
                for market, outs in mk.items():
                    for o in outs:
                        bi = o.get("bet_info") or {}
                        w.writerow(base + [book, period, market, o.get("side"), o.get("team_id"),
                                           o.get("value"), o.get("odds"),
                                           (bi.get("tickets") or {}).get("percent"),
                                           (bi.get("money") or {}).get("percent")])
    (day / f"{now:%H%M}_{div}.csv.gz").write_bytes(gzip.compress(buf.getvalue().encode()))
