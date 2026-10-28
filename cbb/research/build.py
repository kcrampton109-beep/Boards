"""Turn the raw daily scoreboards into two tables (cbb/data/, gitignored):

  games.parquet  every completed D1-vs-D1 game from ESPN (scores, neutral flag, conference game, postseason),
                 joined to Action Network's opener (book 30), consensus (15) and DK / FD / BetMGM (68 / 69 / 75)
                 closing spreads and totals, all on ESPN's home team.
  teams.parquet  ESPN team id -> name, Action conference, season.

ESPN is the game list (Action's D1 feed misses some low-profile games); Action supplies the lines.
"""
import collections, datetime as dt, difflib, glob, gzip, json, pathlib, re
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ET = "US/Eastern"
BOOKS = {15: "cons", 30: "open", 68: "dk", 69: "fd", 75: "mgm"}


def season_of(ts):
    """Season named by its start year: Nov 2024 - Apr 2025 = 2024."""
    return ts.year if ts.month >= 7 else ts.year - 1


def load_espn(files=None, completed_only=True):
    """ESPN events from the daily files (default: every file in data/espn_raw). completed_only=False also keeps
    scheduled / live / postponed games (the live board), with their status in `state` and `status`."""
    rows = []
    for f in sorted(files if files is not None else glob.glob(str(DATA / "espn_raw" / "*.json.gz"))):
        for ev in json.loads(gzip.decompress(pathlib.Path(f).read_bytes())).get("events", []):
            c = ev["competitions"][0]
            done = bool(c["status"]["type"].get("completed"))
            if completed_only and not done:
                continue
            side = {t["homeAway"]: t for t in c["competitors"]}
            if set(side) != {"home", "away"}:
                continue
            h, a = side["home"], side["away"]
            notes = " | ".join(n.get("headline", "") for n in c.get("notes") or [])
            rows.append(dict(
                espn_id=int(ev["id"]), start=ev["date"], season_type=ev["season"]["type"],
                neutral=bool(c.get("neutralSite")), conf_game=bool(c.get("conferenceCompetition")),
                tourney_id=c.get("tournamentId"), notes=notes,
                home_eid=int(h["team"]["id"]), away_eid=int(a["team"]["id"]),
                home_name=h["team"].get("displayName"), away_name=a["team"].get("displayName"),
                home_loc=h["team"].get("location"), away_loc=a["team"].get("location"),
                home_conf_eid=h["team"].get("conferenceId"), away_conf_eid=a["team"].get("conferenceId"),
                home_pts=float(h["score"]) if done else None, away_pts=float(a["score"]) if done else None,
                completed=done, state=c["status"]["type"].get("state"), status=c["status"]["type"].get("name")))
    e = pd.DataFrame(rows).drop_duplicates("espn_id", keep="last")
    if not len(e):
        return e
    e["start"] = pd.to_datetime(e["start"], utc=True)
    e["season"] = e["start"].dt.tz_convert(ET).map(season_of)
    return e


def load_action(files=None):
    rows = []
    for f in sorted(files if files is not None else glob.glob(str(DATA / "an_raw" / "*.json.gz"))):
        for g in json.loads(gzip.decompress(pathlib.Path(f).read_bytes())).get("games", []):
            tm = {t["id"]: t for t in g.get("teams", [])}
            box = g.get("boxscore") or {}
            h, a = tm.get(g.get("home_team_id"), {}), tm.get(g.get("away_team_id"), {})
            r = dict(an_id=g["id"], an_start=g.get("start_time"), an_status=g.get("status"), an_type=g.get("type"),
                     an_home_id=g.get("home_team_id"), an_away_id=g.get("away_team_id"),
                     an_home=h.get("full_name"), an_away=a.get("full_name"),
                     an_home_conf=h.get("conference_type"), an_away_conf=a.get("conference_type"),
                     an_home_div=h.get("division_type"), an_away_div=a.get("division_type"),
                     an_home_pts=box.get("total_home_points"), an_away_pts=box.get("total_away_points"))
            for o in g.get("odds") or []:
                b = BOOKS.get(o.get("book_id"))
                if b is None or o.get("type") != "game":
                    continue
                r.update({f"{b}_spread": o.get("spread_home"), f"{b}_spread_away": o.get("spread_away"),
                          f"{b}_sp_odds_home": o.get("spread_home_line"), f"{b}_sp_odds_away": o.get("spread_away_line"),
                          f"{b}_total": o.get("total"), f"{b}_over_odds": o.get("over"), f"{b}_under_odds": o.get("under"),
                          f"{b}_ml_home": o.get("ml_home"), f"{b}_ml_away": o.get("ml_away"), f"{b}_ts": o.get("inserted")})
                if b == "cons":
                    r.update(cons_sp_tix_home=o.get("spread_home_public"), cons_sp_money_home=o.get("spread_home_money"),
                             cons_num_bets=o.get("num_bets"))
            rows.append(r)
    a = pd.DataFrame(rows).drop_duplicates("an_id", keep="last")
    if not len(a):
        return a
    a["an_start"] = pd.to_datetime(a["an_start"], utc=True)
    for c in [c for c in a.columns if c.endswith("_ts")]:
        a[c] = pd.to_datetime(a[c], utc=True, format="ISO8601")
    return a


