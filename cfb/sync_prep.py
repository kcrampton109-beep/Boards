"""Prepare a CFB board sync for the Claude scheduled task.

Copies the board page + latest state into OUT, then prints one JSON line:
  notify_text  - message for a phone push ("" when there is nothing new)
  stale        - true when the engine hasn't produced fresh prices for 6+ hours
Plays are marked as notified here, so each new play is announced once."""
import datetime as dt, json, pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parent
B = ROOT / "board"
out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
shutil.copy(B / "index.html", out / "index.html")
shutil.copy(B / "state.json", out / "state.json")
shutil.copy(B / "ledger.json", out / "ledger.json")   # full history for the Today / Results tabs

st = json.loads((B / "state.json").read_text())
memo_p = B / "notified.json"
memo = json.loads(memo_p.read_text()) if memo_p.exists() else {"ids": [], "stale_warned": ""}
seen = set(memo["ids"])
BOOK = {"DK": "DK", "FD": "FD", "MGM": "MGM"}
CODE = {"STRONG": "Strong", "FCS": "FCS", "WEATHER": "Wind under"}
new = [p for p in st.get("plays", []) if p["tier"] in CODE and p["id"] not in seen and not p.get("provisional")]
games = {g["game_id"]: g for g in st.get("games", [])}
parts = []
for p in new:
    o = p.get("odds")
    o = "" if o is None else (f"+{int(o)}" if o > 0 else f"{int(o)}")
    st_ = p.get("stake")
    st_ = f" {st_:g}u" if st_ is not None else ""
    parts.append(f"{CODE[p['tier']]}{st_}: {p['pick']} {o} {BOOK.get(p['book'], p['book'] or '')}".strip())
text = ""
if parts:
    text = f"CFB board: {len(parts)} new play{'s' if len(parts) > 1 else ''}. " + "; ".join(parts)
    if len(text) > 195:
        text = text[:190].rsplit(";", 1)[0] + "; more on board"
memo["ids"] = sorted(seen | {p["id"] for p in new})

gen = dt.datetime.fromisoformat(st["generated_iso"]) if st.get("generated_iso") else None
now = dt.datetime.now(gen.tzinfo) if gen else dt.datetime.now()
stale = gen is None or (now - gen) > dt.timedelta(hours=6)
today = now.strftime("%Y-%m-%d")
if stale and memo.get("stale_warned") != today and not text:
    text = f"CFB board: no fresh prices since {st.get('generated', 'unknown')}. Check the CFB board runs on GitHub (Actions tab)."
    memo["stale_warned"] = today
memo_p.write_text(json.dumps(memo))
print(json.dumps({"generated": st.get("generated"), "stale": stale, "new_plays": len(parts), "notify_text": text,
                  "page": str(out / "index.html"), "state": str(out / "state.json")}))
