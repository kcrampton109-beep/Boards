"""CBB board engine (paper only).

Each run: (1) fetch Action Network + ESPN scoreboards from yesterday to 3 days ahead (plus any day of this season not
saved yet); (2) refit both rating models on this season's finished games, pulled toward last season; (3) price every
upcoming D1-vs-D1 game against DraftKings / FanDuel / BetMGM with the BLEND (average of the results model and the
market-ratings model, research/REPORT.md); (4) log new plays to the paper ledger at the best price available right now;
(5) record the first number each book shows for every game: is Action's opener (what the backtest used) bettable at
our books?; (6) grade finished plays with closing-line value; (7) write board/state.json.

Backtest, Dec-Mar 2023-24 to 2025-26, against Action's opener (research/REPORT.md):
  spreads, blend gap 3+: CLV +1.32, beat the close 68%, 55.7% ATS   (gap 4+: +1.87, 74%, 59.8%)
  totals,  blend gap 5+: CLV +2.29, beat the close 77%, 56.2% ATS   (gap 6+: +2.90, 82%, 59.1%)
  November is weak for every model (CLV about +0.5), so November plays are their own tier.
Paper only: no money tiers, no phone alerts, flat 1u stakes. Money tiers are the owner's call once live CLV is in.

Usage: python engine.py [--asof "YYYY-MM-DD HH:MM"]   --asof replays a past date from the saved history (no fetching;
the "live" prices are then closing lines, so it only tests the plumbing). Set CBB_BOARD to write somewhere else.
"""
import datetime as dt, json, math, os, pathlib, re, sys, time, traceback
from zoneinfo import ZoneInfo
import difflib
import numpy as np
import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "research"))
import build, model, fetch as fetchmod  # noqa: E402  (research code: parsers, ridge, prior, URLs)

DATA = ROOT / "data"
SEASON_DIR = DATA / "season"
BOARD = pathlib.Path(os.environ.get("CBB_BOARD", ROOT / "board"))
ET = ZoneInfo("America/New_York")
BOOKS = {"dk": "DK", "fd": "FD", "mgm": "MGM"}
MODELS = {"pts": (4.0, 60.0), "market": (1.0, 30.0)}   # prior weight, half-life in days (research/tune.py)
RULES = dict(spread_lean=3.0, spread=4.0, total_lean=5.0, total=6.0,
             max_spread=12.0, max_total=20.0,   # bigger gaps are usually news the model can't see (injury, suspension)
             worst_odds=-125, stale_spread=3.0, stale_total=5.0, main_from="12-01", ahead_days=3, stake=1.0,
             # Nov 4 2026 (owner's call, research/EDGE.md): the ratings are shrunk, so margins are scaled up by k (actual
             # margin / model margin over earlier seasons; hist.json margin_scale, this value if missing), and totals are
             # measured against the market's level: minus the average (model - opening total) over the last 7 days + today
             margin_scale=1.063, level_days=7,
             # RLM research (Nov 8 2026, owner's request, research/rlm.py): tickets on one side, line moved the other way
             rlm_tickets=65, rlm_strict=80, rlm_move=1.0, rlm_min_bets=250)
TIERS = {
    "SPREAD": "Spread 4+", "SPREAD_LEAN": "Spread 3-4", "TOTAL": "Total 6+", "TOTAL_LEAN": "Total 5-6",
    "NOV_FAV": "Nov favourites 4+", "NOV_OVER": "Nov overs 5+", "NOV": "November research",
    "RLM": "RLM spreads", "RLM_80": "RLM spreads 80%+", "RLM_T": "RLM totals",
}
TIER_RANK = {"NOV": 1, "SPREAD_LEAN": 1, "TOTAL_LEAN": 1, "SPREAD": 2, "TOTAL": 2, "NOV_FAV": 2, "NOV_OVER": 2, "RLM": 1, "RLM_T": 1, "RLM_80": 2}
P6 = {"2", "4", "7", "8", "23"}   # ESPN conference ids: ACC, Big East, Big Ten, Big 12, SEC (the Pac-12 left in 2024)
D1_CONF = {str(k) for k in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27,
                            29, 30, 44, 45, 46, 49, 62)}
BACKTEST = {   # corrected blend, against the opener at -110, 2023-24 / 2024-25 / 2025-26 (research/EDGE.md)
    "spread": "Spreads, December-March: gap 3+ CLV +1.40, beat the close 71%, 57.6% ATS, +9.8% ROI (+11.6 / +7.2 / +10.4 by season, about 3 a day); gap 4+ CLV +2.03, 82%, 62.5%, +19.3% ROI (+22.9 / +11.1 / +22.1, about 1 a day)",
    "total": "Totals, December-March: gap 5+ CLV +2.42, beat the close 76%, 56.3% ATS, +7.5% ROI (+3.6 / +7.1 / +11.2, about 2 a day); gap 6+ +9.9% ROI",
    "nov": "November: favourites at a spread gap of 4+ (+7.7% ROI; CLV +1.32, but only +0.68 beyond the normal November drift toward favourites) and overs at a total gap of 5+ (+8.2% ROI, CLV +1.82) were the only cuts that made money, and both were chosen after seeing those same seasons; underdogs beat the close 45% of the time and unders lost every season",
    "rlm": "Reverse line movement (65%+ of tickets on one side, line moved 1+ the other way), graded at the closing number: 2023-25 51.4% and 2025-26 48.8% on spreads (needs 52.4%), totals about 50%. It can only pay at the number when the signal first fires, which history can't show, so the RLM tiers log that number live",
    "caveat": "ROI assumes -110 at the opener. DraftKings' first number equals Action's opener 89% of the time (Openers tab); FanDuel and BetMGM 40-48%, so bet the DK number when you can.",
}
LEDGER, STATE = BOARD / "ledger.json", BOARD / "state.json"
SEEN_OPEN, SEEN_DONE = BOARD / "seen_pending.json", BOARD / "first_seen.jsonl"
AN2 = "https://api.actionnetwork.com/web/v2/scoreboard/ncaab"   # the v1 feed stopped carrying ticket / money shares in 2026


