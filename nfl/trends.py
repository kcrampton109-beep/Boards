"""Trend picks carried over unchanged from the old NFL board engine (situational ATS / over-under records this season).
A situation becomes a pick at 6+ decided games this season at 80%+ (or 20% or worse -> other side). Backtest note below."""
SEASON = 2026

# ---------------------------------------------------------------- trends (situational ATS / over-under records)
# A trend becomes a pick when it has 6+ decided games this season at 80%+ (bet it) or 20% or worse (bet against it).
# Walk-forward test of this rule, 2012-2025 seasons: 154-129 (54.4%), about 20 picks a season, but 90-61 in 2012-18
# and 64-68 in 2019-25, so the edge has faded. Picks whose situation also hit 53%+ in the prior three seasons went 13-17,
# so that "Backed by history" label is no longer shown (every pick is tagged "This season only").
# Team-specific streaks ("X is 6-0 ATS") are shown for information only: betting them went 47% over the same years.
TREND_BACKTEST = "154-129 (54.4%), but 64-68 (48.5%) since 2019"
FIX = {"LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
TREND_RULE = dict(min_n=6, min_pct=0.80, first_week=4, hist_seasons=3, hist_min_n=50, hist_pct=0.53)
WATCH_PCT = 0.70


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_games(text, first_season=2009):
    import csv
    import io
    """Parse games.csv text -> list of game dicts (regular season only)."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if r["game_type"] != "REG" or int(r["season"]) < first_season:
            continue
        sl = _f(r["spread_line"])
        hs, as_ = _f(r["home_score"]), _f(r["away_score"])
        hour = int(r["gametime"].split(":")[0]) if r.get("gametime") else 13
        out.append(dict(
            game_id=r["game_id"], season=int(r["season"]), week=int(r["week"]), gameday=r["gameday"],
            gametime=r["gametime"], weekday=r["weekday"],
            home=FIX.get(r["home_team"], r["home_team"]), away=FIX.get(r["away_team"], r["away_team"]),
            home_pts=hs, away_pts=as_, home_line=(-sl if sl is not None else None), total_line=_f(r["total_line"]),
            neutral=r["location"] == "Neutral", division=r["div_game"] == "1", ot=r["overtime"] == "1",
            night=hour >= 19, roof=r["roof"], home_rest=_f(r["home_rest"]), away_rest=_f(r["away_rest"]), stadium=r.get("stadium"),
            played=hs is not None and as_ is not None))
    out.sort(key=lambda g: (g["season"], g["week"], g["gameday"], g["gametime"], g["game_id"]))
    return out


def sgn(x):
    return (x > 0) - (x < 0)


def team_rows(games):
    """One row per team per game with the previous-game context (same season)."""
    rows, hist = [], {}
    for g in games:
        for side in ("home", "away"):
            home = side == "home"
            team, opp = (g["home"], g["away"]) if home else (g["away"], g["home"])
            line = None if g["home_line"] is None else (g["home_line"] if home else -g["home_line"])
            pts, opts = (g["home_pts"], g["away_pts"]) if home else (g["away_pts"], g["home_pts"])
            rest, orest = (g["home_rest"], g["away_rest"]) if home else (g["away_rest"], g["home_rest"])
            r = dict(game_id=g["game_id"], season=g["season"], week=g["week"], team=team, opp=opp, lhome=home, home=home and not g["neutral"],
                     road=(not home) and not g["neutral"], neutral=g["neutral"], line=line, total_line=g["total_line"],
                     division=g["division"], night=g["night"], weekday=g["weekday"], rest=rest, orest=orest, played=g["played"])
            if g["played"]:
                r["margin"] = pts - opts
                r["cover"] = sgn(r["margin"] + line) if line is not None else None
                r["pts"], r["opts"] = pts, opts
                tl = g["total_line"]
                r["ou"] = sgn(pts + opts - tl) if tl is not None else None
            prev = hist.get((g["season"], team), [])
            r["prev"] = prev[-3:]           # up to 3 previous rows, oldest first
            rows.append(r)
            if g["played"]:
                hist.setdefault((g["season"], team), []).append(r)
    return rows


def _p(r, i=1):
    """i-th previous game (1 = last game) or None."""
    return r["prev"][-i] if len(r["prev"]) >= i else None


def _all(r, n, f):
    ps = [_p(r, i) for i in range(1, n + 1)]
    return all(p is not None and f(p) for p in ps)


# key: (label, kind, test(row) -> bool). kind "ats" = team point of view; "ou" = game totals (tested on the home row).
SITUATIONS = {
    "off_loss20": ("Off a loss by 20+", "ats", lambda r: _all(r, 1, lambda p: p["margin"] <= -20)),
    "off_win20": ("Off a win by 20+", "ats", lambda r: _all(r, 1, lambda p: p["margin"] >= 20)),
    "off_loss10": ("Off a double-digit loss", "ats", lambda r: _all(r, 1, lambda p: p["margin"] <= -10)),
    "off_win10": ("Off a double-digit win", "ats", lambda r: _all(r, 1, lambda p: p["margin"] >= 10)),
    "off_close_loss": ("Off a loss by 3 or less", "ats", lambda r: _all(r, 1, lambda p: -3 <= p["margin"] < 0)),
    "off_close_win": ("Off a win by 3 or less", "ats", lambda r: _all(r, 1, lambda p: 0 < p["margin"] <= 3)),
    "off_fav_loss": ("Off a loss as a favorite", "ats", lambda r: _all(r, 1, lambda p: p["margin"] < 0 and p["line"] is not None and p["line"] < 0)),
    "off_dog_win": ("Off a win as an underdog", "ats", lambda r: _all(r, 1, lambda p: p["margin"] > 0 and p["line"] is not None and p["line"] > 0)),
    "off_bigfav_loss": ("Off a loss as a 7+ point favorite", "ats", lambda r: _all(r, 1, lambda p: p["margin"] < 0 and p["line"] is not None and p["line"] <= -6.5)),
    "off_bigdog_win": ("Off a win as a 7+ point underdog", "ats", lambda r: _all(r, 1, lambda p: p["margin"] > 0 and p["line"] is not None and p["line"] >= 6.5)),
    "off_home_loss": ("Off a home loss", "ats", lambda r: _all(r, 1, lambda p: p["home"] and p["margin"] < 0)),
    "off_road_win": ("Off a road win", "ats", lambda r: _all(r, 1, lambda p: p["road"] and p["margin"] > 0)),
    "off_2L": ("Off 2 straight losses", "ats", lambda r: _all(r, 2, lambda p: p["margin"] < 0)),
    "off_2W": ("Off 2 straight wins", "ats", lambda r: _all(r, 2, lambda p: p["margin"] > 0)),
    "off_3L": ("Off 3+ straight losses", "ats", lambda r: _all(r, 3, lambda p: p["margin"] < 0)),
    "off_3W": ("Off 3+ straight wins", "ats", lambda r: _all(r, 3, lambda p: p["margin"] > 0)),
    "off_2atsL": ("Failed to cover 2 straight", "ats", lambda r: _all(r, 2, lambda p: p["cover"] == -1)),
    "off_2atsW": ("Covered 2 straight", "ats", lambda r: _all(r, 2, lambda p: p["cover"] == 1)),
    "off_3atsL": ("Failed to cover 3 straight", "ats", lambda r: _all(r, 3, lambda p: p["cover"] == -1)),
    "off_3atsW": ("Covered 3 straight", "ats", lambda r: _all(r, 3, lambda p: p["cover"] == 1)),
    "off_ot": ("Off an overtime game", "ats", lambda r: _all(r, 1, lambda p: p.get("_ot", False))),
    "off_30for": ("Off scoring 30+", "ats", lambda r: _all(r, 1, lambda p: p["pts"] >= 30)),
    "off_30against": ("Off allowing 30+", "ats", lambda r: _all(r, 1, lambda p: p["opts"] >= 30)),
    "off_10for": ("Off scoring 10 or fewer", "ats", lambda r: _all(r, 1, lambda p: p["pts"] <= 10)),
    "off_primetime": ("Off a prime-time game", "ats", lambda r: _all(r, 1, lambda p: p["night"])),
    "home_dog": ("Home underdog", "ats", lambda r: r["home"] and r["line"] is not None and r["line"] > 0),
    "home_fav": ("Home favorite", "ats", lambda r: r["home"] and r["line"] is not None and r["line"] < 0),
    "div_dog": ("Division underdog", "ats", lambda r: r["division"] and r["line"] is not None and r["line"] > 0),
    "dog7": ("Underdog of 7+", "ats", lambda r: r["line"] is not None and r["line"] >= 7),
    "dog_mid": ("Underdog of 3.5 to 6.5", "ats", lambda r: r["line"] is not None and 3.5 <= r["line"] <= 6.5),
    "dog3": ("Underdog of 3 or less", "ats", lambda r: r["line"] is not None and 0 < r["line"] <= 3),
    "off_bye": ("Off a bye", "ats", lambda r: r["week"] >= 3 and r["rest"] is not None and r["rest"] >= 12),
    "rest_edge": ("3+ more days of rest than the opponent", "ats", lambda r: r["rest"] is not None and r["orest"] is not None and r["rest"] - r["orest"] >= 3),
    "thu_home": ("Thursday home team", "ats", lambda r: r["weekday"] == "Thursday" and r["home"]),
    "night_dog": ("Prime-time underdog", "ats", lambda r: r["night"] and r["line"] is not None and r["line"] > 0),
    "road2": ("Second straight road game", "ats", lambda r: r["road"] and _all(r, 1, lambda p: p["road"])),
    "home_after_road2": ("Home after 2 straight road games", "ats", lambda r: r["home"] and _all(r, 2, lambda p: p["road"])),
    "off_monday": ("Off a Monday night game", "ats", lambda r: _all(r, 1, lambda p: p["weekday"] == "Monday")),
    # game totals (evaluated once per game on the home-team row)
    "ou_div": ("Division games", "ou", lambda r: r["division"]),
    "ou_night": ("Prime-time games", "ou", lambda r: r["night"]),
    "ou_thu": ("Thursday games", "ou", lambda r: r["weekday"] == "Thursday"),
    "ou_high": ("Totals of 47 or more", "ou", lambda r: r["total_line"] is not None and r["total_line"] >= 47),
    "ou_low": ("Totals of 41 or less", "ou", lambda r: r["total_line"] is not None and r["total_line"] <= 41),
    "ou_bigspread": ("Games with a 7+ point favorite", "ou", lambda r: r["line"] is not None and abs(r["line"]) >= 7),
    "ou_both_over": ("Both teams off an over", "ou", lambda r: _all(r, 1, lambda p: p["ou"] == 1) and _all(r["_opp"], 1, lambda p: p["ou"] == 1)),
    "ou_both_under": ("Both teams off an under", "ou", lambda r: _all(r, 1, lambda p: p["ou"] == -1) and _all(r["_opp"], 1, lambda p: p["ou"] == -1)),
    "ou_shootout": ("A team off a 55+ point game", "ou", lambda r: _all(r, 1, lambda p: p["pts"] + p["opts"] >= 55) or _all(r["_opp"], 1, lambda p: p["pts"] + p["opts"] >= 55)),
}

# team-specific trends (this season only): key prefix -> (label template, kind, test)
TEAM_SITUATIONS = {
    "t_ats": ("{T} against the spread this season", "ats", lambda r: True),
    "t_home": ("{T} at home this season", "ats", lambda r: r["home"]),
    "t_road": ("{T} on the road this season", "ats", lambda r: r["road"]),
    "t_fav": ("{T} as a favorite this season", "ats", lambda r: r["line"] is not None and r["line"] < 0),
    "t_dog": ("{T} as an underdog this season", "ats", lambda r: r["line"] is not None and r["line"] > 0),
    "t_ou": ("{T} games over/under this season", "ou", lambda r: True),
}


def attach_context(rows, games):
    """Link each row to its opponent row and mark overtime on played rows."""
    ot = {g["game_id"]: g["ot"] for g in games}
    by = {}
    for r in rows:
        by[(r["game_id"], r["team"])] = r
        r["_ot"] = ot.get(r["game_id"], False)
    for r in rows:
        r["_opp"] = by[(r["game_id"], r["opp"])]
    return rows


def tag_rows(rows):
    """Precompute which trend keys each row belongs to."""
    for r in rows:
        keys = []
        for k, (_, kind, f) in SITUATIONS.items():
            if kind == "ou" and not r["lhome"]:
                continue
            try:
                if f(r):
                    keys.append(k)
            except (KeyError, TypeError):
                pass
        for k, (_, kind, f) in TEAM_SITUATIONS.items():
            if f(r):
                keys.append(f"{k}:{r['team']}")
        r["keys"] = keys
    return rows


def result_of(r, key):
    """+1/-1/0 for the row under this key (ATS cover, or over/under for totals)."""
    kind = SITUATIONS[key][1] if key in SITUATIONS else TEAM_SITUATIONS[key.split(":")[0]][1]
    return (r.get("ou") if kind == "ou" else r.get("cover")), kind


def prepare(games):
    rows = team_rows(games)
    attach_context(rows, games)
    tag_rows(rows)
    return rows


def records_by(rows, season, before_week=None, seasons=None):
    """{key: [w, l, p]} over played rows of `season` before `before_week` (or over the given seasons)."""
    rec = {}
    for r in rows:
        if not r["played"]:
            continue
        if seasons is not None:
            if r["season"] not in seasons:
                continue
        elif r["season"] != season or (before_week is not None and r["week"] >= before_week):
            continue
        for k in r["keys"]:
            if seasons is not None and ":" in k:
                continue
            res, _ = result_of(r, k)
            if res is None:
                continue
            x = rec.setdefault(k, [0, 0, 0])
            x[0 if res > 0 else 1 if res < 0 else 2] += 1
    return rec


def label_of(key):
    if key in SITUATIONS:
        return SITUATIONS[key][0]
    pre, team = key.split(":")
    return TEAM_SITUATIONS[pre][0].format(T=team)


def kind_of(key):
    return SITUATIONS[key][1] if key in SITUATIONS else TEAM_SITUATIONS[key.split(":")[0]][1]


def fmt_rec(w, l, p=0):
    return f"{w}-{l}" + (f"-{p}" if p else "")


def pct(w, l):
    return w / (w + l) if w + l else None


def live_trends(rows, season, week, rule=TREND_RULE):
    """Trends that qualify going into `week` of `season`: {key: dict(direction, this, hist, tag)}."""
    if week < rule["first_week"]:
        return {}
    ytd = records_by(rows, season, before_week=week)
    hist = records_by(rows, season, seasons=set(range(season - rule["hist_seasons"], season)))
    live = {}
    for k, (w, l, p) in ytd.items():
        if w + l < rule["min_n"]:
            continue
        q = w / (w + l)
        if q >= rule["min_pct"]:
            direction = 1
        elif q <= 1 - rule["min_pct"]:
            direction = -1
        else:
            continue
        hw, hl, hp = hist.get(k, [0, 0, 0])
        # prior-season record is shown in each reason; it is not used as a quality label (see TREND_BACKTEST note)
        live[k] = dict(direction=direction, this=(w, l, p), hist=(hw, hl, hp) if ":" not in k else None,
                       tag="This season only")
    return live


def _side_text(kind, key, direction, r):
    """Which bet a live trend points to for this row, as (bet_key, bet_team_or_OU)."""
    if kind == "ou":
        return ("total", "Over" if direction == 1 else "Under")
    return ("spread", r["team"] if direction == 1 else r["opp"])


def reason_text(key, t, season):
    w, l, p = t["this"]
    kind = kind_of(key)
    lab = label_of(key)
    unit = "over/under" if kind == "ou" else "ATS"
    if kind == "ou":
        s = f"{lab}: {fmt_rec(w, l, p)} to the over this season"
    else:
        s = f"{lab}: {fmt_rec(w, l, p)} ATS this season"
    if t["hist"] is not None:
        hw, hl, hp = t["hist"]
        hq = pct(hw, hl)
        if hq is not None:
            what = "over" if kind == "ou" else "covered"
            s += f" ({season - 3}-{str(season - 1)[2:]}: {what} {hq * 100:.0f}% of {hw + hl})"
    return s


def week_flags(rows, season, week, rule=TREND_RULE):
    """Trend picks for the games of `week`: list of dicts, one per (game, bet) with every reason behind it."""
    live = live_trends(rows, season, week, rule)
    if not live:
        return []
    picks = {}
    for r in rows:
        if r["season"] != season or r["week"] != week:
            continue
        for k in r["keys"]:
            if k not in live:
                continue
            t = live[k]
            kind = kind_of(k)
            bet_type, side = _side_text(kind, k, t["direction"], r)
            pk = (r["game_id"], bet_type, side)
            x = picks.setdefault(pk, dict(game_id=r["game_id"], bet=bet_type, side=side, reasons=[], tags=set(), keys=[]))
            who = r["team"]
            if kind == "ou":
                txt = reason_text(k, t, season)
            elif t["direction"] == 1:
                txt = f"{who} — " + reason_text(k, t, season)
            else:
                txt = f"Against {who} — " + reason_text(k, t, season)
            x["reasons"].append(txt)
            x["tags"].add(t["tag"])
            x["keys"].append(k)
    # drop games where trends point both ways on the same bet
    out, by_game = [], {}
    for pk, x in picks.items():
        by_game.setdefault((x["game_id"], x["bet"]), []).append(x)
    for (gid, bet), xs in by_game.items():
        if len({x["side"] for x in xs}) > 1:
            for x in xs:
                x["conflict"] = True
        for x in xs:
            x["tag"] = "Backed by history" if "Backed by history" in x["tags"] else "This season only"
            x["tags"] = sorted(x["tags"])
            x.setdefault("conflict", False)
            out.append(x)
    return out


def grade_flag(x, rows_by_game):
    """+1/-1/0 result of a trend pick, None if not played."""
    g = rows_by_game.get(x["game_id"])
    if g is None or not g["played"]:
        return None
    if x["bet"] == "total":
        return g["ou"] if x["side"] == "Over" else (-g["ou"] if g["ou"] is not None else None)
    r = g if g["team"] == x["side"] else g["_opp"]
    return r["cover"]


