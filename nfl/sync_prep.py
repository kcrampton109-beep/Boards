"""Stage the NFL board for publishing and work out which new plays to push to the phone.
Usage: python sync_prep.py <staging dir>
Prints one JSON line: generated, stale, new_plays, notify_text, page, state."""
import datetime as dt, json, pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parent
BOARD = ROOT / "board"
NOTIFY_TIERS = ("STRONG", "WEATHER", "TEASER", "RLM")


def main(dest):
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    state = json.loads((BOARD / "state.json").read_text())
    shutil.copy(ROOT / "page" / "index.html", dest / "index.html")
    shutil.copy(BOARD / "state.json", dest / "state.json")
    gen = dt.datetime.fromisoformat(state["generated_iso"])
    stale = (dt.datetime.now(gen.tzinfo) - gen).total_seconds() > 2 * 3600
    notified = set(json.loads((BOARD / "notified.json").read_text())) if (BOARD / "notified.json").exists() else set()
    games = {g["game_id"]: g for g in state["games"]}
    lines, new_ids = [], []
    for p in state["plays"]:
        if p["tier"] not in NOTIFY_TIERS or p["id"] in notified or p.get("lookahead"):
            continue
        g = games.get(p["game_id"], {})
        if g.get("started"):
            continue
        price = "teaser leg" if p["market"] == "teaser" else f"{'+' if p['odds'] > 0 else ''}{p['odds']} {p['book']}"
        tag = {"STRONG": "STRONG", "WEATHER": "WIND UNDER", "TEASER": "TEASER LEG", "RLM": "RLM (tracked)"}[p["tier"]]
        if p.get("stake") and p["market"] != "teaser":
            tag += f" {p['stake']:g}u"
        lines.append(f"{tag}: {p['pick']} ({price}) - {g.get('matchup', '')}, {g.get('kickoff', '')}")
        new_ids.append(p["id"])
    text = ""
    if lines:
        text = "NFL board: " + ("; ".join(lines[:6]) + (f" (+{len(lines) - 6} more)" if len(lines) > 6 else ""))
    (BOARD / "notified.json").write_text(json.dumps(sorted(notified | set(new_ids))))
    print(json.dumps(dict(generated=state["generated"], stale=stale, new_plays=len(new_ids), notify_text=text,
                          page=str((dest / "index.html").resolve()), state=str((dest / "state.json").resolve()))))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "stage"))
