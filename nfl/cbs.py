"""Parse the CBS Sports NFL odds page (https://www.cbssports.com/nfl/odds/), the user's line-movement source.
Per game CBS shows an 'Open' column (the favourite's row holds the opening spread, the other row the opening total)
and current spread / moneyline / total buttons, each naming its sportsbook (FanDuel for spreads and totals)."""
import html as H
import re

COLS = ["cbs_game", "away", "home", "open_spread_home", "open_total", "spread_home", "spread_odds_home", "spread_book",
        "total", "total_book", "ml_away", "ml_home"]
FIX = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}


def _num(x):
    x = (x or "").strip().replace("+", "")
    if x.upper() in ("PK", "EVEN", "EV"):
        return 0.0
    try:
        return float(x)
    except ValueError:
        return None


def _cell(td):
    t = re.findall(r'class="BetButton-text"[^>]*>\s*([^<]*?)\s*<', td)
    t2 = re.findall(r'class="BetButton-text2"[^>]*>\s*([^<]*?)\s*<', td)
    cfg = re.search(r"data-config=([^ >]+)", td)
    conf = H.unescape(cfg.group(1)) if cfg else ""
    op = re.search(r'"operatorName":"([^"]+)"', conf)
    return (t[0] if t else ""), (t2[0] if t2 else ""), (op.group(1) if op else "")


def parse(page):
    out = []
    for m in re.finditer(r"<table[^>]*OddsBlock-game[^>]*data-game-abbrev=\"?([A-Z0-9_@]+)", page):
        end = page.find("</table>", m.start())
        t = re.sub(r"\s+", " ", page[m.start():end])
        abbrev = m.group(1)
        body = t[t.find("<tbody>"):]
        rows = re.findall(r"<tr>(.*?)</tr>", body)
        if len(rows) < 2:
            continue
        teams, opens, spreads, mls, totals = [], [], [], [], []
        for r in rows[:2]:
            ab = re.search(r'OddsBlock-teamText--short">\s*<a[^>]*>\s*([A-Z]+)\s*<', r)
            teams.append(FIX.get(ab.group(1), ab.group(1)) if ab else None)
            o = re.search(r'<th class="OddsBlock-betOdds OddsBlock-betOdds--openingLines">(.*?)</th>', r)
            opens.append(_cell(o.group(1)) if o else ("", "", ""))
            s = re.search(r'OddsBlock-betOdds--spread[^>]*>(.*?)</td>', r)
            spreads.append(_cell(s.group(1)) if s else ("", "", ""))
            ml = re.search(r'OddsBlock-betOdds--moneyline[^>]*>(.*?)</td>', r)
            mls.append(_cell(ml.group(1)) if ml else ("", "", ""))
            tt = re.search(r'OddsBlock-betOdds--total[^>]*>(.*?)</td>', r)
            totals.append(_cell(tt.group(1)) if tt else ("", "", ""))
        away, home = teams
        open_spread_home, open_total = None, None
        for i, (txt, _, _) in enumerate(opens):
            if txt[:1].lower() in ("o", "u"):
                open_total = _num(txt[1:])
            elif txt:
                v = _num(txt)
                if v is not None:
                    open_spread_home = v if i == 1 else -v
        sp_home = _num(spreads[1][0])
        if sp_home is None and _num(spreads[0][0]) is not None:
            sp_home = -_num(spreads[0][0])
        tot = _num(totals[0][0][1:]) if totals[0][0][:1].lower() in ("o", "u") else None
        out.append([abbrev, away, home, open_spread_home, open_total, sp_home, _num(spreads[1][1]), spreads[1][2] or spreads[0][2],
                    tot, totals[0][2], _num(mls[0][0]), _num(mls[1][0])])
    return out


if __name__ == "__main__":
    import requests
    page = requests.get("https://www.cbssports.com/nfl/odds/", headers={"User-Agent": "Mozilla/5.0"}, timeout=60).text
    for r in parse(page):
        print(dict(zip(COLS, r)))
