"""Sunday weekly refresh for the CFB Play Card.

1. Force a data refresh (Saturday's results), re-rate every team, grade the paper ledger and price the new week.
2. Build the weekly report and the Excel workbook (~/cfb/exports/CFB_Model_2026.xlsx).
3. Stage the board page + data for publishing into OUT (argv[1]) and mark this week's plays as announced.
Prints one JSON line: message (phone push, <= 200 chars), report (lines), workbook, page, state."""
import datetime as dt, json, pathlib, shutil, sys
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import engine  # noqa: E402

B = ROOT / "board"
EXP = ROOT / "exports"
HIST = B / "ratings_hist"
EXP.mkdir(exist_ok=True)
HIST.mkdir(exist_ok=True)
MAIN = ("STRONG", "FCS", "WEATHER")


def tier_line(rows):
    w = sum(e["status"] == "W" for e in rows)
    l = sum(e["status"] == "L" for e in rows)
    p = sum(e["status"] == "P" for e in rows)
    u = sum(e.get("units") or 0 for e in rows)
    clv = [e["clv"] for e in rows if e.get("clv") is not None]
    risk = sum(e["stake"] for e in rows if e["status"] in ("W", "L"))
    return dict(w=w, l=l, p=p, units=round(u, 2), roi=round(u / risk, 3) if risk else None,
                clv=round(float(np.mean(clv)), 2) if clv else None,
                beat=round(float(np.mean([c > 0 for c in clv])), 3) if clv else None)


def fmt(t):
    rec = f"{t['w']}-{t['l']}" + (f"-{t['p']}" if t["p"] else "")
    clv = "" if t["clv"] is None else f", CLV {t['clv']:+.1f}"
    return f"{rec} ({t['units']:+.1f}u{clv})"


