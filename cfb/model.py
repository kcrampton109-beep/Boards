"""Walk-forward power ratings for every FBS + FCS team.

For each slate (season, week) the ratings are fit ONLY on games already played that season, pulled
toward a preseason prior built from last season's final ratings, returning production, talent and
recruiting. Three rating sets are fit each week:
  pts  points scored / allowed            (offense O, defense D, + scoring level mu, home field h)
  ppa  EPA per play, garbage time removed (offense o, defense d)
  sr   success rate, garbage time removed
Output: data/preds.parquet, one row per game with each model's projected margin and total.
"""
import json, pathlib
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
D1 = ("fbs", "fcs")


# ------------------------------------------------------------------ data
def load():
    g = pd.read_parquet(DATA / "games.parquet")
    g = g[g["home_cls"].isin(D1) & g["away_cls"].isin(D1)].copy()
    g["site"] = np.where(g["neutral"].fillna(False).astype(bool), 0.0, 1.0)
    # slate = a week of games; postseason weeks are numbered after the regular season
    g["wk"] = np.where(g["season_type"] == "postseason", 20 + g["week"], g["week"])
    g = g.sort_values("start").reset_index(drop=True)
    tg = pd.read_parquet(DATA / "tg.parquet")
    side = pd.concat([g[["game_id", "home", "home_id"]].rename(columns={"home": "team", "home_id": "team_id"}),
                      g[["game_id", "away", "away_id"]].rename(columns={"away": "team", "away_id": "team_id"})])
    tg = tg.merge(side, on=["game_id", "team"], how="inner")
    return g, tg


def team_season_features():
    """Talent, recruiting (4-class avg) and returning production by team name and season."""
    rows = {}
    for y in range(2014, 2027):
        for kind, fname, key in (("talent", f"talent__year-{y}.json", "talent"),
                                 ("recruit", f"recruiting_teams__year-{y}.json", "points"),
                                 ("ret", f"player_returning__year-{y}.json", "percentPPA"),
                                 ("ret_pass", f"player_returning__year-{y}.json", "percentPassingPPA")):
            p = DATA / "raw" / fname
            if not p.exists():
                continue
            for r in json.loads(p.read_text()):
                team = r.get("team") or r.get("school")
                yr = r.get("year") or r.get("season")
                if team is None or r.get(key) is None:
                    continue
                rows.setdefault((team, int(yr)), {})[kind] = float(r[key])
    f = pd.DataFrame([dict(team=k[0], season=k[1], **v) for k, v in rows.items()])
    f = f.sort_values(["team", "season"])
    # recruiting: average of this and the previous three classes (the roster)
    f["recruit4"] = f.groupby("team")["recruit"].transform(lambda s: s.rolling(4, min_periods=1).mean())
    return f


# ------------------------------------------------------------------ ridge with priors
def solve(off, dfn, site, y, w, n, prior_o, prior_d, lam, mu0, h0, lam_mu=5.0, lam_h=40.0):
    """y_i = mu + h*site_i + O[off_i] - D[def_i]; ratings shrunk toward priors with weight lam
    (in units of weighted observations). Normal equations are accumulated sparsely (4 nonzeros/row)."""
    k = 2 + 2 * n
    m = len(y)
    cols = np.column_stack([np.zeros(m, int), np.ones(m, int), 2 + off, 2 + n + dfn])
    vals = np.column_stack([np.ones(m), site, np.ones(m), -np.ones(m)])
    A = np.zeros((k, k))
    wv = vals * w[:, None]
    for a in range(4):
        for b in range(4):
            np.add.at(A, (cols[:, a], cols[:, b]), wv[:, a] * vals[:, b])
    bvec = np.zeros(k)
    for a in range(4):
        np.add.at(bvec, cols[:, a], wv[:, a] * y)
    pen = np.r_[lam_mu, lam_h, np.full(n, lam), np.full(n, lam)]
    pri = np.r_[mu0, h0, prior_o, prior_d]
    A[np.diag_indices(k)] += pen
    bvec += pen * pri
    x = np.linalg.solve(A, bvec)
    return x[0], x[1], x[2:2 + n], x[2 + n:]