def now_et():
    return NOW or dt.datetime.now(ET)


NOW = None      # set by --asof
ASOF = False


def load_json(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _clean(o):
    """JSON has no NaN (a NaN in state.json blanks the page): missing numbers become null, numpy -> python."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if o != o else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def save_json(p, obj, indent=1):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(_clean(obj), indent=indent, default=str))
    tmp.replace(p)


def season_of(d):
    return d.year if d.month >= 7 else d.year - 1


def week_of(day, season):
    """Season week (Monday-Sunday) for the Results tab; week 1 is the week of the first Tuesday of November."""
    anchor = dt.date(season, 11, 3)
    anchor -= dt.timedelta(days=anchor.weekday())
    return (day - anchor).days // 7 + 1


def payout(odds):
    return odds / 100 if odds > 0 else 100 / -odds


# ---------------------------------------------------------------- 1. data
def get(src, d):
    url, params = fetchmod.SRC[src]
    err = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params(d), headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
            r.raise_for_status()
            return r.content
        except Exception as e:
            err = e
            time.sleep(5 * (attempt + 1))
    raise err


def day_final(src, d):
    """True once every game in a saved day file is over (or postponed / cancelled): the file then holds final scores
    and closing lines and is never fetched again."""
    import gzip
    f = SEASON_DIR / src / f"{d:%Y%m%d}.json.gz"
    try:
        x = json.loads(gzip.decompress(f.read_bytes()))
    except Exception:
        return False
    if src == "espn":
        st = [ev["competitions"][0]["status"]["type"] for ev in x.get("events", [])]
        return all(t.get("completed") or t.get("state") == "post" or t.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED")
                   for t in st)
    return all(g.get("status") in ("complete", "closed", "cancelled", "postponed") for g in x.get("games", []))


def refresh_files(season, today):
    """Yesterday .. today+ahead are re-fetched every run (lines move, games finish). Older days of this season are
    fetched until their file shows every game final (a day saved before its games were played would otherwise keep
    pre-game data forever, e.g. after a day with no runs)."""
    import gzip
    window = [today + dt.timedelta(days=k) for k in range(-1, RULES["ahead_days"] + 1)]
    first = dt.date(season, 11, 1)
    catch_up = [first + dt.timedelta(days=k) for k in range((today - first).days - 1)] if today > first else []
    final_p = SEASON_DIR / "final_days.json"
    final = set(load_json(final_p, []))
    ok, n, complete = True, 0, True
    for src in ("an", "espn"):
        out = SEASON_DIR / src
        out.mkdir(parents=True, exist_ok=True)
        stale = [d for d in catch_up if f"{src}:{d:%Y%m%d}" not in final]
        complete &= len(stale) <= 60
        todo = stale[:60] + window
        for d in todo:
            if season_of(d) != season:
                continue
            try:
                (out / f"{d:%Y%m%d}.json.gz").write_bytes(gzip.compress(get(src, f"{d:%Y%m%d}")))
                n += 1
            except Exception as e:
                ok = False
                log(f"fetch {src} {d} failed: {str(e)[:120]}")
            time.sleep(0.4)
        for d in stale[:60]:
            if day_final(src, d):
                final.add(f"{src}:{d:%Y%m%d}")
    final_p.write_text(json.dumps(sorted(final)))
    return ok, n, complete


def season_files(src, season):
    base = (DATA / f"{src}_raw") if ASOF else (SEASON_DIR / src)
    lo, hi = f"{season}0701", f"{season + 1}0630"
    return sorted(str(p) for p in base.glob("*.json.gz") if lo <= p.name[:8] <= hi)


def join_lines(e, a, tmap):
    """Place each Action game (any status) on its ESPN game: same Eastern date and the same two teams through the
    Action->ESPN team map; a team the map doesn't know is matched by name among that day's games."""
    if not len(e) or not len(a):
        return pd.DataFrame()
    e = e.copy()
    a = a.copy()
    e["d"] = e["start"].dt.tz_convert(ET).dt.date
    a["d"] = a["an_start"].dt.tz_convert(ET).dt.date
    key = {}
    for c in e.itertuples():
        for dd in (0, -1, 1):
            key.setdefault((c.d + dt.timedelta(days=dd), frozenset((c.home_eid, c.away_eid))), (c.espn_id, c.home_eid))
    by_day = {d: x for d, x in e.groupby("d")}
    sim = lambda x, y: difflib.SequenceMatcher(None, build._norm(x), build._norm(y)).ratio()
    out = []
    for r in a.itertuples():
        h, w = tmap.get(r.an_home_id), tmap.get(r.an_away_id)
        hit = key.get((r.d, frozenset((h, w)))) if h and w else None
        if hit:
            out.append((r.an_id, hit[0], h == hit[1]))
            continue
        best = None
        for c in by_day.get(r.d, pd.DataFrame()).itertuples():
            for same, (eh, ew, nh, nw) in ((True, (c.home_eid, c.away_eid, c.home_name, c.away_name)),
                                           (False, (c.away_eid, c.home_eid, c.away_name, c.home_name))):
                sh = 1.0 if h == eh else (0.0 if h else sim(r.an_home, nh))
                sw = 1.0 if w == ew else (0.0 if w else sim(r.an_away, nw))
                if min(sh, sw) >= 0.75 and (best is None or sh + sw > best[0]):
                    best = (sh + sw, c.espn_id, same)
        if best:
            out.append((r.an_id, best[1], best[2]))
    m = pd.DataFrame(out, columns=["an_id", "espn_id", "same_orient"]).drop_duplicates("espn_id")
    j = build.to_espn_home(a.merge(m, on="an_id"))
    return j.drop(columns=["d", "an_home", "an_away", "an_home_pts", "an_away_pts", "an_home_id", "an_away_id"])


def load_season(season, now):
    e = build.load_espn(season_files("espn", season), completed_only=False)
    a = build.load_action(season_files("an", season))
    if not len(e):
        return pd.DataFrame(), pd.DataFrame()
    tmap = {int(k): v for k, v in load_json(DATA / "an_team_map.json", {}).items()}
    j = join_lines(e, a, tmap)
    g = e.merge(j, on="espn_id", how="left") if len(j) else e
    g["site"] = np.where(g["neutral"], 0.0, 1.0)
    g["day"] = g["start"].dt.tz_convert(ET).dt.tz_localize(None).dt.normalize()
    if ASOF:  # replay: games after the as-of time are "upcoming" (their saved lines are closes)
        up = g["start"] > pd.Timestamp(now)
        g.loc[up, ["completed", "home_pts", "away_pts"]] = [False, np.nan, np.nan]
        g.loc[up, "state"] = "pre"
    # D1 teams this season: a D1 conference now, or D1 last season
    hist = pd.read_parquet(DATA / "teams.parquet")
    prev = set(hist.loc[(hist["season"] == season - 1) & hist["d1"], "eid"])
    tm = pd.concat([g[["home_eid", "home_name", "home_conf_eid"]].set_axis(["eid", "name", "conf_eid"], axis=1),
                    g[["away_eid", "away_name", "away_conf_eid"]].set_axis(["eid", "name", "conf_eid"], axis=1)]
                   ).drop_duplicates("eid", keep="last")
    tm["conf_eid"] = tm["conf_eid"].astype(object).where(tm["conf_eid"].notna(), None)
    tm = tm[tm["conf_eid"].isin(D1_CONF) | tm["eid"].isin(prev)].reset_index(drop=True)
    d1 = set(tm["eid"])
    g["d1"] = g["home_eid"].isin(d1) & g["away_eid"].isin(d1)
    if "dk_spread" in g or "cons_spread" in g:
        for c in [f"{b}_{x}" for b in ("dk", "fd", "mgm", "cons") for x in ("spread", "spread_away", "total")]:
            if c not in g:
                g[c] = np.nan
        g["close"], g["tclose"] = model.closing(g)
    else:
        g["close"] = g["tclose"] = np.nan
    g["mkt_home"] = (g["tclose"] - g["close"]) / 2
    g["mkt_away"] = (g["tclose"] + g["close"]) / 2
    return g, tm


# ---------------------------------------------------------------- 2. ratings
def ratings_now(target, g, tm, season, today):
    """This season's ratings as of `today`: every finished D1 game before today, older ones decayed, pulled toward
    the preseason prior (last season's final ratings + this season's conference), as in research/model.py."""
    lam, hl = MODELS[target]
    fin = pd.read_parquet(DATA / f"finals_{target}.parquet")
    finals = {int(s): f.drop(columns="season") for s, f in fin.groupby("season") if s < season}
    hist = pd.read_parquet(DATA / "teams.parquet")
    hist = hist[hist["d1"]]
    idx = {t: i for i, t in enumerate(tm["eid"])}
    n = len(tm)
    pf = model.prior_frame(finals, season, tm)
    coefs = model.fit_prior(finals, hist, sorted(finals))
    pO, pD = model.prior_X(pf, "O") @ coefs["O"], model.prior_X(pf, "D") @ coefs["D"]
    mu0, h0 = load_json(DATA / "hist.json", {})["mu_h"][target][str(season - 1)]
    hc, ac = model.TARGET[target]
    done = g[g["completed"].fillna(False).astype(bool) & g["d1"] & (g["day"] < pd.Timestamp(today))
             & g[hc].notna() & g[ac].notna()]
    if target == "pts":
        done = done[(done["home_pts"] > 20) & (done["away_pts"] > 20)]
    R = model.Ridge(n)
    if len(done):
        off, dfn, site, y, _ = model.obs(done, idx, target)
        wd = 0.5 ** ((pd.Timestamp(today) - done["day"]).dt.days.values / hl)
        R.add(off, dfn, site, y, np.r_[wd, wd])
    mu, h, O, D = R.solve(pO, pD, lam, mu0, h0)
    ng = pd.concat([done["home_eid"], done["away_eid"]]).value_counts()
    return dict(idx=idx, mu=mu, h=h, O=O, D=D, n_games=ng, n_done=len(done))


def predict(rt, home, away, site):
    i, j = rt["idx"].get(home), rt["idx"].get(away)
    if i is None or j is None:
        return None
    ph = rt["mu"] + rt["h"] * site + rt["O"][i] - rt["D"][j]
    pa = rt["mu"] - rt["h"] * site + rt["O"][j] - rt["D"][i]
    return ph - pa, ph + pa


# ---------------------------------------------------------------- 3. prices + plays
def num(v):
    try:
        v = float(v)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def book_lines(r):
    """DK / FD / MGM spread (home view), odds and total for one game; a book whose spread sides don't mirror, or that
    sits far off the other books, is dropped for that market (CFB Oct 25 lesson)."""
    out = {}
    for b, name in BOOKS.items():
        sh, sa = num(getattr(r, f"{b}_spread", None)), num(getattr(r, f"{b}_spread_away", None))
        if sh is not None and sa is not None and abs(sh + sa) > 0.6:
            sh = sa = None
        if sh is None and sa is not None:
            sh = -sa
        out[name] = dict(sp_home=sh, sp_home_odds=num(getattr(r, f"{b}_sp_odds_home", None)),
                         sp_away_odds=num(getattr(r, f"{b}_sp_odds_away", None)), tot=num(getattr(r, f"{b}_total", None)),
                         over_odds=num(getattr(r, f"{b}_over_odds", None)), under_odds=num(getattr(r, f"{b}_under_odds", None)))
    stale = []
    for key, lim in (("sp_home", RULES["stale_spread"]), ("tot", RULES["stale_total"])):
        vals = [v[key] for v in out.values() if v[key] is not None]
        cons = num(getattr(r, "cons_spread" if key == "sp_home" else "cons_total", None))
        ref = cons if cons is not None else (float(np.median(vals)) if len(vals) >= 2 else None)
        for k, v in out.items():
            if ref is not None and v[key] is not None and abs(v[key] - ref) >= lim:
                stale.append(f"{k} {'spread' if key == 'sp_home' else 'total'} {v[key]:g}")
                v[key] = None
    return {k: v for k, v in out.items() if v["sp_home"] is not None or v["tot"] is not None}, stale


def best(books, side):
    """Best line for a side across the books (then the best odds): spreads by the side's own number, totals by the
    lowest over / highest under."""
    c = []
    for k, v in books.items():
        if side in ("home", "away") and v["sp_home"] is not None:
            line = v["sp_home"] if side == "home" else -v["sp_home"]
            odds = v["sp_home_odds" if side == "home" else "sp_away_odds"] or -110
            c.append((line, odds, k))
        if side in ("over", "under") and v["tot"] is not None:
            odds = v[f"{side}_odds"] or -110
            c.append((-v["tot"] if side == "over" else v["tot"], odds, k))
    c = [x for x in c if x[1] >= RULES["worst_odds"]]
    if not c:
        return None
    line, odds, k = max(c)
    return (-line if side == "over" else line), odds, k


def tier_for(market, side, gap, day, line):
    """(tier, gap the tier needs) or None. December on: spreads 3-4 / 4+, totals 5-6 / 6+. November (research/EDGE.md):
    favourites at a spread gap of 4+ and overs at a total gap of 5+; every other gap of 3+ / 5+ is logged as research."""
    lean, main = (RULES["spread_lean"], RULES["spread"]) if market == "spread" else (RULES["total_lean"], RULES["total"])
    if gap < lean:
        return None
    if day.strftime("%m-%d") < RULES["main_from"] and day.month >= 7:   # November (and anything before December)
        if market == "spread" and line < 0 and gap >= RULES["spread"]:
            return "NOV_FAV", RULES["spread"]
        if market == "total" and side == "over":
            return "NOV_OVER", RULES["total_lean"]
        return "NOV", lean
    if market == "spread":
        return ("SPREAD", main) if gap >= main else ("SPREAD_LEAN", lean)
    return ("TOTAL", main) if gap >= main else ("TOTAL_LEAN", lean)


def total_level(raw, now):
    """Sum and count of (model total - opening total) over the games listed now and those that tipped in the last
    RULES['level_days'] days (first-seen records keep the model's total at first sight): the model's scoring level
    relative to the market's, removed from every total before it is compared with a book."""
    d = [x["total_raw"] - x["open_total"] for x in raw if x["open_total"] is not None]
    cut = pd.Timestamp(now) - pd.Timedelta(days=RULES["level_days"])
    recs = []
    if SEEN_DONE.exists():
        for ln in SEEN_DONE.read_text().splitlines()[-3000:]:
            try:
                recs.append(json.loads(ln))
            except Exception:
                pass
    recs += [r for r in load_json(SEEN_OPEN, {}).values() if pd.Timestamp(r["tip"]) <= pd.Timestamp(now)]
    for r in recs:
        m, o = r.get("model_tot_raw", r.get("model_tot")), r.get("open_tot")
        if m is not None and o is not None and pd.Timestamp(r["tip"]) >= cut:
            d.append(m - o)
    return float(np.sum(d)), len(d)


def fetch_public(days):
    """Consensus ticket and money shares by Action game id, on Action's home team / the over."""
    out = {}
    for d in days:
        r = requests.get(AN2, params={"bookIds": "15", "periods": "event", "division": "D1", "date": f"{d:%Y%m%d}"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=(10, 20))
        r.raise_for_status()
        for g in r.json().get("games", []):
            ev_ = ((g.get("markets") or {}).get("15") or {}).get("event") or {}
            rec = dict(bets=g.get("num_bets"))
            for o in ev_.get("spread") or []:
                if o.get("side") == "home":
                    bi = o.get("bet_info") or {}
                    rec["tix"], rec["mon"] = (bi.get("tickets") or {}).get("percent"), (bi.get("money") or {}).get("percent")
            for o in ev_.get("total") or []:
                if o.get("side") == "over":
                    bi = o.get("bet_info") or {}
                    rec["tot_tix"], rec["tot_mon"] = (bi.get("tickets") or {}).get("percent"), (bi.get("money") or {}).get("percent")
            out[g["id"]] = rec
        time.sleep(0.4)
    return out


def rlm_plays(gm, pub, same, margin, total, now):
    """Reverse line movement: most tickets on one side while the market (DK/FD/MGM median) has moved 1+ points the other
    way from Action's opener. Paper research at the best price when the signal first fires, so the number is the one a
    bettor could really have had. Backtest at the closing number: no edge (research/rlm.py)."""
    if not pub or same is None or (isinstance(same, float) and math.isnan(same)) or (pub.get("bets") or 0) < RULES["rlm_min_bets"]:
        return []
    same = bool(same)
    T, m = RULES["rlm_tickets"], RULES["rlm_move"]
    mins = round((pd.Timestamp(gm["kickoff_iso"]) - pd.Timestamp(now)).total_seconds() / 60)
    out = []
    def add(market, side, share, money, moved, tier, line, odds, book):
        team = (gm["home"] if side == "home" else gm["away"]) if market == "spread" else None
        pick = f"{team} {'PK' if line == 0 else f'{line:+g}'}" if market == "spread" else f"{side.title()} {line:g}"
        opener = gm["open_total"] if market == "total" else (gm["open_line"] if side == "home" else -gm["open_line"])
        model_side = round(total, 1) if market == "total" else round(margin if side == "home" else -margin, 1)
        out.append(dict(game_id=gm["game_id"], signal="rlm", market=market, side=side, pick=pick, team=team,
                        team_eid=(gm["home_eid"] if side == "home" else gm["away_eid"]) if market == "spread" else None,
                        line=line, odds=odds, book=book, edge=round(moved, 1), tier=tier, grp=gm["grp"], model=model_side, limit=line,
                        opener=opener, tix=share, money=money, bets=pub.get("bets"), moved=round(moved, 1), mins=mins))
    sp = [v["sp_home"] for v in gm["books"].values() if v["sp_home"] is not None]
    if sp and gm["open_line"] is not None and pub.get("tix") is not None:
        tix = pub["tix"] if same else 100 - pub["tix"]
        mon = None if pub.get("mon") is None else (pub["mon"] if same else 100 - pub["mon"])
        mv = float(np.median(sp)) - gm["open_line"]
        for side, share, money, moved in (("away", tix, mon, mv), ("home", 100 - tix, None if mon is None else 100 - mon, -mv)):
            if share >= T and moved >= m:
                b = best(gm["books"], side)
                if b:
                    add("spread", side, share, money, moved, "RLM_80" if share >= RULES["rlm_strict"] else "RLM", *b)
    tt = [v["tot"] for v in gm["books"].values() if v["tot"] is not None]
    if tt and gm["open_total"] is not None and pub.get("tot_tix") is not None:
        mv = float(np.median(tt)) - gm["open_total"]
        o, om = pub["tot_tix"], pub.get("tot_mon")
        for side, share, money, moved in (("under", o, om, -mv), ("over", 100 - o, None if om is None else 100 - om, mv)):
            if share >= T and moved >= m:
                b = best(gm["books"], side)
                if b:
                    add("total", side, share, money, moved, "RLM_T", *b)
    return out


def price(g, rts, now, publics=None):
    """Every upcoming D1 game with at least one book line: blend numbers, prices, gaps and the plays they make."""
    up = g[g["d1"] & (g["state"] == "pre") & (g["start"] > pd.Timestamp(now))]
    up = up[up["start"] < pd.Timestamp(now) + pd.Timedelta(days=RULES["ahead_days"] + 1)]
    k = load_json(DATA / "hist.json", {}).get("margin_scale", RULES["margin_scale"])
    raw = []
    for r in up.sort_values("start").itertuples():
        site = 0.0 if r.neutral else 1.0
        preds = {t: predict(rt, r.home_eid, r.away_eid, site) for t, rt in rts.items()}
        if any(v is None for v in preds.values()):
            continue
        raw.append(dict(r=r, preds=preds, total_raw=float(np.mean([v[1] for v in preds.values()])),
                        open_total=num(getattr(r, "open_total", None))))
    lv_sum, lv_n = total_level(raw, now)
    games, plays = [], []
    for x in raw:
        r, preds = x["r"], x["preds"]
        own = x["total_raw"] - x["open_total"] if x["open_total"] is not None else 0.0
        n_other = lv_n - (x["open_total"] is not None)
        level = (lv_sum - own) / n_other if n_other > 0 else 0.0   # leave the game itself out
        margin = k * float(np.mean([v[0] for v in preds.values()]))
        total = x["total_raw"] - level
        books, stale = book_lines(r)
        kick = r.start.tz_convert(ET)
        grp = group_of(r.home_conf_eid, r.away_conf_eid)
        gm = dict(game_id=int(r.espn_id), an_id=None if pd.isna(getattr(r, "an_id", np.nan)) else int(r.an_id),
                  kickoff_iso=kick.isoformat(), kickoff=kick.strftime("%a %b %d %I:%M %p ET"), home=r.home_loc or r.home_name, away=r.away_loc or r.away_name,
                  home_eid=int(r.home_eid), away_eid=int(r.away_eid), neutral=bool(r.neutral), grp=grp, notes=r.notes or None,
                  model_line=round(-margin, 1), model_total=round(total, 1), total_raw=round(x["total_raw"], 1),
                  total_level=round(level, 1),
                  model_pts=round(-k * preds["pts"][0], 1), model_mkt=round(-k * preds["market"][0], 1),
                  total_pts=round(preds["pts"][1] - level, 1), total_mkt=round(preds["market"][1] - level, 1),
                  open_line=num(getattr(r, "open_spread", None)), open_total=num(getattr(r, "open_total", None)),
                  cons_line=num(getattr(r, "cons_spread", None)), cons_total=num(getattr(r, "cons_total", None)),
                  books=books, stale_books=stale or None)
        games.append(gm)
        plays += rlm_plays(gm, (publics or {}).get(gm["an_id"]), getattr(r, "same_orient", None), margin, total, now)
        day = kick.date()
        cand = []
        for side in ("home", "away", "over", "under"):
            b = best(books, side)
            if b is None:
                continue
            line, odds, book = b
            market = "spread" if side in ("home", "away") else "total"
            gap = (margin + line if side == "home" else -margin + line) if market == "spread" else \
                  (total - line if side == "over" else line - total)
            cap = RULES["max_spread"] if market == "spread" else RULES["max_total"]
            tt = tier_for(market, side, gap, day, line)
            if tt is None:
                continue
            tier, thr = tt
            if gap >= cap:
                gm.setdefault("suspect", []).append(f"{book} {side} {line:+g}" if market == "spread" else f"{book} {side} {line:g}")
                continue
            if market == "spread":
                team = gm["home"] if side == "home" else gm["away"]
                need = thr - margin if side == "home" else thr + margin   # worst line that still clears the tier
                limit = math.ceil(need * 2) / 2
                if tier == "NOV_FAV":
                    limit = min(limit, -0.5)   # a November main play must stay a favourite
                pick = f"{team} {'PK' if line == 0 else f'{line:+g}'}"
                model_side = round(margin if side == "home" else -margin, 1)
                opener = gm["open_line"] if side == "home" else (None if gm["open_line"] is None else -gm["open_line"])
            else:
                team = None
                limit = math.floor((total - thr) * 2) / 2 if side == "over" else math.ceil((total + thr) * 2) / 2
                pick = f"{side.title()} {line:g}"
                model_side = round(total, 1)
                opener = gm["open_total"]
            team_eid = (gm["home_eid"] if side == "home" else gm["away_eid"]) if market == "spread" else None
            cand.append(dict(game_id=gm["game_id"], market=market, side=side, pick=pick, team=team, team_eid=team_eid,
                             line=line, odds=odds,
                             book=book, edge=round(gap, 1), tier=tier, grp=grp, model=model_side, limit=limit, opener=opener))
        plays += cand
    return games, plays


GROUP_MID = set()


def group_of(h, a):
    def tier(c):
        c = None if c is None or (isinstance(c, float) and math.isnan(c)) else str(c)
        return 0 if c in P6 else (1 if c in GROUP_MID else 2)
    return ["power 6", "mid-major", "low-major"][min(tier(h), tier(a))]


# ---------------------------------------------------------------- 4. first numbers seen at each book
def track_seen(games, g, now):
    """For every upcoming game remember the first spread / total each book showed and when, plus Action's opener.
    Settled games move to first_seen.jsonl with the closing numbers (append-only, so the repo grows slowly)."""
    pend = load_json(SEEN_OPEN, {})
    ts = now.isoformat(timespec="minutes")
    for gm in games:
        k = str(gm["game_id"])
        rec = pend.setdefault(k, dict(matchup=f"{gm['away']} {'vs' if gm['neutral'] else '@'} {gm['home']}",
                                      tip=gm["kickoff_iso"], grp=gm["grp"], first_seen=ts, home_eid=gm["home_eid"]))
        reorient(rec, gm["home_eid"])
        if gm["open_line"] is not None and "open_sp" not in rec:
            rec["open_sp"] = gm["open_line"]
        if gm["open_total"] is not None and "open_tot" not in rec:
            rec["open_tot"] = gm["open_total"]
        for b, v in gm["books"].items():
            if v["sp_home"] is not None and f"{b}_sp" not in rec:
                rec[f"{b}_sp"], rec[f"{b}_sp_t"] = v["sp_home"], ts
            if v["tot"] is not None and f"{b}_tot" not in rec:
                rec[f"{b}_tot"], rec[f"{b}_tot_t"] = v["tot"], ts
        if "model_sp" not in rec:
            rec["model_sp"], rec["model_tot"], rec["model_tot_raw"] = gm["model_line"], gm["model_total"], gm["total_raw"]
    done = g[g["completed"].fillna(False).astype(bool)].set_index("espn_id")
    out = []
    for k in list(pend):
        rec = pend[k]
        gid = int(k)
        if gid in done.index:
            r = done.loc[gid]
            reorient(rec, int(r["home_eid"]))
            rec.update(close_sp=num(r.get("close")), close_tot=num(r.get("tclose")),
                       final=f"{int(r['away_pts'])}-{int(r['home_pts'])}" if pd.notna(r["home_pts"]) else None)
            out.append(dict(game_id=gid, **rec))
            del pend[k]
        elif pd.Timestamp(rec["tip"]) < pd.Timestamp(now) - pd.Timedelta(days=3):
            del pend[k]   # postponed / cancelled
    if out and not ASOF:
        with SEEN_DONE.open("a") as fh:
            for x in out:
                fh.write(json.dumps(_clean(x)) + "\n")
    save_json(SEEN_OPEN, pend, indent=None)


def reorient(rec, home_eid):
    """ESPN sometimes swaps which team is home after a game is listed (neutral sites): stored spreads are home-team
    numbers, so flip them to stay with the same team."""
    if rec.get("home_eid") is None:
        rec["home_eid"] = home_eid
    elif rec["home_eid"] != home_eid:
        for k in [k for k in rec if k.endswith("_sp") and isinstance(rec[k], (int, float))]:
            rec[k] = -rec[k]
        rec["home_eid"] = home_eid
        rec["swapped"] = True


def seen_stats():
    """How the first DK / FD / MGM number compares with Action's opener, and how much of the opener-to-close move was
    still there when each book first posted (only games where we saw the book post: the game was listed before it)."""
    rows = []
    if SEEN_DONE.exists():
        for ln in SEEN_DONE.read_text().splitlines():
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    out = dict(games=len(rows), books={})
    for mk, o, c in (("sp", "open_sp", "close_sp"), ("tot", "open_tot", "close_tot")):
        for b in BOOKS.values():
            xs = [r for r in rows if r.get(f"{b}_{mk}") is not None and r.get(o) is not None and r.get(c) is not None
                  and r.get(f"{b}_{mk}_t", "") > r.get("first_seen", "")]
            if not xs:
                continue
            first = np.array([r[f"{b}_{mk}"] for r in xs], float)
            op = np.array([r[o] for r in xs], float)
            cl = np.array([r[c] for r in xs], float)
            mv = cl - op
            moved = np.abs(mv) >= 1
            left = (np.sign(mv) * (cl - first))[moved]
            out["books"].setdefault(b, {})[mk] = dict(
                n=len(xs), same_as_opener=round(float((first == op).mean()), 3),
                avg_diff=round(float(np.abs(first - op).mean()), 2), moved=int(moved.sum()),
                avg_move=round(float(np.abs(mv[moved]).mean()), 2) if moved.any() else None,
                move_left=round(float(left.mean()), 2) if moved.any() else None)
    return out


# ---------------------------------------------------------------- 5. ledger
def play_id(f, gidx=None):
    """Spreads are keyed by the team backed (ESPN can swap home / away after a play is logged), totals by over/under.
    Signal plays (RLM) have their own keys, so they are tracked independently of the model's plays on the same game."""
    pre = f"{f['signal']}|" if f.get("signal") else ""
    if f["market"] == "spread":
        t = team_of(f, gidx)
        if t is not None:
            return f"{pre}{f['game_id']}|spread|{t}"
    return f"{pre}{f['game_id']}|{f['market']}|{f['side']}"


def team_of(e, gidx=None):
    """ESPN id of the team a spread play backs: stored since Nov 4 2026; older entries are matched by the team name
    in the pick against the game's names."""
    if e.get("team_eid") is not None:
        return int(e["team_eid"])
    if gidx is None or e["game_id"] not in gidx.index:
        return None
    r = gidx.loc[e["game_id"]]
    name = re.sub(r"\s(PK|[-+][\d.]+)$", "", e["pick"])
    for nc, ic in (("home_loc", "home_eid"), ("away_loc", "away_eid"), ("home_name", "home_eid"), ("away_name", "away_eid")):
        if r[nc] == name:
            return int(r[ic])
    return None


def update_ledger(plays, games, g, season, now):
    led = load_json(LEDGER, [])
    gidx = g.drop_duplicates("espn_id").set_index("espn_id")
    for e in led:   # entries logged before Nov 4 2026 don't name the team: find it once, by name
        if e["market"] == "spread" and e.get("team_eid") is None:
            e["team_eid"] = team_of(e, gidx)
    first = {}   # one entry per team (or over/under) per tier: before Nov 4 a home/away swap could log a play twice
    for e in led:
        if e["status"] == "void":
            continue
        k = (play_id(e, gidx), e["tier"])
        if k in first:
            e.update(status="void", note=f"duplicate of {first[k]} (logged again after ESPN swapped home/away)")
            e.pop("auto_void", None)
        else:
            first[k] = e["id"]
    have = {}
    for e in led:
        if e["status"] != "void":
            b = play_id(e, gidx)
            have[b] = max(have.get(b, -1), TIER_RANK.get(e["tier"], 0))
    gmap = {x["game_id"]: x for x in games}
    ts = now.isoformat(timespec="minutes")
    for f in plays:
        base = play_id(f)
        rank = TIER_RANK[f["tier"]]
        if base in have and rank <= have[base]:
            continue
        gm = gmap[f["game_id"]]
        kick = pd.Timestamp(gm["kickoff_iso"])
        led.append(dict(id=base if base not in have else f"{base}|{f['tier']}", base=base, flagged=ts, season=season,
                        week=week_of(kick.date(), season), game_id=f["game_id"],
                        matchup=f"{gm['away']} {'vs' if gm['neutral'] else '@'} {gm['home']}", kickoff=gm["kickoff_iso"],
                        tier=f["tier"], market=f["market"], side=f["side"], team_eid=f.get("team_eid"), pick=f["pick"],
                        book=f["book"], line=f["line"],
                        odds=f["odds"], stake=RULES["stake"], edge=f["edge"], model=f["model"], opener=f["opener"],
                        grp=f["grp"], limit=f["limit"], status="open",
                        **{k: f[k] for k in ("signal", "tix", "money", "bets", "moved", "mins") if f.get(k) is not None}))
        have[base] = rank
    # grade: final score, and the DK/FD/MGM median closing number (Action consensus if none) for CLV. Every settled
    # play is re-graded each run (cheap), so a fix to grading or a late score correction reaches the whole record.
    res = g[g["completed"].fillna(False).astype(bool)].drop_duplicates("espn_id").set_index("espn_id")
    for e in led:
        if e["status"] == "void" and not e.get("auto_void"):
            continue   # voided by hand: leave it
        if e["game_id"] not in res.index:
            if e["status"] == "open" and pd.Timestamp(e["kickoff"]) < pd.Timestamp(now) - pd.Timedelta(days=3):
                e["status"], e["note"], e["auto_void"] = "void", "no result (postponed or cancelled)", True
            continue
        e.pop("auto_void", None)
        e.pop("note", None)
        r = res.loc[e["game_id"]]
        if pd.isna(r["home_pts"]):
            continue
        margin, pts = r["home_pts"] - r["away_pts"], r["home_pts"] + r["away_pts"]
        cs, ct = num(r.get("close")), num(r.get("tclose"))
        if e["market"] == "spread":
            t = team_of(e, gidx)
            if t is not None:
                e["team_eid"] = t
                s = 1 if t == int(r["home_eid"]) else -1
            else:
                s = 1 if e["side"] == "home" else -1
            v = s * margin + e["line"]
            e["close"] = None if cs is None else s * cs
            e["clv"] = None if cs is None else round(e["line"] - s * cs, 1)
        else:
            v = (pts - e["line"]) if e["side"] == "over" else (e["line"] - pts)
            e["close"] = ct
            e["clv"] = None if ct is None else round((ct - e["line"]) if e["side"] == "over" else (e["line"] - ct), 1)
        e["status"] = "W" if v > 0 else ("L" if v < 0 else "P")
        e["units"] = round(e["stake"] * payout(e["odds"]) if e["status"] == "W" else (-e["stake"] if e["status"] == "L" else 0.0), 3)
        e["final"] = f"{r['away_loc'] or r['away_name']} {int(r['away_pts'])} - {r['home_loc'] or r['home_name']} {int(r['home_pts'])}"
    save_json(LEDGER, led)
    return led


def summary(led):
    out = {}
    for tier in TIERS:
        d = [e for e in led if e["tier"] == tier and e["status"] in ("W", "L", "P")]
        w, l, p = (sum(e["status"] == s for e in d) for s in "WLP")
        u = sum(e.get("units", 0) for e in d)
        risk = sum(e["stake"] for e in d if e["status"] != "P")
        clv = [e["clv"] for e in d if e.get("clv") is not None]
        out[tier] = dict(w=w, l=l, p=p, units=round(u, 2), roi=round(u / risk, 3) if risk else None,
                         clv_avg=round(float(np.mean(clv)), 2) if clv else None,
                         beat_close=round(float(np.mean([c > 0 for c in clv])), 3) if clv else None,
                         open=sum(e["tier"] == tier and e["status"] == "open" for e in led))
    return out


# ---------------------------------------------------------------- 6. state
def write_state(games, plays, led, rts, tm, season, live_ok, now):
    lid = {}
    for e in led:
        if e["status"] != "void":
            lid.setdefault((play_id(e), e["tier"]), e["id"])
            lid.setdefault((play_id(e), None), e["id"])
    for f in plays:
        f["id"] = play_id(f)
        f["ledger_id"] = lid.get((f["id"], f["tier"])) or lid.get((f["id"], None))
        f["stake"] = RULES["stake"]
    # ratings: blend net rating (points better than an average D1 team on a neutral floor)
    net = {}
    for t, rt in rts.items():
        v = rt["O"] + rt["D"]
        net[t] = v - v.mean()
    tm = tm.assign(net=(net["pts"] + net["market"]) / 2, net_pts=net["pts"], net_mkt=net["market"],
                   gp=tm["eid"].map(rts["pts"]["n_games"]).fillna(0).astype(int))
    tm = tm.sort_values("net", ascending=False).reset_index(drop=True)
    ratings = [dict(rank=i + 1, team=r.name, conf=CONF_NAME.get(str(r.conf_eid), ""), net=round(r.net, 1),
                    pts=round(r.net_pts, 1), mkt=round(r.net_mkt, 1), gp=r.gp) for i, r in enumerate(tm.head(60).itertuples())]
    today = now.date()
    st = dict(generated=now.strftime("%a %b %d %Y %I:%M %p ET"), generated_iso=now.isoformat(timespec="minutes"),
              season=season, season_label=f"{season}-{str(season + 1)[2:]}", week=week_of(today, season), live_ok=live_ok,
              rules=RULES, tiers=TIERS, backtest=BACKTEST, plays=plays, games=games, ledger=led[-400:],
              summary=summary(led), ratings=ratings, seen=seen_stats(),
              model=dict(games_used=rts["pts"]["n_done"], home_court=round(2 * rts["pts"]["h"], 1),
                         margin_scale=load_json(DATA / "hist.json", {}).get("margin_scale", RULES["margin_scale"]),
                         total_level=round(float(np.mean([x["total_level"] for x in games])), 1) if games else None))
    save_json(STATE, st)
    return st


CONF_NAME = {"1": "America East", "2": "ACC", "3": "A-10", "4": "Big East", "5": "Big Sky", "6": "Big South",
             "7": "Big Ten", "8": "Big 12", "9": "Big West", "10": "CAA", "11": "C-USA", "12": "Ivy", "13": "MAAC",
             "14": "MAC", "16": "MEAC", "18": "MVC", "19": "NEC", "20": "OVC", "21": "Pac-12", "22": "Patriot",
             "23": "SEC", "24": "SoCon", "25": "Southland", "26": "SWAC", "27": "Sun Belt", "29": "WCC", "30": "WAC",
             "44": "Mountain West", "45": "Horizon", "46": "ASUN", "49": "Summit", "62": "AAC"}


def log(msg):
    BOARD.mkdir(parents=True, exist_ok=True)
    with (BOARD / "engine.log").open("a") as fh:
        fh.write(f"{now_et():%Y-%m-%d %H:%M} {msg}\n")


def main():
    t0 = time.time()
    BOARD.mkdir(parents=True, exist_ok=True)
    try:
        now = now_et()
        season = season_of(now.date())
        hist = load_json(DATA / "hist.json", {})
        GROUP_MID.update(str(k) for k, v in CONF_NAME.items() if v in hist.get("mid", []))
        GROUP_MID.add("21")   # the Pac-12 since 2024-25
        live_ok, fetched, complete = (True, 0, True) if ASOF else refresh_files(season, now.date())
        g, tm = load_season(season, now)
        if not len(tm):
            save_json(STATE, dict(generated=now.strftime("%a %b %d %Y %I:%M %p ET"), generated_iso=now.isoformat(timespec="minutes"),
                                  season=season, live_ok=live_ok, rules=RULES, tiers=TIERS, backtest=BACKTEST, plays=[], games=[],
                                  ledger=load_json(LEDGER, [])[-400:], summary=summary(load_json(LEDGER, [])), ratings=[],
                                  seen=seen_stats(), offseason=True))
            msg = f"ok no games in the feed yet (fetched {fetched})"
        else:
            rts = {t: ratings_now(t, g, tm, season, now.date()) for t in MODELS}
            publics = {}
            if not ASOF:
                try:
                    publics = fetch_public([now.date(), now.date() + dt.timedelta(days=1)])
                except Exception as e:   # RLM research only: prices and model plays go on without it
                    log(f"public shares fetch failed: {str(e)[:120]}")
            games, plays = price(g, rts, now, publics)
            if complete:
                track_seen(games, g, now)
                led = update_ledger(plays, games, g, season, now)
            else:   # still catching up on this season's files: ratings are incomplete, so nothing is logged yet
                led = load_json(LEDGER, [])
                log(f"catching up on season files (fetched {fetched}); plays not logged this run")
            write_state(games, plays, led, rts, tm, season, live_ok, now)
            msg = (f"ok games={len(games)} plays={len(plays)} finished={rts['pts']['n_done']} fetched={fetched} "
                   f"live_ok={live_ok} ({time.time() - t0:.0f}s)")
    except Exception:
        msg = f"ERROR\n{traceback.format_exc()}"
    log(msg)
    print(msg)


if __name__ == "__main__":
    if "--asof" in sys.argv:
        ASOF = True
        NOW = pd.Timestamp(sys.argv[sys.argv.index("--asof") + 1]).tz_localize(ET).to_pydatetime()
    main()
