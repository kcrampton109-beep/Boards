"""Live data sources for the NFL engine (no sportsbook sites are opened; all public feeds):
  Action Network  lines for DraftKings (68), FanDuel (69), BetMGM (75), consensus (15) and its opener (30), bet % and
                  money %, game status and scores; anytime-TD prices (DK / FD / MGM when listed)
  CBS Sports      open and current line per game (the user's RLM source)
  ESPN            injury report
  Open-Meteo      hourly wind / temperature forecast at the stadium"""
import re, time, unicodedata
import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
AN = "https://api.actionnetwork.com/web/v2/scoreboard/nfl"
BOOKS = {"68": "DK", "69": "FD", "75": "MGM"}
FIX = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "LVR": "LV"}

# home stadium coordinates (lat, lon); roof comes from the nflverse schedule
STADIUM = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4008), "BAL": (39.2780, -76.6227), "BUF": (42.7738, -78.7870),
    "CAR": (35.2258, -80.8528), "CHI": (41.8623, -87.6167), "CIN": (39.0955, -84.5161), "CLE": (41.5061, -81.6995),
    "DAL": (32.7473, -97.0945), "DEN": (39.7439, -105.0201), "DET": (42.3400, -83.0456), "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107), "IND": (39.7601, -86.1639), "JAX": (30.3239, -81.6373), "KC": (39.0489, -94.4839),
    "LAC": (33.9535, -118.3392), "LA": (33.9535, -118.3392), "LV": (36.0909, -115.1833), "MIA": (25.9580, -80.2389),
    "MIN": (44.9737, -93.2577), "NE": (42.0909, -71.2643), "NO": (29.9511, -90.0812), "NYG": (40.8135, -74.0745),
    "NYJ": (40.8135, -74.0745), "PHI": (39.9008, -75.1675), "PIT": (40.4468, -80.0158), "SEA": (47.5952, -122.3316),
    "SF": (37.4030, -121.9700), "TB": (27.9759, -82.5033), "TEN": (36.1665, -86.7713), "WAS": (38.9077, -76.8645),
}


def get(url, tries=3, **kw):
    err = None
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=60, **kw)
            r.raise_for_status()
            return r
        except Exception as e:
            err = e
            time.sleep(10 * (attempt + 1))
    raise err


def norm_name(n):
    n = unicodedata.normalize("NFKD", str(n)).encode("ascii", "ignore").decode()
    n = re.sub(r"[.'`]", "", n.lower())
    n = re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", n.strip())
    return re.sub(r"\s+", " ", n)


# ---------------------------------------------------------------- Action Network
def an_games(season, weeks):
    """Games for the given weeks with every book's lines, consensus splits, status and score."""
    out = []
    for w in weeks:
        js = get(AN, params={"bookIds": "15,30,68,69,75", "periods": "event", "season": season, "week": w}).json()
        for g in js.get("games", []):
            ab = {t["id"]: FIX.get(t.get("abbr"), t.get("abbr")) for t in g.get("teams", [])}
            box = g.get("boxscore") or {}
            r = dict(an_id=g["id"], week=g.get("week"), start=pd.Timestamp(g["start_time"]), status=g.get("status"),
                     away=ab.get(g["away_team_id"]), home=ab.get(g["home_team_id"]),
                     away_pts=box.get("total_away_points"), home_pts=box.get("total_home_points"), books={})
            for book, per in (g.get("markets") or {}).items():
                ev = per.get("event") or {}
                b = {}
                for o in ev.get("spread", []):
                    if o.get("side") == "home":
                        b["sp_home"], b["sp_home_odds"] = o.get("value"), o.get("odds")
                    elif o.get("side") == "away":
                        b["sp_away"], b["sp_away_odds"] = o.get("value"), o.get("odds")
                    bi = o.get("bet_info") or {}
                    if o.get("side") == "home" and book == "15":
                        r["tix_home"] = (bi.get("tickets") or {}).get("percent")
                        r["money_home"] = (bi.get("money") or {}).get("percent")
                for o in ev.get("total", []):
                    if o.get("side") == "over":
                        b["total"], b["over_odds"] = o.get("value"), o.get("odds")
                    elif o.get("side") == "under":
                        b["total_u"], b["under_odds"] = o.get("value"), o.get("odds")
                    bi = o.get("bet_info") or {}
                    if o.get("side") == "over" and book == "15":
                        r["tix_over"] = (bi.get("tickets") or {}).get("percent")
                        r["money_over"] = (bi.get("money") or {}).get("percent")
                for o in ev.get("moneyline", []):
                    b["ml_" + o.get("side", "?")] = o.get("odds")
                # splits are consensus numbers repeated on every book: take them from any book if 15 is missing
                if "tix_home" not in r:
                    for o in ev.get("spread", []):
                        if o.get("side") == "home" and (o.get("bet_info") or {}).get("tickets", {}).get("percent") is not None:
                            r["tix_home"] = o["bet_info"]["tickets"]["percent"]
                            r["money_home"] = (o["bet_info"].get("money") or {}).get("percent")
                if "tix_over" not in r:
                    for o in ev.get("total", []):
                        if o.get("side") == "over" and (o.get("bet_info") or {}).get("tickets", {}).get("percent") is not None:
                            r["tix_over"] = o["bet_info"]["tickets"]["percent"]
                            r["money_over"] = (o["bet_info"].get("money") or {}).get("percent")
                r["books"][book] = b
            # Action Network reports 0 / 0 before any bets are counted: treat as missing
            for t, m in (("tix_home", "money_home"), ("tix_over", "money_over")):
                if not r.get(t) and not r.get(m):
                    r[t] = r[m] = None
            out.append(r)
    return out