def observations(g, tg, kind):
    """Rows of (season, wk, start, off team_id, def team_id, site, y, weight)."""
    if kind == "pts":
        h = pd.DataFrame(dict(game_id=g["game_id"], season=g["season"], wk=g["wk"], start=g["start"],
                              off=g["home_id"], dfn=g["away_id"], site=g["site"], y=g["home_pts"], w=1.0))
        a = pd.DataFrame(dict(game_id=g["game_id"], season=g["season"], wk=g["wk"], start=g["start"],
                              off=g["away_id"], dfn=g["home_id"], site=-g["site"], y=g["away_pts"], w=1.0))
        o = pd.concat([h, a])
        return o.dropna(subset=["y"])
    col = {"ppa": "o_ppa", "sr": "o_successRate"}[kind]
    t = tg.merge(g[["game_id", "season", "wk", "start", "home_id", "away_id", "site"]], on="game_id")
    t = t[t[col].notna() & (t["o_plays"] > 20)]
    opp = np.where(t["team_id"] == t["home_id"], t["away_id"], t["home_id"])
    s = np.where(t["team_id"] == t["home_id"], t["site"], -t["site"])
    return pd.DataFrame(dict(game_id=t["game_id"], season=t["season"], wk=t["wk"], start=t["start"],
                             off=t["team_id"], dfn=opp, site=s, y=t[col], w=t["o_plays"] / 65.0))


# ------------------------------------------------------------------ priors
def fit_prior_model(final, feats, seasons):
    """OLS of season-S final rating on last season's rating + roster features, for O and D."""
    df = build_prior_frame(final, feats)
    df = df[df["season"].isin(seasons) & df["O"].notna() & df["last_O"].notna()]
    coefs = {}
    for tgt, last in (("O", "last_O"), ("D", "last_D")):
        X = prior_X(df, last)
        beta, *_ = np.linalg.lstsq(X, df[tgt].values, rcond=None)
        coefs[tgt] = beta
    return coefs


def prior_X(df, last):
    return np.column_stack([np.ones(len(df)), df[last].fillna(df["cls_mean_" + last[-1]]),
                            df["last_missing"], df["fcs"], df["talent_z"], df["recruit_z"],
                            df["ret_c"] * df[last].fillna(0), df["ret_c"], df["ret_missing"]])


def build_prior_frame(final, feats):
    """final: DataFrame(team_id, team, season, cls, O, D) of end-of-season ratings."""
    f = final.copy()
    last = final[["team_id", "season", "O", "D"]].rename(columns={"O": "last_O", "D": "last_D"})
    last["season"] += 1
    f = f.merge(last, on=["team_id", "season"], how="left")
    f = f.merge(feats, on=["team", "season"], how="left")
    f["fcs"] = (f["cls"] == "fcs").astype(float)
    f["last_missing"] = f["last_O"].isna().astype(float)
    for c in ("O", "D"):
        f["cls_mean_" + c] = f.groupby(["season", "cls"])["last_" + c].transform("mean").fillna(0)
    for c, z in (("talent", "talent_z"), ("recruit4", "recruit_z")):
        m = f.groupby("season")[c].transform("mean")
        s = f.groupby("season")[c].transform("std")
        f[z] = ((f[c] - m) / s).fillna(-1.5 * f["fcs"]).clip(-3, 3)
    f["ret_missing"] = f["ret"].isna().astype(float)
    f["ret_c"] = (f["ret"] - 0.6).fillna(0)
    return f


