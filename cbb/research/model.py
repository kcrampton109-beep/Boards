"""Walk-forward points ratings for every D1 team (CBB port of cfb/model.py).

  team points = mu + h*site + O[offense] - D[defense]       site = +1 home, -1 away, 0 neutral

For each Eastern game date the ratings are fit ONLY on games from earlier dates, with older games decayed by a
half-life (days), and shrunk toward a preseason prior built from last season's final ratings and the conference
the team plays in this season. Two targets:
  pts     the points each team actually scored (results model)
  market  the points each team was projected to score by earlier games' CLOSING lines,
          home = (total - spread) / 2 (DK/FD/BetMGM median, else Action consensus): "market ratings"
Output: cbb/data/preds_<target>.parquet, one row per game (projected margin + total).
"""
import pathlib
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ET = "US/Eastern"


def load():
    g = pd.read_parquet(DATA / "games.parquet")
    g = g[g["d1"] & (g["home_pts"] > 20) & (g["away_pts"] > 20)].copy()  # drops forfeits (2-0) and stray non-D1 games
    g["site"] = np.where(g["neutral"], 0.0, 1.0)
    g["day"] = g["start"].dt.tz_convert(ET).dt.tz_localize(None).dt.normalize()
    g["close"], g["tclose"] = closing(g)
    g["mkt_home"] = (g["tclose"] - g["close"]) / 2
    g["mkt_away"] = (g["tclose"] + g["close"]) / 2
    teams = pd.read_parquet(DATA / "teams.parquet")
    return g.sort_values("start").reset_index(drop=True), teams[teams["d1"]]


def closing(g):
    """Closing home spread and total: median of DK / FD / BetMGM, else Action's consensus. A sportsbook whose two
    spread sides don't mirror has an outcome tagged to the wrong team, so that book's spread is ignored."""
    sp = []
    for b in ("dk", "fd", "mgm"):
        if f"{b}_spread" not in g:
            sp.append(pd.Series(np.nan, index=g.index))
            continue
        ok = (g[f"{b}_spread"] + g[f"{b}_spread_away"]).abs() <= 0.6
        sp.append(g[f"{b}_spread"].where(ok))
    close = pd.concat(sp, axis=1).median(axis=1)
    tot = g[[c for c in ("dk_total", "fd_total", "mgm_total") if c in g]].median(axis=1)
    if "cons_spread" in g:
        close, tot = close.fillna(g["cons_spread"]), tot.fillna(g["cons_total"])
    return close, tot


class Ridge:
    """Normal equations for y = mu + h*site + O[off] - D[def], accumulated game by game (4 nonzeros per row)."""

    def __init__(self, n):
        self.n, self.k = n, 2 + 2 * n
        self.A = np.zeros((self.k, self.k))
        self.b = np.zeros(self.k)

    def add(self, off, dfn, site, y, w):
        m = len(y)
        cols = np.column_stack([np.zeros(m, int), np.ones(m, int), 2 + off, 2 + self.n + dfn])
        vals = np.column_stack([np.ones(m), site, np.ones(m), -np.ones(m)])
        wv = vals * w[:, None]
        for a in range(4):
            for c in range(4):
                np.add.at(self.A, (cols[:, a], cols[:, c]), wv[:, a] * vals[:, c])
            np.add.at(self.b, cols[:, a], wv[:, a] * y)

    def decay(self, f):
        self.A *= f
        self.b *= f

    def solve(self, prior_o, prior_d, lam, mu0, h0, lam_mu=2.0, lam_h=50.0):
        n = self.n
        pen = np.r_[lam_mu, lam_h, np.full(n, lam), np.full(n, lam)]
        pri = np.r_[mu0, h0, prior_o, prior_d]
        x = np.linalg.solve(self.A + np.diag(pen), self.b + pen * pri)
        return x[0], x[1], x[2:2 + n], x[2 + n:]


TARGET = {"pts": ("home_pts", "away_pts"), "market": ("mkt_home", "mkt_away")}


def obs(gs, idx, target="pts"):
    """Two rows per game: each team's points (actual, or implied by the closing line) against the other's defense."""
    hc, ac = TARGET[target]
    gs = gs[gs[hc].notna() & gs[ac].notna()]
    hi, ai = gs["home_eid"].map(idx).values, gs["away_eid"].map(idx).values
    s = gs["site"].values
    y = np.r_[gs[hc].values, gs[ac].values]
    return np.r_[hi, ai], np.r_[ai, hi], np.r_[s, -s], y, np.ones(len(y))


def prior_frame(finals, season, tm):
    """Last season's final O/D for this season's teams + this season's conference mean of those numbers."""
    last = finals.get(season - 1)
    f = tm[["eid", "conf_eid"]].copy()
    if last is None:
        f["last_O"] = f["last_D"] = np.nan
    else:
        f = f.merge(last[["eid", "O", "D"]].rename(columns={"O": "last_O", "D": "last_D"}), on="eid", how="left")
    f["missing"] = f["last_O"].isna().astype(float)
    for c in ("O", "D"):
        # teams new to D1 start near the bottom of last season's distribution
        f["last_" + c] = f["last_" + c].fillna(f["last_" + c].quantile(0.1) if f["last_" + c].notna().any() else 0.0)
        # independents (no conference) use their own number
        f["conf_" + c] = f.groupby("conf_eid")["last_" + c].transform("mean").fillna(f["last_" + c])
    return f


