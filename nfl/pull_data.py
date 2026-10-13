"""Download the NFL research data once (cached; re-run only refetches what is missing or current season).
  nflverse games.csv            schedule, scores, closing spread/total, roof, weather, QBs, coaches, rest
  nflverse play_by_play_YYYY    EPA / success rate per play (2006+)
  nflverse player stats week    per-player weekly TDs, red-zone usage inputs for the TD sheet
  Action Network NFL scoreboard per season/week: opening line (book 30), consensus close, bet % / money %
Usage: python pull_data.py [pbp|an|stats|games|all] [--current]"""
import gzip, pathlib, sys, time
import requests

ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0"}
CUR = 2026
NV = "https://github.com/nflverse/nflverse-data/releases/download"


def get(url, dest, force=False, **kw):
    if dest.exists() and not force:
        return False
    for attempt in range(3):
        try:
            r = requests.get(url, headers=H, timeout=120, **kw)
            if r.status_code == 404:
                print("404", url, flush=True)
                return False
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(r.content)
            tmp.replace(dest)
            print("got", dest.name, len(r.content) // 1024, "KB", flush=True)
            return True
        except Exception as e:
            print("retry", url, e, flush=True)
            time.sleep(5)
    return False


def games(force):
    get("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv", RAW / "games.csv", force=force)


def pbp(force):
    for y in range(2006, CUR + 1):
        get(f"{NV}/pbp/play_by_play_{y}.parquet", RAW / f"pbp_{y}.parquet", force=force and y == CUR)


def stats(force):
    for y in range(2012, CUR + 1):
        ok = get(f"{NV}/stats_player/stats_player_week_{y}.parquet", RAW / f"stats_player_week_{y}.parquet",
                 force=force and y == CUR)
        if not ok and not (RAW / f"stats_player_week_{y}.parquet").exists():
            get(f"{NV}/player_stats/player_stats_{y}.parquet", RAW / f"stats_player_week_{y}.parquet")


def an(force):
    out = RAW / "an"
    out.mkdir(exist_ok=True)
    for season in range(2017, CUR + 1):
        for week in range(1, 23):
            f = out / f"{season}_w{week:02d}.json.gz"
            if f.exists() and not (force and season == CUR):
                continue
            params = {"bookIds": "15,30,68,69,75", "season": season, "week": week}
            if week > 18:  # postseason weeks are 1-4 with seasonType=post in Action's API
                params.update(week=week - 18, seasonType="post")
            try:
                r = requests.get("https://api.actionnetwork.com/web/v1/scoreboard/nfl", params=params, headers=H, timeout=60)
                r.raise_for_status()
            except Exception as e:
                print("an fail", season, week, e, flush=True)
                time.sleep(5)
                continue
            n = len(r.json().get("games", []))
            if n:
                f.write_bytes(gzip.compress(r.content))
            print("an", season, week, n, flush=True)
            time.sleep(1.2)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    force = "--current" in sys.argv
    for name, fn in (("games", games), ("pbp", pbp), ("stats", stats), ("an", an)):
        if what in (name, "all"):
            fn(force)
    print("done", what)
