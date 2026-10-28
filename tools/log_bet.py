"""Record a bet from a "Log a bet" issue (.github/ISSUE_TEMPLATE/bet.yml), run by .github/workflows/mybets.yml.

  python tools/log_bet.py                      read the issue event ($GITHUB_EVENT_PATH): save or update the bet as
                                               <board>/board/mybets/<issue>.json, or delete it on a "remove" comment.
                                               Writes the reply to _mybets_reply.md and board / ok to $GITHUB_OUTPUT.
  python tools/log_bet.py --combine cfb OUT    write OUT/mybets.json: every saved bet of a board, oldest first.

One file per bet, so bets logged at the same time never conflict. Bets on a board play are linked to the play's
ledger entry (game, market, side) and graded on the page from the published final scores."""
import datetime as dt, glob, json, os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDER = {"CFB": "cfb", "NFL": "nfl"}
LABELS = {"Board": "board", "Bet": "bet", "Line you got": "line", "Odds you got": "odds", "Sportsbook": "book",
          "Amount bet ($)": "amount", "Game": "game", "Kickoff": "kickoff", "Board play (leave as is)": "play",
          "Bet type (only for a bet that is not a board play)": "type", "Notes": "notes"}
TYPES = {"Away team spread": ("spread", "away"), "Home team spread": ("spread", "home"), "Over": ("total", "over"),
         "Under": ("total", "under"), "Away team moneyline": ("moneyline", "away"), "Home team moneyline": ("moneyline", "home")}


def combine(folder, out):
    bets = [json.loads(pathlib.Path(f).read_text(encoding="utf-8")) for f in glob.glob(str(ROOT / folder / "board" / "mybets" / "*.json"))]
    bets.sort(key=lambda b: (b.get("logged") or "", b.get("id") or 0))
    pathlib.Path(out).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(out) / "mybets.json").write_text(json.dumps(bets, indent=1), encoding="utf-8")
    return len(bets)


def parse_form(body):
    vals, key = {}, None
    for line in (body or "").replace("\r", "").split("\n"):
        if line.startswith("### "):
            key = LABELS.get(line[4:].strip())
            if key:
                vals[key] = []
        elif key:
            vals[key].append(line)
    out = {k: "\n".join(v).strip() for k, v in vals.items()}
    return {k: ("" if v == "_No response_" else v) for k, v in out.items()}


def num(s, what, required=True):
    s = (s or "").strip().replace("$", "").replace(",", "").replace("−", "-").replace("–", "-")
    if not s:
        if required:
            raise ValueError(f"{what} is missing")
        return None
    if s.lower() in ("pk", "pick", "pickem", "pick'em", "ev"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"{what} '{s}' is not a number")


def book_name(s):
    t = (s or "").strip().lower()
    return "DraftKings" if t.startswith(("dk", "draft")) else "FanDuel" if t.startswith(("fd", "fan")) else \
           "BetMGM" if "mgm" in t else (s or "").strip()


