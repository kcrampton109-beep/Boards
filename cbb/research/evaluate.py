"""Backtest the walk-forward ratings against Action Network OPENERS (book 30) and grade each play by
  CLV  opener number vs the DK / FD / BetMGM closing number (median of the books that have the game), in points
  ATS  result at the opener number, -110 (win +0.909u, loss -1u, push 0)
Prints every table and writes them to cbb/research/tables.md (REPORT.md summarises them)."""
import pathlib, sys
import numpy as np
import pandas as pd
import model

CONF = {1: "America East", 2: "ACC", 3: "A-10", 4: "Big East", 5: "Big Sky", 6: "Big South", 7: "Big Ten", 8: "Big 12",
        9: "Big West", 10: "CAA", 11: "C-USA", 12: "Ivy", 13: "MAAC", 14: "MAC", 16: "MEAC", 18: "MVC", 19: "NEC",
        20: "OVC", 21: "Pac-12", 22: "Patriot", 23: "SEC", 24: "SoCon", 25: "Southland", 26: "SWAC", 27: "Sun Belt",
        29: "WCC", 30: "WAC", 44: "Mountain West", 45: "Horizon", 46: "ASUN", 49: "Summit", 62: "AAC"}
P6 = {"ACC", "Big East", "Big Ten", "Big 12", "SEC", "Pac-12"}
LINE_SEASONS = (2023, 2024, 2025)
TIER_ORDER = {"power 6": 0, "mid-major": 1, "low-major": 2}


def conf_tiers(finals):
    """Power 6 by name (Pac-12 through 2023-24, its last full season); mid-major = the 10 next-best conferences by
    average end-of-season net rating over the seasons in the data; everything else low-major."""
    f = pd.read_parquet(model.DATA / "finals_pts.parquet") if finals is None else finals
    f = f.assign(conf=pd.to_numeric(f["conf_eid"], errors="coerce").map(CONF), net=f["O"] + f["D"])
    rank = f[~f["conf"].isin(P6)].groupby("conf")["net"].mean().sort_values(ascending=False)
    return set(rank.index[:10]), rank


def frame(preds, finals=None):
    g, teams = model.load()
    g = g.merge(preds, on="espn_id")
    g["margin"] = g["home_pts"] - g["away_pts"]
    g["points"] = g["home_pts"] + g["away_pts"]
    # CLV is measured against DK / FD / BetMGM only (model.load's close falls back to consensus for older seasons)
    g["close"], g["tclose"] = model.closing(g.drop(columns=["cons_spread", "cons_total"]))
    g["open"] = g["open_spread"].where((g["open_spread"] + g["open_spread_away"]).abs() <= 0.6)
    g["topen"] = g["open_total"]
    # an opener 10+ points from the close (or a total 25+) is a different game / mis-tag, not a line move
    g.loc[(g["open"] - g["close"]).abs() >= 10, "open"] = np.nan
    g.loc[(g["topen"] - g["tclose"]).abs() >= 25, "topen"] = np.nan
    # calendar buckets
    post = (g["season_type"] == 3) | ((g["day"].dt.month.isin([2, 3, 4])) & (g["day"].dt.strftime("%m%d") >= "0220")
                                      & g["notes"].str.contains("Tournament|Championship|Playoffs", na=False))
    m = g["day"].dt.month
    g["period"] = np.select([post, m == 11, m == 12], ["4 postseason", "1 Nov", "2 Dec"], "3 Jan-Mar")
    # conference tiers (this season's conference)
    mids, _ = conf_tiers(finals)
    for s in ("home", "away"):
        c = pd.to_numeric(g[f"{s}_conf_eid"], errors="coerce").map(CONF)
        p6 = c.isin(P6) & ~((c == "Pac-12") & (g["season"] >= 2024))
        g[f"{s}_tier"] = np.select([p6, c.isin(mids)], ["power 6", "mid-major"], "low-major")
    g["grp"] = [min(a, b, key=TIER_ORDER.get) for a, b in zip(g["home_tier"], g["away_tier"])]
    g["hours_before"] = (g["start"] - g["open_ts"]).dt.total_seconds() / 3600 if "open_ts" in g else np.nan
    return g