def _norm(s):
    s = (s or "").lower().replace("&", "and").replace("st.", "state").replace("saint ", "st ")
    return re.sub(r"[^a-z0-9 ]", "", s).strip()


def to_espn_home(a):
    """Put every Action number on ESPN's home team (rows where same_orient is False have home/away swapped)."""
    a = a.copy()
    flip = ~a["same_orient"].astype(bool)
    for b in BOOKS.values():
        if f"{b}_spread" not in a:
            continue
        for x, y in (("spread", "spread_away"), ("sp_odds_home", "sp_odds_away"), ("ml_home", "ml_away")):
            a.loc[flip, [f"{b}_{x}", f"{b}_{y}"]] = a.loc[flip, [f"{b}_{y}", f"{b}_{x}"]].values
    for c in ("cons_sp_tix_home", "cons_sp_money_home"):
        if c in a:
            a.loc[flip, c] = 100 - a.loc[flip, c]
    a.loc[flip, ["an_home_conf", "an_away_conf"]] = a.loc[flip, ["an_away_conf", "an_home_conf"]].values
    a.loc[flip, ["an_home_div", "an_away_div"]] = a.loc[flip, ["an_away_div", "an_home_div"]].values
    return a


def match(e, an):
    """Map Action team ids -> ESPN team ids from games whose dates and final scores agree, then join game by game."""
    e = e.copy()
    an = an[(an["an_status"] == "complete") & (an["an_home_pts"].fillna(0) + an["an_away_pts"].fillna(0) > 0)].copy()
    e["day"] = e["start"].dt.tz_convert(ET).dt.date
    an["day"] = an["an_start"].dt.tz_convert(ET).dt.date
    by_day = {d: x for d, x in e.groupby("day")}
    # candidate pairings: same Eastern date, and each Action team's score equals the ESPN team's score
    pairs = []
    for r in an.itertuples():
        cand = by_day.get(r.day)
        if cand is None:
            continue
        sh, sa = float(r.an_home_pts), float(r.an_away_pts)
        for c in cand.itertuples():
            if (sh, sa) == (c.home_pts, c.away_pts):
                pairs.append(((r.an_home_id, r.an_home, c.home_eid, c.home_name, c.home_loc),
                              (r.an_away_id, r.an_away, c.away_eid, c.away_name, c.away_loc)))
            if (sh, sa) == (c.away_pts, c.home_pts):
                pairs.append(((r.an_home_id, r.an_home, c.away_eid, c.away_name, c.away_loc),
                              (r.an_away_id, r.an_away, c.home_eid, c.home_name, c.home_loc)))

    def similar(an_name, ename, eloc):
        a_, e_ = _norm(an_name), _norm(ename)
        return a_ == e_ or difflib.SequenceMatcher(None, a_, e_).ratio() > 0.8 or a_.startswith(_norm(eloc) + " ")

    # pass 1: names agree; pass 2: the opponent is already mapped, so the other team maps by elimination
    votes = collections.defaultdict(collections.Counter)
    for p in pairs:
        for an_tid, an_name, eid, ename, eloc in p:
            if similar(an_name, ename, eloc):
                votes[an_tid][eid] += 1
    tmap = {k: v.most_common(1)[0][0] for k, v in votes.items()}
    for _ in range(2):
        votes2 = collections.defaultdict(collections.Counter)
        for x, y in pairs:
            for me, opp in ((x, y), (y, x)):
                if tmap.get(opp[0]) == opp[2]:
                    votes2[me[0]][me[2]] += 1
        for k, v in votes2.items():
            eid, n = v.most_common(1)[0]
            if k not in tmap and n >= 2:
                tmap[k] = eid
    an["home_e"] = an["an_home_id"].map(tmap)
    an["away_e"] = an["an_away_id"].map(tmap)
    key = {}
    for c in e.itertuples():
        for dd in (0, -1, 1):
            key.setdefault((c.day + dt.timedelta(days=dd), frozenset((c.home_eid, c.away_eid))), (c.espn_id, c.home_eid))
    out = []
    for r in an.itertuples():
        hit = key.get((r.day, frozenset((r.home_e, r.away_e))))
        if hit:
            out.append((r.an_id, hit[0], r.home_e == hit[1]))
    m = pd.DataFrame(out, columns=["an_id", "espn_id", "same_orient"]).drop_duplicates("espn_id")
    a = to_espn_home(an.merge(m, on="an_id").drop(columns=["day", "home_e", "away_e"]))
    print(f"action: {len(an)} final games, {an['home_e'].notna().mean():.1%} home teams mapped, {len(a)} joined to ESPN")
    return a, tmap


