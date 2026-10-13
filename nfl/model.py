"""Walk-forward NFL power ratings (port of the CFB model, adapted to the NFL's short season).

For each slate (season, week) every rating set is fit ONLY on games played before that slate. Games from
the previous season stay in with a decaying weight and the offseason pulls every team toward average.
Rating sets:
  pts  points scored / allowed (offense O, defense D, scoring level mu, home field h)
  epa  EPA per play with garbage time removed
  sr   success rate with garbage time removed
  mkt  market ratings: one number per team fit to past CLOSING spreads (what the market itself believed)
       plus a market totals rating (offense/defense) fit to past closing totals
Also a QB layer: each starter's EPA per dropback (shrunk by experience); the expected starter for a game is
the team's previous starter (what was known when the opener was posted) or the actual starter (for closes).
Output: data/preds.parquet (one row per game)."""
import pathlib
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"


def load():
    g = pd.read_parquet(DATA / "games.parquet")
    g = g.sort_values(["start", "game_id"]).reset_index(drop=True)
    tg = pd.read_parquet(DATA / "tg.parquet")
    return g, tg


# ------------------------------------------------------------------ ridge solvers
def solve_od(off, dfn, site, y, w, n, prior_o, prior_d, lam, mu0, h0, lam_mu=5.0, lam_h=60.0):
    """y = mu + h*site + O[off] - D[def], ratings shrunk toward priors (lam in weighted observations)."""
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


def solve_net(hi, ai, site, y, w, n, prior, lam, h0, lam_h=200.0):
    """y (home margin or -close) = h*site + R[home] - R[away]."""
    k = 1 + n
    X = np.zeros((len(y), k))
    r = np.arange(len(y))
    X[r, 0] = site
    X[r, 1 + hi] = 1.0
    X[r, 1 + ai] = -1.0
    Xw = X * w[:, None]
    A = X.T @ Xw
    b = Xw.T @ y
    pen = np.r_[lam_h, np.full(n, lam)]
    A[np.diag_indices(k)] += pen
    b += pen * np.r_[h0, prior]
    x = np.linalg.solve(A, b)
    return x[0], x[1:]


TEAMS = None


def team_index(g):
    global TEAMS
    TEAMS = sorted(set(g["home"]) | set(g["away"]))
    return {t: i for i, t in enumerate(TEAMS)}


def observations(g, tg, kind):
    base = g[["game_id", "season", "start", "home", "away", "site"]]
    if kind == "pts":
        h = pd.DataFrame(dict(game_id=g["game_id"], off=g["home"], dfn=g["away"], site=g["site"], y=g["home_pts"], w=1.0))
        a = pd.DataFrame(dict(game_id=g["game_id"], off=g["away"], dfn=g["home"], site=-g["site"], y=g["away_pts"], w=1.0))
        o = pd.concat([h, a]).dropna(subset=["y"])
        o["y"] = o["y"].clip(upper=45)
    else:
        col = {"epa": "epa_gt", "sr": "sr_gt", "ppd": "ppd", "pepa": "pass_epa_gt", "repa": "rush_epa_gt"}[kind]
        t = tg.merge(base, on="game_id")
        t = t[t[col].notna() & (t["plays_gt"] > 20)]
        home = t["team"] == t["home"]
        o = pd.DataFrame(dict(game_id=t["game_id"], off=t["team"], dfn=np.where(home, t["away"], t["home"]),
                              site=np.where(home, t["site"], -t["site"]), y=t[col], w=t["plays_gt"] / 60.0))
    return o.merge(g[["game_id", "season", "start"]], on="game_id")


