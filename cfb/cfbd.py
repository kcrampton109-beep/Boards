"""Cached CollegeFootballData.com fetcher. Every response is saved under data/raw;
a cached file is never re-fetched, so the 1,000-call monthly budget is spent once."""
import json, os, pathlib, time, urllib.parse
import requests

ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
KEY = os.environ.get("CFBD_KEY") or (ROOT / ".cfbd_key").read_text().strip()  # GitHub: repository secret
BASE = "https://api.collegefootballdata.com"
LOG = ROOT / "data" / "calls.log"


def _name(path, params):
    q = urllib.parse.urlencode(sorted(params.items()))
    return (path.strip("/").replace("/", "_") + ("__" + q if q else "")).replace("&", "_").replace("=", "-") + ".json"


def get(path, refresh=False, **params):
    f = RAW / _name(path, params)
    if f.exists() and not refresh:
        return json.loads(f.read_text(encoding="utf-8"))
    for attempt in range(4):   # CFBD returns 502/504 at busy times (Saturday night, Sunday morning): retry with backoff
        try:
            r = requests.get(BASE + path, params=params, timeout=120,
                             headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json"})
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 3:
                raise
            time.sleep(20 * (attempt + 1))
            continue
        left = r.headers.get("X-CallLimit-Remaining")
        with LOG.open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{r.status_code}\t{left}\t{path}\t{params}\n")
        if r.status_code < 500 or attempt == 3:
            break
        time.sleep(20 * (attempt + 1))
    r.raise_for_status()
    data = r.json()
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def remaining():
    lines = LOG.read_text().strip().splitlines() if LOG.exists() else []
    return lines[-1].split("\t")[2] if lines else None
