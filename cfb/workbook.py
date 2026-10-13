"""Monday workbook: rebuild the Excel workbook from the board as published (no data refresh, no alerts).
usage: python workbook.py <published state.json> <out.xlsx>"""
import json, pathlib, sys
import numpy as np
import pandas as pd
import weekly

st = json.loads(pathlib.Path(sys.argv[1]).read_text())
led = json.loads((weekly.B / "ledger.json").read_text())
now = pd.Timestamp.now(tz=weekly.engine.ET)
rt = pd.read_parquet(weekly.B / "ratings.parquet")
rt["change"] = np.nan
olds = sorted(p for p in weekly.HIST.glob("*.parquet") if p.stem <= (now - pd.Timedelta(days=5)).strftime("%Y-%m-%d"))
if olds:  # same comparison as the Sunday report: vs the ratings snapshot from about a week ago
    prev = pd.read_parquet(olds[-1])[["team_id", "net"]].rename(columns={"net": "prev"})
    rt = rt.drop(columns="change").merge(prev, on="team_id", how="left")
    rt["change"] = rt["net"] - rt["prev"]
card = [p for p in st["plays"] if p["tier"] in weekly.MAIN and not p.get("provisional")]
games = {g["game_id"]: g for g in st["games"]}
out = pathlib.Path(sys.argv[2])
out.parent.mkdir(parents=True, exist_ok=True)
weekly.write_workbook(out, st, led, rt, card, games)
print("wrote", out)
