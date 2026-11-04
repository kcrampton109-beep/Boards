"""Daily social post for a faceless picks account, built from both boards' ledgers.

On a day with money-tier plays (CFB STRONG/FCS/WEATHER, NFL STRONG/WEATHER) it shows each play's logged line next to
the current consensus line, so the post shows the edge already captured without giving away an early number. On a day
without plays it shows the previous play day's results. Writes 1080x1350 PNGs (X allows 4 images per post), a caption
under 280 characters (X's limit without Premium) and a phone page (index.html) to save the images and copy the caption.

usage: python social/daily_post.py --cfb-ledger L --nfl-ledger L --cfb-state S --nfl-state S --out DIR [--date YYYY-MM-DD]
The last line printed is JSON: {"post": false} when there is nothing to post, else the post's summary.
"""
import argparse
import datetime as dt
import glob
import html
import json
import os
import shutil
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

ET = ZoneInfo("America/New_York")
BRAND = "Opener Lab"   # placeholder account name: change once the owner picks one
HANDLE = ""            # e.g. "@openerlab": shown next to the brand when set
LINK = ""              # public record link for the caption (blank: "link in bio")
TIERS = {"cfb": {"STRONG": "CFB", "FCS": "FCS", "WEATHER": "WIND"},
         "nfl": {"STRONG": "NFL", "WEATHER": "WIND"}}
ROWS_PER_IMAGE = 10
MAX_IMAGES = 4
W, H = 1080, 1350
X0, X1 = 60, W - 60

BG, CARD, RULE = (13, 17, 23), (24, 30, 38), (45, 52, 61)
TEXT, MUTED, DIM = (240, 246, 252), (150, 160, 170), (110, 118, 128)
GREEN, RED, ACCENT = (63, 185, 80), (248, 96, 88), (88, 166, 255)
CHIP = {"CFB": (88, 166, 255), "FCS": (176, 132, 250), "NFL": (52, 196, 170), "WIND": (226, 168, 52)}


# ---------- fonts: Inter when installed (the workflow installs fonts-inter), else DejaVu, else Windows fonts ----------
def _find_fonts():
    def first(*patterns):
        for p in patterns:
            hits = sorted(glob.glob(p, recursive=True))
            if hits:
                return hits[0]
        return None
    inter = [first(f"/usr/share/fonts/**/Inter-{w}.otf", f"/usr/share/fonts/**/Inter-{w}.ttf")
             for w in ("Regular", "SemiBold", "Bold")]
    if all(inter):
        return inter
    for trio in (("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                 ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/segoeuib.ttf")):
        if all(os.path.exists(p) for p in trio):
            return list(trio)
    return [None, None, None]


FONT_FILES = _find_fonts()
_cache = {}


