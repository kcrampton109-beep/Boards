"""Build the research tables from the raw downloads (no network).
  data/games.parquet   one row per game 2006+: scores, closing line/total (nflverse), opening line/total
                       (SBR 2007-21, Action Network book 30 from ~2024), Action consensus close + bet%/money%,
                       rest, roof, weather, starting QBs, coaches
  data/tg.parquet      one row per team-game: offense EPA/play, success rate, pass/rush splits (all plays and
                       with garbage time removed), points, plays
  data/qb.parquet      one row per QB-game: dropbacks, EPA per dropback (qb_epa), CPOE
Home-spread convention everywhere: negative = home favoured (home -3 -> -3.0)."""
import gzip, io, json, pathlib, re
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
DATA = ROOT / "data"
# franchise continuity: old abbreviations map to the current one
FR = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "WSH": "WAS", "JAC": "JAX"}
SBR_NAMES = {
    "Arizona": "ARI", "Atlanta": "ATL", "Baltimore": "BAL", "Buffalo": "BUF", "Carolina": "CAR", "Chicago": "CHI",
    "Cincinnati": "CIN", "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "GreenBay": "GB",
    "Houston": "HOU", "Indianapolis": "IND", "Jacksonville": "JAX", "KansasCity": "KC", "KCChiefs": "KC",
    "LAChargers": "LAC", "LosAngeles": "LA", "LARams": "LA", "LasVegas": "LV", "Miami": "MIA", "Minnesota": "MIN",
    "NewEngland": "NE", "NewOrleans": "NO", "NYGiants": "NYG", "NYJets": "NYJ", "Oakland": "LV", "Philadelphia": "PHI",
    "Pittsburgh": "PIT", "SanDiego": "LAC", "SanFrancisco": "SF", "Seattle": "SEA", "St.Louis": "LA", "StLouis": "LA",
    "TampaBay": "TB", "Tampa": "TB", "Tennessee": "TEN", "Washington": "WAS", "Washingtom": "WAS", "LVRaiders": "LV",
    "HoustonTexans": "HOU", "Kansas": "KC", "BuffaloBills": "BUF",
}


def team(x):
    return FR.get(x, x)


# ---------------------------------------------------------------- nflverse games
def nflverse_games():
    g = pd.read_csv(RAW / "games.csv")
    g = g[g["season"] >= 2006].copy()
    g["away"] = g["away_team"].map(team)
    g["home"] = g["home_team"].map(team)
    g["date"] = pd.to_datetime(g["gameday"])
    g["start"] = pd.to_datetime(g["gameday"] + " " + g["gametime"].fillna("13:00")).dt.tz_localize(
        "America/New_York", nonexistent="shift_forward", ambiguous=False)
    g["neutral"] = g["location"].eq("Neutral")
    g["site"] = np.where(g["neutral"], 0.0, 1.0)
    g["close"] = -g["spread_line"]          # nflverse spread_line = expected home margin
    g["tclose"] = g["total_line"]
    g["home_pts"] = g["home_score"]
    g["away_pts"] = g["away_score"]
    g["margin"] = g["home_pts"] - g["away_pts"]
    g["points"] = g["home_pts"] + g["away_pts"]
    g["post"] = g["game_type"].ne("REG")
    # slate index: playoff rounds continue after week 18 (17 before 2021)
    g["wk"] = g["week"]
    keep = ["game_id", "season", "week", "wk", "game_type", "post", "date", "start", "weekday", "away", "home", "neutral",
            "site", "away_pts", "home_pts", "margin", "points", "overtime", "close", "tclose", "away_moneyline",
            "home_moneyline", "away_rest", "home_rest", "div_game", "roof", "surface", "temp", "wind", "away_qb_id",
            "home_qb_id", "away_qb_name", "home_qb_name", "away_coach", "home_coach", "stadium_id", "stadium"]
    return g[keep].reset_index(drop=True)


# ---------------------------------------------------------------- SBR opening/closing lines 2007-21
def num(v):
    v = str(v).strip().lower()
    if v in ("pk", "p", "pick", "0"):
        return 0.0
    try:
        return float(v.replace("½", ".5"))
    except ValueError:
        return np.nan


