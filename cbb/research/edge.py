"""Where is the CBB edge? (Nov 4 2026, after live November ran CLV -0.44.)

Two fixes to the blend, both using only information available at the time:
  margin scale   the ridge ratings are shrunk, so the model's margins run ~7% narrower than the market's and it kept
                 backing big underdogs; margin x k, k = OLS (no intercept) of actual margin on the model margin over
                 all earlier seasons (walk-forward: 1.08 for 2023 ... 1.06 for 2026)
  totals level   the model's scoring level can drift from the market's (2026 opened ~5 pts under the openers, so it
                 backed nearly every under); total - mean(model total - opening total) over games of the last 7 days
                 and the same day (the game itself excluded)
Then candidate rules are graded by season, against the opener at -110 (DK's first number = Action's opener 89% of
the time, live Openers tab). Writes the tables to stdout (research/EDGE.md quotes them).
Usage: python research/edge.py   (needs preds_pts / preds_market from model.py, as for evaluate.py)
"""
import numpy as np
import pandas as pd
import evaluate as ev


def frame():
    g = ev.frame(ev.preds("blend"))
    k = {s: float((x.margin * x.margin_hat).sum() / (x.margin_hat ** 2).sum())
         for s in range(2022, 2027) for x in [g[g["season"] < s]]}
    g["k"] = g["season"].map(k)
    g["m_adj"] = g["margin_hat"] * g["k"]
    x = g[g["topen"].notna()]
    d = x["total_hat"] - x["topen"]
    bias = pd.Series(np.nan, index=g.index)
    for s, xs in x.groupby("season"):
        agg = d.loc[xs.index].groupby(xs["day"]).agg(["sum", "count"])
        for day in agg.index:
            w = agg.loc[(agg.index >= day - pd.Timedelta(days=7)) & (agg.index <= day)]
            idx = xs.index[xs["day"] == day]
            bias.loc[idx] = (w["sum"].sum() - d.loc[idx]) / max(w["count"].sum() - 1, 1)
    g["t_adj"] = g["total_hat"] - bias
    return g, k


def grade(d):
    s = ev.summary(d)
    seas = [ev.summary(y)["ROI %"] for _, y in d.groupby("season")]
    return dict(n=s["n"], CLV=s["CLV pts"], beat=s["beat close %"], ATS=s["ATS %"], units=s["units"], ROI=s["ROI %"],
                by_season=" / ".join(f"{v:+.1f}" for v in seas))


def rules(L, days):
    """Candidate rule set: Dec-Mar (incl. postseason) spreads 3+ and 4+, totals 5+ and 6+ (corrected model);
    November spreads 4+ on favourites only, totals 5+ overs only."""
    sp = {t: ev.plays(L, t, "spread", 20.0) for t in (3, 4)}
    to = {t: ev.plays(L, t, "total", 40.0) for t in (5, 6)}
    nov = lambda d: d[d["period"] == "1 Nov"]
    rest = lambda d: d[d["period"] != "1 Nov"]
    out = {
        "Dec-Mar spreads 3+": rest(sp[3]), "Dec-Mar spreads 4+": rest(sp[4]),
        "Dec-Mar totals 5+": rest(to[5]), "Dec-Mar totals 6+": rest(to[6]),
        "Nov spreads 4+, favourites": nov(sp[4])[lambda d: d["fav"] > 0],
        "Nov spreads 4+, underdogs (not a rule)": nov(sp[4])[lambda d: d["fav"] < 0],
        "Nov totals 5+, overs": nov(to[5])[lambda d: d["pick"] > 0],
        "Nov totals 5+, unders (not a rule)": nov(to[5])[lambda d: d["pick"] < 0],
    }
    rows = []
    for lab, d in out.items():
        n_days = days["nov" if lab.startswith("Nov") else "rest"]
        rows.append(dict(rule=lab, **grade(d), per_day=round(len(d) / n_days, 1)))
    return pd.DataFrame(rows).set_index("rule")


def main():
    g, k = frame()
    print("margin scale by season:", {s: round(v, 3) for s, v in k.items()})
    L = g[g["season"].isin(ev.LINE_SEASONS)]
    lined = L[L["open"].notna()]
    days = dict(nov=lined[lined.period == "1 Nov"].groupby("season")["day"].nunique().sum(),
                rest=lined[lined.period != "1 Nov"].groupby("season")["day"].nunique().sum())
    pd.set_option("display.width", 200)
    for lab, mcol, tcol in (("raw blend (live board until Nov 4)", "margin_hat", "total_hat"),
                            ("corrected blend", "m_adj", "t_adj")):
        print(f"\n## {lab}\n")
        print(ev.md(rules(L.assign(margin_hat=L[mcol], total_hat=L[tcol]), days)))
    C = L.assign(margin_hat=L["m_adj"], total_hat=L["t_adj"])
    for t in (3, 4):
        d = ev.plays(C, t, "spread", 20.0)
        d["side"] = np.where(d.fav > 0, "favourite", np.where(d.fav < 0, "underdog", "pick'em"))
        d["when"] = np.where(d.period == "1 Nov", "Nov", "Dec-Mar")
        print(f"\n### corrected spreads {t}+: month x side\n")
        print(ev.md(pd.DataFrame({f"{w} {s}": grade(x) for (w, s), x in d.groupby(["when", "side"]) if len(x) > 20}).T))
    for t in (5,):
        d = ev.plays(C, t, "total", 40.0)
        d["side"] = np.where(d.pick > 0, "over", "under")
        d["when"] = np.where(d.period == "1 Nov", "Nov", "Dec-Mar")
        print(f"\n### corrected totals {t}+: month x side\n")
        print(ev.md(pd.DataFrame({f"{w} {s}": grade(x) for (w, s), x in d.groupby(["when", "side"])}).T))


if __name__ == "__main__":
    main()
