"""Weekly check-in data for the CFB and NFL boards (run by the Monday Claude check-in on the laptop).

Reads only public sources: each board's published state.json, the repo's GitHub Actions runs, the published
workbooks and this clone's git log. Prints one JSON object: per board the season and last-graded-week records by
tier, the doc table rows, scorecard progress and freshness; run health; changes made to the code this week.
usage: python tools/checkin.py   (from the repo root, after `git pull`)"""
import datetime as dt, json, subprocess, urllib.request
from email.utils import parsedate_to_datetime

REPO = "kcrampton109-beep/Boards"
SITE = "https://kcrampton109-beep.github.io/Boards"
NOW = dt.datetime.now(dt.timezone.utc)
BOARDS = {
    "cfb": dict(name="CFB Play Card", workbook="CFB_Model_2026.xlsx",
                tiers=[("STRONG", "Strong"), ("FCS", "FCS"), ("WEATHER", "Wind unders"), ("LEAN", "Leans"),
                       ("RESEARCH", "Research")],
                scorecard=("STRONG", "FCS"), target=dict(n=60, clv=0.5, beat=0.55, review="2026-11-01")),
    "nfl": dict(name="NFL Game Day Board", workbook="NFL_weekly.xlsx",
                tiers=[("STRONG", "Strong"), ("LEAN", "Lean"), ("WEATHER", "Wind unders"), ("TEASER", "Teaser legs"),
                       ("RLM", "Reverse line move (tracked)"), ("TREND", "Trend (tracked)"), ("TD", "Anytime TD (research)")],
                scorecard=("STRONG",), target=dict(n=40, clv=0.5, beat=0.55, review="2026-11-03")),
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "boards-checkin", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers


def rec(es):
    w = sum(e["status"] == "W" for e in es)
    l = sum(e["status"] == "L" for e in es)
    p = sum(e["status"] == "P" for e in es)
    return f"{w}-{l}" + (f"-{p}" if p else "")


def tier_stats(es):
    graded = [e for e in es if e["status"] in ("W", "L", "P")]
    clv = [e["clv"] for e in graded if e.get("clv") is not None]
    units = sum(e.get("units") or 0 for e in graded)
    return dict(graded=rec(graded), n=len(graded), units=round(units, 2),
                clv=round(sum(clv) / len(clv), 2) if clv else None,
                beat=round(sum(c > 0 for c in clv) / len(clv), 3) if clv else None,
                open=sum(e["status"] == "open" for e in es))


def board(key, cfg):
    raw, _ = get(f"{SITE}/{key}/state.json")
    st = json.loads(raw)
    led = [e for e in st.get("ledger", []) if e.get("status") != "void"]
    gen = dt.datetime.fromisoformat(st["generated_iso"])
    weeks = sorted({e["week"] for e in led if e["status"] in ("W", "L", "P") and e.get("week") is not None})
    last_wk = weeks[-1] if weeks else None
    out = dict(name=cfg["name"], url=f"{SITE}/{key}/", generated=st["generated"],
               hours_old=round((NOW - gen).total_seconds() / 3600, 1), last_graded_week=last_wk,
               season={}, last_week={}, doc_rows=[])
    for t, label in cfg["tiers"]:
        es = [e for e in led if e["tier"] == t]
        s = tier_stats(es)
        out["season"][t] = s
        out["last_week"][t] = tier_stats([e for e in es if e.get("week") == last_wk]) if last_wk is not None else None
        out["doc_rows"].append(dict(tier=label, graded=s["graded"], units=f"{s['units']:+.2f}" if s["n"] else "0",
                                    avg_clv="none yet" if s["clv"] is None else f"{s['clv']:+.1f} pts", open_now=s["open"]))
    sc = [e for e in led if e["tier"] in cfg["scorecard"]]
    s = tier_stats(sc)
    tg = cfg["target"]
    out["scorecard"] = dict(tiers="+".join(cfg["scorecard"]), plays=s["n"], avg_clv=s["clv"], beat_close=s["beat"],
                            target=tg, met=bool(s["n"] >= tg["n"] and (s["clv"] or 0) >= tg["clv"] and (s["beat"] or 0) >= tg["beat"]))
    try:
        _, h = get(f"{SITE}/{key}/{cfg['workbook']}")
        out["workbook"] = dict(url=f"{SITE}/{key}/{cfg['workbook']}",
                               updated=parsedate_to_datetime(h["Last-Modified"]).astimezone().strftime("%a %b %d %I:%M %p"))
    except Exception as e:
        out["workbook"] = dict(url=f"{SITE}/{key}/{cfg['workbook']}", error=str(e)[:120])
    return out


def runs():
    since = (NOW - dt.timedelta(days=7)).strftime("%Y-%m-%d")
    raw, _ = get(f"https://api.github.com/repos/{REPO}/actions/runs?per_page=100&created=%3E%3D{since}")
    rs = json.loads(raw).get("workflow_runs", [])
    by = {}
    for r in rs:
        b = by.setdefault(r["name"], dict(success=0, failure=0, other=0, last=None))
        c = r.get("conclusion")
        b["success" if c == "success" else "failure" if c == "failure" else "other"] += 1
        b["last"] = max(b["last"] or "", r["created_at"])
    fails = [dict(workflow=r["name"], when=r["created_at"], commit=r["head_commit"]["message"].splitlines()[0])
             for r in rs if r.get("conclusion") == "failure"]
    return dict(window_days=7, counted=len(rs), by_workflow=by, failures=fails[:10],
                note="the runs API returns at most 100 runs; counts cover the newest 100")


def changes():
    log = subprocess.run(["git", "log", "--since=7 days ago", "--format=%h|%an|%ad|%s", "--date=short"],
                         capture_output=True, text=True).stdout.splitlines()
    return [dict(zip(("commit", "author", "date", "subject"), l.split("|", 3))) for l in log
            if "github-actions" not in l]


def calls_left():
    try:
        lines = open("cfb/data/calls.log").read().strip().splitlines()
        return lines[-1].split("\t")[2]
    except Exception:
        return None


print(json.dumps(dict(checked=NOW.astimezone().strftime("%a %b %d %Y %I:%M %p"),
                      boards={k: board(k, v) for k, v in BOARDS.items()}, runs=runs(), code_changes=changes(),
                      cfbd_calls_left=calls_left()), indent=1))
