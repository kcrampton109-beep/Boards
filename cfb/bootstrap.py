"""Download the historical data the model is built from (cloud setup, or after GitHub's cache drops it).

CFBD, 2014-2026: games, lines, advanced stats (garbage time excluded), havoc, drives, talent, recruiting, returning
production, plus teams and venues: about 106 calls of the 1,000/month budget. Action Network scoreboards 2018-2026 via
an_history.py (no key). Kept in the Actions cache, not in the public repo (third-party data)."""
import subprocess, sys
import cfbd

for y in range(2014, 2027):
    for path, kw in (("/games", dict(seasonType="both")),
                     ("/lines", dict(seasonType="both")),
                     ("/stats/game/advanced", dict(seasonType="both", excludeGarbageTime="true")),
                     ("/stats/game/havoc", dict(seasonType="both")),
                     ("/drives", dict(seasonType="both")),
                     ("/talent", {}), ("/recruiting/teams", {}), ("/player/returning", {})):
        cfbd.get(path, year=y, **kw)
    print("CFBD", y, "ok", flush=True)
cfbd.get("/teams")
cfbd.get("/venues")
print("CFBD calls left:", cfbd.remaining(), flush=True)
subprocess.run([sys.executable, "an_history.py", ",".join(str(s) for s in range(2018, 2027))], check=True)
