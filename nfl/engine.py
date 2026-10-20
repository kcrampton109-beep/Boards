"""NFL board engine (runs every 30 minutes from Windows Task Scheduler; same design as the CFB board).

Each run: (1) refresh nflverse data at most once a day (or after games finish) and refit the walk-forward ratings;
(2) pull live DraftKings / FanDuel / BetMGM lines + consensus bet % and money % (Action Network), CBS open/current
lines, ESPN injuries, stadium forecasts; (3) price every upcoming game; (4) log new plays to the paper ledger at the
price available right now; (5) grade finished plays with closing-line value; (6) write board/state.json.

Rules come from the backtest (research/r1-r8):
  STRONG spread  model differs from your best book by >= 3 pts. Beat the OPENING line 58-60% (2008-21, SBR openers)
                 and 65% (2023-25 Monday openers); about 52-55% against the closing line -> bet early in the week.
  LEAN spread    2-3 pts, logged at 0.5 unit to learn from
  SIZING (2026-10-06, user switched to sized bets): strong 3-4 pt edge 1u, 4-6 pts 1.5u, 6+ pts 2u; lean 0.5u;
                 weather and teaser pairs 1u; tracked tiers (RLM, TREND, TD) 1u. Bands from 2008-25 win rate by edge
                 (57.5% / 61.5% / ~70%; leans ~54%).
  WEATHER under  forecast wind 18+ mph within 24 h of kickoff at an open-air stadium (since 2026-10-19; was 15+ up to 4
                 days out, and forecasts that far out run windy): unders 56-60% vs the CLOSING total in every era
  TEASER legs    6-pt teaser underdogs +1.5..+2.5 with a total of 49.5 or less: legs won 75-78% (73.9% needed at -120)
  RLM (tracked)  the user's rule: 57%+ of bets on one side and the CBS line moved 1+ pt from its open the other way.
                 Backtest 2018-25 at the closing line: 49% - shown and tracked, not a play.
  TREND (tracked) this-season situations at 80%+ over 6+ games (old board rule; 48.5% since 2019) - tracked
  TD value       anytime TD where the model's chance beats the best DK/FD price by 5+ points - research, 1 unit
"""
import datetime as dt, json, math, pathlib, sys, time, traceback
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
BOARD = ROOT / "board"
BOARD.mkdir(exist_ok=True)
DATA = ROOT / "data"
ET = ZoneInfo("America/New_York")
SEASON = 2026
LEDGER = BOARD / "ledger.json"
STATE = BOARD / "state.json"
SEEN = BOARD / "lines_seen.json"
CACHE = BOARD / "cache.json"
BOOKN = {"DK": "DraftKings", "FD": "FanDuel", "MGM": "BetMGM"}
RULES = dict(strong=3.0, lean=2.0, wind_under=18.0, wind_hours=24.0, teaser_lo=1.5, teaser_hi=2.5, teaser_total_max=49.5, rlm_tix=57.0,
             rlm_move=1.0, td_value=0.05, td_min_prob=0.15, resid_sd=13.3)
