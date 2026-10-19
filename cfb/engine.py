"""CFB board engine.

Each run: (1) refresh CFBD data at most once a day, rebuild tables and team ratings when new results
arrive; (2) pull live lines + splits for DK / FanDuel / BetMGM (Action Network); (3) price every
upcoming FBS/FCS game against the model; (4) log new plays to the paper ledger at the price available
right now; (5) grade finished plays with closing-line value; (6) write board/state.json.

Rules come from the backtest (research/opener.py): the model beats OPENING lines, not closing
lines, so plays are flagged on early-week prices and graded on CLV as well as wins.
  STRONG spread  FBS / FBS-vs-FCS: model differs from your best book by >= 7 pts (regular season)
  FCS spread     FCS-vs-FCS: >= 5 pts (DK/FD only; tracked separately, paper-only by your choice)
  RESEARCH       smaller gaps, totals, moneylines, 1H, angles: logged to learn from
SIZING (since 2026-10-06, see stake_for): strong 9+ 1.5u, strong/FCS/weather 1u, FCS lean 4+ 1u, other leans 0.5u,
research unders 1u, overs/near-wind 0.5u, ML alts 0.25u. Flat ledger backup: board/ledger_backup_flat_2026-10-06.json.
"""
import datetime as dt, json, math, os, pathlib, sys, threading, time, traceback
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
BOARD = ROOT / "board"
BOARD.mkdir(exist_ok=True)
ET = ZoneInfo("America/New_York")
SEASON = 2026
BOOKS = {68: "DK", 69: "FD", 75: "MGM"}
ALLB = "15,30,68,69,75,71,79,123"
AN = "https://api.actionnetwork.com/web/v2/scoreboard/ncaaf"
LEDGER = BOARD / "ledger.json"
STATE = BOARD / "state.json"
SEEN = BOARD / "lines_seen.json"
RULES = dict(strong_fbs=7.0, strong_fcs=5.0, lean_fbs=5.0, lean_fcs=3.0, tot_fcs=5.0, tot_fbs=9.0,
             ml_ev=0.06, worst_odds=-125, margin_sd=15.5, wind_under=18.0, wind_hours=24.0,
             stale_pts=3.0)  # a book this far off the market is treated as a stale, unbettable line
TIER_RANK = {"RESEARCH": 0, "LEAN": 1, "STRONG": 2, "FCS": 2, "WEATHER": 2}
BACKTEST = {  # median-open results 2021-25 (+2026 YTD), research/opener.py
    "strong_fbs": "FBS & FBS-vs-FCS, gap 7+: 55.5% ATS vs opener in 2021-26 (485 bets); line moved our way about 1.2 pts on average",
    "strong_fcs": "FCS-vs-FCS, gap 5+: 58.2% ATS vs opener (557 bets); line moved our way 1.9 pts on average",
    "weather": "Wind 15-20 mph at kickoff: unders hit 59.0% (2014-19) and 56.6% (2020-26) vs the CLOSING total (559 games)",
    "vs_close": "Against CLOSING lines the same model is ~50% (FBS) / ~52% (FCS): the edge is getting the early number",
}


def now_et():
    return dt.datetime.now(ET)