def sbr():
    rows = []
    for f in sorted((RAW / "sbr").glob("nfl_*.html")):
        season = int(f.stem.split("_")[1])
        t = pd.read_html(io.StringIO(f.read_text(encoding="utf-8")))[0]
        t.columns = t.iloc[0]
        t = t.iloc[1:].reset_index(drop=True)
        for i in range(0, len(t) - 1, 2):
            a, h = t.iloc[i], t.iloc[i + 1]
            if str(a["VH"]) not in ("V", "N") or str(h["VH"]) not in ("H", "N"):
                continue
            d = str(a["Date"]).zfill(4)
            mm, dd = int(d[:-2]), int(d[-2:])
            yr = season if mm >= 8 else season + 1
            out = dict(season=season, date=pd.Timestamp(yr, mm, dd), sbr_away=a["Team"], sbr_home=h["Team"],
                       sbr_neutral=str(a["VH"]) == "N")
            for col, key in (("Open", "open"), ("Close", "sclose")):
                va, vh = num(a[col]), num(h[col])
                # the larger number is the total; the smaller is the favourite's spread in its own row
                if np.isnan(va) or np.isnan(vh):
                    out[key], out["t" + key] = np.nan, np.nan
                    continue
                if va > vh:   # away row holds the total -> home is the favourite (or pick)
                    out["t" + key], out[key] = va, -vh
                else:
                    out["t" + key], out[key] = vh, va
            rows.append(out)
    s = pd.DataFrame(rows)
    s["away"] = s["sbr_away"].map(lambda x: SBR_NAMES.get(str(x).replace(" ", ""), None))
    s["home"] = s["sbr_home"].map(lambda x: SBR_NAMES.get(str(x).replace(" ", ""), None))
    bad = s[s["away"].isna() | s["home"].isna()]
    if len(bad):
        print("SBR unmapped names:", sorted(set(bad["sbr_away"][bad["away"].isna()]) | set(bad["sbr_home"][bad["home"].isna()])))
    # totals sanity: an NFL total under 25 or spread over 30 means the columns were misread
    s.loc[(s["topen"] < 25) | (s["topen"] > 80) | (s["open"].abs() > 30), ["open", "topen"]] = np.nan
    s.loc[(s["tsclose"] < 25) | (s["tsclose"] > 80) | (s["sclose"].abs() > 30), ["sclose", "tsclose"]] = np.nan
    return s


def fill_ny(s, g):
    """SBR writes 'NewYork' for both Giants and Jets in some seasons: resolve from the schedule."""
    for i, r in s[s["away"].isna() ^ s["home"].isna()].iterrows():
        known_home = pd.notna(r["home"])
        cand = g[(g["date"] - r["date"]).abs() <= pd.Timedelta(days=2)]
        cand = cand[cand["home"] == r["home"]] if known_home else cand[cand["away"] == r["away"]]
        cand = cand[cand["away"].isin(["NYG", "NYJ"])] if known_home else cand[cand["home"].isin(["NYG", "NYJ"])]
        if len(cand) == 1:
            s.loc[i, "away" if known_home else "home"] = cand.iloc[0]["away" if known_home else "home"]
    return s.dropna(subset=["away", "home"])


# ---------------------------------------------------------------- Action Network history
def action():
    rows = []
    for f in sorted((RAW / "an").glob("*.json.gz")):
        d = json.loads(gzip.decompress(f.read_bytes()))
        for x in d.get("games", []):
            ab = {t["id"]: t.get("abbr") for t in x.get("teams", [])}
            r = dict(an_id=x["id"], an_start=pd.Timestamp(x["start_time"]), away=team(ab.get(x["away_team_id"])),
                     home=team(ab.get(x["home_team_id"])))
            for o in x.get("odds") or []:
                if o.get("type") != "game":
                    continue
                b = o.get("book_id")
                tag = {15: "cons", 30: "an_open", 68: "dk", 69: "fd", 75: "mgm"}.get(b)
                if not tag:
                    continue
                r[f"{tag}_spread"] = o.get("spread_home")
                r[f"{tag}_total"] = o.get("total")
                r[f"{tag}_ml_home"] = o.get("ml_home")
                r[f"{tag}_ml_away"] = o.get("ml_away")
                r[f"{tag}_ins"] = o.get("inserted")
                if b == 15:
                    r.update(tix_home=o.get("spread_home_public"), money_home=o.get("spread_home_money"),
                             tix_over=o.get("total_over_public"), money_over=o.get("total_over_money"),
                             ml_tix_home=o.get("ml_home_public"), ml_money_home=o.get("ml_home_money"),
                             num_bets=o.get("num_bets"))
            rows.append(r)
    a = pd.DataFrame(rows)
    a["an_date"] = a["an_start"].dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    return a.drop_duplicates("an_id")


