"""Possession-based (tempo-free) results model: the standard way college basketball is rated.

  pace      possessions per team in a game = mu_p + T[home] + T[away]                  (additive ridge)
  offense   points per 100 possessions     = mu + h*site + O[offense] - D[defense]     (model.Ridge)
  game      team points = pace * efficiency / 100;  margin and total follow

Fit walk-forward exactly like model.py (each date sees only earlier dates, older games decayed, shrunk toward a prior
from last season's final ratings and this season's conference). Box scores: research/box.py (2022-23 on, so 2022-23
is the burn-in season). Output: data/preds_tempo.parquet (espn_id, margin_hat, total_hat), comparable to preds_pts.
Usage: python research/tempo.py [lam] [half_life]
"""
import sys
import numpy as np
import pandas as pd
import model, box


class PaceRidge:
    """y = mu + T[a] + T[b] for a game between teams a and b."""

    def __init__(self, n):
        self.n, self.A, self.b = n, np.zeros((n + 1, n + 1)), np.zeros(n + 1)

    def add(self, a, b, y, w):
        cols = np.column_stack([np.zeros(len(y), int), 1 + a, 1 + b])
        for i in range(3):
            for j in range(3):
                np.add.at(self.A, (cols[:, i], cols[:, j]), w)
            np.add.at(self.b, cols[:, i], w * y)

    def decay(self, f):
        self.A *= f
        self.b *= f

    def solve(self, prior, lam, mu0, lam_mu=2.0):
        pen = np.r_[lam_mu, np.full(self.n, lam)]
        x = np.linalg.solve(self.A + np.diag(pen), self.b + pen * np.r_[mu0, prior])
        return x[0], x[1:]


def load():
    g, teams = model.load()
    seasons = sorted(s for s in g["season"].unique() if (box.OUT / f"{s}.jsonl").exists())
    b = pd.concat([box.load(s) for s in seasons])
    b = b.groupby(["espn_id", "eid"], as_index=False)["poss"].mean()
    g = g[g["season"].isin(seasons)].copy()
    g = g.merge(b.rename(columns={"eid": "home_eid", "poss": "poss_h"}), on=["espn_id", "home_eid"], how="left")
    g = g.merge(b.rename(columns={"eid": "away_eid", "poss": "poss_a"}), on=["espn_id", "away_eid"], how="left")
    g["pace"] = (g["poss_h"] + g["poss_a"]) / 2
    g.loc[(g["pace"] < 45) | (g["pace"] > 110), "pace"] = np.nan    # bad box scores
    g["eff_h"], g["eff_a"] = 100 * g["home_pts"] / g["pace"], 100 * g["away_pts"] / g["pace"]
    return g, teams, seasons


def prior(finals, season, tm, teams, key):
    """Last season's final rating regressed (fit on earlier seasons when there are pairs, else 0.5 last + 0.2 conf)."""
    f = {s: x for s, x in finals.items()}
    pf = model.prior_frame(f, season, tm)
    coefs = model.fit_prior(f, teams, [s for s in f if s < season])
    if coefs is not None:
        return model.prior_X(pf, "O") @ coefs["O"], model.prior_X(pf, "D") @ coefs["D"]
    if season - 1 in f:
        return (0.5 * pf["last_O"] + 0.2 * pf["conf_O"]).values, (0.5 * pf["last_D"] + 0.2 * pf["conf_D"]).values
    return np.zeros(len(tm)), np.zeros(len(tm))


def run(lam=4.0, half_life=60.0, lam_p=4.0):
    g, teams, seasons = load()
    out, fin_e, fin_p = [], {}, {}
    mu, h, mu_p = 104.0, 2.0, 69.0
    for season in seasons:
        tm = teams[teams["season"] == season].reset_index(drop=True)
        idx = {t: i for i, t in enumerate(tm["eid"])}
        n = len(tm)
        gs = g[(g["season"] == season) & g["home_eid"].isin(idx) & g["away_eid"].isin(idx)]
        pO, pD = prior(fin_e, season, tm, teams, "e")
        pT, _ = prior(fin_p, season, tm, teams, "p")
        Re, Rp = model.Ridge(n), PaceRidge(n)
        last = None
        for day, sl in gs.groupby("day"):
            if last is not None:
                f = 0.5 ** ((day - last).days / half_life)
                Re.decay(f); Rp.decay(f)
            last = day
            m_e, hh, O, D = Re.solve(pO, pD, lam, mu, h)
            m_p, T = Rp.solve(pT, lam_p, mu_p)
            hi, ai = sl["home_eid"].map(idx).values, sl["away_eid"].map(idx).values
            s = sl["site"].values
            pace = m_p + T[hi] + T[ai]
            eh = m_e + hh * s + O[hi] - D[ai]
            ea = m_e - hh * s + O[ai] - D[hi]
            out.append(pd.DataFrame(dict(espn_id=sl["espn_id"].values, margin_hat=pace * (eh - ea) / 100,
                                         total_hat=pace * (eh + ea) / 100, pace_hat=pace)))
            ok = sl[sl["pace"].notna()]
            if len(ok):
                hi, ai = ok["home_eid"].map(idx).values, ok["away_eid"].map(idx).values
                s = ok["site"].values
                w = ok["pace"].values / 70
                Re.add(np.r_[hi, ai], np.r_[ai, hi], np.r_[s, -s], np.r_[ok["eff_h"].values, ok["eff_a"].values], np.r_[w, w])
                Rp.add(hi, ai, ok["pace"].values, np.ones(len(ok)))
        # end of season: all games, light prior -> next season's prior
        ok = gs[gs["pace"].notna()]
        hi, ai = ok["home_eid"].map(idx).values, ok["away_eid"].map(idx).values
        s = ok["site"].values
        F, P = model.Ridge(n), PaceRidge(n)
        F.add(np.r_[hi, ai], np.r_[ai, hi], np.r_[s, -s], np.r_[ok["eff_h"].values, ok["eff_a"].values], np.ones(2 * len(ok)))
        P.add(hi, ai, ok["pace"].values, np.ones(len(ok)))
        m_e, hh, O, D = F.solve(pO, pD, 1.0, mu, h)
        m_p, T = P.solve(pT, 1.0, mu_p)
        fin_e[season] = tm.assign(O=O - O.mean(), D=D - D.mean())[["eid", "name", "conf_eid", "O", "D"]]
        fin_p[season] = tm.assign(O=T - T.mean(), D=T - T.mean())[["eid", "name", "conf_eid", "O", "D"]]
        mu, h, mu_p = m_e + O.mean() - D.mean(), hh, m_p + 2 * T.mean()
        print(season, "games", len(gs), "with box", len(ok), f"eff mu {mu:.1f} h {h:.2f} pace mu {mu_p:.1f}", flush=True)
    return pd.concat(out, ignore_index=True)


if __name__ == "__main__":
    lam = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    hl = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    p = run(lam, hl)
    p.to_parquet(model.DATA / "preds_tempo.parquet")
    print("saved", p.shape)