def load_json(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def save_json(p, obj):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.replace(p)


# ---------------------------------------------------------------- 1. data + ratings
def refresh(force=False):
    """CFBD current-season refresh (4 API calls) at most every 20h, or when last week's stats are missing."""
    import cfbd, build, run_models, evaluate as ev
    stamp = load_json(BOARD / "refresh.json", {})
    last = pd.Timestamp(stamp.get("ts", "2000-01-01"))
    age_h = (pd.Timestamp.now() - last).total_seconds() / 3600
    need = force or age_h > 20 or not (BOARD / "model_lines.parquet").exists()
    if not need and stamp.get("missing_stats", 0) > 0 and age_h > 4 and now_et().weekday() == 6:
        need = True  # Sunday: retry until Saturday's advanced stats are in
    if not need and age_h > 2.5 and len(results_pending(pd.read_parquet(BOARD / "model_lines.parquet"))):
        need = True  # games finished since the last refresh: pull results before next week's openers are priced
    recent = [t for t in stamp.get("history", []) if pd.Timestamp(t) > pd.Timestamp.now() - pd.Timedelta(hours=24)]
    if need and not force and len(recent) >= 8:
        return False  # API budget guard (1,000 calls/month)
    if not need:
        return False
    stamp["history"] = recent + [pd.Timestamp.now().isoformat()]
    save_json(BOARD / "refresh.json", stamp)
    for path, kw in (("/games", dict(year=SEASON, seasonType="both")), ("/lines", dict(year=SEASON, seasonType="both")),
                     ("/stats/game/advanced", dict(year=SEASON, seasonType="both", excludeGarbageTime="true")),
                     ("/drives", dict(year=SEASON, seasonType="both"))):
        cfbd.get(path, refresh=True, **kw)
    rebuild()
    return True


def rebuild():
    """Rebuild tables, ratings and this season's model lines from cached data (no API calls)."""
    import build, run_models, evaluate as ev
    build.main()
    wide, long = run_models.run_all(0)
    wide.to_parquet(ROOT / "data" / "preds.parquet")
    long.to_parquet(ROOT / "data" / "preds_long.parquet")  # ratings() reads it; was never rewritten after Oct 2
    g = ev.frame()
    g = g[g["season"] >= 2015].copy()
    g["blend"] = ev.blend(g, ["margin_hat_pts", "margin_hat_ppa", "margin_hat_sr"])
    g["tot_blend"] = ev.blend(g, ["total_hat_pts", "total_hat_ppa", "total_hat_sr"], target="points")
    cur = g[g["season"] == SEASON]
    tg = pd.read_parquet(ROOT / "data" / "tg.parquet")
    done = cur[cur["completed"] == True]
    recent = done[done["start"] > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)]
    missing = int((~recent["game_id"].isin(tg["game_id"])).sum())
    keep = ["game_id", "season", "week", "wk", "season_type", "start", "home", "home_id", "away", "away_id", "home_cls",
            "away_cls", "site", "blend", "tot_blend", "completed", "home_pts", "away_pts", "venue_id", "close", "tclose",
            "n_home_pts", "n_away_pts"]
    prev = g[(g["season"] == SEASON - 1)]
    known = set(prev["home_id"]) | set(prev["away_id"])
    cur = cur.assign(new_home=~cur["home_id"].isin(known), new_away=~cur["away_id"].isin(known))
    keep += ["new_home", "new_away"]
    cur[keep].to_parquet(BOARD / "model_lines.parquet")
    ratings(g)
    import features
    features.build()
    stamp = load_json(BOARD / "refresh.json", {})
    stamp.update(ts=pd.Timestamp.now().isoformat(), missing_stats=missing)
    save_json(BOARD / "refresh.json", stamp)


def ratings(g):
    """Current power ratings (points-model O/D + blended margin vs an average team on a neutral field)."""
    long = pd.read_parquet(ROOT / "data" / "preds_long.parquet")
    p = long[(long["kind"] == "pts")].merge(g[["game_id", "season", "start", "home", "away", "home_id", "away_id",
                                               "home_cls", "away_cls"]], on="game_id")
    p = p[p["season"] == SEASON].sort_values("start")
    rows = {}
    for r in p.itertuples():
        rows[r.home_id] = (r.home, r.home_cls, r.O_home, r.D_home, r.start)
        rows[r.away_id] = (r.away, r.away_cls, r.O_away, r.D_away, r.start)
    rt = pd.DataFrame([dict(team_id=k, team=v[0], cls=v[1], off=v[2], dfn=v[3]) for k, v in rows.items()])
    rt["net"] = rt["off"] + rt["dfn"]
    rt["net"] -= rt.loc[rt["cls"] == "fbs", "net"].mean()
    rt.sort_values("net", ascending=False).to_parquet(BOARD / "ratings.parquet")