def attach(g, other, date_col, cols, tol_days=1):
    """Join `other` onto games by away/home and nearest date within tol_days."""
    other = other.copy()
    out = g.copy()
    for c in cols:
        out[c] = np.nan if c not in out else out[c]
    key = other.groupby(["away", "home"])
    idx = {k: v for k, v in key}
    vals = {c: [] for c in cols}
    for r in g.itertuples():
        hit = None
        for aw, hm in ((r.away, r.home), (r.home, r.away) if r.neutral else (None, None)):
            if aw is None or (aw, hm) not in idx:
                continue
            cand = idx[(aw, hm)]
            dd = (cand[date_col] - r.date).abs()
            j = dd.idxmin()
            if dd[j] <= pd.Timedelta(days=tol_days):
                hit = cand.loc[j]
                if aw != r.away:  # neutral game listed the other way round: flip home-oriented numbers
                    hit = hit.copy()
                    for c in cols:
                        if c.endswith(("_spread",)) or c in ("open", "sclose"):
                            hit[c] = -hit[c] if pd.notna(hit[c]) else hit[c]
                        if c in ("tix_home", "money_home", "ml_tix_home", "ml_money_home") and pd.notna(hit[c]):
                            hit[c] = 100 - hit[c]
                break
        for c in cols:
            vals[c].append(hit[c] if hit is not None and c in hit else np.nan)
    for c in cols:
        out[c] = vals[c]
    return out


# ---------------------------------------------------------------- play-by-play tables
PCOLS = ["season", "week", "season_type", "game_id", "posteam", "defteam", "home_team", "away_team", "play_type", "epa",
         "success", "wp", "qtr", "down", "pass", "rush", "qb_dropback", "qb_epa", "id", "name", "score_differential",
         "game_seconds_remaining", "two_point_attempt", "penalty", "touchdown", "td_player_id", "td_team",
         "rusher_player_id", "receiver_player_id", "yardline_100", "rush_attempt", "pass_attempt", "cpoe",
         "qb_scramble", "fumble_lost", "interception", "fixed_drive", "fixed_drive_result", "rusher_player_name",
         "receiver_player_name", "td_player_name", "complete_pass", "pass_touchdown", "rush_touchdown"]


def pbp_tables(seasons=None):
    tg_rows, qb_rows, rz_rows = [], [], []
    files = sorted(RAW.glob("pbp_*.parquet"))
    if seasons:
        files = [f for f in files if int(f.stem.split("_")[1]) in seasons]
    for f in files:
        p = pd.read_parquet(f, columns=PCOLS)
        p = p[p["posteam"].notna()]
        plays = p[(p["pass"] == 1) | (p["rush"] == 1)]
        plays = plays[plays["epa"].notna() & (plays["two_point_attempt"] != 1)]
        # garbage time: drop plays once the game is decided (win prob < 5% or > 95% in the 2nd half)
        live = ~((plays["qtr"] >= 3) & ((plays["wp"] < 0.05) | (plays["wp"] > 0.95)))
        for tag, sub in (("", plays), ("_gt", plays[live])):
            agg = sub.groupby(["game_id", "posteam"]).agg(
                plays=("epa", "size"), epa=("epa", "mean"), sr=("success", "mean"),
                pass_epa=("epa", lambda s: s[sub.loc[s.index, "pass"] == 1].mean()),
                rush_epa=("epa", lambda s: s[sub.loc[s.index, "rush"] == 1].mean()),
                pass_n=("pass", "sum")).reset_index()
            agg.columns = ["game_id", "team"] + [c + tag for c in agg.columns[2:]]
            tg_rows.append(agg.set_index(["game_id", "team"]))
        # drives / points per drive
        dr = p.dropna(subset=["fixed_drive"]).groupby(["game_id", "posteam", "fixed_drive"])["fixed_drive_result"].first().reset_index()
        dr["pts"] = dr["fixed_drive_result"].map({"Touchdown": 7.0, "Field goal": 3.0, "Opp touchdown": -7.0, "Safety": -2.0}).fillna(0)
        dd = dr.groupby(["game_id", "posteam"]).agg(drives=("pts", "size"), ppd=("pts", "mean")).reset_index()
        dd.columns = ["game_id", "team", "drives", "ppd"]
        tg_rows.append(dd.set_index(["game_id", "team"]))
        # QBs (dropback player, incl. scrambles)
        q = p[(p["qb_dropback"] == 1) & p["id"].notna() & p["qb_epa"].notna()]
        qa = q.groupby(["game_id", "posteam", "id"]).agg(name=("name", "first"), db=("qb_epa", "size"),
                                                         qb_epa=("qb_epa", "sum"), cpoe=("cpoe", "mean")).reset_index()
        qb_rows.append(qa.rename(columns={"posteam": "team", "id": "qb_id"}))
        # red-zone usage + TDs per player (TD sheet research)
        rz = p[(p["rush_attempt"] == 1) | (p["pass_attempt"] == 1)]
        for kind, idc, nm in (("rush", "rusher_player_id", "rusher_player_name"), ("tgt", "receiver_player_id", "receiver_player_name")):
            s = rz[rz[idc].notna()]
            a = s.groupby(["game_id", "posteam", idc]).agg(
                name=(nm, "first"), n=("epa", "size"), n20=("yardline_100", lambda y: (y <= 20).sum()),
                n10=("yardline_100", lambda y: (y <= 10).sum()), n5=("yardline_100", lambda y: (y <= 5).sum())).reset_index()
            a.columns = ["game_id", "team", "player_id", "name", f"{kind}", f"{kind}20", f"{kind}10", f"{kind}5"]
            rz_rows.append(a.set_index(["game_id", "team", "player_id"]))
        tds = p[(p["touchdown"] == 1) & p["td_player_id"].notna() & ((p["rush_touchdown"] == 1) | (p["pass_touchdown"] == 1))]
        t = tds.groupby(["game_id", "td_team", "td_player_id"]).agg(name=("td_player_name", "first"), td=("epa", "size")).reset_index()
        t.columns = ["game_id", "team", "player_id", "name", "td"]
        rz_rows.append(t.set_index(["game_id", "team", "player_id"]))
        print("pbp", f.stem, len(plays), flush=True)
    tg = pd.concat(tg_rows, axis=1)
    tg = tg.T.groupby(level=0).first().T.reset_index() if tg.columns.duplicated().any() else tg.reset_index()
    qb = pd.concat(qb_rows, ignore_index=True)
    rz = pd.concat(rz_rows)
    rz = rz.groupby(level=[0, 1, 2]).first()  # merge rush/target/td rows per player-game
    num_cols = [c for c in rz.columns if c != "name"]
    rz[num_cols] = rz[num_cols].fillna(0)
    rz = rz.reset_index()
    return tg, qb, rz