def naive_clv(g, kind="spread"):
    """Average CLV of blindly betting one kind of side at the opener (same season and period): spreads by
    favourite / underdog x home / away / neutral, totals by over / under. Lines drift toward favourites and home
    teams between the open and the DK/FD/MGM close, so part of any spread CLV is that drift, not the model."""
    if kind == "spread":
        x = g[g["open"].notna() & g["close"].notna()]
        mv, fav = x["open"] - x["close"], np.sign(-x["open"])
        rows = pd.concat([
            pd.DataFrame(dict(season=x["season"], period=x["period"], fav=fav, loc=np.where(x["neutral"], "n", "h"), clv=mv)),
            pd.DataFrame(dict(season=x["season"], period=x["period"], fav=-fav, loc=np.where(x["neutral"], "n", "a"), clv=-mv))])
        return rows.groupby(["season", "period", "fav", "loc"])["clv"].mean().rename("exp_clv").reset_index()
    x = g[g["topen"].notna() & g["tclose"].notna()]
    mv = x["tclose"] - x["topen"]
    rows = pd.concat([pd.DataFrame(dict(season=x["season"], period=x["period"], pick=1.0, clv=mv)),
                      pd.DataFrame(dict(season=x["season"], period=x["period"], pick=-1.0, clv=-mv))])
    return rows.groupby(["season", "period", "pick"])["clv"].mean().rename("exp_clv").reset_index()


def plays(g, thr, kind="spread", max_gap=20.0):
    """One row per play at gap >= thr. pick +1 home/over, -1 away/under. clv and res are from the pick's side;
    xclv = clv minus the naive drift for that kind of side (see naive_clv)."""
    if kind == "spread":
        gap = g["margin_hat"] + g["open"]  # >0: model likes home vs the opener
        d = g.assign(gap=gap)[gap.abs().between(thr, max_gap)].copy()
        d["pick"] = np.sign(d["gap"])
        d["clv"] = d["pick"] * (d["open"] - d["close"])
        d["res"] = d["pick"] * (d["margin"] + d["open"])
        d["res_close"] = d["pick"] * (d["margin"] + d["close"])
        d["fav"] = d["pick"] * np.sign(-d["open"])
        d["loc"] = np.where(d["neutral"], "n", np.where(d["pick"] > 0, "h", "a"))
        d = d.merge(naive_clv(g), on=["season", "period", "fav", "loc"], how="left")
    else:
        gap = g["total_hat"] - g["topen"]
        d = g.assign(gap=gap)[gap.abs().between(thr, max_gap)].copy()
        d["pick"] = np.sign(d["gap"])
        d["clv"] = d["pick"] * (d["tclose"] - d["topen"])
        d["res"] = d["pick"] * (d["points"] - d["topen"])
        d["res_close"] = d["pick"] * (d["points"] - d["tclose"])
        d = d.merge(naive_clv(g, "total"), on=["season", "period", "pick"], how="left")
    d["xclv"] = d["clv"] - d["exp_clv"]
    d["agap"] = d["gap"].abs()
    return d


def summary(d):
    c = d["clv"].dropna()
    w, l, p = (d["res"] > 0).sum(), (d["res"] < 0).sum(), (d["res"] == 0).sum()
    units = w * 100 / 110 - l
    return pd.Series({"n": len(d), "CLV pts": round(c.mean(), 2) if len(c) else np.nan,
                      "xCLV": round(d["xclv"].mean(), 2) if d["xclv"].notna().any() else np.nan,
                      "beat close %": round(100 * (c > 0).mean(), 1) if len(c) else np.nan,
                      "worse %": round(100 * (c < 0).mean(), 1) if len(c) else np.nan,
                      "ATS W-L-P": f"{w}-{l}-{p}", "ATS %": round(100 * w / (w + l), 1) if w + l else np.nan,
                      "units": round(units, 1), "ROI %": round(100 * units / max(w + l + p, 1), 1)})


def table(d, by):
    t = d.groupby(by).apply(summary, include_groups=False) if by else summary(d).to_frame().T
    return t


def md(t):
    """Markdown table (index included) without the tabulate dependency."""
    t = t.reset_index().rename(columns={"index": " "})
    fmt = lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else (
        f"{int(v)}" if isinstance(v, (int, np.integer)) or (isinstance(v, float) and v.is_integer() and abs(v) > 20) else str(v))
    head = "| " + " | ".join(map(str, t.columns)) + " |"
    sep = "|" + "|".join("---" if t[c].dtype == object else "---:" for c in t.columns) + "|"
    return "\n".join([head, sep] + ["| " + " | ".join(fmt(v) for v in r) + " |" for r in t.itertuples(index=False)])


def bucket(a):
    return pd.cut(a, [2, 3, 4, 5, 6, 8, 10, 20], right=False,
                  labels=["2-3", "3-4", "4-5", "5-6", "6-8", "8-10", "10+"]).astype(str)


def preds(variant):
    """pts = results model, market = closing-line ratings, blend = their average."""
    if variant != "blend":
        return pd.read_parquet(model.DATA / f"preds_{variant}.parquet")
    a, b = preds("pts"), preds("market")
    m = a[["espn_id", "margin_hat", "total_hat", "gp_min"]].merge(b[["espn_id", "margin_hat", "total_hat"]], on="espn_id")
    for c in ("margin_hat", "total_hat"):
        m[c] = (m.pop(c + "_x") + m.pop(c + "_y")) / 2
    return m