# ---------------------------------------------------------------- 2. live prices
def fetch_live():
    def an(div, **extra):
        for attempt in range(3):  # retry: the network can hiccup
            try:
                r = requests.get(AN, params={"bookIds": ALLB, "periods": "event,firsthalf", "division": div, **extra},
                                 headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
                r.raise_for_status()
                return r.json().get("games", [])
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(30)
    games = []
    for div in ("FBS", "FCS"):
        cur = an(div)
        for gm in cur:
            gm["_div"] = div
        games += cur
        # Saturday night Action Network keeps the finished week as "current" until about 5 AM Sunday, so next week's
        # openers would be missed for hours: once nothing is left to play, also fetch next week (marked _next)
        if cur and not any(g.get("status") == "scheduled" for g in cur):
            nxt = an(div, week=max(g.get("week") or 0 for g in cur) + 1)
            for gm in nxt:
                gm["_div"], gm["_next"] = div, True
            games += nxt
    return games


def team_map(model_lines):
    m = {int(k): v for k, v in load_json(ROOT / "data" / "an_team_map.json", {}).items()}
    return m


def parse_live(games, ml, tmap):
    """One row per (game, book) with home-oriented spread/total/ML and odds + consensus splits."""
    from build import _norm
    names = pd.concat([ml[["home_id", "home"]].set_axis(["id", "name"], axis=1),
                       ml[["away_id", "away"]].set_axis(["id", "name"], axis=1)]).drop_duplicates("id")
    byname = {_norm(n): i for i, n in zip(names["id"], names["name"])}
    ml = ml.copy()
    ml["day"] = ml["start"].dt.tz_convert(ET).dt.date
    key = {}
    for r in ml.itertuples():
        for dd in (-1, 0, 1):
            key[(r.day + dt.timedelta(days=dd), frozenset((r.home_id, r.away_id)))] = r
    out = []
    for gm in games:
        teams = {t["id"]: t for t in gm.get("teams", [])}
        def cid(aid):
            if aid in tmap:
                return tmap[aid]
            nm = _norm(teams.get(aid, {}).get("full_name"))
            for k, v in byname.items():
                if nm.startswith(k + " ") or nm == k:
                    return v
            return None
        ah, aa = cid(gm["home_team_id"]), cid(gm["away_team_id"])
        start = pd.Timestamp(gm["start_time"]).tz_convert(ET)
        r = key.get((start.date(), frozenset((ah, aa))))
        if r is None:
            continue
        same = ah == r.home_id
        rec = dict(game_id=r.game_id, an_id=gm["id"], status=gm.get("status"), kickoff=start, div=gm["_div"],
                   next=bool(gm.get("_next")))
        for bid, mk in (gm.get("markets") or {}).items():
            bid = int(bid)
            ev_ = mk.get("event") or {}
            row = dict(rec, book=bid)
            for o in ev_.get("spread") or []:
                is_home = (o.get("team_id") == gm["home_team_id"]) == same
                row["sp_home" if is_home else "sp_away"] = o.get("value")
                row["sp_home_odds" if is_home else "sp_away_odds"] = o.get("odds")
                bi = o.get("bet_info") or {}
                if is_home:
                    row["tix_home"] = (bi.get("tickets") or {}).get("percent")
                    row["money_home"] = (bi.get("money") or {}).get("percent")
            for o in ev_.get("total") or []:
                row["tot" ] = o.get("value")
                row["over_odds" if o.get("side") == "over" else "under_odds"] = o.get("odds")
            for o in ev_.get("moneyline") or []:
                is_home = (o.get("team_id") == gm["home_team_id"]) == same
                row["ml_home" if is_home else "ml_away"] = o.get("odds")
            h1 = (mk.get("firsthalf") or {})
            for o in h1.get("spread") or []:
                is_home = (o.get("team_id") == gm["home_team_id"]) == same
                row["h1_home" if is_home else "h1_away"] = o.get("value")
                row["h1_home_odds" if is_home else "h1_away_odds"] = o.get("odds")
            out.append(row)
    df = pd.DataFrame(out)
    # FBS-vs-FCS games appear in both division feeds
    return df.drop_duplicates(["game_id", "book"]) if len(df) else df


def forecast_weather(games):
    """Open-Meteo forecast for outdoor games kicking off within 48h: mean wind (mph) / rain / temp, kickoff..+3h."""
    venues = {v["id"]: v for v in json.loads((ROOT / "data" / "raw" / "venues.json").read_text())}
    ml = pd.read_parquet(BOARD / "model_lines.parquet")
    vid = dict(zip(ml["game_id"], ml["venue_id"]))
    soon = []
    for g in games:
        hrs = (g["kickoff"] - now_et()).total_seconds() / 3600
        v = venues.get(vid.get(g["game_id"]))
        if v and 0 < hrs <= 48 and not v.get("dome") and v.get("latitude") is not None:
            soon.append((g, v))
    out = {}
    for i in range(0, len(soon), 40):
        ch = soon[i:i + 40]
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=60, params=dict(
                latitude=",".join(f"{v['latitude']:.4f}" for _, v in ch), longitude=",".join(f"{v['longitude']:.4f}" for _, v in ch),
                hourly="wind_speed_10m,precipitation,temperature_2m", wind_speed_unit="mph", temperature_unit="fahrenheit",
                timezone="UTC", forecast_days=4))
            d = r.json()
            d = d if isinstance(d, list) else [d]
        except Exception:
            continue
        for (g, v), h in zip(ch, d):
            h = h.get("hourly") or {}
            t = pd.to_datetime(h.get("time", []), utc=True)
            k0 = g["kickoff"].tz_convert("UTC").floor("h")
            m = (t >= k0) & (t <= k0 + pd.Timedelta(hours=3))
            if m.any():
                out[g["game_id"]] = dict(wind=float(np.nanmean(np.array(h["wind_speed_10m"], float)[m])),
                                         rain=float(np.nansum(np.array(h["precipitation"], float)[m])),
                                         temp=float(np.nanmean(np.array(h["temperature_2m"], float)[m])))
    return out


