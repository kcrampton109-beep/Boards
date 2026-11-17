"""Start the boards' scheduled runs ourselves (run by .github/workflows/ticker.yml).

Since Nov 13 2026 GitHub fires only about 1 in 6 of this repo's scheduled runs, so the boards checked prices every
3-8 hours instead of every 30 minutes. Each ticker run takes one half-hour block (UTC): it reads every cron line of
the board workflows, waits for each slot that falls in the block and starts that workflow with workflow_dispatch,
passing the cron string as input `slot` (the plan steps read it like github.event.schedule). The workflow then starts
the next ticker run at the end of the block (a workflow may start others with GITHUB_TOKEN via workflow_dispatch).

A slot is skipped when the board already has a run created in the last few minutes (its own cron fired on time).

Usage: python tools/ticker.py            (needs GH_TOKEN and REPO)
       python tools/ticker.py --dry [N]  (print the next N slots, start nothing)
"""
import datetime as dt, json, os, pathlib, re, sys, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOARDS = ("cfb.yml", "nfl.yml", "cbb.yml")
BLOCK = 30          # minutes per ticker run
LATE = 2            # a slot up to this many minutes past is still started
DEDUPE = 4          # skip a slot if the board has a run created this recently
UTC = dt.timezone.utc


def crons(wf):
    text = (ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
    return re.findall(r'-\s*cron:\s*"([^"]+)"', text)


def _field(spec, lo, hi):
    out = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step = part.split("/")
            step = int(step)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = b = int(part)
        out.update(range(a, b + 1, step))
    return out


def matches(cron, t):
    """Standard 5-field cron, UTC; day of week 0 = Sunday (as GitHub)."""
    mi, ho, dom, mo, dow = cron.split()
    return (t.minute in _field(mi, 0, 59) and t.hour in _field(ho, 0, 23) and t.day in _field(dom, 1, 31)
            and t.month in _field(mo, 1, 12) and (t.isoweekday() % 7) in _field(dow, 0, 6))


def slots(start, end):
    """(time, workflow, cron) for every board cron firing in [start, end), by minute."""
    out = []
    t = start.replace(second=0, microsecond=0)
    while t < end:
        for wf in BOARDS:
            for c in crons(wf):
                if matches(c, t):
                    out.append((t, wf, c))
        t += dt.timedelta(minutes=1)
    return out


def api(path, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['REPO']}{path}",
                                 data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def recent_run(wf, now):
    runs = api(f"/actions/workflows/{wf}/runs?per_page=1").get("workflow_runs", [])
    if not runs:
        return False
    created = dt.datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))
    return (now - created) < dt.timedelta(minutes=DEDUPE)


def main():
    now = dt.datetime.now(UTC)
    if "--dry" in sys.argv:
        n = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 20
        for t, wf, c in slots(now, now + dt.timedelta(hours=6))[:n]:
            print(t.strftime("%a %H:%M UTC"), wf, f'slot="{c}"')
        return
    # this run's block: the half hour that starts at or just before now (+ LATE, so a run started a little early
    # by the previous one still takes the next block)
    b = now + dt.timedelta(minutes=LATE)
    start = b.replace(minute=(b.minute // BLOCK) * BLOCK, second=0, microsecond=0)
    end = start + dt.timedelta(minutes=BLOCK)
    todo = [s for s in slots(start, end) if s[0] >= now - dt.timedelta(minutes=LATE)]
    print(f"block {start:%H:%M}-{end:%H:%M} UTC: {len(todo)} slot(s)", flush=True)
    for t, wf, c in todo:
        wait = (t - dt.datetime.now(UTC)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        try:
            if recent_run(wf, dt.datetime.now(UTC)):
                print(f"{t:%H:%M} {wf}: ran in the last {DEDUPE} min, skipped", flush=True)
                continue
            api(f"/actions/workflows/{wf}/dispatches", {"ref": "main", "inputs": {"slot": c}})
            print(f"{t:%H:%M} {wf} started (slot {c})", flush=True)
        except Exception as e:   # one board's failure must not stop the others or the chain
            print(f"{t:%H:%M} {wf} FAILED: {e}", flush=True)
    # hand over at the end of the block (the workflow's last step starts the next ticker run)
    wait = (end - dt.datetime.now(UTC)).total_seconds() - 20
    if wait > 0:
        time.sleep(wait)


if __name__ == "__main__":
    main()
