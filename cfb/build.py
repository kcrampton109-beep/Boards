"""Assemble the research tables from cached raw data.

data/games.parquet  one row per game (FBS/FCS involved): teams, scores, venue, CFBD lines, Action lines/splits
data/tg.parquet     one row per team-game: offense + defense efficiency, drives/pace, havoc
"""
import collections, difflib, glob, gzip, json, pathlib, re
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
YEARS = range(2014, 2027)


def raw(name):
    p = RAW / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


# ---------------------------------------------------------------- games
def load_games():
    rows = []
    for y in YEARS:
        for g in raw(f"games__seasonType-both_year-{y}.json"):
            if not ({g.get("homeClassification"), g.get("awayClassification")} & {"fbs", "fcs"}):
                continue
            if g.get("seasonType") not in ("regular", "postseason"):
                continue
            rows.append(dict(
                game_id=g["id"], season=g["season"], week=g["week"], season_type=g["seasonType"],
                start=g["startDate"], tbd=g.get("startTimeTBD"), completed=g.get("completed"),
                neutral=g.get("neutralSite"), conf_game=g.get("conferenceGame"), venue_id=g.get("venueId"),
                home=g["homeTeam"], home_id=g["homeId"], home_cls=g.get("homeClassification"),
                home_conf=g.get("homeConference"), home_pts=g.get("homePoints"),
                away=g["awayTeam"], away_id=g["awayId"], away_cls=g.get("awayClassification"),
                away_conf=g.get("awayConference"), away_pts=g.get("awayPoints"),
                home_elo=g.get("homePregameElo"), away_elo=g.get("awayPregameElo"),
                home_q=g.get("homeLineScores"), away_q=g.get("awayLineScores"),
            ))
    df = pd.DataFrame(rows).drop_duplicates("game_id")
    df["start"] = pd.to_datetime(df["start"], utc=True)
    return df


def load_cfbd_lines():
    rows = []
    for y in YEARS:
        for g in raw(f"lines__seasonType-both_year-{y}.json"):
            ls = g.get("lines") or []
            if not ls:
                continue
            def med(k):
                v = [l[k] for l in ls if l.get(k) is not None]
                return float(np.median(v)) if v else np.nan
            r = dict(game_id=g["id"], n_prov=len(ls), providers="|".join(sorted(l["provider"] for l in ls)),
                     spread=med("spread"), spread_open=med("spreadOpen"), total=med("overUnder"),
                     total_open=med("overUnderOpen"), ml_home=med("homeMoneyline"), ml_away=med("awayMoneyline"))
            for l in ls:
                if l["provider"] == "DraftKings":
                    r.update(cf_dk_spread=l.get("spread"), cf_dk_spread_open=l.get("spreadOpen"),
                             cf_dk_total=l.get("overUnder"), cf_dk_total_open=l.get("overUnderOpen"))
            rows.append(r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- team-game stats
def _flat(d, prefix):
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}_"))
        else:
            out[prefix + k] = v
    return out


def load_tg():
    rows = []
    for y in YEARS:
        for r in raw(f"stats_game_advanced__excludeGarbageTime-true_seasonType-both_year-{y}.json"):
            d = dict(game_id=r["gameId"], team=r["team"], opp=r["opponent"])
            d.update(_flat(r.get("offense"), "o_"))
            d.update(_flat(r.get("defense"), "d_"))
            rows.append(d)
    tg = pd.DataFrame(rows).drop_duplicates(["game_id", "team"])

    hv = []
    for y in YEARS:
        for r in raw(f"stats_game_havoc__seasonType-both_year-{y}.json"):
            d = dict(game_id=r.get("gameId"), team=r.get("team"))
            d.update(_flat(r.get("offense"), "hv_o_"))
            d.update(_flat(r.get("defense"), "hv_d_"))
            hv.append(d)
    if hv:
        tg = tg.merge(pd.DataFrame(hv).drop_duplicates(["game_id", "team"]), on=["game_id", "team"], how="left")

    dr = []
    TO = {"INT", "FUMBLE", "INT TD", "FUMBLE TD", "FUMBLE RETURN TD"}
    for y in YEARS:
        for r in raw(f"drives__seasonType-both_year-{y}.json"):
            el = r.get("elapsed") or {}
            secs = (el.get("minutes") or 0) * 60 + (el.get("seconds") or 0)
            res = r.get("driveResult") or ""
            dr.append((r["gameId"], r["offense"], r.get("plays") or 0, secs, res in TO,
                       r.get("startYardsToGoal"), r.get("endYardsToGoal"),
                       (r.get("endOffenseScore") or 0) - (r.get("startOffenseScore") or 0), res))
    if dr:
        d = pd.DataFrame(dr, columns=["game_id", "team", "plays", "secs", "to", "start_ytg", "end_ytg", "pts", "res"])
        d["opp40"] = d["end_ytg"].le(40) | d["res"].isin(["TD", "FG", "MISSED FG"])
        d["opp_pts"] = np.where(d["opp40"], d["pts"].clip(lower=0), 0)
        agg = d.groupby(["game_id", "team"]).agg(
            dr_n=("plays", "size"), dr_plays=("plays", "sum"), dr_secs=("secs", "sum"), dr_to=("to", "sum"),
            dr_start_ytg=("start_ytg", "mean"), dr_opps=("opp40", "sum"), dr_opp_pts=("opp_pts", "sum")).reset_index()
        tg = tg.merge(agg, on=["game_id", "team"], how="left")
    return tg