# ---------------------------------------------------------------- 3. price + flag
def american_to_prob(o):
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def payout(o):
    return o / 100 if o > 0 else 100 / -o


def stake_for(f):
    """Units for a play, sized from what is known when it's flagged (2021-25 opener backtest, sizing replay 2026-10-06):
    strong edge 9+ 1.5u, other strong / FCS / weather 1u; FCS leans 4+ 1u, other leans 0.5u;
    research: model-total unders 1u, overs and near-miss wind 0.5u, moneyline alts 0.25u."""
    t, m, ed = f["tier"], f["market"], f.get("edge") or 0
    if t == "STRONG":
        return 1.5 if ed >= 9 else 1.0
    if t in ("FCS", "WEATHER"):
        return 1.0
    if t == "LEAN":
        return 1.0 if f.get("grp") == "FCS-FCS" and ed >= 4 else 0.5
    if m == "moneyline":
        return 0.25
    if m == "total" and f.get("side") == "under" and "wind" not in (f.get("pick") or ""):
        return 1.0
    return 0.5


def resize(led):
    """Apply stake_for to every ledger entry (idempotent; restates entries logged flat before the switch)."""
    for e in led:
        if e["status"] == "void":
            continue
        st = stake_for(e)
        if e.get("stake") == st:
            continue
        e["stake"] = st
        if e["status"] in ("W", "L", "P"):
            e["units"] = round(st * payout(e["odds"]) if e["status"] == "W" else (-st if e["status"] == "L" else 0.0), 3)


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def tags_for(g, sit):
    """Angles that held in both 2014-19 and 2020-25 (research/angles.py)."""
    t = []
    s = sit.get((g["game_id"], "away"))
    if s is not None and pd.notna(s.get("elev_gain")) and s["elev_gain"] >= 1000 and not g["neutral"]:
        t.append((f"Altitude: fade {g['away']} (visitor climbing 1000m+)", "away", -1))
    for side in ("home", "away"):
        s = sit.get((g["game_id"], side))
        if s is not None and (s.get("losses_before") or 0) >= 7:
            t.append((f"7+ losses: fade {g[side]}", side, -1))
    return t


def results_pending(ml):
    """Games that kicked off 5+ hours ago (within the last 4 days) whose results aren't in the ratings yet."""
    start = pd.to_datetime(ml["start"], utc=True)
    now = pd.Timestamp.now(tz="UTC")
    return ml[(ml["completed"] != True) & (start < now - pd.Timedelta(hours=5)) & (start > now - pd.Timedelta(days=4))]


def price(ml, live):
    """Every upcoming game: model numbers, each user book's prices, best price per side and the gap.
    A game is 'provisional' while either team's last game isn't in the ratings yet: shown, never logged."""
    pend = results_pending(ml)
    stale_teams = set(pend["home_id"]) | set(pend["away_id"])
    up = ml[(ml["completed"] != True)].merge(live[live["book"].isin(list(BOOKS))], on="game_id")
    up = up[up["status"].isin(["scheduled", None]) | up["status"].isna()]
    cons = live[live["book"] == 15].set_index("game_id")
    rows = []
    for gid, d in up.groupby("game_id"):
        if "next" in d and d["next"].any() and (d["sp_home"].notna().sum() if "sp_home" in d else 0) < 2:
            continue  # next week's game posted at only one of the user's books: a look-ahead line, not bettable yet
        r0 = d.iloc[0]
        grp = "FCS-FCS" if (r0.home_cls == "fcs" and r0.away_cls == "fcs") else ("FBS-FCS" if "fcs" in (r0.home_cls, r0.away_cls) else "FBS")
        model_line = -r0.blend if pd.notna(r0.blend) else np.nan
        rec = dict(game_id=gid, week=int(r0.week), season_type=r0.season_type, kickoff=r0.kickoff, grp=grp,
                   home=r0.home, away=r0.away, neutral=r0.site == 0, model_line=model_line,
                   model_total=r0.tot_blend, books={},
                   n_min=int(min(r0.n_home_pts, r0.n_away_pts)) if pd.notna(r0.n_home_pts) and pd.notna(r0.n_away_pts) else 0,
                   new_team=bool(r0.new_home or r0.new_away),
                   provisional=bool({r0.home_id, r0.away_id} & stale_teams))
        if gid in cons.index:
            c = cons.loc[gid]
            c = c.iloc[0] if isinstance(c, pd.DataFrame) else c
            rec.update(cons_line=c.get("sp_home"), cons_total=c.get("tot"), tix_home=c.get("tix_home"), money_home=c.get("money_home"))
        for b in d.itertuples():
            rec["books"][BOOKS[b.book]] = {k: (None if pd.isna(getattr(b, k, np.nan)) else float(getattr(b, k)))
                                           for k in ("sp_home", "sp_home_odds", "sp_away", "sp_away_odds", "tot", "over_odds",
                                                     "under_odds", "ml_home", "ml_away", "h1_home", "h1_home_odds", "h1_away", "h1_away_odds")
                                           if hasattr(b, k)}
        drop_stale(rec)
        rows.append(rec)
    return rows


