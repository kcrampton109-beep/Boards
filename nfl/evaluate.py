"""Backtest frame + helpers: blend the rating models walk-forward, then test against opening and closing lines."""
import numpy as np
import pandas as pd
import model

DATA = model.DATA


def qb_feats(g):
    """QB change features: value of the expected (or actual) starter minus the value of the QBs the team's
    ratings were built on (dropback-weighted, last 8 games), in points per game (x 35 dropbacks)."""
    q = model.qb_layer(g)
    q = q.merge(g[["game_id", "start"]], on="game_id").sort_values("start")
    q["base"] = q.groupby("team")["qb_val_act"].transform(lambda s: s.shift(1).rolling(8, min_periods=1).mean())
    q["qb_adj_exp"] = (q["qb_val_exp"] - q["base"]).fillna(0) * 35
    q["qb_adj_act"] = (q["qb_val_act"] - q["base"]).fillna(0) * 35
    q = q[["game_id", "team", "qb_adj_exp", "qb_adj_act", "qb_changed", "qb_val_act", "qb_val_exp", "qb_act_name"]]
    out = g[["game_id", "home", "away"]]
    h = q.rename(columns={c: "home_" + c for c in q.columns if c not in ("game_id",)}).rename(columns={"home_team": "home"})
    a = q.rename(columns={c: "away_" + c for c in q.columns if c not in ("game_id",)}).rename(columns={"away_team": "away"})
    out = out.merge(h, on=["game_id", "home"], how="left").merge(a, on=["game_id", "away"], how="left")
    out["qb_exp"] = out["home_qb_adj_exp"].fillna(0) - out["away_qb_adj_exp"].fillna(0)
    out["qb_act"] = out["home_qb_adj_act"].fillna(0) - out["away_qb_adj_act"].fillna(0)
    return out.drop(columns=["home", "away"])


def frame(preds="preds.parquet"):
    g, _ = model.load()
    g = g.merge(pd.read_parquet(DATA / preds), on="game_id", how="left")
    g = g.merge(qb_feats(g), on="game_id", how="left")
    g["rest_diff"] = (g["home_rest"] - g["away_rest"]).clip(-7, 7)
    g["completed"] = g["home_pts"].notna()
    return g


def blend(g, feats, target="margin", min_season=2008, train_from=2008):
    """Walk-forward OLS: for season S fit target ~ feats on completed games of seasons < S."""
    out = pd.Series(np.nan, index=g.index)
    for s in sorted(g["season"].unique()):
        if s <= min_season:
            continue
        tr = g[(g["season"] < s) & (g["season"] >= train_from) & g["completed"]].dropna(subset=feats + [target])
        te = g[g["season"] == s].dropna(subset=feats)
        if len(tr) < 200 or not len(te):
            continue
        X = np.column_stack([np.ones(len(tr))] + [tr[f] for f in feats])
        beta, *_ = np.linalg.lstsq(X, tr[target].values, rcond=None)
        Xt = np.column_stack([np.ones(len(te))] + [te[f] for f in feats])
        out.loc[te.index] = Xt @ beta
    return out


def era(s):
    return np.where(s <= 2013, "2008-13", np.where(s <= 2019, "2014-19", np.where(s <= 2025, "2020-25", "2026")))


def ats_table(d, model_line, line_col, thresholds=(1, 1.5, 2, 2.5, 3, 4, 5), clv_to="close", label=""):
    """Bet the side the model likes when |line - model_line| >= t; grade at line_col; CLV = move of the
    closing line toward our side after the bet."""
    d = d[d["completed"] & d[line_col].notna() & model_line.notna()].copy()
    d["ml"] = model_line.loc[d.index]
    d["edge"] = d[line_col] - d["ml"]        # >0: model likes home more than the line does
    rows = []
    for t in thresholds:
        s = d[d["edge"].abs() >= t]
        side = np.sign(s["edge"])
        res = np.sign(s["margin"] + s[line_col]) * side
        for e, ss in s.groupby(era(s["season"])):
            r = res.loc[ss.index]
            w, l = int((r > 0).sum()), int((r < 0).sum())
            clv = ((s.loc[ss.index, clv_to] - s.loc[ss.index, line_col]) * -side.loc[ss.index]).mean() if clv_to else np.nan
            rows.append(dict(label=label, thr=t, era=e, n=w + l, win=round(w / max(1, w + l), 3),
                             roi=round((w * 100 / 110 - l) / max(1, w + l), 3), clv=round(clv, 2),
                             per_wk=round((w + l) / max(1, ss.groupby(["season", "wk"]).ngroups), 1)))
    return pd.DataFrame(rows)


def ou_table(d, model_total, line_col, thresholds=(1, 2, 3, 4, 5), clv_to="tclose", label=""):
    d = d[d["completed"] & d[line_col].notna() & model_total.notna()].copy()
    d["edge"] = model_total.loc[d.index] - d[line_col]   # >0: over
    rows = []
    for t in thresholds:
        s = d[d["edge"].abs() >= t]
        side = np.sign(s["edge"])
        res = np.sign(s["points"] - s[line_col]) * side
        for e, ss in s.groupby(era(s["season"])):
            r = res.loc[ss.index]
            w, l = int((r > 0).sum()), int((r < 0).sum())
            clv = ((s.loc[ss.index, clv_to] - s.loc[ss.index, line_col]) * side.loc[ss.index]).mean() if clv_to else np.nan
            rows.append(dict(label=label, thr=t, era=e, n=w + l, win=round(w / max(1, w + l), 3),
                             roi=round((w * 100 / 110 - l) / max(1, w + l), 3), clv=round(clv, 2)))
    return pd.DataFrame(rows)
