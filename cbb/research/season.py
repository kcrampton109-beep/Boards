"""This season so far, graded like the backtest (Nov 18 2026).

Every finished D1 game of the current season gets the live engine's corrected blend as of the morning of its game day
(ratings_now on earlier games only, margins x margin_scale, totals measured against the market's level), then is graded
against Action's opener and the DK/FD/MGM closing number, like research/edge.py.

Questions fixed before running (no new cuts after looking):
  Q1  The December rules (spreads 3+ / 4+, totals 5+ / 6+, either side) on this season's games, by week: does CLV beyond
      market drift grow as the season matures, as it did in 2023-25?
  Q2  November overs 5+ (the only November main play), since the rule went live on Nov 4.
  Q3  The live ledger: every tier as logged (best DK/FD/MGM price at first sight), CLV and record.
  Q4  The live RLM tiers, logged at the price when the signal fired (Nov 12 on, after the placeholder fix).
Usage: python research/season.py   (needs this season's files in cbb/data/season, e.g. after a local engine run)
"""
import json, pathlib, sys
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import engine, model, evaluate as ev   # noqa: E402

BE = 100 * 110 / 210


def frame(season):
    now = engine.now_et()
    hist = engine.load_json(engine.DATA / "hist.json", {})
    k = hist.get("margin_scale", engine.RULES["margin_scale"])
    g, tm = engine.load_season(season, now)
    g = g[g["d1"]].copy()
    done = g["completed"].fillna(False).astype(bool)
    rows = []
    for day in sorted(g.loc[done, "day"].unique()):
        rts = {t: engine.ratings_now(t, g, tm, season, pd.Timestamp(day).date()) for t in engine.MODELS}
        for r in g[(g.day == day) & done].itertuples():
            p = {t: engine.predict(rt, r.home_eid, r.away_eid, 0.0 if r.neutral else 1.0) for t, rt in rts.items()}
            if any(v is None for v in p.values()):
                continue
            rows.append(dict(espn_id=r.espn_id, m_raw=np.mean([v[0] for v in p.values()]), t_raw=np.mean([v[1] for v in p.values()])))
    l = g.merge(pd.DataFrame(rows), on="espn_id")
    l["open"] = l["open_spread"].where((l["open_spread"] + l["open_spread_away"]).abs() <= 0.6)
    l["topen"] = l["open_total"]
    l["margin"], l["points"] = l.home_pts - l.away_pts, l.home_pts + l.away_pts
    l["close"], l["tclose"] = model.closing(l.drop(columns=["cons_spread", "cons_total"]))
    l.loc[(l.open - l.close).abs() >= 10, "open"] = np.nan
    l.loc[(l.topen - l.tclose).abs() >= 25, "topen"] = np.nan
    # totals level: model minus opener over the last 7 days and the same day, the game itself left out (as live)
    d = l.t_raw - l.topen
    x = l[l.topen.notna()]
    agg = d.loc[x.index].groupby(x.day).agg(["sum", "count"])
    lvl = pd.Series(np.nan, index=l.index)
    for day in agg.index:
        w = agg.loc[(agg.index >= day - pd.Timedelta(days=7)) & (agg.index <= day)]
        idx = x.index[x.day == day]
        lvl.loc[idx] = (w["sum"].sum() - d.loc[idx]) / max(w["count"].sum() - 1, 1)
    l["margin_hat"], l["total_hat"] = k * l.m_raw, l.t_raw - lvl
    first = l.day.min()
    l["wk"] = ((l.day - first).dt.days // 7 + 1).astype(int)
    l["period"] = "1 Nov"
    l["neutral"] = l["neutral"].astype(bool)
    return l


def plays(l, thr, kind):
    """Plays at gap >= thr, with drift-adjusted CLV computed within each week (lines drift toward favourites / home
    teams by a different amount as the season goes)."""
    d = ev.plays(l, thr, kind, 20.0 if kind == "spread" else 40.0)
    y = l[l.open.notna() & l.close.notna()] if kind == "spread" else l[l.topen.notna() & l.tclose.notna()]
    if kind == "spread":
        mv, fav = y.open - y.close, np.sign(-y.open)
        base = pd.concat([pd.DataFrame(dict(wk=y.wk, fav=fav, loc=np.where(y.neutral, "n", "h"), clv=mv)),
                          pd.DataFrame(dict(wk=y.wk, fav=-fav, loc=np.where(y.neutral, "n", "a"), clv=-mv))])
        drift = base.groupby(["wk", "fav", "loc"])["clv"].mean().rename("drift").reset_index()
        d = d.drop(columns=["exp_clv", "xclv"]).merge(drift, on=["wk", "fav", "loc"], how="left")
    else:
        mv = y.tclose - y.topen
        base = pd.concat([pd.DataFrame(dict(wk=y.wk, pick=1.0, clv=mv)), pd.DataFrame(dict(wk=y.wk, pick=-1.0, clv=-mv))])
        drift = base.groupby(["wk", "pick"])["clv"].mean().rename("drift").reset_index()
        d = d.drop(columns=["exp_clv", "xclv"]).merge(drift, on=["wk", "pick"], how="left")
    d["xclv"] = d["clv"] - d["drift"]
    return d


def grade(d):
    w, l_ = int((d.res > 0).sum()), int((d.res < 0).sum())
    c = d.clv.dropna()
    return pd.Series({"n": len(d), "rec": f"{w}-{l_}", "ATS": round(100 * w / max(w + l_, 1), 1),
                      "ROI": round(100 * (w * 100 / 110 - l_) / max(w + l_, 1), 1), "CLV": round(c.mean(), 2),
                      "beyond drift": round(d.xclv.mean(), 2), "beat close %": round(100 * (c > 0).mean())})


def ledger_tables(led):
    d = led[led.status.isin(["W", "L", "P"])].copy()
    def g(x):
        w, l_ = (x.status == "W").sum(), (x.status == "L").sum(); c = x.clv.dropna()
        risk = x[x.status != "P"].stake.sum()
        return pd.Series({"n": len(x), "rec": f"{w}-{l_}", "units": round(x.units.sum(), 2),
                          "ROI": round(100 * x.units.sum() / max(risk, 1), 1), "CLV": round(c.mean(), 2),
                          "beat close %": round(100 * (c > 0).mean())})
    return d.groupby("tier").apply(g, include_groups=False), g(d)


def main():
    pd.set_option("display.width", 220)
    season = engine.season_of(engine.now_et().date())
    l = frame(season)
    l.to_parquet(engine.DATA / f"season_{season}.parquet")
    print(f"{len(l)} finished D1 games with predictions; with an opener {l.open.notna().sum()}, through {l.day.max():%b %d}")
    print("\n## Q1  December rules on this season's games, by week (opener vs DK/FD close)\n")
    rows = []
    for kind, t in (("spread", 3), ("spread", 4), ("total", 5), ("total", 6)):
        d = plays(l, t, kind)
        rows.append(pd.concat([pd.Series({"rule": f"{kind} {t}+", "week": "all"}), grade(d)]))
        for w, x in d.groupby("wk"):
            rows.append(pd.concat([pd.Series({"rule": f"{kind} {t}+", "week": w}), grade(x)]))
    print(pd.DataFrame(rows).set_index(["rule", "week"]).to_string())
    print("\n## Q2  November overs 5+ since Nov 4 (opener-based)\n")
    o = plays(l, 5, "total")
    o = o[(o.pick > 0) & (o.day >= "2026-11-04")]
    print(grade(o).to_string())
    led = pd.DataFrame(json.loads((engine.BOARD / "ledger.json").read_text()))
    t, a = ledger_tables(led)
    print("\n## Q3  Live ledger by tier (as logged)\n"); print(t.to_string()); print("all:", a.to_dict())
    r = led[led.tier.str.startswith("RLM") & (led.flagged >= "2026-11-12T12")]
    t, a = ledger_tables(r)
    print("\n## Q4  RLM tiers since the Nov 12 fix (price when the signal fired)\n"); print(t.to_string()); print("all:", a.to_dict())


if __name__ == "__main__":
    main()