def font(weight, size):
    """weight 0 regular, 1 semibold, 2 bold."""
    key = (weight, size)
    if key not in _cache:
        path = FONT_FILES[weight]
        _cache[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
    return _cache[key]


# ---------- data ----------
def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_time(s):
    try:
        t = dt.datetime.fromisoformat(str(s))
        return (t if t.tzinfo else t.replace(tzinfo=ET)).astimezone(ET)
    except (TypeError, ValueError):
        return None


def money_entries(ledgers):
    out = []
    for board, ledger in ledgers.items():
        for e in ledger:
            label = TIERS[board].get(e.get("tier"))
            if not label or e.get("status") == "void":
                continue
            ko = parse_time(e.get("kickoff_iso") if board == "nfl" else e.get("kickoff"))
            if ko is None:
                continue
            out.append(dict(e, _board=board, _label=label, _ko=ko))
    return out


def current_line(e, games):
    g = games.get((e["_board"], str(e.get("game_id"))))
    if not g:
        return None
    if e.get("market") == "spread":
        home = g.get("cons_line" if e["_board"] == "cfb" else "cons_spread")
        if home is None:
            return None
        return home if e.get("side") == "home" else -home
    if e.get("market") == "total":
        return g.get("cons_total")
    return None


def gained(e, now):
    """Points the line has moved our way since the play was logged (same sign convention as the ledger's clv)."""
    if now is None or e.get("line") is None:
        return None
    return (now - e["line"]) if e.get("side") == "over" else (e["line"] - now)


def fmt_line(e, x):
    if x is None:
        return "-"
    if e.get("market") == "spread":
        return "PK" if x == 0 else f"{x:+g}"
    return f"{x:g}"


def day_label(d):
    return f"{d:%a} {d:%b} {d.day}"


def record(graded):
    w = sum(e["status"] == "W" for e in graded)
    l = sum(e["status"] == "L" for e in graded)
    p = sum(e["status"] == "P" for e in graded)
    units = sum(e.get("units") or 0 for e in graded)
    clv = [e["clv"] for e in graded if e.get("clv") is not None]
    beat = round(100 * sum(c > 0 for c in clv) / len(clv)) if clv else None
    return {"w": w, "l": l, "p": p, "units": units, "beat": beat, "n": len(graded)}


def wl(r):
    return f"{r['w']}-{r['l']}" + (f"-{r['p']}" if r["p"] else "")


# ---------- drawing ----------
def fit(d, text, f, maxw):
    if d.textlength(text, font=f) <= maxw:
        return text
    while text and d.textlength(text + "…", font=f) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"


def chip(d, x, cy, label):
    f = font(2, 22)
    tw = d.textlength(label, font=f)
    col = CHIP[label]
    d.rounded_rectangle((x, cy - 18, x + tw + 24, cy + 18), radius=8, fill=tuple(int(c * 0.22) for c in col))
    d.text((x + 12, cy), label, font=f, fill=col, anchor="lm")
    return x + tw + 24


def badge(d, xr, cy, text, col, width=118):
    d.rounded_rectangle((xr - width, cy - 26, xr, cy + 26), radius=10, fill=tuple(int(c * 0.22) for c in col))
    d.text((xr - width / 2, cy), text, font=font(2, 30), fill=col, anchor="mm")


def tone(x):
    if x is None:
        return MUTED
    return GREEN if x > 0 else RED if x < 0 else MUTED


def header(d, title, page, pages, subtitle):
    d.text((X0, 78), BRAND.upper(), font=font(2, 28), fill=ACCENT, anchor="ls")
    if HANDLE:
        bx = X0 + d.textlength(BRAND.upper(), font=font(2, 28)) + 14
        d.text((bx, 78), HANDLE, font=font(0, 26), fill=MUTED, anchor="ls")
    if pages > 1:
        d.text((X1, 78), f"{page}/{pages}", font=font(1, 26), fill=MUTED, anchor="rs")
    d.text((X0, 148), title, font=font(2, 54), fill=TEXT, anchor="ls")
    d.text((X0, 194), subtitle, font=font(0, 27), fill=MUTED, anchor="ls")


def play_row(d, e, cy, games, target):
    now = current_line(e, games)
    gain = gained(e, now)
    x = chip(d, X0, cy - 16, e["_label"]) + 16
    right_text = X1 - 118 - 22
    left_max = right_text - 250 - x
    d.text((x, cy - 16), fit(d, e.get("pick", ""), font(2, 33), left_max), font=font(2, 33), fill=TEXT, anchor="lm")
    sub = f"{e.get('matchup', '')} · {e['_ko']:%I:%M %p}".replace(" 0", " ")
    d.text((X0, cy + 22), fit(d, sub, font(0, 23), right_text - 240 - X0), font=font(0, 23), fill=MUTED, anchor="lm")
    flagged = parse_time(e.get("flagged"))
    when = (f"{flagged:%a}" if flagged and (target - flagged.date()).days < 6 else
            f"{flagged:%b} {flagged.day}" if flagged else "")
    d.text((right_text, cy - 16), f"now {fmt_line(e, now)}", font=font(1, 30), fill=TEXT, anchor="rm")
    d.text((right_text, cy + 22), f"logged {when} {fmt_line(e, e.get('line'))}".replace("  ", " "),
           font=font(0, 23), fill=MUTED, anchor="rm")
    badge(d, X1, cy, "-" if gain is None else "0.0" if gain == 0 else f"{gain:+.1f}", tone(gain))


def result_row(d, e, cy):
    x = chip(d, X0, cy - 16, e["_label"]) + 16
    right_text = X1 - 118 - 22
    d.text((x, cy - 16), fit(d, e.get("pick", ""), font(2, 33), right_text - 200 - x), font=font(2, 33), fill=TEXT,
           anchor="lm")
    d.text((X0, cy + 22), fit(d, e.get("final") or e.get("matchup", ""), font(0, 23), right_text - 200 - X0),
           font=font(0, 23), fill=MUTED, anchor="lm")
    u = e.get("units") or 0
    d.text((right_text, cy - 16), f"{u:+.2f}u", font=font(1, 30), fill=tone(u), anchor="rm")
    clv = e.get("clv")
    d.text((right_text, cy + 22), "" if clv is None else f"CLV {clv:+.1f}", font=font(0, 23), fill=MUTED, anchor="rm")
    st = e["status"]
    badge(d, X1, cy, st, GREEN if st == "W" else RED if st == "L" else MUTED)


def footer(d, h, season, since, note):
    y0, gap = h - 270, 20
    tw = (X1 - X0 - 2 * gap) / 3
    tiles = [("Live record", wl(season), TEXT), ("Units", f"{season['units']:+.1f}", tone(season["units"])),
             ("Beat the close", "-" if season["beat"] is None else f"{season['beat']}%", TEXT)]
    for i, (lab, val, col) in enumerate(tiles):
        x = X0 + i * (tw + gap)
        d.rounded_rectangle((x, y0, x + tw, y0 + 124), radius=16, fill=CARD)
        d.text((x + 22, y0 + 38), lab, font=font(0, 24), fill=MUTED, anchor="ls")
        d.text((x + 22, y0 + 100), val, font=font(2, 46), fill=col, anchor="ls")
    d.text((X0, y0 + 168), fit(d, f"Live since {since} · 1 unit per play" + (f" · {note}" if note else ""),
                                font(0, 24), X1 - X0), font=font(0, 24), fill=MUTED, anchor="ls")
    d.text((X0, h - 34), "21+ · Not betting advice · Gambling problem? Call 1-800-GAMBLER", font=font(0, 21), fill=DIM,
           anchor="ls")


def render(rows, kind, title, subtitle, season, since, note, games, target, out_dir):
    pages = min(MAX_IMAGES, -(-len(rows) // ROWS_PER_IMAGE))
    per = min(ROWS_PER_IMAGE, -(-len(rows) // pages))   # balanced: 16 plays -> 8 + 8
    chunks = [rows[i * per:(i + 1) * per] for i in range(pages)]
    # as tall as the rows need (X shows 16:9 to 4:5 uncropped); every image of a post the same size
    h = max(700, min(H, 530 + 82 * per))
    shown = sum(len(c) for c in chunks)
    files = []
    for n, chunk in enumerate(chunks, 1):
        im = Image.new("RGB", (W, h), BG)
        d = ImageDraw.Draw(im)
        more = len(rows) - shown if n == len(chunks) else 0
        header(d, title, n, len(chunks), subtitle + (f" {more} more on the public log." if more else ""))
        if kind == "plays":
            d.text((X1, 232), "logged → now · points gained", font=font(0, 22), fill=DIM, anchor="rs")
        top, row_h = 250, 82
        for i, e in enumerate(chunk):
            cy = top + i * row_h + row_h / 2
            if i:
                d.line((X0, top + i * row_h, X1, top + i * row_h), fill=RULE, width=1)
            (play_row(d, e, cy, games, target) if kind == "plays" else result_row(d, e, cy))
        footer(d, h, season, since, note)
        name = f"post_{n}.png"
        im.save(os.path.join(out_dir, name), optimize=True)
        files.append(name)
    return files


# ---------- caption + phone page ----------
def caption(kind, day, rows, season, result):
    boards = sorted({e["_board"] for e in rows}, key=["cfb", "nfl"].index)
    tags = " ".join({"cfb": "#CFB", "nfl": "#NFL"}[b] for b in boards)
    link = LINK or "link in bio"
    live = f"Live record {wl(season)}, {season['units']:+.1f}u" + (f", {season['beat']}% beat the close" if season["beat"] is not None else "")
    if kind == "plays":
        n = len(rows)
        text = (f"Model plays for {day}: {n} play{'s' if n != 1 else ''}, logged at the opening line and graded in public. "
                f"{live}. Full log: {link}. 21+ {tags}")
    else:
        text = (f"Results for {day}: {wl(result)}, {result['units']:+.1f}u. Every play was logged before kickoff. "
                f"{live}. Full log: {link}. 21+ {tags}")
    return text if len(text) <= 280 else text[:279] + "…"


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Today's post</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--text:#14171a;--muted:#5b6570;--line:#d9dee3;--accent:#1d6fd6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d1117;--card:#161b22;--text:#f0f6fc;--muted:#9aa4ae;--line:#30363d;--accent:#58a6ff}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:560px;margin:0 auto;padding:16px}}
h1{{font-size:20px;margin:4px 0 2px}} p{{margin:0 0 12px;color:var(--muted);font-size:14px}}
.cap{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:12px 0}}
textarea{{width:100%;box-sizing:border-box;min-height:120px;border:0;background:transparent;color:var(--text);font:inherit;resize:vertical}}
button{{font:inherit;padding:10px 16px;border-radius:10px;border:1px solid var(--accent);background:var(--accent);color:#fff}}
img{{width:100%;border-radius:12px;margin:0 0 12px;display:block}}
</style></head><body><main>
<h1>{title}</h1><p>{count} · made {made}. Long-press each image to save it, then copy the caption.</p>
<div class="cap"><textarea id="cap" readonly>{caption}</textarea>
<button id="copy" type="button">Copy caption</button> <span id="ok" style="color:var(--muted);font-size:14px"></span></div>
{images}
</main><script>
document.getElementById('copy').onclick=async()=>{{const t=document.getElementById('cap');
try{{await navigator.clipboard.writeText(t.value)}}catch(e){{t.select();document.execCommand('copy')}}
document.getElementById('ok').textContent='Copied';}};
</script></body></html>
"""


def page(title, count, cap, files):
    imgs = "\n".join(f'<img src="{f}?v=__V__" alt="Post image {i}">' for i, f in enumerate(files, 1))
    made = dt.datetime.now(ET).strftime("%a %b %d, %I:%M %p ET").replace(" 0", " ")
    return PAGE.format(title=html.escape(title), count=html.escape(count), made=made, caption=html.escape(cap),
                       images=imgs)


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    for k in ("cfb-ledger", "nfl-ledger", "cfb-state", "nfl-state", "out"):
        ap.add_argument(f"--{k}", required=True)
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    target = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(ET).date()

    entries = money_entries({"cfb": load(a.cfb_ledger), "nfl": load(a.nfl_ledger)})
    games = {}
    for board, path in (("cfb", a.cfb_state), ("nfl", a.nfl_state)):
        for g in load(path).get("games", []):
            games[(board, str(g.get("game_id")))] = g

    graded = [e for e in entries if e.get("status") in ("W", "L", "P") and e["_ko"].date() < target]
    season = record(graded)
    since = min((e["_ko"] for e in graded), default=None)
    since = f"{since:%b} {since.day}" if since else "-"
    last_day = max((e["_ko"].date() for e in graded), default=None)
    last = [e for e in graded if e["_ko"].date() == last_day]
    last_rec = record(last) if last else None
    order = lambda e: (e["_ko"], ["cfb", "nfl"].index(e["_board"]), e.get("pick", ""))

    today = sorted((e for e in entries if e["_ko"].date() == target and e.get("status") not in ("void",)), key=order)
    if today:
        kind, rows, day = "plays", today, day_label(target)
        title, subtitle = f"Model plays · {day}", "Logged at the opening line. Graded in public."
        note = f"{day_label(last_day)}: {wl(last_rec)}, {last_rec['units']:+.1f}u" if last_rec else ""
    elif last and (target - last_day).days == 1:
        kind, rows, day = "results", sorted(last, key=order), day_label(last_day)
        title, subtitle = f"Results · {day}", "Every play logged before kickoff."
        note = ""
    else:
        print(json.dumps({"post": False, "date": str(target)}))
        return

    if os.path.isdir(a.out):
        shutil.rmtree(a.out)
    os.makedirs(a.out)
    files = render(rows, kind, title, subtitle, season, since, note, games, target, a.out)
    cap = caption(kind, day, rows, season, last_rec)
    count = f"{len(rows)} play{'s' if len(rows) != 1 else ''}, {len(files)} image{'s' if len(files) != 1 else ''}"
    with open(os.path.join(a.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(title, count, cap, files))
    with open(os.path.join(a.out, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(cap + "\n")
    notify = f"{day}: {count}. Tap to save the images and copy the caption."
    summary = {"post": True, "date": str(target), "kind": kind, "rows": len(rows), "images": files, "caption": cap,
               "notify": notify, "fonts": FONT_FILES[0]}
    with open(os.path.join(a.out, "post.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