def update_pbp(seasons):
    """Rebuild the play-by-play tables for `seasons` only and splice them into the cached tables."""
    tg, qb, rz = pbp_tables(seasons)
    for name, new in (("tg", tg), ("qb", qb), ("rz", rz)):
        path = DATA / f"{name}.parquet"
        if path.exists():
            old = pd.read_parquet(path)
            old = old[~old["game_id"].str[:4].astype(int).isin(seasons)]
            new = pd.concat([old, new], ignore_index=True)
        new.to_parquet(path)


def main(part="all"):
    if part in ("pbp", "all"):
        tg, qb, rz = pbp_tables()
        tg.to_parquet(DATA / "tg.parquet")
        qb.to_parquet(DATA / "qb.parquet")
        rz.to_parquet(DATA / "rz.parquet")
        print("tg", tg.shape, "qb", qb.shape, "rz", rz.shape)
    if part == "pbp":
        return
    g = nflverse_games()
    s = fill_ny(sbr(), g)
    g = attach(g, s, "date", ["open", "topen", "sclose", "tsclose"], tol_days=2)
    a = action()
    acols = [c for c in a.columns if c not in ("an_start", "an_date", "away", "home")]
    g = attach(g, a, "an_date", acols, tol_days=2)
    g["an_open"] = g["an_open_spread"]
    g["open"] = g["open"].where(g["open"].notna(), g["an_open_spread"])
    g["topen"] = g["topen"].where(g["topen"].notna(), g["an_open_total"])
    for c in g.columns:
        if c.endswith("_ins"):
            g[c] = pd.to_datetime(g[c], utc=True, errors="coerce")
            continue
        if g[c].dtype == object and c not in ("game_id", "game_type", "weekday", "away", "home", "roof", "surface",
                                              "away_qb_id", "home_qb_id", "away_qb_name", "home_qb_name", "away_coach",
                                              "home_coach", "stadium_id", "stadium", "an_id"):
            g[c] = pd.to_numeric(g[c], errors="coerce")
    g["an_id"] = g["an_id"].astype("string")
    g.to_parquet(DATA / "games.parquet")
    cov = g.groupby("season").agg(n=("game_id", "size"), open=("open", lambda x: x.notna().mean()),
                                  topen=("topen", lambda x: x.notna().mean()), splits=("tix_home", lambda x: x.notna().mean()))
    print(cov.round(2).to_string())
    chk = g.dropna(subset=["sclose", "close"])
    print("SBR close vs nflverse close: mean abs diff", round((chk["sclose"] - chk["close"]).abs().mean(), 3),
          "share > 1.5:", round(((chk["sclose"] - chk["close"]).abs() > 1.5).mean(), 4))


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
