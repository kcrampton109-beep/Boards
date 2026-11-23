"""Deeper look for a better edge (Nov 23 2026). Discipline: every choice is made on the development seasons
(2023-24, 2024-25) by prediction accuracy, never by betting results; 2025-26 and this season are checked once after.

  E1  blend weight: how much of the results model vs the market model, separately for margins and totals
      (chosen by RMSE against the closing line on dev: the model that best anticipates the close captures the most CLV)
  E2  agreement: plays where both component models clear the gap on their own vs the blend only (fixed definition)
  E3  possession-based results model (research/tempo.py) in place of the points model inside the blend: accuracy on
      dev first; the betting rules on dev, then once on 2025-26
Usage: python research/deep.py e1|e2|e3
"""
import sys
import numpy as np
import pandas as pd
import evaluate as ev, edge

DEV, HOLD = (2023, 2024), (2025,)


def frame():
    g, k = edge.frame()
    pts = pd.read_parquet(ev.model.DATA / "preds_pts.parquet")[["espn_id", "margin_hat", "total_hat"]]
    mkt = pd.read_parquet(ev.model.DATA / "preds_market.parquet")[["espn_id", "margin_hat", "total_hat"]]
    g = g.merge(pts.rename(columns={"margin_hat": "m_pts", "total_hat": "t_pts"}), on="espn_id")
    g = g.merge(mkt.rename(columns={"margin_hat": "m_mkt", "total_hat": "t_mkt"}), on="espn_id")
    g["lvl"] = g["total_hat"] - g["t_adj"]          # the market-level correction applied to the blend total
    return g[g["season"].isin(DEV + HOLD)]


def e1(g):
    rows = []
    for w in (0.0, 0.25, 0.5, 0.75, 1.0):        # weight on the market model
        m = g["k"] * ((1 - w) * g["m_pts"] + w * g["m_mkt"])
        t = (1 - w) * g["t_pts"] + w * g["t_mkt"] - g["lvl"]
        for part, s in (("dev", g["season"].isin(DEV)), ("2025-26", g["season"].isin(HOLD))):
            x = g[s]; ms, ts = m[s], t[s]
            ok = x["close"].notna(); okt = x["tclose"].notna()
            rows.append(dict(w_market=w, seasons=part,
                             margin_vs_close=np.sqrt(((ms + x["close"])[ok] ** 2).mean()),
                             margin_vs_actual=np.sqrt(((x["margin"] - ms) ** 2).mean()),
                             total_vs_close=np.sqrt(((ts - x["tclose"])[okt] ** 2).mean()),
                             total_vs_actual=np.sqrt(((x["points"] - ts) ** 2).mean())))
    print(pd.DataFrame(rows).round(3).set_index(["w_market", "seasons"]).unstack().to_string())


def e2(g):
    C = g.assign(margin_hat=g["m_adj"], total_hat=g["t_adj"])
    rows = []
    for kind, t in (("spread", 3), ("spread", 4), ("total", 5), ("total", 6)):
        d = ev.plays(C, t, kind, 20.0 if kind == "spread" else 40.0)
        d = d[d["period"] != "1 Nov"]
        if kind == "spread":
            a = d["pick"] * (d["k"] * d["m_pts"] + d["open"]); b = d["pick"] * (d["k"] * d["m_mkt"] + d["open"])
        else:
            a = d["pick"] * (d["t_pts"] - d["lvl"] - d["topen"]); b = d["pick"] * (d["t_mkt"] - d["lvl"] - d["topen"])
        d["agree"] = np.where((a >= t) & (b >= t), "both clear it", np.where((a > 0) & (b > 0), "same side, one short", "split"))
        for part, seas in (("dev", DEV), ("2025-26", HOLD)):
            for lab, x in d[d["season"].isin(seas)].groupby("agree"):
                s = ev.summary(x)
                rows.append(dict(rule=f"Dec-Mar {kind} {t}+", seasons=part, agree=lab, n=s["n"], CLV=s["CLV pts"],
                                 xCLV=s["xCLV"], beat=s["beat close %"], ATS=s["ATS %"], ROI=s["ROI %"]))
    print(pd.DataFrame(rows).set_index(["rule", "seasons", "agree"]).to_string())