def drop_stale(rec):
    """Blank a book's spread or total when it sits stale_pts+ off the market (consensus, else the other books' median).
    A line that far behind is usually stale and can't really be bet, e.g. BetMGM still at Alabama -1.5 on Oct 11
    while every other book was at -6.5."""
    for key, fields, cons in (("sp_home", ("sp_home", "sp_home_odds", "sp_away", "sp_away_odds"), rec.get("cons_line")),
                              ("tot", ("tot", "over_odds", "under_odds"), rec.get("cons_total"))):
        vals = {k: v[key] for k, v in rec["books"].items() if v.get(key) is not None}
        for k, val in vals.items():
            others = [x for kk, x in vals.items() if kk != k]
            ref = cons if cons is not None and not pd.isna(cons) else (float(np.median(others)) if others else None)
            if ref is not None and abs(val - ref) >= RULES["stale_pts"]:
                for f in fields:
                    rec["books"][k][f] = None
                rec.setdefault("stale_books", []).append(f"{k} {'spread' if key == 'sp_home' else 'total'} {val:g}")


def best(books, line_key, odds_key, higher_better=True):
    c = [(v[line_key], v.get(odds_key) or -110, k) for k, v in books.items() if v.get(line_key) is not None]
    c = [x for x in c if x[1] >= RULES["worst_odds"]]
    if not c:
        return None
    c.sort(key=lambda x: ((x[0] if higher_better else -x[0]), x[1]), reverse=True)
    return c[0]