FEATS = ["mkt_margin", "pts_margin", "epa_margin", "qb_exp", "rest_diff"]
TFEATS = ["mkt_total", "pts_total", "epa_off_sum"]
BACKTEST = {
    "strong": "Model vs the OPENING line, gap 3+: 59.5% (2008-21, 622 bets, SBR openers) and 65% (2023-25, 358 bets, Monday openers); the closing line moved our way 1.2 pts on average",
    "close": "Against the CLOSING line the same picks were 52-56%: most of the edge is getting the early number",
    "weather": "Wind 15+ mph at an open-air stadium: unders hit 58% (2008-13), 56% (2014-19) and 56% (2020-25) vs the CLOSING total (435 games)",
    "teaser": "6-pt teaser underdogs +1.5 to +2.5, total 49.5 or less: legs won 75% (2008-13), 77% (2014-19), 78% (2020-25); a 2-team teaser at -120 needs 73.9% per leg",
    "rlm": "Reverse line movement (57%+ of bets one way, line moves 1+ pt the other way), graded at the closing line 2018-25: 150-158 (48.7%). No variant beat 52.4% in both periods",
    "trend": "This-season trends (6+ games at 80%+): 154-129 overall but 64-68 (48.5%) since 2019",
    "angles": "Situations that beat the closing line in both 2007-15 and 2016-25: playoff underdogs (56%), underdogs off a 20+ point loss (55%), fading dome teams outdoors below 35F (55%), fading teams 3+ net covers hot (53%)",
    "td": "TD model (red-zone and touch share x team implied TDs) on 2022-25: log loss 0.475 vs 0.484 for the old board's recipe; well calibrated",
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


def clean(o):
    """JSON-safe copy: NaN/inf -> None, numpy/pandas scalars -> Python, timestamps -> ISO strings."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.datetime, dt.date)):
        return o.isoformat()
    if o is pd.NaT or o is None:
        return None
    try:
        if pd.isna(o):
            return None
    except (TypeError, ValueError):
        pass
    return o


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def payout(odds):
    return 100 / -odds if odds < 0 else odds / 100


def fmt_et(ts):
    t = pd.Timestamp(ts).tz_convert(ET)
    return t.strftime("%a %b %-d, %-I:%M %p ET") if sys.platform != "win32" else t.strftime("%a %b %#d, %#I:%M %p ET")


# ---------------------------------------------------------------- 1. data + ratings
def refresh(force=False):
    """Daily: nflverse schedule/scores, this season's play-by-play and roster; rebuild tables and ratings."""
    import pull_data, build, model
    stamp = load_json(BOARD / "refresh.json", {})
    last = pd.Timestamp(stamp.get("ts", "2000-01-01"))
    age_h = (pd.Timestamp.now() - last).total_seconds() / 3600
    need = force or age_h > 20 or not (DATA / "preds.parquet").exists()
    if not need and age_h > 3 and stamp.get("pending_results"):
        need = True       # games finished since the last refresh: pull scores before pricing the next week
    if not need:
        return False
    pull_data.games(True)
    pull_data.get(f"{pull_data.NV}/pbp/play_by_play_{SEASON}.parquet", pull_data.RAW / f"pbp_{SEASON}.parquet", force=True)
    pull_data.get(f"{pull_data.NV}/rosters/roster_{SEASON}.parquet", pull_data.RAW / f"roster_{SEASON}.parquet", force=True)
    build.main("games")
    build.update_pbp([SEASON])
    model.run_all()
    fit_blend()
    g = pd.read_parquet(DATA / "games.parquet")
    played = g[(g["season"] == SEASON) & (g["start"] < pd.Timestamp.now(tz=ET) - pd.Timedelta(hours=4))]
    stamp.update(ts=pd.Timestamp.now().isoformat(), pending_results=int(played["home_pts"].isna().sum()))
    save_json(BOARD / "refresh.json", stamp)
    return True


def fit_blend():
    import evaluate as ev
    g = ev.frame()
    g["epa_off_sum"] = g["epa_home"] + g["epa_away"]
    tr = g[(g["season"] >= 2008) & (g["season"] < SEASON) & g["completed"]].dropna(subset=FEATS + ["margin"])
    X = np.column_stack([np.ones(len(tr))] + [tr[f] for f in FEATS])
    b, *_ = np.linalg.lstsq(X, tr["margin"].values, rcond=None)
    tt = g[(g["season"] >= 2008) & (g["season"] < SEASON) & g["completed"]].dropna(subset=TFEATS + ["points"])
    Xt = np.column_stack([np.ones(len(tt))] + [tt[f] for f in TFEATS])
    bt, *_ = np.linalg.lstsq(Xt, tt["points"].values, rcond=None)
    save_json(BOARD / "blend.json", dict(feats=FEATS, beta=b.tolist(), tfeats=TFEATS, tbeta=bt.tolist(), n=len(tr)))


# ---------------------------------------------------------------- 2. QBs + injuries
def qb_status(inj, roster):
    """Per team: expected starter (last starter unless ESPN lists him Out/Doubtful/IR), his value, the team's base
    value and a flag when the starter is questionable."""
    import model
    g = pd.read_parquet(DATA / "games.parquet")
    q = model.qb_layer(g)
    q = q.merge(g[["game_id", "start"]], on="game_id").sort_values("start")
    qb_all = pd.read_parquet(DATA / "qb.parquet").merge(g[["game_id", "start"]], on="game_id")
    tot = qb_all.groupby("qb_id").agg(db=("db", "sum"), epa=("qb_epa", "sum"), name=("name", "last"))
    tot["val"] = (tot["epa"] + (-0.08) * 250) / (tot["db"] + 250)
    out = {}
    names = roster.set_index("gsis_id")["full_name"].to_dict() if len(roster) else {}
    inj_status = {}
    if len(inj):
        for r in inj.itertuples():
            if r.pos == "QB" and r.gsis_id:
                inj_status[r.gsis_id] = r.status
    for team, tq in q[q["start"].dt.year >= SEASON - 1].groupby("team"):
        tq = tq.sort_values("start")
        last = tq.iloc[-1]
        base = tq["qb_val_act"].tail(8).mean()
        starter = last["qb_act"]
        status = inj_status.get(starter)
        note = None
        exp = starter
        if status in ("Out", "Doubtful", "Injured Reserve"):
            # next man up: the team's other rostered QB with the most dropbacks, else replacement level
            cands = roster[(roster["team"] == team) & (roster["position"] == "QB") & (roster["gsis_id"] != starter)
                           & roster["status"].isin(["ACT"])]["gsis_id"].tolist() if len(roster) else []
            cands = [c for c in cands if inj_status.get(c) not in ("Out", "Doubtful", "Injured Reserve")]
            cands.sort(key=lambda c: -(tot["db"].get(c, 0)))
            exp = cands[0] if cands else None
            note = f"{names.get(starter, last['qb_act_name'])} {status.lower()}"
        val = tot["val"].get(exp, -0.08) if exp else -0.08
        out[team] = dict(starter=names.get(starter, last["qb_act_name"]), starter_id=starter, expected=names.get(exp, tot["name"].get(exp, "backup")) if exp else "backup",
                         expected_id=exp, value=round(float(val), 3), base=round(float(base), 3),
                         adj=round(float((val - base) * 35), 2), questionable=status == "Questionable", note=note,
                         starter_status=status)
    return out


def injuries(roster):
    import live
    try:
        inj = live.espn_injuries()
    except Exception:
        return pd.DataFrame(columns=["team", "name", "pos", "status", "gsis_id"]), False
    if len(roster):
        m = roster.dropna(subset=["espn_id"]).copy()
        m["espn_id"] = m["espn_id"].astype(str).str.replace(r"\.0$", "", regex=True)
        inj = inj.merge(m[["espn_id", "gsis_id", "team"]], on="espn_id", how="left")
    else:
        inj["gsis_id"], inj["team"] = None, None
    return inj, True


# ---------------------------------------------------------------- 3. pricing
def model_lines(qbs):
    import evaluate as ev
    bl = load_json(BOARD / "blend.json", None)
    g = ev.frame()
    g["epa_off_sum"] = g["epa_home"] + g["epa_away"]
    cur = g[(g["season"] == SEASON)].copy()
    for side in ("home", "away"):
        cur[f"{side}_qb_live"] = cur[side].map(lambda t: (qbs.get(t) or {}).get("adj", 0.0))
    up = ~cur["completed"]
    cur.loc[up, "qb_exp"] = cur.loc[up, "home_qb_live"] - cur.loc[up, "away_qb_live"]
    X = np.column_stack([np.ones(len(cur))] + [cur[f].fillna(0) for f in bl["feats"]])
    cur["model_margin"] = X @ np.array(bl["beta"])
    Xt = np.column_stack([np.ones(len(cur))] + [cur[f].fillna(cur[f].mean()) for f in bl["tfeats"]])
    cur["model_total"] = Xt @ np.array(bl["tbeta"])
    cur["model_line"] = -cur["model_margin"]
    return cur


def best_prices(books):
    """Best DK/FD/MGM numbers for each side."""
    out = {}
    for side, key, better in (("home", "sp_home", max), ("away", "sp_away", max)):
        c = [(b[key], b.get(key + "_odds") or -110, k) for k, b in books.items() if k in ("68", "69", "75") and b.get(key) is not None]
        if c:
            v = better(x[0] for x in c)
            o, bk = max(((x[1], x[2]) for x in c if x[0] == v), key=lambda t: t[0])
            out[side] = dict(line=v, odds=o, book={"68": "DK", "69": "FD", "75": "MGM"}[bk])
    tc = [(b["total"], b.get("over_odds") or -110, b.get("under_odds") or -110, k) for k, b in books.items()
          if k in ("68", "69", "75") and b.get("total") is not None]
    if tc:
        lo = min(x[0] for x in tc)
        o, bk = max(((x[1], x[3]) for x in tc if x[0] == lo), key=lambda t: t[0])
        out["over"] = dict(line=lo, odds=o, book={"68": "DK", "69": "FD", "75": "MGM"}[bk])
        hi = max(x[0] for x in tc)
        o, bk = max(((x[2], x[3]) for x in tc if x[0] == hi), key=lambda t: t[0])
        out["under"] = dict(line=hi, odds=o, book={"68": "DK", "69": "FD", "75": "MGM"}[bk])
    return out


def stake_for(tier, edge):
    """Units for a play: bigger bets on bigger model edges (see SIZING in the module docstring)."""
    if tier == "LEAN":
        return 0.5
    if tier == "STRONG" and edge is not None:
        return 2.0 if edge >= 6 else 1.5 if edge >= 4 else 1.0
    return 1.0


def resize(ledger):
    """Apply stake_for to every strong/lean entry (idempotent; restates entries logged flat before the switch)."""
    for e in ledger:
        if e["tier"] not in ("STRONG", "LEAN"):
            continue
        st = stake_for(e["tier"], e.get("edge"))
        if e.get("stake") == st:
            continue
        e["stake"] = st
        if e["status"] in ("W", "L", "P"):
            e["units"] = round(st * payout(e["odds"]), 2) if e["status"] == "W" else (-st if e["status"] == "L" else 0.0)


def team_name(t):
    return t


# ---------------------------------------------------------------- 4. plays
def spread_play(gm, bp, qbs):
    ml = gm["model_line"]
    if "home" not in bp or "away" not in bp or ml is None or np.isnan(ml):
        return None
    e_home = bp["home"]["line"] - ml
    e_away = bp["away"]["line"] + ml
    side, e = ("home", e_home) if e_home >= e_away else ("away", e_away)
    if e < RULES["lean"]:
        return None
    tier = "STRONG" if e >= RULES["strong"] else "LEAN"
    team = gm[side]
    note = []
    for s in ("home", "away"):
        q = qbs.get(gm[s]) or {}
        if q.get("questionable"):
            note.append(f"{gm[s]} QB {q['starter']} questionable")
        if q.get("note"):
            note.append(f"{gm[s]} QB: {q['note']}, model uses {q['expected']}")
    if tier == "STRONG" and any("questionable" in n for n in note):
        tier = "LEAN"
        note.append("demoted: QB status uncertain")
    b = bp[side]
    limit = (ml + RULES[tier.lower()]) if side == "home" else (-ml + RULES[tier.lower()])
    return dict(tier=tier, market="spread", side=side, team=team, line=b["line"], odds=b["odds"], book=b["book"],
                edge=round(e, 1), cover=round(phi(e / RULES["resid_sd"]), 3), limit=round(limit * 2) / 2,
                pick=f"{team} {fmt_line(b['line'])}", note="; ".join(note) or None)


def fmt_line(v):
    if v is None:
        return "–"
    return "PK" if abs(v) < 0.25 else (f"+{v:g}" if v > 0 else f"{v:g}")


def rlm_flags(gm, cb):
    """The user's RLM: 57%+ of bets on one side and the CBS line moved 1+ pt from its open the other way."""
    out = []
    if not cb:
        return out
    t, mv = gm.get("tix_home"), None
    if cb.get("open_spread_home") is not None and cb.get("spread_home") is not None:
        mv = cb["spread_home"] - cb["open_spread_home"]          # >0: home got more points (moved toward away)
    if t is not None and mv is not None:
        if t >= RULES["rlm_tix"] and mv >= RULES["rlm_move"]:
            out.append(("spread", "away", f"{t:.0f}% of bets on {gm['home']}, line moved {fmt_line(cb['open_spread_home'])} → {fmt_line(cb['spread_home'])}"))
        elif 100 - t >= RULES["rlm_tix"] and -mv >= RULES["rlm_move"]:
            out.append(("spread", "home", f"{100 - t:.0f}% of bets on {gm['away']}, line moved {fmt_line(-cb['open_spread_home'])} → {fmt_line(-cb['spread_home'])} for {gm['away']}"))
    to, tm = gm.get("tix_over"), None
    if cb.get("open_total") is not None and cb.get("total") is not None:
        tm = cb["total"] - cb["open_total"]
    if to is not None and tm is not None:
        if to >= RULES["rlm_tix"] and -tm >= RULES["rlm_move"]:
            out.append(("total", "under", f"{to:.0f}% of bets on the over, total moved {cb['open_total']:g} → {cb['total']:g}"))
        elif 100 - to >= RULES["rlm_tix"] and tm >= RULES["rlm_move"]:
            out.append(("total", "over", f"{100 - to:.0f}% of bets on the under, total moved {cb['open_total']:g} → {cb['total']:g}"))
    return out


# ---------------------------------------------------------------- TD sheet
def td_table():
    """Each player's decayed share of his team's touches / red-zone chances / TDs, as of now."""
    rz = pd.read_parquet(DATA / "rz.parquet")
    g = pd.read_parquet(DATA / "games.parquet")[["game_id", "season", "start"]]
    rz = rz.merge(g, on="game_id")
    rz = rz[rz["season"] >= SEASON - 1].sort_values("start")
    OPP = ["rush", "rush20", "rush10", "rush5", "tgt", "tgt20", "tgt10", "tgt5", "td"]
    tt = rz.groupby(["game_id", "team"])[OPP].sum().add_prefix("team_").reset_index()
    rz = rz.merge(tt, on=["game_id", "team"])
    acc = {}
    cols = OPP + [f"team_{c}" for c in OPP]
    for r in rz.itertuples(index=False):
        k = r.player_id
        a = acc.get(k)
        vals = np.array([getattr(r, c) for c in cols], float)
        if a is None:
            a = dict(v=np.zeros(len(cols)), n=0.0, season=r.season)
        if r.season != a["season"]:
            a["v"], a["n"] = a["v"] * 0.35, a["n"] * 0.35
        a["v"] = a["v"] * 0.5 ** (1 / 6.0) + vals
        a["n"] = a["n"] * 0.5 ** (1 / 6.0) + 1
        a.update(season=r.season, team=r.team, name=r.name, last=r.start)
        acc[k] = a
    rows = []
    ix = {c: i for i, c in enumerate(cols)}
    for pid, a in acc.items():
        v = a["v"]
        rows.append(dict(player_id=pid, name=a["name"], team=a["team"], last=a["last"], n=a["n"],
                         sh_rush10=v[ix["rush10"]] / (v[ix["team_rush10"]] + 1), sh_tgt10=v[ix["tgt10"]] / (v[ix["team_tgt10"]] + 1),
                         sh_rush20=v[ix["rush20"]] / (v[ix["team_rush20"]] + 1), sh_tgt20=v[ix["tgt20"]] / (v[ix["team_tgt20"]] + 1),
                         sh_touch=(v[ix["rush"]] + v[ix["tgt"]]) / (v[ix["team_rush"]] + v[ix["team_tgt"]] + 1),
                         sh_td=(v[ix["td"]] + 0.5) / (v[ix["team_td"]] + 3), tds=v[ix["td"]]))
    return pd.DataFrame(rows)


def td_sheet(games, roster, inj, cache, now):
    import live
    beta = np.load(ROOT / "research" / "td_beta.npy")
    F = ["sh_rush10", "sh_tgt10", "sh_rush20", "sh_tgt20", "sh_touch", "sh_td"]
    tbl = td_table()
    ros = roster[roster["status"] == "ACT"].set_index("gsis_id") if len(roster) else pd.DataFrame()
    tbl = tbl[tbl["player_id"].isin(ros.index)] if len(ros) else tbl
    if len(ros):
        tbl["team"] = tbl["player_id"].map(ros["team"]).fillna(tbl["team"])
        tbl["full"] = tbl["player_id"].map(ros["full_name"]).fillna(tbl["name"])
        tbl["pos"] = tbl["player_id"].map(ros["position"])
    else:
        tbl["full"], tbl["pos"] = tbl["name"], None
    tbl = tbl[tbl["pos"].isin(["RB", "WR", "TE", "QB", "FB"]) & (tbl["n"] >= 1.5)]
    out_ids = set(inj.loc[inj["status"].isin(["Out", "Doubtful", "Injured Reserve"]), "gsis_id"].dropna()) if len(inj) else set()
    q_ids = set(inj.loc[inj["status"] == "Questionable", "gsis_id"].dropna()) if len(inj) else set()
    sheet, values = [], []
    props_cache = cache.setdefault("props", {})
    for gm in games:
        if gm["started"] or not gm.get("an_id") or gm["kickoff_ts"] > now + pd.Timedelta(days=7):
            continue
        key = str(gm["an_id"])
        pc = props_cache.get(key)
        if not pc or (now - pd.Timestamp(pc["ts"])).total_seconds() > 3 * 3600:
            try:
                pc = dict(ts=now.isoformat(), odds=live.an_anytime_td(gm["an_id"]))
                props_cache[key] = pc
            except Exception:
                pc = pc or dict(ts=now.isoformat(), odds={})
        odds = pc["odds"]
        teams = []
        for side in ("away", "home"):
            team, opp = gm[side], gm["home" if side == "away" else "away"]
            sp = gm.get("cons_spread")
            tot = gm.get("cons_total")
            if sp is None or tot is None:
                continue
            exp_margin = -sp if side == "home" else sp
            imp = (tot + exp_margin) / 2
            team_td = max(5, min(45, imp)) / 7.0 * 0.78
            tp = tbl[tbl["team"] == team].copy()
            if not len(tp):
                continue
            share = np.clip(tp[F].values @ beta[1:], 1e-4, None)
            # shares within a team must not imply more TDs than the team scores
            share = share / max(1.0, share.sum())
            tp["prob"] = 1 - np.exp(-team_td * share * math.exp(beta[0]))
            tp = tp[~tp["player_id"].isin(out_ids)].sort_values("prob", ascending=False)
            players = []
            for r in tp.head(6).itertuples():
                o = odds.get(__import__("live").norm_name(r.full), {})
                bo = max(((o[b], b) for b in ("DK", "FD", "MGM") if o.get(b) is not None), default=(None, None))
                imp_p = (100 / (bo[0] + 100) if bo[0] > 0 else -bo[0] / (-bo[0] + 100)) if bo[0] is not None else None
                val = imp_p is not None and r.prob >= RULES["td_min_prob"] and r.prob - imp_p >= RULES["td_value"]
                d = dict(player=r.full, pid=r.player_id, pos=r.pos, prob=round(float(r.prob), 3), odds=bo[0], book=bo[1],
                         book_prob=round(imp_p, 3) if imp_p else None, value=bool(val), q=r.player_id in q_ids,
                         dk=o.get("DK"), fd=o.get("FD"))
                players.append(d)
                if val and r.prob >= 0.2:
                    values.append(dict(d, game_id=gm["game_id"], team=team, matchup=gm["matchup"]))
            teams.append(dict(team=team, opp=opp, imp_pts=round(imp, 1), players=players))
        sheet.append(dict(game_id=gm["game_id"], matchup=gm["matchup"], kickoff=gm["kickoff"], teams=teams))
    return sheet, values


# ---------------------------------------------------------------- 5. ledger
def grade_entry(e, gm, final):
    """W/L/P for one ledger entry given final scores {home_pts, away_pts}."""
    hp, ap = final["home_pts"], final["away_pts"]
    if e["market"] in ("spread", "teaser"):
        m = (hp - ap) if e["side"] == "home" else (ap - hp)
        r = m + e["line"] + (6 if e["market"] == "teaser" else 0)
    elif e["market"] == "total":
        r = (hp + ap - e["line"]) * (1 if e["side"] == "over" else -1)
    else:
        return None
    return "W" if r > 0 else "L" if r < 0 else "P"


def grade(ledger, results, seen, td_results):
    for e in ledger:
        if e["status"] != "open":
            continue
        gid = e["game_id"]
        if e["market"] == "td":
            tds = td_results.get(gid)
            if tds is None:
                continue
            e["status"] = "W" if tds.get(e["pid"], 0) > 0 else "L"
            e["units"] = round(e["stake"] * payout(e["odds"]), 2) if e["status"] == "W" else -e["stake"]
            continue
        f = results.get(gid)
        if not f:
            continue
        res = grade_entry(e, None, f)
        if res is None:
            continue
        e["status"] = res
        e["final"] = f"{f['away']} {f['away_pts']} - {f['home']} {f['home_pts']}"
        if e["market"] == "teaser":
            e["units"] = None        # legs are paired into 2-team teasers in the summary
        else:
            e["units"] = round(e["stake"] * payout(e["odds"]), 2) if res == "W" else (-e["stake"] if res == "L" else 0.0)
        s = seen.get(gid) or {}
        if e["market"] in ("spread", "teaser") and s.get("close_spread") is not None:
            c = s["close_spread"] if e["side"] == "home" else -s["close_spread"]
            e["close"], e["clv"] = c, round(e["line"] - c, 1)
        elif e["market"] == "total" and s.get("close_total") is not None:
            e["close"] = s["close_total"]
            e["clv"] = round((e["line"] - s["close_total"]) * (1 if e["side"] == "under" else -1), 1)


def summarize(ledger):
    out = {}
    for tier in ("STRONG", "LEAN", "WEATHER", "TEASER", "RLM", "TREND", "TD"):
        es = [e for e in ledger if e["tier"] == tier and e["status"] != "void"]
        w = sum(e["status"] == "W" for e in es)
        l = sum(e["status"] == "L" for e in es)
        p = sum(e["status"] == "P" for e in es)
        units = sum(e.get("units") or 0 for e in es)
        risk = sum(e["stake"] for e in es if e["status"] in ("W", "L"))
        clv = [e["clv"] for e in es if e.get("clv") is not None]
        d = dict(w=w, l=l, p=p, open=sum(e["status"] == "open" for e in es), units=round(units, 2),
                 roi=round(units / risk, 3) if risk else None, clv_avg=round(float(np.mean(clv)), 2) if clv else None,
                 beat_close=round(float(np.mean([c > 0 for c in clv])), 3) if clv else None)
        if tier == "TEASER":
            # pair graded legs in logging order into 2-team 6-pt teasers at -120
            legs = [e for e in es if e["status"] in ("W", "L")]
            tw = tl = 0
            for i in range(0, len(legs) - 1, 2):
                if legs[i]["status"] == "W" and legs[i + 1]["status"] == "W":
                    tw += 1
                else:
                    tl += 1
            d.update(pairs_w=tw, pairs_l=tl, units=round(tw * (100 / 120) - tl, 2), leg_pct=round(w / (w + l), 3) if w + l else None)
        out[tier] = d
    return out


# ---------------------------------------------------------------- main
def main(args=()):
    import live, trends
    now = pd.Timestamp.now(tz=ET)
    log = []
    try:
        refresh(force="--refresh" in args)
    except Exception as e:
        log.append(f"refresh failed: {e!r}")
        traceback.print_exc()
    g = pd.read_parquet(DATA / "games.parquet")
    roster = pd.read_parquet(DATA / "raw" / f"roster_{SEASON}.parquet") if (DATA / "raw" / f"roster_{SEASON}.parquet").exists() else pd.DataFrame()
    if len(roster):
        roster = roster.copy()
        roster["team"] = roster["team"].replace({"LAR": "LA"})
    inj, inj_ok = injuries(roster)
    qbs = qb_status(inj, roster)
    cur = model_lines(qbs)
    # weeks on the board: the current week (any game not final) and the next one once its lines are up
    sched = cur.sort_values("start")
    open_weeks = sorted(sched.loc[sched["start"] > now - pd.Timedelta(hours=5), "week"].unique())[:2]
    if not open_weeks:
        open_weeks = [int(sched["week"].max())]
    live_ok = True
    try:
        an = live.an_games(SEASON, [int(w) for w in open_weeks] + ([int(open_weeks[0]) - 1] if open_weeks[0] > 1 else []))
    except Exception as e:
        an, live_ok = [], False
        log.append(f"Action Network failed: {e!r}")
    an_by = {(x["away"], x["home"], x["week"]): x for x in an}
    cbs = {}
    for w in open_weeks:
        try:
            cbs.update({(k[0], k[1], w): v for k, v in live.cbs_week(SEASON, int(w)).items()})
        except Exception as e:
            log.append(f"CBS week {w} failed: {e!r}")
    cache = load_json(CACHE, {})
    seen = load_json(SEEN, {})
    ledger = load_json(LEDGER, [])
    starts = dict(zip(cur["game_id"], cur["start"]))
    for e in ledger:   # entries logged before Oct 19 have only a display kickoff: add the exact one
        if not e.get("kickoff_iso") and e.get("game_id") in starts:
            e["kickoff_iso"] = pd.Timestamp(starts[e["game_id"]]).isoformat()
    have = {e["id"] for e in ledger if e.get("status") != "void"}   # a voided play can log again later

    # results for grading (Action Network finals; nflverse scores as a fallback)
    results = {}
    for x in an:
        if x["status"] == "complete" and x["home_pts"] is not None:
            r = cur[(cur["home"] == x["home"]) & (cur["away"] == x["away"]) & (cur["week"] == x["week"])]
            if len(r):
                results[r.iloc[0]["game_id"]] = dict(home=x["home"], away=x["away"], home_pts=x["home_pts"], away_pts=x["away_pts"])
    for r in cur[cur["completed"]].itertuples():
        results.setdefault(r.game_id, dict(home=r.home, away=r.away, home_pts=int(r.home_pts), away_pts=int(r.away_pts)))
    rz = pd.read_parquet(DATA / "rz.parquet")
    rz = rz[rz["game_id"].str.startswith(str(SEASON))]
    td_results = {gid: dict(zip(d["player_id"], d["td"])) for gid, d in rz.groupby("game_id")}

    # trend picks for the current week (old board rule)
    try:
        txt = (DATA / "raw" / "games.csv").read_text(encoding="utf-8")
        trows = trends.prepare(trends.load_games(txt))
        tflags = {}
        for w in open_weeks:
            for x in trends.week_flags(trows, SEASON, int(w)):
                if not x["conflict"]:
                    tflags.setdefault(x["game_id"], []).append(x)
    except Exception as e:
        tflags = {}
        log.append(f"trends failed: {e!r}")

    games, plays, rlm_list = [], [], []
    wk0 = sched[sched["week"] == int(open_weeks[0])]
    cur_started = float((wk0["start"] <= now).mean()) if len(wk0) else 1.0   # share of this week's games already started
    wx_cache = cache.setdefault("wx", {})
    for r in sched[sched["week"].isin(open_weeks)].itertuples():
        x = an_by.get((r.away, r.home, r.week)) or {}
        cb = cbs.get((r.away, r.home, r.week)) or cbs.get((r.home, r.away, r.week))
        started = r.start <= now or x.get("status") in ("inprogress", "complete")
        books = x.get("books", {})
        # A book's line counts only if it agrees with the others: the user's books (DK/FD/MGM) whose spread is 1.5+
        # points off their median are ignored for prices (two books 3+ apart -> neither). Oct 20: next week's DK and FD
        # look-ahead lines were up to 5 points apart and neither matched the user's apps.
        sp = {k: books[k]["sp_home"] for k in ("68", "69", "75") if (books.get(k) or {}).get("sp_home") is not None}
        med = float(np.median(list(sp.values()))) if sp else None
        agree = {k for k, v in sp.items() if abs(v - med) < 1.5}
        bp = best_prices({k: v for k, v in books.items() if k not in ("68", "69", "75") or k in agree})
        cons = books.get("15", {})
        # Look-ahead: books post next week's lines ~10 days early (DK right after Monday night, FD Tuesday afternoon) and
        # they move a lot, are often off the user's apps, and are not what the backtest bet (openers, Sunday night on).
        # Next week counts only once 3/4 of this week's games have started and 2+ of the user's books agree on it.
        # Shown on the board, never logged or alerted.
        lookahead = int(r.week) > int(open_weeks[0]) and (cur_started < 0.75 or len(agree) < 2)
        gm = dict(game_id=r.game_id, week=int(r.week), kickoff=fmt_et(r.start), kickoff_ts=r.start, kickoff_iso=r.start.isoformat(),
                  away=r.away, home=r.home,
                  matchup=f"{r.away} {'vs' if r.neutral else '@'} {r.home}", neutral=bool(r.neutral), roof=r.roof,
                  started=bool(started), final=results.get(r.game_id), an_id=x.get("an_id"),
                  model_line=None if pd.isna(r.model_line) else round(float(r.model_line), 1),
                  model_total=None if pd.isna(r.model_total) else round(float(r.model_total), 1),
                  cons_spread=cons.get("sp_home"), cons_total=cons.get("total"),
                  books={BOOKN_K: books.get(k, {}) for k, BOOKN_K in (("68", "DK"), ("69", "FD"), ("75", "MGM"))},
                  best=bp, tix_home=x.get("tix_home"), money_home=x.get("money_home"), tix_over=x.get("tix_over"),
                  money_over=x.get("money_over"), cbs=cb, qb={s: qbs.get(getattr(r, s)) for s in ("home", "away")},
                  rest=dict(home=r.home_rest, away=r.away_rest), lookahead=bool(lookahead))
        # remember the last pre-kickoff numbers (closing line for CLV)
        if not started and cons.get("sp_home") is not None:
            seen[r.game_id] = dict(ts=now.isoformat(), close_spread=cons.get("sp_home"), close_total=cons.get("total"),
                                   best=bp)
        # weather (open-air stadiums, within 4 days)
        wx = None
        if r.roof == "outdoors" and not r.neutral and r.home in live.STADIUM and not started and r.start - now < pd.Timedelta(days=4):
            c = wx_cache.get(r.game_id)
            if not c or (now - pd.Timestamp(c["ts"])).total_seconds() > 3 * 3600:
                try:
                    c = dict(ts=now.isoformat(), wx=live.forecast(*live.STADIUM[r.home], r.start.tz_convert("UTC")))
                    wx_cache[r.game_id] = c
                except Exception:
                    c = c or dict(ts=now.isoformat(), wx=None)
            wx = c.get("wx")
        elif r.game_id in wx_cache:
            wx = wx_cache[r.game_id].get("wx")
        gm["wx"] = wx
        # angles (context tags, not logged)
        angles = []
        tg = cur[(cur["season"] == SEASON) & cur["completed"] & (cur["start"] < r.start)]
        for side in ("home", "away"):
            team = getattr(r, side)
            prev = tg[(tg["home"] == team) | (tg["away"] == team)].sort_values("start")
            line_side = None
            if bp.get(side):
                line_side = bp[side]["line"]
            if len(prev):
                p = prev.iloc[-1]
                m = p["margin"] if p["home"] == team else -p["margin"]
                if m <= -20 and line_side is not None and line_side > 0:
                    angles.append(f"{team}: underdog off a {int(-m)}-point loss (bounce-back, 55% ATS)")
                covers = 0
                for q in prev.itertuples():
                    mm = q.margin if q.home == team else -q.margin
                    ll = q.close if q.home == team else -q.close
                    covers += int(np.sign(mm + ll)) if not pd.isna(ll) else 0
                if covers >= 3:
                    angles.append(f"Fade {team}: {covers} net covers this season (hot teams 47% ATS)")
            if side == "away" and live.STADIUM and wx and wx.get("temp") is not None and wx["temp"] < 35:
                dome = cur[(cur["home"] == team) & (cur["season"] == SEASON)]["roof"].isin(["dome", "closed"]).any()
                if dome:
                    angles.append(f"Fade {team}: dome team outdoors below 35F (45% ATS)")
        if r.game_type != "REG" and bp.get("home") and bp.get("away"):
            dog = "home" if bp["home"]["line"] > 0 else "away"
            angles.append(f"{getattr(r, dog)}: playoff underdog (56% ATS)")
        gm["angles"] = angles
        gm["trends"] = [dict(bet=t["bet"], side=t["side"], reasons=t["reasons"]) for t in tflags.get(r.game_id, [])]
        games.append(gm)
        if started:
            continue
        # ---- plays
        sp = spread_play(gm, bp, qbs)
        if sp:
            plays.append(dict(sp, game_id=r.game_id))
        if (wx and wx.get("wind") is not None and wx["wind"] >= RULES["wind_under"] and bp.get("under")
                and r.start - now <= pd.Timedelta(hours=RULES["wind_hours"])):
            u = bp["under"]
            plays.append(dict(tier="WEATHER", market="total", side="under", team=None, line=u["line"], odds=u["odds"],
                              book=u["book"], edge=wx["wind"], pick=f"Under {u['line']:g} ({wx['wind']:.0f} mph wind)",
                              game_id=r.game_id, note=f"gusts {wx['gust']:.0f} mph, {wx['temp']:.0f}F"))
        tot = cons.get("total")
        for side in ("home", "away"):
            b = bp.get(side)
            if b and RULES["teaser_lo"] <= b["line"] <= RULES["teaser_hi"] and tot is not None and tot <= RULES["teaser_total_max"]:
                plays.append(dict(tier="TEASER", market="teaser", side=side, team=getattr(r, side), line=b["line"], odds=-120,
                                  book=b["book"], edge=None, pick=f"{getattr(r, side)} {fmt_line(b['line'])} → {fmt_line(b['line'] + 6)}",
                                  game_id=r.game_id, note=f"total {tot:g}"))
        for market, side, why in rlm_flags(gm, cb):
            b = bp.get(side)
            if not b:
                continue
            team = getattr(r, side) if market == "spread" else None
            pick = f"{team} {fmt_line(b['line'])}" if market == "spread" else f"{side.title()} {b['line']:g}"
            item = dict(tier="RLM", market=market, side=side, team=team, line=b["line"], odds=b["odds"], book=b["book"],
                        edge=None, pick=pick, game_id=r.game_id, note=why)
            plays.append(item)
            rlm_list.append(dict(item, matchup=gm["matchup"], kickoff=gm["kickoff"]))
        for t in gm["trends"]:
            if t["bet"] == "spread":
                side = "home" if trends.FIX.get(r.home, r.home) == t["side"] or r.home == t["side"] else "away"
                b = bp.get(side)
                if b:
                    plays.append(dict(tier="TREND", market="spread", side=side, team=getattr(r, side), line=b["line"], odds=b["odds"],
                                      book=b["book"], edge=None, pick=f"{getattr(r, side)} {fmt_line(b['line'])}", game_id=r.game_id,
                                      note=t["reasons"][0]))
            else:
                side = t["side"].lower()
                b = bp.get(side)
                if b:
                    plays.append(dict(tier="TREND", market="total", side=side, team=None, line=b["line"], odds=b["odds"],
                                      book=b["book"], edge=None, pick=f"{t['side']} {b['line']:g}", game_id=r.game_id, note=t["reasons"][0]))

    # TD sheet
    try:
        sheet, td_vals = td_sheet(games, roster, inj, cache, now)
    except Exception as e:
        sheet, td_vals = [], []
        log.append(f"TD sheet failed: {e!r}")
        traceback.print_exc()
    for v in td_vals:
        plays.append(dict(tier="TD", market="td", side=None, team=v["team"], line=None, odds=v["odds"], book=v["book"],
                          edge=round(v["prob"] - v["book_prob"], 3), pick=f"{v['player']} anytime TD", game_id=v["game_id"],
                          pid=v["pid"], note=f"model {v['prob']:.0%} vs {v['book']} {v['book_prob']:.0%}"))

    # log new plays (once, at the price available now); look-ahead games are not bettable yet
    by_game = {gm["game_id"]: gm for gm in games}
    for p in plays:
        p["lookahead"] = bool(by_game.get(p["game_id"], {}).get("lookahead"))
    for e in ledger:   # entries logged at a look-ahead line before this rule (Oct 13): void, they log again at the openers
        if e.get("status") == "open" and by_game.get(e["game_id"], {}).get("lookahead"):
            e["status"] = "void"
            e["note"] = ("Voided: logged at a look-ahead line (next week's line, posted before this week was played, "
                         "or books that disagreed); it logs again once the line is real")
    new = []
    for p in plays:
        pid = f"{p['game_id']}:{p['tier']}:{p['market']}:{p.get('side') or p.get('pid')}"
        p["id"] = pid
        p["stake"] = stake_for(p["tier"], p.get("edge"))
        if pid in have or p["lookahead"]:
            continue
        gm = by_game[p["game_id"]]
        e = dict(id=pid, tier=p["tier"], market=p["market"], side=p.get("side"), team=p.get("team"), pid=p.get("pid"),
                 game_id=p["game_id"], week=gm["week"], matchup=gm["matchup"], kickoff=gm["kickoff"], pick=p["pick"],
                 line=p["line"], odds=p["odds"], book=p["book"], stake=p["stake"], edge=p.get("edge"), note=p.get("note"), limit=p.get("limit"),
                 model_line=gm["model_line"], flagged=now.isoformat(), kickoff_iso=gm["kickoff_iso"], status="open", units=None, close=None, clv=None, final=None)
        ledger.append(e)
        have.add(pid)
        new.append(e)
    resize(ledger)
    grade(ledger, results, seen, td_results)
    summary = summarize(ledger)

    # ratings table
    bl = load_json(BOARD / "blend.json", {"beta": [0] * 6})
    lastrow = {}
    for r in cur.sort_values("start").itertuples():
        for side in ("home", "away"):
            lastrow[getattr(r, side)] = (r, side)
    ratings = []
    b = bl["beta"]
    for team, (r, side) in lastrow.items():
        mk = getattr(r, f"mkt_r_{side}")
        pn = getattr(r, f"pts_net_{side}")
        en = getattr(r, f"epa_net_{side}")
        q = qbs.get(team) or {}
        net = b[1] * mk + b[2] * pn + b[3] * en
        ratings.append(dict(team=team, net=round(float(net), 1), mkt=round(float(mk), 1), pts=round(float(pn), 1),
                            epa=round(float(en), 3), qb=q.get("expected"), qb_val=q.get("value")))
    ratings.sort(key=lambda d: -d["net"])
    for i, d in enumerate(ratings):
        d["rank"] = i + 1

    inj_rows = []
    if len(inj):
        playing = {gm["home"] for gm in games} | {gm["away"] for gm in games}
        ii = inj[inj["team"].isin(playing) & inj["status"].isin(["Out", "Doubtful", "Questionable", "Injured Reserve"])]
        for x in ii.itertuples():
            if x.status == "Out" and isinstance(x.detail, str) and "Coach" in x.detail:
                continue
            inj_rows.append(dict(team=x.team, player=x.name, pos=x.pos, status=x.status, detail=x.detail or "",
                                 comment=(x.comment or "")[:240], date=x.date))
    week_now = int(open_weeks[0])
    state = dict(generated=fmt_et(now), generated_iso=now.isoformat(), season=SEASON, week=week_now, weeks=[int(w) for w in open_weeks],
                 live_ok=live_ok, injuries_ok=inj_ok, log=log, rules=RULES, backtest=BACKTEST, summary=summary,
                 games=[{k: v for k, v in gm.items() if k not in ("kickoff_ts",)} for gm in games],
                 plays=[dict(p, matchup=by_game[p["game_id"]]["matchup"], kickoff=by_game[p["game_id"]]["kickoff"],
                             week=by_game[p["game_id"]]["week"]) for p in plays],
                 ledger=ledger, td=sheet, injuries=inj_rows, ratings=ratings, rlm=rlm_list,
                 qbs={t: q for t, q in qbs.items()}, new_ids=[e["id"] for e in new])
    save_json(STATE, clean(state))
    # final scores + closing lines (board/scores.json): the My Bets tab grades the user's own bets at their own line
    save_json(BOARD / "scores.json", clean({str(gid): dict(f, close_spread=(seen.get(gid) or {}).get("close_spread"),
                                                          close_total=(seen.get(gid) or {}).get("close_total"))
                                            for gid, f in results.items()}))
    save_json(LEDGER, clean(ledger))
    save_json(SEEN, clean(seen))
    save_json(CACHE, clean(cache))
    with (BOARD / "engine.log").open("a") as f:
        f.write(f"{now.isoformat()} plays={len(plays)} new={len(new)} games={len(games)} live_ok={live_ok} {'; '.join(log)}\n")
    return state


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        with (BOARD / "engine.log").open("a") as f:
            f.write(f"{dt.datetime.now().isoformat()} CRASH {traceback.format_exc()}\n")
        raise
