"""Not-available flag: the owner says a board play isn't on offer at any of their books (the "Not available" button on a
Bets card opens a "Flag" issue). Run by .github/workflows/flags.yml.

  python tools/flag_play.py                     read the issue event ($GITHUB_EVENT_PATH): check the play, write
                                                <board>/board/flags/<issue>.json, the reply (_flag_reply.md) and the step
                                                outputs (ok, board, verdict, workflow, close)
  python tools/flag_play.py --combine cfb OUT   write OUT/flags.json: every flag of a board, newest first

The check reads FanDuel's own site (the public JSON its pages load, no login) for the game's main spread or total, and
the numbers the board's feed showed (the published state.json). DraftKings and BetMGM refuse automated requests, so
they are seen only through the feed. Verdict "void" unless FanDuel offers the logged number or better right now;
a "void anyway" comment from the owner forces it. On their next run the engines void a "void" flag's ledger entry and
ignore every book quote at that number or better for that side (a different number can still log).
Standard library only. BOARDS_ROOT points the script at another copy of the repo (dry runs)."""
import ast, datetime as dt, json, os, pathlib, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from log_bet import parse_form   # same "### Label" issue layout as a logged bet

HERE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("BOARDS_ROOT") or HERE)
SITE = "https://kcrampton109-beep.github.io/Boards"
FOLDER = {"CFB": "cfb", "NFL": "nfl"}
WORKFLOW = {"cfb": "cfb.yml", "nfl": "nfl.yml"}
FD_API, FD_KEY = "https://sbapi.nj.sportsbook.fanduel.com/api", "FhMFpcPWXMeyZxOx"   # FanDuel's public web-app key
FD_PAGE = {"cfb": "ncaaf", "nfl": "nfl"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "application/json"}
BOOKN = {"DK": "DraftKings", "FD": "FanDuel", "MGM": "BetMGM"}
# college names that FanDuel spells differently from the board (both sides go through canon())
ALIAS = {"miami oh": "miami ohio", "miami florida": "miami", "miami fl": "miami","fiu": "florida international", "connecticut": "uconn", "ul monroe": "louisiana monroe",
         "ulm": "louisiana monroe", "sam houston state": "sam houston", "southern miss": "southern mississippi",
         "umass": "massachusetts", "app state": "appalachian state", "ole miss": "mississippi", "pitt": "pittsburgh",
         "utsa": "ut san antonio", "nc state": "north carolina state", "louisiana lafayette": "louisiana",
         "ul lafayette": "louisiana"}