def flags(games, sit):
    out = []
    for g in games:
        if pd.isna(g["model_line"]):
            continue
        reg = g["season_type"] == "regular"
        fcs = g["grp"] == "FCS-FCS"
        # spreads: home side gets book_line; edge = book line - model line from that side's view
        for side, key, okey, sign in (("home", "sp_home", "sp_home_odds", 1), ("away", "sp_away", "sp_away_odds", -1)):
            b = best(g["books"], key, okey)
            if not b:
                continue
            line, odds, book = b
            edge = line - sign * g["model_line"]
            strong = RULES["strong_fcs"] if fcs else RULES["strong_fbs"]
            lean = RULES["lean_fcs"] if fcs else RULES["lean_fbs"]
            if edge >= lean:
                # week 1 runs on preseason priors (backtested); programs new to D1 need 5 D1 games first
                enough = (g["n_min"] >= (5 if g["new_team"] else 2)) or (g["week"] <= 1 and not g["new_team"])
                tier = ("FCS" if fcs else "STRONG") if (edge >= strong and reg and enough) else "LEAN"
                team = g[side]
                need = strong if tier in ("STRONG", "FCS") else lean
                out.append(dict(game_id=g["game_id"], market="spread", side=side, pick=f"{team} {line:+g}", team=team,
                                line=line, odds=odds, book=book, edge=round(edge, 1), tier=tier, grp=g["grp"],
                                model=round(sign * g["model_line"], 1),
                                limit=math.ceil((sign * g["model_line"] + need) * 2) / 2))
        # totals (research only)
        if pd.notna(g.get("model_total")):
            for side, okey in (("over", "over_odds"), ("under", "under_odds")):
                b = best(g["books"], "tot", okey, higher_better=(side == "under"))
                if not b:
                    continue
                line, odds, book = b
                edge = (g["model_total"] - line) if side == "over" else (line - g["model_total"])
                if edge >= (RULES["tot_fcs"] if fcs else RULES["tot_fbs"]):
                    out.append(dict(game_id=g["game_id"], market="total", side=side, pick=f"{side.title()} {line:g}",
                                    team=None, line=line, odds=odds, book=book, edge=round(edge, 1), tier="RESEARCH",
                                    grp=g["grp"], model=round(g["model_total"], 1)))
        # weather -> under. Backtest (56.6-59% vs close) used wind MEASURED at kickoff; the board only has forecasts, which
        # run windy 1-2 days out (Oct 15-17: 31 plays on 15-18 mph forecasts went 14-17). Since Oct 19 a WEATHER play
        # needs an 18+ mph forecast within 24 h of kickoff; 15+ mph or 24-48 h out is logged as near-miss research.
        wx = g.get("wx")
        if wx and wx["wind"] >= 15.0:
            b = best(g["books"], "tot", "under_odds", higher_better=True)
            if b:
                line, odds, book = b
                hrs = (g["kickoff"] - now_et()).total_seconds() / 3600
                tier = "WEATHER" if wx["wind"] >= RULES["wind_under"] and hrs <= RULES["wind_hours"] else "RESEARCH"
                out.append(dict(game_id=g["game_id"], market="total", side="under", pick=f"Under {line:g} (wind {wx['wind']:.0f} mph)",
                                team=None, line=line, odds=odds, book=book, edge=round(wx["wind"], 1), tier=tier,
                                grp=g["grp"], model=None))
        # moneylines (research only): model win prob from the projected margin
        p_home = norm_cdf(-g["model_line"] / RULES["margin_sd"])
        for side, key, p in (("home", "ml_home", p_home), ("away", "ml_away", 1 - p_home)):
            c = [(v[key], k) for k, v in g["books"].items() if v.get(key) is not None]
            if not c:
                continue
            odds, book = max(c)
            evv = p * payout(odds) - (1 - p)
            strong_side = any(f["game_id"] == g["game_id"] and f["market"] == "spread" and f["side"] == side
                              and f["tier"] in ("STRONG", "FCS") for f in out)
            if strong_side and evv >= RULES["ml_ev"] and -300 <= odds <= 400:
                out.append(dict(game_id=g["game_id"], market="moneyline", side=side, pick=f"{g[side]} ML {odds:+g} (alt to spread)",
                                team=g[side], line=None, odds=odds, book=book, edge=round(evv * 100, 1), tier="RESEARCH",
                                grp=g["grp"], model=round(p * 100, 1)))
        # angle tags
        for label, side, direction in tags_for(g, sit):
            out.append(dict(game_id=g["game_id"], market="angle", side=side if direction > 0 else ("away" if side == "home" else "home"),
                            pick=label, team=None, line=None, odds=None, book=None, edge=None, tier="ANGLE", grp=g["grp"], model=None))
    return out


