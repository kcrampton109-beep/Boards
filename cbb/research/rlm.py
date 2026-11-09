"""Does reverse line movement (RLM) or sharp money pay in college basketball?

  RLM          T%+ of the tickets are on one side and the line moved m+ points AWAY from that side (opener -> close);
               bet the other side
  money split  a side's share of the money is D+ points above its share of the tickets; bet that side
Tickets and money are Action Network's consensus (book 15) just before tip. Opener = book 30, close = DK/FD/BetMGM median.

The signal only exists after the line has moved, so plays are graded at the CLOSING number at -110 ("mid" = halfway
between open and close, the best a bettor reacting partway could expect; the opener is never available to an RLM bet).

To avoid mining: rules are chosen on the development seasons only (2023-24, 2024-25). The holdout (2025-26) is run
once, with "python research/rlm.py holdout" after the rule is written down in HOLDOUT below.
"""
import glob, gzip, json, pathlib, sys
import numpy as np
import pandas as pd
import evaluate as ev, edge, model

DEV, HOLD = (2023, 2024), (2025,)
BE = 100 * 110 / 210   # break-even at -110


def totals_public():
    """Consensus ticket / money share on the over, by Action game id (build.py keeps only the spread shares)."""
    rows = []
    for f in sorted(glob.glob(str(model.DATA / "an_raw" / "*.json.gz"))):
        if pathlib.Path(f).name[:4] < "2023":
            continue
        for g in json.loads(gzip.decompress(pathlib.Path(f).read_bytes())).get("games", []):
            for o in g.get("odds") or []:
                if o.get("book_id") == 15 and o.get("type") == "game":
                    rows.append((g["id"], o.get("total_over_public"), o.get("total_over_money")))
    return pd.DataFrame(rows, columns=["an_id", "tot_tix", "tot_money"]).drop_duplicates("an_id", keep="last")


def frame():
    g, _ = edge.frame()
    g = g[g["season"].isin(DEV + HOLD) & g["open"].notna() & g["close"].notna()].copy()
    g = g.merge(totals_public(), on="an_id", how="left")
    g["tix"], g["mon"] = g["cons_sp_tix_home"], g["cons_sp_money_home"]
    g["bets"] = pd.to_numeric(g["cons_num_bets"], errors="coerce")
    return g


def result(pick, line, actual):
    return pick * (actual + line)


def rlm_spread(g, T, m):
    mv = g["close"] - g["open"]
    pick = np.select([(g["tix"] >= T) & (mv >= m), (g["tix"] <= 100 - T) & (mv <= -m)], [-1.0, 1.0], 0.0)
    return pick


def rlm_total(g, T, m):
    mv = g["tclose"] - g["topen"]
    return np.select([(g["tot_tix"] >= T) & (mv <= -m), (g["tot_tix"] <= 100 - T) & (mv >= m)], [-1.0, 1.0], 0.0)


def split_spread(g, D):
    d = g["mon"] - g["tix"]
    return np.select([d >= D, d <= -D], [1.0, -1.0], 0.0)


def split_total(g, D):
    d = g["tot_money"] - g["tot_tix"]
    return np.select([d >= D, d <= -D], [1.0, -1.0], 0.0)


def bets(g, pick, kind):
    d = g[pick != 0].copy()
    d["pick"] = pick[pick != 0]
    if kind == "spread":
        d["res"] = d["pick"] * (d["margin"] + d["close"])
        d["res_mid"] = d["pick"] * (d["margin"] + (d["open"] + d["close"]) / 2)
        d["mgap"] = d["pick"] * (d["m_adj"] + d["close"])          # model gap vs the closing number, on the bet's side
    else:
        d["res"] = d["pick"] * (d["points"] - d["tclose"])
        d["res_mid"] = d["pick"] * (d["points"] - (d["topen"] + d["tclose"]) / 2)
        d["mgap"] = d["pick"] * (d["t_adj"] - d["tclose"])
    return d


def score(d, col="res"):
    w, l = int((d[col] > 0).sum()), int((d[col] < 0).sum())
    n = w + l
    z = (w - n * BE / 100) / np.sqrt(n * .25) if n else np.nan
    return dict(n=n, rec=f"{w}-{l}", ATS=round(100 * w / n, 1) if n else None,
                ROI=round(100 * (w * 100 / 110 - l) / n, 1) if n else None, z=round(z, 2) if n else None)


