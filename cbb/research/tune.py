"""Pick each model's prior weight (lam) and recency half-life WITHOUT looking at betting lines: RMSE of the projected
margin against actual results in 2022-23 (before any season with openers). Usage: python tune.py [pts|market]
Chosen (Oct 28 2026): pts lam 4 / half-life 60 days (11.38), market lam 1 / half-life 30 days (11.36); both flat nearby."""
import itertools, sys
import numpy as np
import pandas as pd
import model

target = sys.argv[1] if len(sys.argv) > 1 else "pts"
grid = ((2, 4, 6, 10, 16), (0, 30, 45, 60, 90, 150)) if target == "pts" else ((1, 2, 4, 8), (0, 15, 30, 60))
g, _ = model.load()
res = []
for lam, hl in itertools.product(*grid):
    p, _, _ = model.run(lam, hl or None, seasons=[2021, 2022], target=target)
    d = g.merge(p, on="espn_id")
    d = d[d["season"] == 2022]
    e = (d.home_pts - d.away_pts) - d.margin_hat
    et = (d.home_pts + d.away_pts) - d.total_hat
    m = d["day"].dt.month
    res.append(dict(lam=lam, half_life=hl, rmse=np.sqrt((e ** 2).mean()), rmse_total=np.sqrt((et ** 2).mean()),
                    nov_dec=np.sqrt((e[m.isin([11, 12])] ** 2).mean()), jan_apr=np.sqrt((e[~m.isin([11, 12])] ** 2).mean())))
    print(res[-1], flush=True)
r = pd.DataFrame(res).round(3)
print(r.sort_values("rmse").to_string(index=False))