# ---------------------------------------------------------------- 4/5. ledger
def update_ledger(fl, games, ml, live):
    led = load_json(LEDGER, [])
    # best tier already logged per play; a play first logged as a lean that later reaches a main tier (the gap grew)
    # is logged again at the new tier and today's price, under id "<play id>|<tier>"
    have = {}
    for e in led:
        if e["status"] != "void":
            b = e.get("base", e["id"])
            have[b] = max(have.get(b, -1), TIER_RANK.get(e["tier"], 0))
    gmap = {g["game_id"]: g for g in games}
    ts = now_et().isoformat(timespec="minutes")
    for f in fl:
        if f["tier"] == "ANGLE" or gmap[f["game_id"]].get("provisional"):
            continue  # provisional = ratings don't have last week's results yet; log once they do
        base = play_id(f)
        rank = TIER_RANK.get(f["tier"], 0)
        if base in have and rank <= have[base]:
            continue
        eid = base if base not in have else f"{base}|{f['tier']}"
        g = gmap[f["game_id"]]
        led.append(dict(id=eid, base=base, flagged=ts, season=SEASON, week=g["week"], game_id=f["game_id"],
                        matchup=f"{g['away']} {'vs' if g['neutral'] else '@'} {g['home']}", kickoff=str(g["kickoff"]),
                        tier=f["tier"], market=f["market"], side=f["side"], pick=f["pick"], book=f["book"], line=f["line"],
                        odds=f["odds"], stake=stake_for(f), edge=f["edge"], model=f["model"], grp=f["grp"], limit=f.get("limit"),
                        status="open"))
        have[base] = rank
    # remember the last consensus line seen before kickoff = our closing line
    seen = load_json(SEEN, {})
    c = live[live["book"] == 15]
    for r in c.itertuples():
        if r.status == "scheduled":
            seen[str(r.game_id)] = dict(sp_home=getattr(r, "sp_home", None), tot=getattr(r, "tot", None),
                                        ml_home=getattr(r, "ml_home", None), ml_away=getattr(r, "ml_away", None),
                                        ts=ts)
    save_json(SEEN, seen)
    # grade
    res = ml.set_index("game_id")
    for e in led:
        if e["status"] != "open" or e["game_id"] not in res.index:
            continue
        r = res.loc[e["game_id"]]
        if r["completed"] != True or pd.isna(r["home_pts"]):
            continue
        margin = r["home_pts"] - r["away_pts"]
        cl = seen.get(str(e["game_id"]), {})
        close_sp = cl.get("sp_home") if cl.get("sp_home") is not None else (r["close"] if pd.notna(r["close"]) else None)
        close_tot = cl.get("tot") if cl.get("tot") is not None else (r["tclose"] if pd.notna(r["tclose"]) else None)
        if e["market"] == "spread":
            s = 1 if e["side"] == "home" else -1
            v = s * margin + e["line"]
            e["clv"] = round(e["line"] - s * close_sp, 1) if close_sp is not None else None
            e["close"] = s * close_sp if close_sp is not None else None
        elif e["market"] == "total":
            pts = r["home_pts"] + r["away_pts"]
            v = (pts - e["line"]) if e["side"] == "over" else (e["line"] - pts)
            e["clv"] = round((close_tot - e["line"]) if e["side"] == "over" else (e["line"] - close_tot), 1) if close_tot is not None else None
            e["close"] = close_tot
        else:
            s = 1 if e["side"] == "home" else -1
            v = s * margin
        e["status"] = "W" if v > 0 else ("L" if v < 0 else "P")
        e["units"] = round(e["stake"] * payout(e["odds"]) if e["status"] == "W" else (-e["stake"] if e["status"] == "L" else 0.0), 3)
        e["final"] = f"{r['away']} {int(r['away_pts'])} - {r['home']} {int(r['home_pts'])}"
    resize(led)
    save_json(LEDGER, led)
    return led


def summary(led):
    out = {}
    for tier in ("STRONG", "FCS", "WEATHER", "LEAN", "RESEARCH"):
        d = [e for e in led if e["tier"] == tier and e["status"] in ("W", "L", "P")]
        w = sum(e["status"] == "W" for e in d)
        l = sum(e["status"] == "L" for e in d)
        p = sum(e["status"] == "P" for e in d)
        u = sum(e.get("units", 0) for e in d)
        risk = sum(e["stake"] for e in d if e["status"] != "P")
        clv = [e["clv"] for e in d if e.get("clv") is not None]
        out[tier] = dict(w=w, l=l, p=p, units=round(u, 2), roi=round(u / risk, 3) if risk else None,
                         clv_avg=round(float(np.mean(clv)), 2) if clv else None,
                         beat_close=round(float(np.mean([c > 0 for c in clv])), 3) if clv else None,
                         open=sum(e["tier"] == tier and e["status"] == "open" for e in led))
    return out


# ---------------------------------------------------------------- 6. state
def play_id(f):
    return f"{f['game_id']}|{f['market']}|{f['side']}|{'wx' if 'wind' in (f['pick'] or '') else 'm'}"