def per_day(d, L):
    """Plays per day with games, over the seasons and periods the plays come from."""
    if not len(d):
        return 0.0
    x = L[L["open"].notna() & L["season"].isin(d["season"].unique()) & L["period"].isin(d["period"].unique())]
    return round(len(d) / x.groupby("season")["day"].nunique().sum(), 1)


def timing(L):
    x = L[L["open_ts"].notna()].copy()
    x["post_hour_et"] = x["open_ts"].dt.tz_convert("US/Eastern").dt.hour
    x["day_before"] = x["open_ts"].dt.tz_convert("US/Eastern").dt.tz_localize(None).dt.normalize() < x["day"]
    f = lambda v: pd.Series({"n": len(v), "median h": v["hours_before"].median(), "p25 h": v["hours_before"].quantile(.25),
                             "p75 h": v["hours_before"].quantile(.75), "posted day before %": 100 * v["day_before"].mean(),
                             "after tip %": 100 * (v["hours_before"] < 0).mean(),
                             "common ET hour": v["post_hour_et"].mode().iloc[0]})
    return x.groupby(["season", "period"]).apply(f, include_groups=False).round(1)


def drift(L):
    x = L[L["open"].notna() & L["close"].notna()]
    mv = x["open"] - x["close"]
    fav = np.sign(-x["open"])
    f = lambda v, m, fv: pd.Series({"n": len(v), "home side": (m).mean(), "favourite": (fv * m).mean(),
                                    "opener = close %": 100 * (m == 0).mean(), "avg |move|": m.abs().mean()})
    return x.groupby("season").apply(lambda v: f(v, mv.loc[v.index], fav.loc[v.index]), include_groups=False).round(2)


def compare(L_of, kind="spread", thresholds=(2, 3, 4, 5, 6), skip_nov=False, by_season=False):
    """One row per model x threshold (x season): the headline numbers side by side."""
    rows = []
    for v, L in L_of.items():
        for t in thresholds:
            d = plays(L, t, kind, 20.0 if kind == "spread" else 40.0)
            if skip_nov:
                d = d[d["period"] != "1 Nov"]
            parts = [("all", d)] + (list(d.groupby("season")) if by_season else [])
            for sea, x in parts:
                rows.append(pd.concat([pd.Series({"model": v, "gap": f">= {t}", "season": sea}), summary(x),
                                       pd.Series({"plays/day": per_day(x, L)})]))
    idx = ["model", "gap", "season"] if by_season else ["model", "gap"]
    return pd.DataFrame(rows).drop(columns=[] if by_season else ["season"]).set_index(idx)


def detail(L, out, label):
    pr = out.append
    pr(f"\n## {label}: spreads vs opener, by threshold (all line seasons)\n")
    pr(md(pd.DataFrame({f"gap >= {t}": pd.concat([summary(d := plays(L, t)), pd.Series({"plays/day": per_day(d, L)})])
                        for t in (2, 3, 4, 5, 6)}).T))
    for t in (3, 4, 5):
        d = plays(L, t)
        pr(f"\n### {label} gap >= {t}: by season\n"); pr(md(table(d, "season")))
        pr(f"\n### {label} gap >= {t}: by period\n"); pr(md(table(d, "period")))
        pr(f"\n### {label} gap >= {t}: by conference group (best tier in the game)\n"); pr(md(table(d, "grp")))
        pr(f"\n### {label} gap >= {t}: group x period\n"); pr(md(table(d, ["grp", "period"])))
        pr(f"\n### {label} gap >= {t}: December onward, by conference group\n")
        pr(md(table(d[d["period"] != "1 Nov"], "grp")))
    d = plays(L, 2)
    d["bucket"] = bucket(d["agap"])
    pr(f"\n### {label}: by gap bucket (non-overlapping)\n"); pr(md(table(d, "bucket")))
    pr(f"\n### {label}: gap bucket x season\n"); pr(md(table(d, ["bucket", "season"])))
    pr(f"\n### {label}: gap >= 3, favourite vs underdog picks\n")
    d = plays(L, 3)
    pr(md(table(d.assign(side=np.where(d["fav"] > 0, "favourite", np.where(d["fav"] < 0, "underdog", "pick'em"))), "side")))
    rows = []
    for t in (2, 3, 4, 5, 6):
        d = plays(L, t)
        if not len(d):
            continue
        per = d.groupby("day").size()
        for sea, x in d.groupby("season"):
            rows.append(dict(gap=f">= {t}", season=sea, plays=len(x), per_day=per_day(x, L),
                             busiest_day=int(per.loc[x["day"].unique()].max())))
    pr(f"\n### {label}: plays per game day\n"); pr(md(pd.DataFrame(rows).set_index(["gap", "season"])))