# ---------------------------------------------------------------- Action Network
BOOKS = {15: "cons", 30: "open", 68: "dk", 69: "fd", 75: "mgm"}


def load_action():
    rows = []
    for f in sorted(glob.glob(str(ROOT / "data" / "an_raw" / "*.json.gz"))):
        season, div, wk = re.match(r".*?(\d{4})_(\w+)_w(\d+)", f).groups()
        d = json.loads(gzip.decompress(pathlib.Path(f).read_bytes()))
        for g in d.get("games", []):
            names = {t["id"]: t.get("full_name") for t in g.get("teams", [])}
            box = g.get("boxscore") or {}
            r = dict(an_id=g["id"], an_season=int(season), an_div=div, an_week=int(wk), an_start=g.get("start_time"),
                     an_status=g.get("status"), an_away_id=g.get("away_team_id"), an_home_id=g.get("home_team_id"),
                     an_away=names.get(g.get("away_team_id")), an_home=names.get(g.get("home_team_id")),
                     an_away_pts=box.get("total_away_points"), an_home_pts=box.get("total_home_points"),
                     an_num_bets=g.get("num_bets"))
            for o in g.get("odds") or []:
                b = BOOKS.get(o.get("book_id"))
                per = {"game": "", "firsthalf": "h1_"}.get(o.get("type"))
                if b is None or per is None:
                    continue
                p = f"{b}_{per}"
                r.update({p + "spread": o.get("spread_home"), p + "spread_odds_home": o.get("spread_home_line"),
                          p + "spread_odds_away": o.get("spread_away_line"), p + "total": o.get("total"),
                          p + "over_odds": o.get("over"), p + "under_odds": o.get("under"),
                          p + "ml_home": o.get("ml_home"), p + "ml_away": o.get("ml_away"),
                          p + "ts": o.get("inserted")})
                if per == "" and b in ("cons", "open"):
                    r.update({p + "sp_tix_home": o.get("spread_home_public"), p + "sp_money_home": o.get("spread_home_money"),
                              p + "tot_tix_over": o.get("total_over_public"), p + "tot_money_over": o.get("total_over_money"),
                              p + "ml_tix_home": o.get("ml_home_public"), p + "ml_money_home": o.get("ml_home_money"),
                              p + "num_bets": o.get("num_bets")})
                if per == "" and b in ("dk", "fd", "mgm"):
                    r.update({p + "home_tt": o.get("home_total"), p + "away_tt": o.get("away_total")})
            rows.append(r)
    a = pd.DataFrame(rows).drop_duplicates("an_id", keep="last")
    a["an_start"] = pd.to_datetime(a["an_start"], utc=True)
    return a


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("&", "and").replace("st.", "state"))