# ------------------------------------------------------------------ walk forward
def run(kind, lam=4.0, seasons=range(2014, 2027), half_life=None, cap=56, lag_days=0):
    g, tg = load()
    feats = team_season_features()
    obs = observations(g, tg, kind)
    teams = pd.concat([g[["home_id", "home", "home_cls", "season"]].set_axis(["team_id", "team", "cls", "season"], axis=1),
                       g[["away_id", "away", "away_cls", "season"]].set_axis(["team_id", "team", "cls", "season"], axis=1)]
                      ).drop_duplicates(["team_id", "season"])
    finals, out = [], []
    mu_prev, h_prev = obs["y"].mean(), (2.0 if kind == "pts" else 0.02)
    for season in seasons:
        tm = teams[teams["season"] == season].reset_index(drop=True)
        idx = {t: i for i, t in enumerate(tm["team_id"])}
        n = len(tm)
        # preseason prior
        fin = pd.concat(finals) if finals else None
        if fin is not None and fin["season"].nunique() >= 2:
            coefs = fit_prior_model(fin, feats, set(fin["season"]))
        else:
            coefs = None
        cur = tm.assign(O=np.nan, D=np.nan)
        pf = build_prior_frame(pd.concat([fin, cur]) if fin is not None else cur, feats)
        pf = pf[pf["season"] == season].set_index("team_id").reindex(tm["team_id"])
        if coefs is not None:
            pO = prior_X(pf, "last_O") @ coefs["O"]
            pD = prior_X(pf, "last_D") @ coefs["D"]
        else:
            pO = (0.6 * pf["last_O"].fillna(pf["cls_mean_O"])).values
            pD = (0.6 * pf["last_D"].fillna(pf["cls_mean_D"])).values
            fcs = (tm["cls"] == "fcs").values
            if fin is None:  # very first season: FCS teams start well below FBS
                sc = 9.0 if kind == "pts" else (0.12 if kind == "ppa" else 0.05)
                pO = np.where(fcs, -sc / 2, sc / 4)
                pD = np.where(fcs, -sc / 2, sc / 4)
        so = obs[obs["season"] == season]
        so = so[so["off"].isin(idx) & so["dfn"].isin(idx)]
        gs = g[g["season"] == season]
        for wk, slate in gs.groupby("wk"):
            t0 = slate["start"].min()
            tr = so[so["start"] < t0 - pd.Timedelta(hours=6) - pd.Timedelta(days=lag_days)]
            if len(tr):
                w = tr["w"].values
                if half_life:
                    w = w * 0.5 ** ((t0 - tr["start"]).dt.days.values / 7.0 / half_life)
                y = tr["y"].clip(upper=cap).values if kind == "pts" else tr["y"].values
                mu, h, O, D = solve(tr["off"].map(idx).values, tr["dfn"].map(idx).values, tr["site"].values,
                                    y, w, n, pO, pD, lam, mu_prev, h_prev)
            else:
                mu, h, O, D = mu_prev, h_prev, pO, pD
            ng = tr.groupby("off").size()
            for r in slate.itertuples():
                if r.home_id not in idx or r.away_id not in idx:
                    continue
                i, j = idx[r.home_id], idx[r.away_id]
                ph = mu + h * r.site + O[i] - D[j]
                pa = mu - h * r.site + O[j] - D[i]
                out.append(dict(game_id=r.game_id, kind=kind, home_hat=ph, away_hat=pa,
                                margin_hat=ph - pa, total_hat=ph + pa,
                                prior_margin=(pO[i] - pD[j]) - (pO[j] - pD[i]) + 2 * h_prev * r.site,
                                n_home=int(ng.get(r.home_id, 0)), n_away=int(ng.get(r.away_id, 0)),
                                O_home=O[i], D_home=D[i], O_away=O[j], D_away=D[j], mu=mu, h=h))
        # end-of-season ratings (all games, light prior) feed next year's prior
        if len(so):
            mu, h, O, D = solve(so["off"].map(idx).values, so["dfn"].map(idx).values, so["site"].values,
                                so["y"].values, so["w"].values, n, pO, pD, 1.0, mu_prev, h_prev)
            finals.append(tm.assign(O=O, D=D, season=season)[["team_id", "team", "season", "cls", "O", "D"]])
            mu_prev, h_prev = mu, h
    return pd.DataFrame(out)


if __name__ == "__main__":
    import sys
    lam = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    res = []
    for kind in ("pts", "ppa", "sr"):
        p = run(kind, lam=lam)
        print(kind, len(p))
        res.append(p)
    p = pd.concat(res)
    wide = p.pivot_table(index="game_id", columns="kind",
                         values=["margin_hat", "total_hat", "prior_margin", "n_home", "n_away"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide.reset_index().to_parquet(DATA / "preds.parquet")
    p.to_parquet(DATA / "preds_long.parquet")
    print("saved", wide.shape)