# ------------------------------------------------------------------ walk forward
def run_od(g, tg, kind, lam=3.0, carry=0.4, decay_hl=6.0, prev_w=0.35, lag_days=0):
    """Offense/defense ratings refit before every slate. Weight of a past game = 0.5**(weeks_ago/decay_hl),
    times prev_w if it was last season; priors = carry * last season's final ratings."""
    idx = team_index(g)
    n = len(TEAMS)
    obs = observations(g, tg, kind)
    obs["oi"] = obs["off"].map(idx)
    obs["di"] = obs["dfn"].map(idx)
    out = []
    final_o, final_d = np.zeros(n), np.zeros(n)
    mu_prev = obs["y"].mean()
    h_prev = 2.0 if kind == "pts" else 0.02
    for season, gs in g.groupby("season"):
        pO, pD = carry * final_o, carry * final_d
        so = obs[(obs["season"] >= season - 1) & (obs["season"] <= season)]
        for wk, slate in gs.groupby("wk"):
            t0 = slate["start"].min()
            tr = so[so["start"] < t0 - pd.Timedelta(hours=8) - pd.Timedelta(days=lag_days)]
            if len(tr):
                age = (t0 - tr["start"]).dt.days.values / 7.0
                w = tr["w"].values * 0.5 ** (age / decay_hl) * np.where(tr["season"].values < season, prev_w, 1.0)
                mu, h, O, D = solve_od(tr["oi"].values, tr["di"].values, tr["site"].values, tr["y"].values, w, n,
                                       pO, pD, lam, mu_prev, h_prev)
            else:
                mu, h, O, D = mu_prev, h_prev, pO, pD
            for r in slate.itertuples():
                i, j = idx[r.home], idx[r.away]
                ph = mu + h * r.site + O[i] - D[j]
                pa = mu - h * r.site + O[j] - D[i]
                out.append(dict(game_id=r.game_id, **{f"{kind}_home": ph, f"{kind}_away": pa,
                                                      f"{kind}_margin": ph - pa, f"{kind}_total": ph + pa,
                                                      f"{kind}_net_home": O[i] + D[i], f"{kind}_net_away": O[j] + D[j]}))
        cur = so[so["season"] == season]
        if len(cur):
            mu, h, O, D = solve_od(cur["oi"].values, cur["di"].values, cur["site"].values, cur["y"].values,
                                   cur["w"].values, n, pO, pD, 2.0, mu_prev, h_prev)
            final_o, final_d = O - O.mean(), D - D.mean()
            mu_prev, h_prev = mu, h
    return pd.DataFrame(out)


def run_market(g, lam=0.75, carry=0.45, decay_hl=3.0, prev_w=0.3, line="close", tline="tclose", lag_days=0):
    """Market ratings from past closing spreads + market totals ratings from past closing totals."""
    idx = team_index(g)
    n = len(TEAMS)
    out = []
    final_r = np.zeros(n)
    final_o, final_d = np.zeros(n), np.zeros(n)
    h_prev, mu_prev = 2.5, 21.5
    lined = g.dropna(subset=[line]).copy()
    lined["hi"], lined["ai"] = lined["home"].map(idx), lined["away"].map(idx)
    for season, gs in g.groupby("season"):
        prior = carry * final_r
        pO, pD = carry * final_o, carry * final_d
        so = lined[(lined["season"] >= season - 1) & (lined["season"] <= season)]
        for wk, slate in gs.groupby("wk"):
            t0 = slate["start"].min()
            tr = so[so["start"] < t0 - pd.Timedelta(hours=8) - pd.Timedelta(days=lag_days)]
            if len(tr):
                age = (t0 - tr["start"]).dt.days.values / 7.0
                w = 0.5 ** (age / decay_hl) * np.where(tr["season"].values < season, prev_w, 1.0)
                h, r = solve_net(tr["hi"].values, tr["ai"].values, tr["site"].values, -tr[line].values, w, n, prior, lam, h_prev)
                tt = tr.dropna(subset=[tline])
                wt = 0.5 ** ((t0 - tt["start"]).dt.days.values / 7.0 / decay_hl) * np.where(tt["season"].values < season, prev_w, 1.0)
                # totals: each game's closing total split evenly is noisy; fit total = 2mu + (O_h - D_a) + (O_a - D_h)
                y = np.r_[tt[tline].values / 2.0 + (-tt[line].values) / 2.0, tt[tline].values / 2.0 - (-tt[line].values) / 2.0]
                off = np.r_[tt["hi"].values, tt["ai"].values]
                dfn = np.r_[tt["ai"].values, tt["hi"].values]
                site = np.r_[tt["site"].values, -tt["site"].values]
                mu, hh, O, D = solve_od(off, dfn, site, y, np.r_[wt, wt], n, pO, pD, lam, mu_prev, h / 2.0)
            else:
                h, r, mu, O, D = h_prev, prior, mu_prev, pO, pD
            for x in slate.itertuples():
                i, j = idx[x.home], idx[x.away]
                out.append(dict(game_id=x.game_id, mkt_margin=r[i] - r[j] + h * x.site, mkt_h=h,
                                mkt_total=2 * mu + O[i] - D[j] + O[j] - D[i], mkt_r_home=r[i], mkt_r_away=r[j]))
        cur = so[so["season"] == season]
        if len(cur):
            h, r = solve_net(cur["hi"].values, cur["ai"].values, cur["site"].values, -cur[line].values,
                             np.ones(len(cur)), n, prior, 0.5, h_prev)
            final_r = r - r.mean()
            h_prev = h
            tt = cur.dropna(subset=[tline])
            y = np.r_[tt[tline].values / 2.0 + (-tt[line].values) / 2.0, tt[tline].values / 2.0 - (-tt[line].values) / 2.0]
            mu, hh, O, D = solve_od(np.r_[tt["hi"].values, tt["ai"].values], np.r_[tt["ai"].values, tt["hi"].values],
                                    np.r_[tt["site"].values, -tt["site"].values], y, np.ones(2 * len(tt)), n, pO, pD, 0.5,
                                    mu_prev, h / 2.0)
            final_o, final_d, mu_prev = O - O.mean(), D - D.mean(), mu
    return pd.DataFrame(out)