def main(out):
    engine.main(force_refresh=True)
    st = json.loads((B / "state.json").read_text())
    led = json.loads((B / "ledger.json").read_text())
    now = pd.Timestamp.now(tz=engine.ET)

    # last week = graded entries whose game kicked off in the past 8 days
    def ko(e):
        try:
            return pd.Timestamp(e["kickoff"]).tz_convert(engine.ET)
        except Exception:
            return None
    week = [e for e in led if e["status"] in ("W", "L", "P") and ko(e) is not None and ko(e) > now - pd.Timedelta(days=8)]
    last = {t: tier_line([e for e in week if e["tier"] == t]) for t in ("STRONG", "FCS", "WEATHER", "LEAN", "RESEARCH")}
    season = {t: tier_line([e for e in led if e["tier"] == t and e["status"] in ("W", "L", "P")]) for t in last}

    # this week's card
    card = [p for p in st["plays"] if p["tier"] in MAIN and not p.get("provisional")]
    prov = [p for p in st["plays"] if p["tier"] in MAIN and p.get("provisional")]
    games = {g["game_id"]: g for g in st["games"]}

    # ratings movers vs the snapshot from about a week ago
    rt = pd.read_parquet(B / "ratings.parquet")
    snap = HIST / f"{now:%Y-%m-%d}.parquet"
    rt.to_parquet(snap)
    olds = sorted(p for p in HIST.glob("*.parquet") if p.stem <= (now - pd.Timedelta(days=5)).strftime("%Y-%m-%d"))
    movers = []
    rt["change"] = np.nan
    if olds:
        prev = pd.read_parquet(olds[-1])[["team_id", "net"]].rename(columns={"net": "prev"})
        rt = rt.merge(prev, on="team_id", how="left")
        rt["change"] = rt["net"] - rt["prev"]
        f = rt[rt["cls"] == "fbs"].dropna(subset=["change"]).sort_values("change")
        movers = [f"{r.team} {r.change:+.1f}" for r in f.tail(3).iloc[::-1].itertuples()] + \
                 [f"{r.team} {r.change:+.1f}" for r in f.head(3).itertuples()]

    # health
    calls_left = None
    cl = ROOT / "data" / "calls.log"
    if cl.exists():
        calls_left = cl.read_text().strip().splitlines()[-1].split("\t")[2]
    log = (B / "engine.log").read_text(errors="ignore").splitlines()
    recent = [l for l in log if l[:10] >= (now - pd.Timedelta(days=7)).strftime("%Y-%m-%d")]
    errors = sum("ERROR" in l for l in recent)
    stamps = sorted(pd.Timestamp(l[:16]) for l in recent if l[:4].isdigit() and " ok " in l)
    gap = max([(b - a).total_seconds() / 3600 for a, b in zip(stamps, stamps[1:])], default=0)
    snaps = sum(1 for d in (ROOT / "snapshots").glob("*") if d.is_dir() and d.name >= (now - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
                for _ in d.glob("*.csv.gz"))

    # report
    rep = [f"CFB weekly refresh, {now:%a %b %d %I:%M %p} ET",
           f"Last week: Strong {fmt(last['STRONG'])}; FCS {fmt(last['FCS'])}; Wind {fmt(last['WEATHER'])}",
           f"Research tiers last week: Leans {fmt(last['LEAN'])}; Research {fmt(last['RESEARCH'])}",
           f"Season: Strong {fmt(season['STRONG'])}; FCS {fmt(season['FCS'])}; Wind {fmt(season['WEATHER'])}",
           f"This week's card so far: {len(card)} play{'s' if len(card) != 1 else ''}"
           + (f" (+{len(prov)} provisional, waiting on results)" if prov else "")]
    for p in card[:12]:
        g = games[p["game_id"]]
        lim = f", playable to {p['limit']:+g}" if p.get("limit") is not None else ""
        rep.append(f"  {p['tier'].title()}: {p['pick']} {int(p['odds']):+d} {p['book']} ({g['away']} @ {g['home']}{lim})")
    if movers:
        rep.append("Biggest FBS rating moves: " + ", ".join(movers))
    rep.append(f"Health: {errors} engine errors, longest gap between checks {gap:.1f}h, "
               f"{snaps} line snapshots this week, {calls_left} data calls left this month")

    msg = (f"CFB week: Strong {fmt(last['STRONG'])}, FCS {fmt(last['FCS'])}. "
           f"New card: {len(card)} plays. Workbook attached.")
    if len(msg) > 200:
        msg = msg[:197] + "..."

    # workbook
    wb = EXP / "CFB_Model_2026.xlsx"
    write_workbook(wb, st, led, rt, card, games)

    # stage board files + mark this week's plays as announced (the sync task won't re-push them)
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(B / "index.html", out / "index.html")
    shutil.copy(B / "state.json", out / "state.json")
    shutil.copy(B / "ledger.json", out / "ledger.json")
    memo_p = B / "notified.json"
    memo = json.loads(memo_p.read_text()) if memo_p.exists() else {"ids": [], "stale_warned": ""}
    memo["ids"] = sorted(set(memo["ids"]) | {p["id"] for p in card})
    memo_p.write_text(json.dumps(memo))
    (B / "weekly_report.txt").write_text("\n".join(rep), encoding="utf-8")
    print(json.dumps({"message": msg, "report": rep, "workbook": str(wb),
                      "page": str(out / "index.html"), "state": str(out / "state.json")}))


def write_workbook(path, st, led, rt, card, games):
    def kick(s):
        try:
            return pd.Timestamp(s).tz_convert(engine.ET).strftime("%a %b %d %I:%M %p")
        except Exception:
            return s
    this = pd.DataFrame([dict(Tier=p["tier"], Game=f"{games[p['game_id']]['away']} @ {games[p['game_id']]['home']}",
                              Kickoff=games[p["game_id"]]["kickoff"], Pick=p["pick"], Book=p["book"], Odds=p["odds"],
                              Edge=p["edge"], **{"Playable to": p.get("limit"), "Model line (home)": games[p["game_id"]]["model_line"],
                                                 "Consensus (home)": games[p["game_id"]]["cons_line"]}) for p in card])
    lg = pd.DataFrame(led)
    if len(lg):
        lg = lg.assign(kickoff=lg["kickoff"].map(kick))
        cols = ["flagged", "kickoff", "tier", "grp", "matchup", "market", "pick", "book", "line", "odds", "stake", "edge",
                "model", "status", "units", "close", "clv", "final", "note"]
        lg = lg[[c for c in cols if c in lg.columns]].iloc[::-1]
    by = []
    if len(led):
        d = pd.DataFrame([e for e in led if e["status"] in ("W", "L", "P")])
        if len(d):
            d["Week of"] = d["kickoff"].map(lambda s: (pd.Timestamp(s).tz_convert(engine.ET) - pd.Timedelta(days=(pd.Timestamp(s).tz_convert(engine.ET).weekday() + 1) % 7)).strftime("%Y-%m-%d"))
            for (wk, tier), x in d.groupby(["Week of", "tier"]):
                t = tier_line(x.to_dict("records"))
                by.append({"Week of (Sun)": wk, "Tier": tier, "W": t["w"], "L": t["l"], "P": t["p"], "Units": t["units"],
                           "ROI": t["roi"], "Avg CLV (pts)": t["clv"], "Beat close": t["beat"]})
    byw = pd.DataFrame(by)
    rr = rt.copy()
    rr["Rank"] = rr.groupby("cls")["net"].rank(ascending=False, method="first").astype(int)
    rr = rr.sort_values(["cls", "Rank"])[["cls", "Rank", "team", "net", "off", "dfn", "change"]]
    rr.columns = ["Division", "Rank", "Team", "Rating (pts vs avg FBS)", "Offense", "Defense", "Change vs last week"]
    allg = []
    for g in st["games"]:
        row = {"Kickoff": g["kickoff"], "Group": g["grp"], "Away": g["away"], "Home": g["home"],
               "Model line (home)": g["model_line"], "Consensus (home)": g["cons_line"]}
        for b in ("DK", "FD", "MGM"):
            v = g["books"].get(b) or {}
            row[f"{b} spread"] = v.get("sp_home")
            row[f"{b} odds"] = v.get("sp_home_odds")
        row.update({"Model total": g["model_total"], "Consensus total": g["cons_total"],
                    "Home tickets %": g.get("tix_home"), "Home money %": g.get("money_home"),
                    "Wind (mph)": round(g["wx"]["wind"], 1) if g.get("wx") else None})
        allg.append(row)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in (("This Week", this), ("Weekly Results", byw), ("Ledger", lg), ("Ratings", rr), ("All Games", pd.DataFrame(allg))):
            (df if len(df) else pd.DataFrame({"Note": ["Nothing yet"]})).to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.font = cell.font.copy(bold=True)
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 48)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "board" / "publish"))