def write_state(games, fl, led, live_ok):
    # the ledger entry behind each play: the one logged at the play's tier if any (a lean can be upgraded), else the first
    lid = {}
    for e in led:
        if e["status"] != "void":
            lid.setdefault((e.get("base", e["id"]), e["tier"]), e["id"])
            lid.setdefault((e.get("base", e["id"]), None), e["id"])
    for f in fl:
        f["id"] = play_id(f)
        f["ledger_id"] = lid.get((f["id"], f["tier"])) or lid.get((f["id"], None))
        f["stake"] = stake_for(f) if f["tier"] != "ANGLE" else None
        f["provisional"] = bool(next((g.get("provisional") for g in games if g["game_id"] == f["game_id"]), False))
    wk = pd.Series([g["week"] for g in games]).mode()
    week = int(wk.iloc[0]) if len(wk) else None
    rt = pd.read_parquet(BOARD / "ratings.parquet") if (BOARD / "ratings.parquet").exists() else pd.DataFrame()
    def fmt_rt(cls):
        d = rt[rt["cls"] == cls].head(25)
        return [dict(rank=i + 1, team=r.team, net=round(r.net, 1), off=round(r.off, 1), dfn=round(r.dfn, 1)) for i, r in enumerate(d.itertuples())]
    gl = []
    for g in sorted(games, key=lambda x: x["kickoff"]):
        gl.append(dict(game_id=g["game_id"], kickoff=g["kickoff"].strftime("%a %b %d %I:%M %p ET"), grp=g["grp"],
                       away=g["away"], home=g["home"], neutral=bool(g["neutral"]),
                       model_line=None if pd.isna(g["model_line"]) else round(g["model_line"], 1),
                       model_total=None if pd.isna(g.get("model_total")) else round(g["model_total"], 1),
                       cons_line=g.get("cons_line"), cons_total=g.get("cons_total"),
                       tix_home=g.get("tix_home"), money_home=g.get("money_home"), books=g["books"], wx=g.get("wx"),
                       stale_books=g.get("stale_books")))
    st = dict(generated=now_et().strftime("%a %b %d %Y %I:%M %p ET"), generated_iso=now_et().isoformat(timespec="minutes"),
              season=SEASON, week=week, live_ok=live_ok, rules=RULES,
              backtest=BACKTEST, plays=[f for f in fl], games=gl, ledger=led[-400:], summary=summary(led),
              ratings_fbs=fmt_rt("fbs"), ratings_fcs=fmt_rt("fcs"))
    save_json(STATE, st)
    return st


def watchdog(t0, limit=900):
    """A run that spans laptop sleep can sit for hours after wake and block every later run (the task ignores new
    starts while one is running; Oct 11 23:35 run ended Oct 12 14:06). Exit once 15 min of wall-clock time pass."""
    def run():
        while True:
            time.sleep(20)
            if time.time() - t0 > limit:
                with (BOARD / "engine.log").open("a") as fh:
                    fh.write(f"{now_et():%Y-%m-%d %H:%M} ABORTED after {time.time() - t0:.0f}s (laptop likely slept mid-run)\n")
                os._exit(3)
    threading.Thread(target=run, daemon=True).start()


def main(force_refresh=False, dry=False):
    log = BOARD / "engine.log"
    t0 = time.time()
    watchdog(t0)
    try:
        try:
            refreshed = refresh(force_refresh)
        except Exception as e:
            if not (BOARD / "model_lines.parquet").exists():
                raise
            refreshed = False
            with log.open("a") as fh:
                fh.write(f"{now_et():%Y-%m-%d %H:%M} refresh failed, priced with the last good data: {str(e)[:160]}\n")
        ml = pd.read_parquet(BOARD / "model_lines.parquet")
        ml["start"] = pd.to_datetime(ml["start"], utc=True)
        live_ok = True
        try:
            live = parse_live(fetch_live(), ml, team_map(ml))
        except Exception as e:
            live_ok = False
            live = pd.DataFrame(columns=["game_id", "book", "status"])
            with log.open("a") as fh:
                fh.write(f"{now_et()} live fetch failed: {e}\n")
        sitp = ROOT / "data" / "sit.parquet"
        sit = {}
        if sitp.exists():
            s = pd.read_parquet(sitp)
            sit = {(r["game_id"], r["side"]): r for r in s.to_dict("records")}
        games = price(ml, live) if len(live) else []
        wxf = forecast_weather(games) if games else {}
        for g in games:
            g["wx"] = wxf.get(g["game_id"])
        fl = flags(games, sit)
        led = load_json(LEDGER, []) if dry else update_ledger(fl, games, ml, live)
        st = write_state(games, fl, led, live_ok)
        msg = (f"{now_et():%Y-%m-%d %H:%M} ok refreshed={refreshed} games={len(games)} plays="
               f"{sum(f['tier'] in ('STRONG', 'FCS') for f in fl)} ({time.time() - t0:.0f}s)")
    except Exception:
        msg = f"{now_et():%Y-%m-%d %H:%M} ERROR\n{traceback.format_exc()}"
    with log.open("a") as fh:
        fh.write(msg + "\n")
    print(msg)


if __name__ == "__main__":
    now = dt.datetime.now()
    # task fires every 30 min; outside Sun/Mon (when openers post) only the top-of-hour run does work
    if now.weekday() not in (6, 0) and now.minute >= 30 and "--force" not in sys.argv:
        sys.exit(0)
    main(force_refresh="--refresh" in sys.argv, dry="--dry" in sys.argv)