def get_json(url, timeout=40):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def canon(name):
    s = str(name or "").lower().replace("&", " and ").replace("é", "e")
    s = re.sub(r"[().'’`]", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ALIAS.get(s, s)


def nfl_nicknames():
    """Nickname -> board abbreviation, read from nfl/live.py (NICK) without importing it (it needs pandas)."""
    src = (HERE / "nfl" / "live.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "NICK" for t in node.targets):
            return ast.literal_eval(node.value)
    return {}


def fmt_line(v):
    return "PK" if abs(v) < 0.01 else (f"+{v:g}" if v > 0 else f"{v:g}".replace("-", "−"))


def fmt_odds(o):
    return "" if o is None else (f"+{int(o)}" if o > 0 else f"{int(o)}".replace("-", "−"))


def side_text(entry, line):
    if entry["market"] == "total":
        return f"{'Under' if entry['side'] == 'under' else 'Over'} {line:g}"
    team = re.sub(r"\s[-+−]?[\d.]+$|\s?PK$", "", str(entry.get("pick") or "")).strip() or entry.get("team") or entry["side"]
    return f"{team} {fmt_line(line)}"


# ---------------------------------------------------------------- FanDuel's own site
def fd_check(folder, game, entry):
    """What FanDuel's site offers on the play's side: {listed, offered, line, odds, name} or {error}."""
    try:
        page = get_json(f"{FD_API}/content-managed-page?" + urllib.parse.urlencode(
            {"page": "CUSTOM", "customPageId": FD_PAGE[folder], "_ak": FD_KEY, "timezone": "America/New_York"}))
    except Exception as e:
        return dict(error=f"FanDuel's site didn't answer ({type(e).__name__})")
    att = page.get("attachments") or {}
    nick = nfl_nicknames() if folder == "nfl" else {}
    key = (lambda n: nick.get(str(n).strip().split(" ")[-1])) if folder == "nfl" else canon
    want = (key(game["away"]) if folder == "cfb" else game["away"], key(game["home"]) if folder == "cfb" else game["home"])
    ev = None
    for e in (att.get("events") or {}).values():
        parts = re.split(r" @ | v | vs ", str(e.get("name", "")))
        if len(parts) == 2 and {key(parts[0]), key(parts[1])} == set(want) and None not in want:
            ev = e
            break
    if ev is None:
        return dict(listed=False)
    mtype = "MATCH_HANDICAP_(2-WAY)" if entry["market"] == "spread" else None
    def main_market(markets):
        for m in markets:
            if m.get("eventId") != ev["eventId"]:
                continue
            if entry["market"] == "spread" and m.get("marketType") == mtype:
                return m
            if entry["market"] == "total" and str(m.get("marketName", "")).lower() in ("total points", "total"):
                return m
        return None
    m = main_market((att.get("markets") or {}).values())
    if m is None:
        try:
            ep = get_json(f"{FD_API}/event-page?" + urllib.parse.urlencode({"_ak": FD_KEY, "eventId": ev["eventId"]}))
            m = main_market(((ep.get("attachments") or {}).get("markets") or {}).values())
        except Exception as e:
            return dict(listed=True, error=f"FanDuel's game page didn't answer ({type(e).__name__})", name=ev.get("name"))
    if m is None or m.get("marketStatus") != "OPEN":
        return dict(listed=True, offered=False, name=ev.get("name"))
    our = key(game[entry["side"]]) if entry["market"] == "spread" and folder == "cfb" else game.get(entry["side"])
    for r in m.get("runners") or []:
        if r.get("runnerStatus") != "ACTIVE" or r.get("handicap") is None:
            continue
        name = r.get("runnerName", "")
        hit = name.strip().lower() == entry["side"] if entry["market"] == "total" else key(name) == our
        if hit:
            o = ((r.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt")
            return dict(listed=True, offered=True, line=float(r["handicap"]), odds=o, name=ev.get("name"))
    return dict(listed=True, offered=False, name=ev.get("name"))


# ---------------------------------------------------------------- the board's feed (published state.json)
def feed_check(folder, entry):
    """The numbers the board last saw for this side at DK / FD / BetMGM: {as_of, books: {DK: (line, odds)}} or {}."""
    try:
        st = get_json(f"{SITE}/{folder}/state.json?x={int(time.time())}")
    except Exception:
        return {}, None
    game = next((g for g in st.get("games", []) if str(g.get("game_id")) == str(entry["game_id"])), None)
    if game is None:
        return {"as_of": st.get("generated")}, None
    books = {}
    for b in ("DK", "FD", "MGM"):
        v = (game.get("books") or {}).get(b) or {}
        if entry["market"] == "spread":
            line = v.get("sp_home") if entry["side"] == "home" else v.get("sp_away")
            if line is None and entry["side"] == "away" and v.get("sp_home") is not None:
                line = -v["sp_home"]
            odds = v.get(f"sp_{entry['side']}_odds")
        else:
            line = v.get("tot", v.get("total"))
            odds = v.get(f"{entry['side']}_odds")
        if line is not None:
            books[b] = (line, odds)
    return {"as_of": st.get("generated"), "books": books}, game


def at_least_as_good(entry, line):
    """True when `line` is the logged number or better for the play's side."""
    if entry["market"] == "total":
        return line >= entry["line"] - 1e-9 if entry["side"] == "under" else line <= entry["line"] + 1e-9
    return line >= entry["line"] - 1e-9


# ---------------------------------------------------------------- main
def check(issue, force):
    v = parse_form(issue.get("body"))
    board = (v.get("board") or "").strip().upper()
    if board not in FOLDER:
        raise ValueError("Board must be CFB or NFL")
    folder = FOLDER[board]
    play = (v.get("play") or "").strip()
    ledger = json.loads((ROOT / folder / "board" / "ledger.json").read_text(encoding="utf-8"))
    entry = next((e for e in reversed(ledger) if e["id"] == play and e.get("status") == "open"), None)
    if entry is None:
        raise ValueError("this play isn't open on the board any more (graded, voided or kicked off)")
    if entry["market"] not in ("spread", "total") or entry.get("line") is None:
        raise ValueError("only spreads and totals can be flagged")
    kick = entry.get("kickoff_iso") or entry.get("kickoff")
    if kick and dt.datetime.fromisoformat(str(kick).replace(" ", "T")) <= dt.datetime.now(dt.timezone.utc):
        raise ValueError("this game has already kicked off")

    feed, game = feed_check(folder, entry)
    fd = fd_check(folder, game, entry) if game else dict(error="the board's feed no longer lists this game")
    logged = side_text(entry, entry["line"])
    parts = []
    if fd.get("error"):
        parts.append(f"Couldn't read FanDuel: {fd['error']}.")
    elif not fd.get("listed"):
        parts.append("FanDuel's site doesn't list this game.")
    elif not fd.get("offered"):
        parts.append("FanDuel lists the game but has no open main " + ("spread" if entry["market"] == "spread" else "total") + " on it.")
    else:
        parts.append(f"FanDuel has {side_text(entry, fd['line'])} {fmt_odds(fd.get('odds'))}".rstrip() + " right now.")
    if feed.get("books"):
        nums = ", ".join(f"{BOOKN[b]} {fmt_line(l) if entry['market'] == 'spread' else f'{l:g}'}" for b, (l, o) in feed["books"].items())
        parts.append(f"The board's feed{' (' + feed['as_of'] + ')' if feed.get('as_of') else ''} had {nums}.")
    parts.append("DraftKings and BetMGM can't be checked directly.")
    found = bool(fd.get("offered") and at_least_as_good(entry, fd["line"]))
    verdict = "found" if found and not force else "void"
    reason = " ".join(parts)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    flag = dict(id=issue["number"], issue=issue.get("html_url"), board=board, play=play, game_id=entry["game_id"],
                market=entry["market"], side=entry["side"], line=entry["line"], odds=entry.get("odds"), book=entry.get("book"),
                pick=entry.get("pick"), tier=entry.get("tier"), matchup=entry.get("matchup"), kickoff=kick, verdict=verdict,
                forced=bool(force), reason=reason, fanduel=fd, feed=feed, notes=v.get("notes") or "",
                flagged_at=issue.get("created_at"), checked_at=now)
    if verdict == "void":
        head = ("Voided at your request (void anyway). " if force and found else "Voided as not available. ")
        reply = (f"{head}{reason}\n\n**{logged}** leaves the record on the board's next update (started now) and won't log "
                 f"again at this number or better; a different number can still log.")
    else:
        reply = (f"Not voided: {reason}\n\nIf you still can't get **{logged}** at any of your books, comment `void anyway` "
                 f"and it will be voided.")
    return folder, flag, reply, verdict


def combine(folder, out):
    flags = [json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / folder / "board" / "flags").glob("*.json")]
    flags.sort(key=lambda f: (f.get("checked_at") or "", f.get("id") or 0), reverse=True)
    pathlib.Path(out).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(out) / "flags.json").write_text(json.dumps(flags, indent=1), encoding="utf-8")
    return len(flags)


def main():
    ev = json.loads(pathlib.Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    issue = ev["issue"]
    force = bool(ev.get("comment")) and "void anyway" in (ev["comment"].get("body") or "").lower()
    out = {"ok": "false", "board": "", "verdict": "", "workflow": "", "close": "false"}
    try:
        folder, flag, reply, verdict = check(issue, force)
        d = ROOT / folder / "board" / "flags"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{issue['number']}.json").write_text(json.dumps(flag, indent=1), encoding="utf-8")
        out.update(ok="true", board=folder, verdict=verdict, workflow=WORKFLOW[folder], close="true" if verdict == "void" else "false")
    except (ValueError, KeyError) as e:
        reply = f"Couldn't flag this play: {e}."
        out.update(close="true")
    pathlib.Path("_flag_reply.md").write_text(reply, encoding="utf-8")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as fh:
        for k, val in out.items():
            fh.write(f"{k}={val}\n")
    print(reply)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["--combine"]:
        print("combined", combine(sys.argv[2], sys.argv[3]), "flags")
    else:
        main()