def main():
    e = load_espn()
    an = load_action()
    a, tmap = match(e, an)
    # Action team id -> ESPN team id, reused by the live board to place each day's lines on ESPN's games
    (DATA / "an_team_map.json").write_text(json.dumps({str(k): int(v) for k, v in tmap.items()}))
    g = e.merge(a.drop(columns=["an_home", "an_away", "an_home_pts", "an_away_pts", "an_home_id", "an_away_id"]),
                on="espn_id", how="left")
    # D1: a team with 10+ games against the ESPN D1 scoreboard that season (non-D1 visitors play one to three)
    cnt = pd.concat([g[["season", "home_eid"]].set_axis(["season", "eid"], axis=1),
                     g[["season", "away_eid"]].set_axis(["season", "eid"], axis=1)]).value_counts()
    d1 = set(cnt[cnt >= 10].index)
    g["d1"] = [(s, h) in d1 and (s, w) in d1 for s, h, w in zip(g["season"], g["home_eid"], g["away_eid"])]
    g = g.sort_values("start").reset_index(drop=True)
    # conference by team-season: Action's label (as of that game) wherever the team was matched, most common value
    conf = pd.concat([g[["season", "home_eid", "an_home_conf"]].set_axis(["season", "eid", "conf"], axis=1),
                      g[["season", "away_eid", "an_away_conf"]].set_axis(["season", "eid", "conf"], axis=1)]).dropna()
    conf = conf.groupby(["season", "eid"])["conf"].agg(lambda s: s.value_counts().index[0]).rename("conf").reset_index()
    names = pd.concat([g[["season", "home_eid", "home_name", "home_conf_eid"]].set_axis(["season", "eid", "name", "conf_eid"], axis=1),
                       g[["season", "away_eid", "away_name", "away_conf_eid"]].set_axis(["season", "eid", "name", "conf_eid"], axis=1)]
                      ).drop_duplicates(["season", "eid"], keep="last")
    teams = names.merge(conf, on=["season", "eid"], how="left")
    teams["d1"] = [(s, t) in d1 for s, t in zip(teams["season"], teams["eid"])]
    g.to_parquet(DATA / "games.parquet")
    teams.to_parquet(DATA / "teams.parquet")
    print("games", g.shape, "teams", teams.shape)
    agg = dict(n=("espn_id", "size"), neutral=("neutral", "sum"), action=("an_id", "count"))
    agg.update({b: (f"{b}_spread", "count") for b in BOOKS.values() if f"{b}_spread" in g})
    print(g[g["d1"]].groupby("season").agg(**agg).to_string())


if __name__ == "__main__":
    main()