def prior_X(f, c):
    return np.column_stack([np.ones(len(f)), f["last_" + c], f["conf_" + c], f["missing"]])


def fit_prior(finals, teams, seasons):
    """OLS: season-S final rating ~ last season's rating + this season's conference mean of last ratings."""
    X, Y = {"O": [], "D": []}, {"O": [], "D": []}
    for s in seasons:
        if s - 1 not in finals:
            continue
        f = prior_frame(finals, s, teams[teams["season"] == s]).merge(finals[s][["eid", "O", "D"]], on="eid")
        for c in ("O", "D"):
            X[c].append(prior_X(f, c))
            Y[c].append(f[c].values)
    if not X["O"]:
        return None
    return {c: np.linalg.lstsq(np.vstack(X[c]), np.concatenate(Y[c]), rcond=None)[0] for c in ("O", "D")}


def run(lam=4.0, half_life=60.0, seasons=None, verbose=False, target="pts", lag_days=0):
    """lag_days > 0 leaves out games from the last lag_days dates before each slate (leakage check: an opener
    posted the evening before can predate the previous day's late tips and closes)."""
    g, teams = load()
    seasons = seasons or sorted(g["season"].unique())
    finals, out, coefs_log = {}, [], {}
    mu_prev, h_prev = 70.0, 1.5
    for season in seasons:
        tm = teams[teams["season"] == season].reset_index(drop=True)
        idx = {t: i for i, t in enumerate(tm["eid"])}
        n = len(tm)
        gs = g[(g["season"] == season) & g["home_eid"].isin(idx) & g["away_eid"].isin(idx)]
        pf = prior_frame(finals, season, tm)
        coefs = fit_prior(finals, teams, [s for s in finals if s < season])
        if coefs is not None:
            pO, pD = prior_X(pf, "O") @ coefs["O"], prior_X(pf, "D") @ coefs["D"]
        elif season - 1 in finals:
            pO = (0.5 * pf["last_O"] + 0.2 * pf["conf_O"]).values
            pD = (0.5 * pf["last_D"] + 0.2 * pf["conf_D"]).values
        else:
            pO = pD = np.zeros(n)
        coefs_log[season] = coefs
        R = Ridge(n)
        last_day, pending = None, []
        for day, slate in gs.groupby("day"):
            if last_day is not None and half_life:
                R.decay(0.5 ** ((day - last_day).days / half_life))
            last_day = day
            while pending and (day - pending[0][0]).days > lag_days:
                d0, sl = pending.pop(0)
                off, dfn, site, y, w = obs(sl, idx, target)
                R.add(off, dfn, site, y, w * (0.5 ** ((day - d0).days / half_life) if half_life else 1.0))
            mu, h, O, D = R.solve(pO, pD, lam, mu_prev, h_prev)
            hi, ai = slate["home_eid"].map(idx).values, slate["away_eid"].map(idx).values
            s = slate["site"].values
            ph = mu + h * s + O[hi] - D[ai]
            pa = mu - h * s + O[ai] - D[hi]
            out.append(pd.DataFrame(dict(espn_id=slate["espn_id"].values, home_hat=ph, away_hat=pa,
                                         margin_hat=ph - pa, total_hat=ph + pa,
                                         prior_margin=(pO[hi] - pD[ai]) - (pO[ai] - pD[hi]) + 2 * h_prev * s,
                                         net_home=O[hi] + D[hi], net_away=O[ai] + D[ai], mu=mu, h=h)))
            pending.append((day, slate))
        # end-of-season ratings (every game equally weighted, light prior) feed next season's prior
        F = Ridge(n)
        F.add(*obs(gs, idx, target))
        mu, h, O, D = F.solve(pO, pD, 1.0, mu_prev, h_prev)
        finals[season] = tm.assign(O=O - O.mean(), D=D - D.mean())[["eid", "name", "conf_eid", "O", "D"]]
        mu_prev, h_prev = mu + O.mean() - D.mean(), h
        if verbose:
            print(season, "games", len(gs), "mu %.1f h %.2f" % (mu_prev, h_prev), "prior coefs", coefs)
    p = pd.concat(out, ignore_index=True)
    # D1 games each team had played before this one (early-season filters)
    long = pd.concat([g[["espn_id", "season", "start", "home_eid"]].set_axis(["espn_id", "season", "start", "eid"], axis=1),
                      g[["espn_id", "season", "start", "away_eid"]].set_axis(["espn_id", "season", "start", "eid"], axis=1)],
                     ignore_index=True).sort_values("start")
    long["gp"] = long.groupby(["season", "eid"]).cumcount()
    p = p.merge(long.groupby("espn_id")["gp"].min().rename("gp_min").reset_index(), on="espn_id", how="left")
    return p, finals, coefs_log


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "pts"
    lam = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    hl = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
    lag = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    p, finals, _ = run(lam, hl or None, verbose=True, target=target, lag_days=lag)
    p.to_parquet(DATA / f"preds_{target}{'_lag%d' % lag if lag else ''}.parquet")
    pd.concat([f.assign(season=s) for s, f in finals.items()]).to_parquet(DATA / f"finals_{target}.parquet")
    print("saved", p.shape)