def corrected(g, m, t):
    """Margin scale (actual / model over earlier seasons) and market-relative totals, as edge.frame, for any model."""
    k = {s: float((g.loc[g.season < s, "margin"] * m[g.season < s]).sum() / (m[g.season < s] ** 2).sum())
         for s in sorted(g["season"].unique())}
    m_adj = m * g["season"].map(k)
    d = t - g["topen"]
    x = g[g["topen"].notna()]
    lvl = pd.Series(np.nan, index=g.index)
    for s, xs in x.groupby("season"):
        agg = d.loc[xs.index].groupby(xs["day"]).agg(["sum", "count"])
        for day in agg.index:
            w = agg.loc[(agg.index >= day - pd.Timedelta(days=7)) & (agg.index <= day)]
            idx = xs.index[xs["day"] == day]
            lvl.loc[idx] = (w["sum"].sum() - d.loc[idx]) / max(w["count"].sum() - 1, 1)
    return m_adj, t - lvl, k


def e3():
    g, _ = edge.frame()
    pts = pd.read_parquet(ev.model.DATA / "preds_pts.parquet")[["espn_id", "margin_hat", "total_hat"]]
    mkt = pd.read_parquet(ev.model.DATA / "preds_market.parquet")[["espn_id", "margin_hat", "total_hat"]]
    tmp = pd.read_parquet(ev.model.DATA / "preds_tempo.parquet")[["espn_id", "margin_hat", "total_hat"]]
    g = g.merge(pts.rename(columns={"margin_hat": "m_pts", "total_hat": "t_pts"}), on="espn_id")
    g = g.merge(mkt.rename(columns={"margin_hat": "m_mkt", "total_hat": "t_mkt"}), on="espn_id")
    g = g.merge(tmp.rename(columns={"margin_hat": "m_tmp", "total_hat": "t_tmp"}), on="espn_id")
    g = g[g["season"] >= 2022].reset_index(drop=True)     # tempo starts in 2022-23 (its burn-in season)
    variants = {"current: points + market": (0.5 * (g.m_pts + g.m_mkt), 0.5 * (g.t_pts + g.t_mkt)),
                "tempo + market": (0.5 * (g.m_tmp + g.m_mkt), 0.5 * (g.t_tmp + g.t_mkt)),
                "points alone": (g.m_pts, g.t_pts), "tempo alone": (g.m_tmp, g.t_tmp)}
    acc, rules = [], []
    for lab, (m, t) in variants.items():
        m_adj, t_adj, k = corrected(g, m, t)
        for part, seas in (("dev", DEV), ("2025-26", HOLD)):
            s = g["season"].isin(seas); x = g[s]
            acc.append(dict(model=lab, seasons=part,
                            margin_vs_actual=np.sqrt(((x.margin - m_adj[s]) ** 2).mean()),
                            margin_vs_close=np.sqrt(((m_adj[s] + x.close)[x.close.notna()] ** 2).mean()),
                            total_vs_actual=np.sqrt(((x.points - t_adj[s]) ** 2).mean()),
                            total_vs_close=np.sqrt(((t_adj[s] - x.tclose)[x.tclose.notna()] ** 2).mean())))
        if not lab.endswith("market"):
            continue
        C = g.assign(margin_hat=m_adj, total_hat=t_adj)
        for kind, th in (("spread", 3), ("spread", 4), ("total", 5), ("total", 6)):
            d = ev.plays(C, th, kind, 20.0 if kind == "spread" else 40.0)
            d = d[d["period"] != "1 Nov"]
            for part, seas in (("dev", DEV), ("2025-26", HOLD)):
                x = d[d["season"].isin(seas)]; q = ev.summary(x)
                rules.append(dict(rule=f"Dec-Mar {kind} {th}+", seasons=part, model=lab, n=q["n"], CLV=q["CLV pts"],
                                  xCLV=q["xCLV"], beat=q["beat close %"], ATS=q["ATS %"], ROI=q["ROI %"]))
    print("## accuracy (RMSE, corrected models)\n")
    print(pd.DataFrame(acc).round(3).set_index(["model", "seasons"]).unstack().to_string())
    print("\n## December rules\n")
    print(pd.DataFrame(rules).set_index(["rule", "seasons", "model"]).to_string())


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    if sys.argv[1] == "e3":
        e3()
    else:
        g = frame()
        {"e1": e1, "e2": e2}[sys.argv[1]](g)