# ------------------------------------------------------------------ QB layer
def qb_layer(g, prior_db=250.0, repl=-0.08):
    """Per game: EPA/dropback value of the expected starter (previous starter) and the actual starter,
    each shrunk toward replacement level with prior_db dropbacks. Values use only earlier games."""
    q = pd.read_parquet(DATA / "qb.parquet")
    q = q.merge(g[["game_id", "start", "season"]], on="game_id")
    q = q.sort_values("start")
    # the game's starter = QB with the most dropbacks for that team in that game
    st = q.sort_values("db", ascending=False).drop_duplicates(["game_id", "team"])
    hist = {}  # qb_id -> [sum_epa, sum_db]
    rows = []
    last_starter = {}
    team_db = q.groupby(["game_id", "team"])["db"].sum()
    for gid, grp in q.groupby("game_id", sort=False):
        start_rows = st[st["game_id"] == gid]
        for r in start_rows.itertuples():
            prev = last_starter.get(r.team)
            def val(qid):
                s, d = hist.get(qid, (0.0, 0.0))
                return (s + repl * prior_db) / (d + prior_db), d
            v_act, n_act = val(r.qb_id)
            v_exp, n_exp = val(prev) if prev else (np.nan, 0)
            rows.append(dict(game_id=gid, team=r.team, qb_act=r.qb_id, qb_act_name=r.name, qb_exp=prev,
                             qb_val_act=v_act, qb_val_exp=v_exp, qb_db_act=n_act, qb_changed=prev is not None and prev != r.qb_id,
                             team_db=team_db.get((gid, r.team), np.nan)))
        for r in grp.itertuples():
            s, d = hist.get(r.qb_id, (0.0, 0.0))
            hist[r.qb_id] = (s + r.qb_epa, d + r.db)
        for r in start_rows.itertuples():
            last_starter[r.team] = r.qb_id
    return pd.DataFrame(rows)


def run_all(save=True, lag_days=0):
    g, tg = load()
    parts = [run_od(g, tg, k, lag_days=lag_days) for k in ("pts", "epa", "sr")]
    parts.append(run_market(g, lag_days=lag_days))
    p = parts[0]
    for x in parts[1:]:
        p = p.merge(x, on="game_id", how="outer")
    if save:
        p.to_parquet(DATA / ("preds.parquet" if not lag_days else f"preds_lag{lag_days}.parquet"))
    return p


if __name__ == "__main__":
    import sys
    p = run_all(lag_days=int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(p.describe().T.round(3).to_string())