def main(*variants):
    """python evaluate.py [variants to detail]  ->  prints every table and writes cbb/research/tables.md"""
    variants = variants or ("market", "blend")
    gs = {v: frame(preds(v)) for v in ("pts", "market", "blend")}
    L_of = {v: g[g["season"].isin(LINE_SEASONS)] for v, g in gs.items()}
    L = L_of["pts"]
    out = ["# CBB backtest: every table (generated by evaluate.py; REPORT.md has the summary)", "",
           "pts = results model (points scored), market = ratings fit to earlier games' closing lines, "
           "blend = their average. Spreads: play when |model margin + opener| >= gap. CLV = opener vs DK/FD/BetMGM "
           "median close (points, + = good). xCLV = CLV minus the average drift for that kind of side (favourite/"
           "underdog x home/away/neutral, over/under) in the same season and period. ATS graded at the opener, -110."]
    pr = out.append
    pr("\n## Coverage (D1 vs D1 games)\n")
    pr(md(L.groupby("season").agg(games=("espn_id", "size"), opener=("open", "count"), dk=("dk_spread", "count"),
                                   fd=("fd_spread", "count"), mgm=("mgm_spread", "count"), close=("close", "count"),
                                   open_total=("topen", "count"))))
    pr("\n## When openers post (Action Network opener row `inserted`, hours before tip)\n"); pr(md(timing(L)))
    pr("\n## Market drift, opener to DK/FD/MGM close (points, + = the line moved toward that side)\n"); pr(md(drift(L)))
    _, rank = conf_tiers(None)
    pr("\n## Conference tiers (non-power conferences by average end-of-season net rating; top 10 = mid-major)\n")
    pr(", ".join(f"{c} {v:+.1f}" for c, v in rank.items()))
    pr("\n## Accuracy: RMSE of the final margin (games with opener and close)\n")
    rows = []
    for v, Lv in L_of.items():
        b = Lv[Lv["open"].notna() & Lv["close"].notna()]
        for (sea, per), x in b.groupby(["season", "period"]):
            rows.append(dict(season=sea, period=per, n=len(x), model=v, rmse=np.sqrt(((x.margin - x.margin_hat) ** 2).mean())))
    acc = pd.DataFrame(rows).pivot_table(index=["season", "period", "n"], columns="model", values="rmse")
    b = L[L["open"].notna() & L["close"].notna()]
    acc["opener"] = b.groupby(["season", "period"]).apply(lambda x: np.sqrt(((x.margin + x.open) ** 2).mean()), include_groups=False).values
    acc["close"] = b.groupby(["season", "period"]).apply(lambda x: np.sqrt(((x.margin + x.close) ** 2).mean()), include_groups=False).values
    pr(md(acc.round(2)))
    pr("\n## Model comparison: spreads vs opener, all periods\n"); pr(md(compare(L_of)))
    pr("\n## Model comparison: spreads vs opener, December onward (November excluded), by season\n")
    pr(md(compare(L_of, thresholds=(3, 4, 5, 6), skip_nov=True, by_season=True)))
    pr("\n## Model comparison: totals vs opening total, all periods\n")
    pr(md(compare(L_of, "total", (3, 4, 5, 6, 8, 10))))
    pr("\n## Model comparison: totals, December onward, by season\n")
    pr(md(compare(L_of, "total", (4, 5, 6, 8), skip_nov=True, by_season=True)))
    for v in variants:
        detail(L_of[v], out, v)
    for v in variants:
        Lv = L_of[v]
        for t in (5, 6):
            d = plays(Lv, t, "total", 40)
            pr(f"\n### {v} totals gap >= {t}: by period\n"); pr(md(table(d, "period")))
            pr(f"\n### {v} totals gap >= {t}: by conference group\n"); pr(md(table(d, "grp")))
            pr(f"\n### {v} totals gap >= {t}: December onward, by conference group\n")
            pr(md(table(d[d["period"] != "1 Nov"], "grp")))
            pr(f"\n### {v} totals gap >= {t}: over vs under\n")
            pr(md(table(d.assign(side=np.where(d.pick > 0, "over", "under")), "side")))
        d = plays(Lv, 3, "total", 40)
        d["bucket"] = pd.cut(d["agap"], [3, 4, 5, 6, 8, 10, 40], right=False,
                             labels=["3-4", "4-5", "5-6", "6-8", "8-10", "10+"]).astype(str)
        pr(f"\n### {v} totals: by gap bucket\n"); pr(md(table(d, "bucket")))
    text = "\n".join(out) + "\n"
    print(text)
    (pathlib.Path(__file__).resolve().parent / "tables.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:])
