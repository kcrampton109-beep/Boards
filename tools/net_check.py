"""Can GitHub's servers reach every data source the CFB and NFL models use?

Makes the same requests the laptop engines make and prints one line per source:
OK / BLOCKED with the HTTP status and a sanity count (games, markets, rows).
No API keys are needed: CFBD is checked for reachability only (401 without a key = reachable)."""
import builtins, os, sys
import requests

if os.environ.get("GITHUB_ACTIONS"):  # also post each line as a run annotation (readable without signing in)
    _print = builtins.print
    def print(*a, **k):
        _print(*a, **k)
        line = " ".join(map(str, a)).strip()
        if line:
            _print(f"::notice title=net check::{line}")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
AN = "https://api.actionnetwork.com/web"


def an_games(r):
    g = r.json().get("games", [])
    priced = sum(bool(x.get("markets")) for x in g)
    return f"{len(g)} games, {priced} with book prices"


CHECKS = [
    ("Action Network CFB (FBS)", f"{AN}/v2/scoreboard/ncaaf",
     dict(params={"bookIds": "15,30,68,69,75", "periods": "event", "division": "FBS"}), an_games),
    ("Action Network CFB (FCS)", f"{AN}/v2/scoreboard/ncaaf",
     dict(params={"bookIds": "15,30,68,69,75", "periods": "event", "division": "FCS"}), an_games),
    ("Action Network NFL", f"{AN}/v2/scoreboard/nfl",
     dict(params={"bookIds": "15,30,68,69,75", "periods": "event"}), an_games),
    ("Action Network NFL history (v1)", f"{AN}/v1/scoreboard/nfl",
     dict(params={"bookIds": "15,30", "season": 2025, "week": 1}), an_games),
    ("CBS NFL odds page", "https://www.cbssports.com/nfl/odds/", {},
     lambda r: f"{len(r.text) // 1024} KB page"),
    ("ESPN NFL injuries", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries", {},
     lambda r: f"{len(r.json().get('injuries', []))} teams"),
    ("Open-Meteo forecast", "https://api.open-meteo.com/v1/forecast",
     dict(params={"latitude": 40.0, "longitude": -83.0, "hourly": "wind_speed_10m", "forecast_days": 2}),
     lambda r: f"{len(r.json().get('hourly', {}).get('time', []))} hours"),
    ("nflverse games", "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv", {},
     lambda r: f"{r.text.count(chr(10))} rows"),
    ("CFBD (reachability only)", "https://api.collegefootballdata.com/games", dict(params={"year": 2026, "week": 1}),
     lambda r: "reachable"),
]

bad = 0
for name, url, kw, check in CHECKS:
    try:
        r = requests.get(url, headers=UA, timeout=60, **kw)
        if name.startswith("CFBD") and r.status_code == 401:
            print(f"OK       {name}: HTTP 401 without a key, reachable")
            continue
        if r.status_code != 200:
            bad += 1
            print(f"BLOCKED  {name}: HTTP {r.status_code} ({r.text[:120]!r})")
            continue
        print(f"OK       {name}: HTTP 200, {check(r)}")
    except Exception as e:
        bad += 1
        print(f"BLOCKED  {name}: {type(e).__name__}: {str(e)[:160]}")
print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} sources reachable")
sys.exit(0)
