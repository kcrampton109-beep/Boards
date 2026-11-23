"""Team box-score totals (ESPN game summary) for every finished D1-vs-D1 game: what a possession-based (tempo-free)
model needs. Only the few numbers used are kept, one JSON line per game in data/box/<season>.jsonl (gitignored);
games already saved are skipped, so the download resumes where it stopped.

  possessions ~= FGA - OREB + TO + 0.475 * FTA   (per team; a game's pace = the two teams' average)

Usage: python research/box.py 2022,2026 [threads]
"""
import json, pathlib, sys, threading, time
import requests
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "box"
URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary"
KEEP = {"fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"), "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("tpm", "tpa"),
        "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"), "offensiveRebounds": "oreb", "defensiveRebounds": "dreb",
        "totalTurnovers": "to", "turnovers": "to_player"}


def parse(d):
    out = {}
    for t in (d.get("boxscore") or {}).get("teams") or []:
        row = {}
        for s in t.get("statistics") or []:
            k, v = KEEP.get(s.get("name")), s.get("displayValue")
            if k is None or v in (None, ""):
                continue
            try:
                if isinstance(k, tuple):
                    a, b = v.split("-")
                    row[k[0]], row[k[1]] = int(a), int(b)
                else:
                    row[k] = int(float(v))
            except ValueError:
                pass
        out[str(t["team"]["id"])] = row
    return out


def games(season):
    """Finished games of a season from the saved ESPN scoreboards (history in espn_raw, this season in season/espn)."""
    import build
    lo, hi = f"{season}0701", f"{season + 1}0630"
    files = [str(p) for d in (ROOT / "data" / "espn_raw", ROOT / "data" / "season" / "espn") if d.exists()
             for p in d.glob("*.json.gz") if lo <= p.name[:8] <= hi]
    e = build.load_espn(sorted(files))
    return [] if not len(e) else sorted(e["espn_id"].astype(int).tolist())


def worker(todo, fh, lock, pause):
    s = requests.Session()
    while True:
        with lock:
            if not todo:
                return
            gid = todo.pop()
        for attempt in range(4):
            try:
                r = s.get(URL, params={"event": gid}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                if r.status_code == 404:
                    rec = None
                    break
                r.raise_for_status()
                rec = parse(r.json())
                break
            except Exception:
                time.sleep(5 * (attempt + 1))
        else:
            continue
        with lock:
            fh.write(json.dumps({"id": gid, "teams": rec}) + "\n")
            fh.flush()
        time.sleep(pause)


def main(seasons, threads=4, pause=0.25):
    sys.path.insert(0, str(ROOT / "research"))
    OUT.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        f = OUT / f"{season}.jsonl"
        done = set()
        if f.exists():
            for ln in f.read_text().splitlines():
                try:
                    done.add(json.loads(ln)["id"])
                except Exception:
                    pass
        todo = [g for g in games(season) if g not in done]
        print(season, "to fetch", len(todo), "already", len(done), flush=True)
        lock = threading.Lock()
        with f.open("a") as fh:
            th = [threading.Thread(target=worker, args=(todo, fh, lock, pause)) for _ in range(threads)]
            [t.start() for t in th]
            [t.join() for t in th]
        print(season, "done", flush=True)


def load(season):
    """DataFrame: espn_id, team id, possessions and the counting stats."""
    rows = []
    for ln in (OUT / f"{season}.jsonl").read_text().splitlines():
        x = json.loads(ln)
        for tid, s in (x.get("teams") or {}).items():
            if {"fga", "fta", "oreb", "to"} <= set(s):
                rows.append(dict(espn_id=x["id"], eid=int(tid), **s,
                                 poss=s["fga"] - s["oreb"] + s["to"] + 0.475 * s["fta"]))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1].split(",")]
    main(range(a[0], a[-1] + 1), int(sys.argv[2]) if len(sys.argv) > 2 else 4)
