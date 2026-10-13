"""Situational features per game, from each team's point of view (all knowable before kickoff).
Output data/sit.parquet: game_id, side ('home'/'away'), team_id and the features below."""
import json, pathlib
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import model

DATA = model.DATA


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def utc_offset(tz, ts):
    try:
        return ZoneInfo(tz).utcoffset(ts.to_pydatetime().replace(tzinfo=None)).total_seconds() / 3600
    except Exception:
        return np.nan


def build():
    g, tg = model.load()
    g = g.sort_values("start")
    teams = json.loads((DATA / "raw" / "teams.json").read_text())
    loc = {t["id"]: t.get("location") or {} for t in teams}
    venues = {v["id"]: v for v in json.loads((DATA / "raw" / "venues.json").read_text())}

    # one row per team-game
    rows = []
    for r in g.itertuples():
        v = venues.get(r.venue_id, {}) if pd.notna(r.venue_id) else {}
        vlat, vlon = v.get("latitude"), v.get("longitude")
        vtz = v.get("timezone") or loc.get(r.home_id, {}).get("timezone")
        velev = v.get("elevation") or loc.get(r.home_id, {}).get("elevation")
        for side, tid, oid, pts, opts in (("home", r.home_id, r.away_id, r.home_pts, r.away_pts),
                                          ("away", r.away_id, r.home_id, r.away_pts, r.home_pts)):
            hl = loc.get(tid, {})
            rows.append(dict(game_id=r.game_id, season=r.season, wk=r.wk, start=r.start, side=side, team_id=tid, opp_id=oid,
                             pts=pts, opp_pts=opts, neutral=r.site == 0, conf_game=r.conf_game,
                             line=(r.spread if side == "home" else -r.spread) if pd.notna(r.spread) else np.nan,
                             elo=r.home_elo if side == "home" else r.away_elo,
                             opp_elo=r.away_elo if side == "home" else r.home_elo,
                             home_lat=hl.get("latitude"), home_lon=hl.get("longitude"), home_tz=hl.get("timezone"),
                             home_elev=float(hl["elevation"]) if hl.get("elevation") else np.nan,
                             v_lat=vlat, v_lon=vlon, v_tz=vtz, v_elev=float(velev) if velev else np.nan))
    t = pd.DataFrame(rows).sort_values(["team_id", "start"]).reset_index(drop=True)
    t["margin"] = t["pts"] - t["opp_pts"]
    t["cover"] = t["margin"] + t["line"]
    grp = t.groupby(["team_id", "season"])
    t["game_no"] = grp.cumcount() + 1
    t["rest_days"] = (t["start"] - grp["start"].shift()).dt.total_seconds() / 86400
    t["prev_margin"] = grp["margin"].shift()
    t["prev_cover"] = grp["cover"].shift()
    t["prev_line"] = grp["line"].shift()
    t["prev_side"] = grp["side"].shift()
    t["next_opp"] = grp["opp_id"].shift(-1)
    t["next_start"] = grp["start"].shift(-1)
    t["is_away"] = (t["side"] == "away") & ~t["neutral"]
    # consecutive road games including this one
    t["road_streak"] = t.groupby(["team_id", "season", (~t["is_away"]).cumsum()])["is_away"].cumsum()
    t["wins_before"] = grp["margin"].transform(lambda s: (s.shift() > 0).cumsum())
    t["losses_before"] = grp["margin"].transform(lambda s: (s.shift() < 0).cumsum())
    t["prev_upset_win"] = (t["prev_margin"] > 0) & (t["prev_line"] >= 7)
    t["prev_blowout_loss"] = t["prev_margin"] <= -21
    t["prev_big_cover"] = t["prev_cover"] >= 14
    t["prev_big_miss"] = t["prev_cover"] <= -14
    # lookahead: next opponent's current Elo (from that opponent's game this week, already known)
    elo_now = t.drop_duplicates(["team_id", "season", "wk"]).set_index(["team_id", "season", "wk"])["elo"]
    t["next_opp_elo"] = [elo_now.get((o, s, w), np.nan) if pd.notna(o) else np.nan
                         for o, s, w in zip(t["next_opp"], t["season"], t["wk"])]
    t["days_to_next"] = (t["next_start"] - t["start"]).dt.total_seconds() / 86400
    # revenge: lost the previous meeting within the last ~13 months
    pair = t[["team_id", "opp_id", "start", "margin"]].sort_values("start")
    pair["prev_meet_margin"] = pair.groupby(["team_id", "opp_id"])["margin"].shift()
    pair["prev_meet_start"] = pair.groupby(["team_id", "opp_id"])["start"].shift()
    t = t.merge(pair[["team_id", "opp_id", "start", "prev_meet_margin", "prev_meet_start"]], on=["team_id", "opp_id", "start"], how="left")
    t["revenge"] = (t["prev_meet_margin"] < 0) & ((t["start"] - t["prev_meet_start"]).dt.days < 400)
    # travel / clock / altitude
    t["travel_km"] = haversine_km(t["home_lat"], t["home_lon"], t["v_lat"], t["v_lon"])
    t["tz_home"] = [utc_offset(z, s) if isinstance(z, str) else np.nan for z, s in zip(t["home_tz"], t["start"])]
    t["tz_venue"] = [utc_offset(z, s) if isinstance(z, str) else np.nan for z, s in zip(t["v_tz"], t["start"])]
    t["tz_shift"] = t["tz_venue"] - t["tz_home"]  # >0 travelled east
    t["body_clock_hour"] = (t["start"].dt.hour + t["start"].dt.minute / 60 + t["tz_home"]) % 24
    t["local_hour"] = (t["start"].dt.hour + t["start"].dt.minute / 60 + t["tz_venue"]) % 24
    t["elev_gain"] = t["v_elev"] - t["home_elev"]
    # turnover luck in the previous game
    to = tg[["game_id", "team_id", "dr_to"]]
    t = t.merge(to, on=["game_id", "team_id"], how="left").merge(
        to.rename(columns={"team_id": "opp_id", "dr_to": "opp_dr_to"}), on=["game_id", "opp_id"], how="left")
    t["to_margin"] = t["opp_dr_to"] - t["dr_to"]
    t["prev_to_margin"] = t.groupby(["team_id", "season"])["to_margin"].shift()
    keep = ["game_id", "side", "team_id", "opp_id", "game_no", "rest_days", "prev_margin", "prev_cover", "prev_line",
            "prev_upset_win", "prev_blowout_loss", "prev_big_cover", "prev_big_miss", "road_streak", "wins_before",
            "losses_before", "next_opp_elo", "days_to_next", "revenge", "travel_km", "tz_shift", "body_clock_hour",
            "local_hour", "elev_gain", "v_elev", "prev_to_margin", "elo", "opp_elo"]
    out = t[keep]
    out.to_parquet(DATA / "sit.parquet")
    return out


if __name__ == "__main__":
    s = build()
    print(s.shape)
    print(s.describe().T.round(2).to_string())
