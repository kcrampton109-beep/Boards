"""Backtest helpers: blend the rating models walk-forward, then test against opening and closing lines."""
import numpy as np
import pandas as pd
import model

DATA = model.DATA


def frame():
    g, _ = model.load()
    p = pd.read_parquet(DATA / "preds.parquet")
    g = g.merge(p, on="game_id", how="left")
    try:
        g = g.merge(pd.read_parquet(DATA / "market_proj.parquet"), on="game_id", how="left")
    except FileNotFoundError:
        pass
    try:
        g = g.merge(pd.read_parquet(DATA / "weather.parquet"), on="game_id", how="left")
    except FileNotFoundError:
        pass
    g["margin"] = g["home_pts"] - g["away_pts"]
    g["points"] = g["home_pts"] + g["away_pts"]
    # Action consensus lines that disagree with CFBD by > 3 points are mislabeled games; drop them
    bad = (g["cons_spread"] - g["spread"]).abs() > 3
    acols = [c for c in g.columns if c.startswith(("cons_", "open_", "dk_", "fd_", "mgm_"))]
    g.loc[bad, acols] = np.nan
    # best available close / open (home spread convention: negative = home favoured)
    g["close"] = g["spread"].fillna(g["cons_spread"])
    g["open"] = g["spread_open"]
    if "open_spread" in g:
        g["open"] = g["open"].fillna(g["open_spread"])
    g["tclose"] = g["total"].fillna(g["cons_total"])
    g["topen"] = g["total_open"]
    if "open_total" in g:
        g["topen"] = g["topen"].fillna(g["open_total"])
    g["fcs_game"] = (g["home_cls"] == "fcs") | (g["away_cls"] == "fcs")
    g["fcs_fcs"] = (g["home_cls"] == "fcs") & (g["away_cls"] == "fcs")
    return g


def blend(g, feats, target="margin", min_season=2015, by=None):
    """Walk-forward OLS: for season S fit target ~ feats on seasons < S (completed games)."""
    out = pd.Series(np.nan, index=g.index)
    for s in sorted(g["season"].unique()):
        if s < min_season + 1:
            continue
        tr = g[(g["season"] < s) & (g["season"] >= min_season)].dropna(subset=feats + [target])
        te = g[g["season"] == s].dropna(subset=feats)
        if len(tr) < 200 or len(te) == 0:
            continue
        X = np.column_stack([np.ones(len(tr))] + [tr[f] for f in feats])
        beta, *_ = np.linalg.lstsq(X, tr[target].values, rcond=None)
        Xt = np.column_stack([np.ones(len(te))] + [te[f] for f in feats])
        out.loc[te.index] = Xt @ beta
    return out


def ats(g, pick_home, line, juice=-110):
    """Grade picks against a home-spread `line`. pick_home: +1 home, -1 away, 0 no bet."""
    res = g["margin"] + line  # >0 home covers
    win = np.sign(res) * pick_home
    d = pd.DataFrame(dict(win=win, pick=pick_home)).loc[pick_home != 0].dropna()
    w, l = (d["win"] > 0).sum(), (d["win"] < 0).sum()
    n = w + l
    pct = w / n if n else np.nan
    roi = (w * (100 / -juice) - l) / (w + l) if n else np.nan
    return dict(n=int(n), win=round(pct, 4) if n else None, roi=round(roi, 4) if n else None,
                pushes=int((d["win"] == 0).sum()))


def report(g, edge, line, thresholds=(1, 2, 3, 4, 5, 6), label="", groups=("season_block",)):
    rows = []
    gb = g.assign(season_block=np.where(g["season"] <= 2019, "2015-19", np.where(g["season"] <= 2025, "2020-25", "2026")))
    for t in thresholds:
        pick = np.where(edge >= t, 1, np.where(edge <= -t, -1, 0))
        for k, d in gb.groupby(list(groups)):
            r = ats(d, pd.Series(pick, index=g.index).loc[d.index], line.loc[d.index])
            rows.append(dict(label=label, thr=t, grp=k if isinstance(k, str) else "/".join(map(str, k)), **r))
    return pd.DataFrame(rows)
