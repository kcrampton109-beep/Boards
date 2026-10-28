"""Is Action Network's opener (book 30) a number you can bet at DK / FD / BetMGM?

There is no CBB history of each sportsbook's first number, so this uses the live CFB line snapshots (Action v2,
every 30-60 min, Oct 2026) as a proxy: for each game, the first spread DK / FD / BetMGM showed vs book 30's opener
and vs the books' last pre-kickoff number. slope = share of the opener->close move already in a book's first number.

Usage: python opener_check.py [snapshot dir]   (default: the laptop CFB copy, C:/Users/kcram/cfb/snapshots)
"""
import glob, sys
import numpy as np
import pandas as pd

root = sys.argv[1] if len(sys.argv) > 1 else "C:/Users/kcram/cfb/snapshots"
d = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{root}/*/*.csv.gz"))], ignore_index=True)
d = d[(d["period"] == "event") & (d["market"] == "spread") & (d["team_id"] == d["home_id"])].copy()
d["value"] = pd.to_numeric(d["value"], errors="coerce")
d["ts"], d["start"] = pd.to_datetime(d["ts"]), pd.to_datetime(d["start"])
d = d[d["ts"] < d["start"]]
op = d[d["book"] == 30].groupby("game_id")["value"].last().rename("open")
bk = d[d["book"].isin([68, 69, 75])].sort_values("ts")
first = bk.groupby(["game_id", "book"]).agg(first=("value", "first"), t0=("ts", "first")).reset_index()
close = bk.groupby(["game_id", "book"])["value"].last().groupby("game_id").median().rename("close")
g0 = d.groupby("game_id")["ts"].min().rename("g0")
x = first.merge(g0, on="game_id").merge(op, on="game_id").merge(close, on="game_id").dropna()
print(f"{d['game_id'].nunique()} games, {d['ts'].dt.date.nunique()} days of snapshots")
for label, y in (("book first seen after the game was already listed (we saw it post)", x[x["t0"] > x["g0"]]),
                 ("every game/book", x)):
    same = (y["first"] == y["open"]).mean()
    m = y[(y["close"] - y["open"]).abs() >= 1]
    mv, pre = (m["close"] - m["open"]).astype(float), (m["first"] - m["open"]).astype(float)
    print(f"{label}: n {len(y)}, first number = opener {same:.0%}, mean |first - opener| "
          f"{(y['first'] - y['open']).abs().mean():.2f}; games that moved 1+: {len(m)}, avg move {mv.abs().mean():.2f}, "
          f"share of move already in the book's first number {np.polyfit(mv, pre, 1)[0]:.2f}, "
          f"move left from the book's first number {(np.sign(mv) * (m['close'] - m['first'])).mean():.2f}")
