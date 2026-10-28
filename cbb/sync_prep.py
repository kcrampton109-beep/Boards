"""Stage the CBB board for publishing: copies the page, state and full ledger into OUT and prints one JSON line
(generated, stale). Paper board: no phone alerts."""
import datetime as dt, json, pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parent
B = ROOT / "board"
out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for f in ("index.html", "state.json", "ledger.json"):
    if (B / f).exists():
        shutil.copy(B / f, out / f)
st = json.loads((B / "state.json").read_text()) if (B / "state.json").exists() else {}
gen = dt.datetime.fromisoformat(st["generated_iso"]) if st.get("generated_iso") else None
stale = gen is None or (dt.datetime.now(gen.tzinfo) - gen) > dt.timedelta(hours=6)
print(json.dumps({"generated": st.get("generated"), "stale": stale, "plays": len(st.get("plays", [])),
                  "games": len(st.get("games", []))}))