def match_action(games, an):
    """Map Action team ids -> CFBD team ids using games whose dates and final scores agree, then join."""
    g = games[["game_id", "start", "home_id", "away_id", "home", "away", "home_pts", "away_pts"]].copy()
    g["day"] = g["start"].dt.tz_convert("US/Eastern").dt.date
    an = an.copy()
    an["day"] = an["an_start"].dt.tz_convert("US/Eastern").dt.date
    votes = collections.defaultdict(collections.Counter)
    by_day = {d: x for d, x in g.groupby("day")}
    for r in an.itertuples():
        if pd.isna(r.an_home_pts) or pd.isna(r.an_away_pts):
            continue
        for dd in (0, -1, 1):
            cand = by_day.get(r.day + pd.Timedelta(days=dd))
            if cand is None:
                continue
            for c in cand.itertuples():
                if {c.home_pts, c.away_pts} != {r.an_home_pts, r.an_away_pts}:
                    continue
                for an_tid, an_name in ((r.an_home_id, r.an_home), (r.an_away_id, r.an_away)):
                    for cid, cname in ((c.home_id, c.home), (c.away_id, c.away)):
                        if _norm(an_name).startswith(_norm(cname)) or difflib.SequenceMatcher(
                                None, _norm(an_name), _norm(cname)).ratio() > 0.75:
                            votes[an_tid][cid] += 1
    tmap = {k: v.most_common(1)[0][0] for k, v in votes.items()}
    an["home_cid"] = an["an_home_id"].map(tmap)
    an["away_cid"] = an["an_away_id"].map(tmap)
    key = {}
    for c in g.itertuples():
        for dd in (0, -1, 1):
            key[(c.day + pd.Timedelta(days=dd), frozenset((c.home_id, c.away_id)))] = (c.game_id, c.home_id)
    out = []
    for r in an.itertuples():
        hit = key.get((r.day, frozenset((r.home_cid, r.away_cid))))
        if hit:
            out.append((r.an_id, hit[0], r.home_cid == hit[1]))
    m = pd.DataFrame(out, columns=["an_id", "game_id", "same_orient"]).drop_duplicates("game_id")
    a = an.merge(m, on="an_id")
    # put every Action spread/ML/split on CFBD's home team
    flip = ~a["same_orient"]
    for c in [c for c in a.columns if c.endswith("spread")]:
        a.loc[flip, c] = -a.loc[flip, c]
    for b in ("cons_", "open_", "dk_", "fd_", "mgm_", "cons_h1_", "dk_h1_", "fd_h1_", "mgm_h1_", "open_h1_"):
        for x, y in (("spread_odds_home", "spread_odds_away"), ("ml_home", "ml_away"), ("home_tt", "away_tt")):
            if b + x in a and b + y in a:
                a.loc[flip, [b + x, b + y]] = a.loc[flip, [b + y, b + x]].values
        for c in ("sp_tix_home", "sp_money_home", "ml_tix_home", "ml_money_home"):
            if b + c in a:
                a.loc[flip, b + c] = 100 - a.loc[flip, b + c]
    print(f"action: {len(an)} games, {an['home_cid'].notna().mean():.0%} team-mapped, {len(a)} joined to CFBD")
    return a.drop(columns=["day"]), tmap


def main():
    games = load_games()
    lines = load_cfbd_lines()
    games = games.merge(lines, on="game_id", how="left")
    ven = pd.DataFrame(raw("venues.json"))
    if len(ven):
        ven = ven.rename(columns={"id": "venue_id"})[["venue_id", "latitude", "longitude", "elevation", "dome", "grass", "timezone", "state"]]
        games = games.merge(ven, on="venue_id", how="left")
    an = load_action()
    a, tmap = match_action(games, an)
    keep = [c for c in a.columns if c not in ("an_away", "an_home", "an_away_id", "an_home_id", "an_home_pts", "an_away_pts", "home_cid", "away_cid")]
    games = games.merge(a[keep], on="game_id", how="left")
    games.to_parquet(ROOT / "data" / "games.parquet")
    json.dump({str(k): int(v) for k, v in tmap.items()}, open(ROOT / "data" / "an_team_map.json", "w"))
    tg = load_tg()
    tg.to_parquet(ROOT / "data" / "tg.parquet")
    print("games", games.shape, "tg", tg.shape)
    print(games.groupby("season").agg(n=("game_id", "size"), cfbd_line=("spread", lambda s: s.notna().sum()),
                                      an_line=("cons_spread", lambda s: s.notna().sum()),
                                      splits=("cons_sp_tix_home", lambda s: s.notna().sum())).to_string())


if __name__ == "__main__":
    main()