def an_anytime_td(an_id):
    """{normalized player name: {'name', 'team', 'pos', 'DK': odds, 'FD': odds, 'MGM': odds}}"""
    js = get(f"https://api.actionnetwork.com/web/v2/games/{an_id}/props", params={"bookIds": "15,68,69,75"}).json()
    players = js.get("players") or {}
    out = {}
    for m in (js.get("player_props") or {}).get("core_bet_type_62_anytime_touchdown_scorer", []):
        for book, lines in (m.get("lines") or {}).items():
            if book not in BOOKS:
                continue
            for l in lines:
                p = players.get(str(l.get("player_id"))) or {}
                name = p.get("full_name")
                if not name or l.get("odds") is None:
                    continue
                d = out.setdefault(norm_name(name), dict(name=name, info=p.get("display_text", "")))
                d[BOOKS[book]] = l["odds"]
    return out


# ---------------------------------------------------------------- CBS
def cbs_week(season, week):
    import cbs
    url = f"https://www.cbssports.com/nfl/odds/{season}/regular/week-{week}/" if week <= 18 else "https://www.cbssports.com/nfl/odds/"
    rows = cbs.parse(get(url).text)
    return {(r[1], r[2]): dict(zip(cbs.COLS, r)) for r in rows}


# ---------------------------------------------------------------- ESPN injuries
def espn_injuries():
    js = get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries").json()
    rows = []
    for t in js.get("injuries", []):
        team = t.get("displayName")
        for i in t.get("injuries", []):
            a = i.get("athlete") or {}
            href = next((l.get("href", "") for l in a.get("links", []) if "playercard" in (l.get("rel") or [])), "")
            m = re.search(r"/id/(\d+)/", href)
            pos = (a.get("position") or {}).get("abbreviation") if isinstance(a.get("position"), dict) else None
            det = i.get("details") or {}
            rows.append(dict(team_name=team, name=a.get("displayName"), espn_id=m.group(1) if m else None, pos=pos,
                             status=i.get("status"), date=i.get("date"), comment=i.get("shortComment"),
                             detail=" ".join(str(x) for x in (det.get("type"), det.get("detail"), det.get("side")) if x),
                             return_date=det.get("returnDate")))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- weather
def forecast(lat, lon, kickoff_utc):
    """Mean wind (mph), max gust, temperature (F) and precip chance over the 3 hours from kickoff."""
    d0 = kickoff_utc.strftime("%Y-%m-%d")
    d1 = (kickoff_utc + pd.Timedelta(hours=4)).strftime("%Y-%m-%d")
    js = get("https://api.open-meteo.com/v1/forecast", params=dict(
        latitude=lat, longitude=lon, hourly="wind_speed_10m,wind_gusts_10m,temperature_2m,precipitation_probability",
        wind_speed_unit="mph", temperature_unit="fahrenheit", timezone="UTC", start_date=d0, end_date=d1)).json()
    h = pd.DataFrame(js["hourly"])
    h["time"] = pd.to_datetime(h["time"]).dt.tz_localize("UTC")
    k = kickoff_utc.floor("h")
    w = h[(h["time"] >= k) & (h["time"] < k + pd.Timedelta(hours=3))]
    if not len(w):
        return None
    return dict(wind=round(float(w["wind_speed_10m"].mean()), 1), gust=round(float(w["wind_gusts_10m"].max()), 1),
                temp=round(float(w["temperature_2m"].mean()), 0), precip=int(w["precipitation_probability"].max() or 0))