def record(issue):
    """Build the bet from the issue; returns (folder, bet dict). Raises ValueError with a readable reason."""
    v = parse_form(issue.get("body"))
    board = (v.get("board") or "").strip().upper()
    if board not in FOLDER:
        raise ValueError("Board must be CFB or NFL")
    folder = FOLDER[board]
    odds = num(v.get("odds"), "Odds")
    if abs(odds) < 100:
        raise ValueError(f"Odds {odds:g} look wrong: use American odds like -110 or +105")
    amount = num(v.get("amount"), "Amount")
    if amount <= 0:
        raise ValueError("Amount must be more than $0")
    bet = dict(id=issue["number"], issue=issue["html_url"], board=board, pick=(v.get("bet") or "").strip(),
               odds=int(round(odds)), book=book_name(v.get("book")), amount=round(amount, 2), notes=v.get("notes") or "",
               matchup=v.get("game") or "", kickoff=v.get("kickoff") or "", play=(v.get("play") or "").strip(),
               logged=issue.get("created_at"), updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    ledger = json.loads((ROOT / folder / "board" / "ledger.json").read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in ledger if e.get("status") != "void"}
    play = bet["play"]
    if play and not play.startswith(("game:", "look:")):
        ents = [by_id.get(i) for i in play.split("+")]
        if any(e is None for e in ents):
            raise ValueError(f"board play '{play}' was not found; open the form from the board's button")
        e = ents[0]
        bet.update(tier=e["tier"], game_id=e["game_id"], matchup=bet["matchup"] or e["matchup"],
                   kickoff=e.get("kickoff_iso") or e.get("kickoff") or bet["kickoff"],
                   board_line=e.get("line"), board_odds=e.get("odds"), board_book=e.get("book"))
        if len(ents) == 2:   # a 2-team teaser made of two board legs
            bet.update(market="teaser", side=None, line=None, tier="TEASER2",
                       legs=[dict(game_id=x["game_id"], side=x["side"], line=x["line"], pick=x["pick"], matchup=x["matchup"],
                                  kickoff=x.get("kickoff_iso") or x.get("kickoff")) for x in ents])
        else:
            bet.update(market=e["market"], side=e["side"])
            bet["line"] = num(v.get("line"), "Line", required=e["market"] in ("spread", "total"))
    elif play.startswith("look:"):
        # a bet on an NFL look-ahead play (next week's line, shown on the board but never in its ledger):
        # tied to the game and graded like any bet; play id is "<game_id>:<tier>:<market>:<side>"
        parts = play[5:].split(":")
        gid = parts[0]
        t = TYPES.get((v.get("type") or "").strip()) or (tuple(parts[2:4]) if len(parts) >= 4 else None)
        if not t or t[0] not in ("spread", "total", "moneyline"):
            raise ValueError("pick the bet type (spread, total or moneyline)")
        bet.update(tier="LOOKAHEAD", game_id=int(gid) if gid.isdigit() else gid, market=t[0], side=t[1],
                   line=num(v.get("line"), "Line", required=t[0] != "moneyline"))
    elif play.startswith("game:"):
        t = TYPES.get((v.get("type") or "").strip())
        if not t:
            raise ValueError("pick the bet type (spread, total or moneyline) for a bet that is not a board play")
        gid = play[5:]
        bet.update(tier="OTHER", game_id=int(gid) if gid.isdigit() else gid, market=t[0], side=t[1],
                   line=num(v.get("line"), "Line", required=t[0] != "moneyline"))
    else:
        bet.update(tier="OTHER", game_id=None, market=None, side=None, line=num(v.get("line"), "Line", required=False))
    return folder, bet


def main():
    ev = json.loads(pathlib.Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    issue = ev["issue"]
    out = {"ok": "false", "board": "", "close": "false"}
    try:
        folder = FOLDER.get((parse_form(issue.get("body")).get("board") or "").strip().upper())
        if ev.get("comment"):   # "remove" comment from the owner
            if not folder:
                raise ValueError("this issue has no board, nothing to remove")
            f = ROOT / folder / "board" / "mybets" / f"{issue['number']}.json"
            if f.exists():
                f.unlink()
            reply = "Removed this bet from My Bets."
            out.update(ok="true", board=folder, close="true")
        else:
            folder, bet = record(issue)
            d = ROOT / folder / "board" / "mybets"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{issue['number']}.json").write_text(json.dumps(bet, indent=1), encoding="utf-8")
            win = bet["amount"] * (bet["odds"] / 100 if bet["odds"] > 0 else 100 / abs(bet["odds"]))
            price = f"{bet['odds']:+d}"
            vs = ""
            if bet.get("board_line") is not None and bet.get("line") is not None and bet["market"] in ("spread", "total"):
                diff = (bet["line"] - bet["board_line"]) * (-1 if bet.get("side") == "over" else 1)
                vs = f" Board logged {bet['board_line']:+g}" if bet["market"] == "spread" else f" Board logged {bet['board_line']:g}"
                vs += "; same number." if abs(diff) < 0.01 else f"; you got {abs(diff):g} pt{'s' if abs(diff) != 1 else ''} {'better' if diff > 0 else 'worse'}."
            graded = "" if bet.get("market") else "\n\nThis bet isn't linked to a board game, so it can't be graded automatically."
            reply = (f"Logged on My Bets: **{bet['pick']}** {price} at {bet['book']}, ${bet['amount']:g} to win ${win:.2f}.{vs}"
                     f"{graded}\n\nEdit this issue to change it; comment `remove` to delete it.")
            out.update(ok="true", board=folder, close="true")
    except (ValueError, KeyError) as e:
        reply = f"Couldn't log this bet: {e}. Edit the issue to fix it and it will be logged."
    pathlib.Path("_mybets_reply.md").write_text(reply, encoding="utf-8")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as fh:
        for k, val in out.items():
            fh.write(f"{k}={val}\n")
    print(reply)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--combine"]:
        print("combined", combine(sys.argv[2], sys.argv[3]), "bets")
    else:
        main()