def grid(g, kind):
    rows = []
    fn = rlm_spread if kind == "spread" else rlm_total
    for T in (55, 60, 65, 70, 75, 80):
        row = dict(tickets=f"{T}%+")
        for m in (0.5, 1.0, 1.5, 2.0, 3.0):
            s = score(bets(g, fn(g, T, m), kind))
            row[f"move {m}+"] = f"{s['n']}: {s['ATS']}%" if s["n"] else ""
        rows.append(row)
    return pd.DataFrame(rows).set_index("tickets")


def main(mode="dev"):
    g = frame()
    pd.set_option("display.width", 220)
    D = g[g["season"].isin(DEV)]
    print(f"development games {len(D)}, with spread tickets {D['tix'].notna().sum()}, total tickets {D['tot_tix'].notna().sum()}; break-even {BE:.1f}%")
    if mode == "dev":
        print("median bets per game", D["bets"].median(), "| share of games the line moved 0.5+:", round((D.close != D.open).mean(), 3))
        print("\n## RLM spreads, development seasons: plays: ATS at the closing number\n"); print(grid(D, "spread").to_string())
        print("\n## RLM totals\n"); print(grid(D, "total").to_string())
        rows = []
        for kind, fn in (("spread", split_spread), ("total", split_total)):
            for x in (5, 10, 15, 20, 30, 40):
                rows.append(dict(kind=kind, money_minus_tickets=f"{x}+", **score(bets(D, fn(D, x), kind))))
        print("\n## Money share above ticket share\n"); print(pd.DataFrame(rows).set_index(["kind", "money_minus_tickets"]).to_string())
        # the standard definition, fixed in advance: 65%+ tickets, line moved 1+ the other way
        print("\n## Standard RLM (65%+ tickets, 1+ point the other way), by slice\n")
        rows = []
        for kind, fn in (("spread", rlm_spread), ("total", rlm_total)):
            d = bets(D, fn(D, 65, 1.0), kind)
            rows.append(dict(cut=f"{kind}: all", **score(d), mid_ATS=score(d, "res_mid")["ATS"]))
            for s, x in d.groupby("season"):
                rows.append(dict(cut=f"{kind}: {s}", **score(x), mid_ATS=score(x, "res_mid")["ATS"]))
            for lab, x in (("Nov", d[d.period == "1 Nov"]), ("Dec-Mar", d[d.period != "1 Nov"])):
                rows.append(dict(cut=f"{kind}: {lab}", **score(x), mid_ATS=score(x, "res_mid")["ATS"]))
            for lab, x in d.groupby("grp"):
                rows.append(dict(cut=f"{kind}: {lab}", **score(x), mid_ATS=score(x, "res_mid")["ATS"]))
            for t in (0, 1, 2, 3):
                x = d[d.mgap >= t]
                rows.append(dict(cut=f"{kind}: model agrees by {t}+", **score(x), mid_ATS=score(x, "res_mid")["ATS"]))
            x = d[d.mgap < 0]
            rows.append(dict(cut=f"{kind}: model disagrees", **score(x), mid_ATS=score(x, "res_mid")["ATS"]))
        print(pd.DataFrame(rows).set_index("cut").to_string())


# Fixed on the development seasons (Nov 8 2026): the standard rule and the best cell of the spread grid. Totals and money
# splits showed nothing to test (shown for the record).
HOLDOUT = [("spread", 65, 1.0), ("spread", 80, 1.0), ("total", 65, 1.0)]


def holdout():
    g = frame()
    H = g[g["season"].isin(HOLD)]
    print(f"holdout games {len(H)}, with tickets {H['tix'].notna().sum()}")
    rows = []
    for kind, T, m in HOLDOUT:
        fn = rlm_spread if kind == "spread" else rlm_total
        d = bets(H, fn(H, T, m), kind)
        rows.append(dict(rule=f"{kind} {T}%+ / {m}+", **score(d), mid_ATS=score(d, "res_mid")["ATS"]))
        for lab, x in (("Nov", d[d.period == "1 Nov"]), ("Dec-Mar", d[d.period != "1 Nov"])):
            rows.append(dict(rule=f"   {lab}", **score(x), mid_ATS=score(x, "res_mid")["ATS"]))
    print(pd.DataFrame(rows).set_index("rule").to_string())


if __name__ == "__main__":
    if sys.argv[1:] == ["holdout"]:
        holdout()
        sys.exit()
    main(*sys.argv[1:])
