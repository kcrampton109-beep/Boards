# CBB backtest: does a walk-forward power rating beat the opening line?

Research only, Oct 28 2026. Three seasons with openers (2023-24, 2024-25, 2025-26; about 17,200 D1-vs-D1 games),
two earlier seasons (2021-22, 2022-23) to warm up the ratings. Every table is in [tables.md](tables.md), produced by
`evaluate.py`. CLV = the opener number vs the DK / FD / BetMGM median closing number, in points (+ = you beat the close).
ATS is graded at the opener at -110.

## Bottom line

- **A straight port of the CFB model is not enough.** The results model (ridge on points scored, as in
  `cfb/model.py`) gets only +0.5 pts CLV and goes 51% ATS: a loser at -110.
- **A "market ratings" model clearly beats the opener.** It uses the same ridge, but each team's target is the points
  that earlier games' **closing lines** projected it to score. From December on it beats the opener in every season:
  spreads with a gap of 4+ get CLV +1.19, beat the close 66% of the time and go 54.2% ATS. Totals with a gap of 6+ get
  CLV +2.19, beat the close 77% and go 55.8% ATS.
- **Averaging the two models ("blend") is best per play.** December on, spreads at gap 3+ get CLV +1.32, beat the
  close 68% and go 55.7% ATS, about 2 plays a day. Totals at gap 5+ get CLV +2.29, beat the close 77% and go 56.2%
  ATS, about 3 a day.
- **November is weak for every model**: CLV about +0.5 and only half the plays beat the close. The early-season prior
  is last season's ratings, and rosters turn over too much for that.
- **Caveat: these CLV numbers are probably too high for what the owner can get.** They are measured from Action
  Network's opener (book 30), not from DK/FD/MGM's own first numbers. In the live CFB snapshots, DK/FD/MGM's first
  spread is the same as book 30's opener only 20-30% of the time, and it already holds about half of the opener-to-close
  move. If CBB works the same way, live CLV could be about half of what is shown here. Only a paper board can measure this.

## Recommendation

**Yes to a paper board for 2026-27** (tip-off Nov 2). No money tiers or stakes are proposed here; that is the owner's
call once live CLV is in.

- **Model:** blend = the average of the market-ratings model and the results model (both are cheap, daily refits).
- **What to log:** spreads at a gap of 3+ against the DK/FD/MGM number on screen when the play appears (gap 4+ is
  the cleaner subset), and totals at a gap of 5+ (6+ is the cleaner subset). That is about 5 plays a day in all.
- **November:** log it but keep it apart (research only), or require a gap of 6+. It shouldn't count toward the verdict.
- **Conferences:** no filter. Low-major spreads are the weakest group (CLV +1.1, ATS 51%). Unlike CFB's FCS tier,
  the low-profile games are not where the edge is; for totals all three groups work.
- **The paper board's main job:** record the first DK/FD/MGM spread and total and when it appeared, then measure CLV
  from that number. Openers appear roughly 6-13 hours before tip, from the night before to the morning of the game, so
  snapshots every 30 minutes overnight would be needed. The current `:07/:37` cron pattern would do.

## Headline tables

### Spreads, all periods (3 seasons)

| model | gap | n | CLV | beat close % | ATS % | units | plays/day |
|---|---:|---:|---:|---:|---:|---:|---:|
| results (pts) | >= 3 | 2723 | 0.57 | 51.8 | 51.0 | -70.3 | 6.1 |
| results (pts) | >= 5 | 711 | 0.67 | 51.8 | 53.6 | +16.5 | 1.6 |
| market | >= 3 | 3694 | 0.77 | 57.3 | 53.5 | +81.5 | 8.3 |
| market | >= 4 | 1745 | 1.00 | 61.8 | 53.7 | +42.4 | 3.9 |
| market | >= 5 | 764 | 1.19 | 64.4 | 54.7 | +34.2 | 1.7 |
| blend | >= 3 | 1808 | 0.93 | 59.5 | 53.9 | +52.1 | 4.1 |
| blend | >= 4 | 718 | 1.06 | 60.9 | 54.8 | +32.5 | 1.6 |

### Spreads, December onward, by season

| model | gap | season | n | CLV | beat close % | ATS W-L-P | ATS % | units |
|---|---:|---|---:|---:|---:|---|---:|---:|
| market | >= 4 | 2023-24 | 385 | 1.26 | 69.1 | 205-175-5 | 53.9 | +11.4 |
| market | >= 4 | 2024-25 | 344 | 1.02 | 64.2 | 182-161-1 | 53.1 | +4.5 |
| market | >= 4 | 2025-26 | 403 | 1.27 | 64.8 | 223-180-0 | 55.3 | +22.7 |
| blend | >= 3 | 2023-24 | 282 | 1.50 | 72.6 | 154-123-5 | 55.6 | +17.0 |
| blend | >= 3 | 2024-25 | 327 | 1.05 | 65.4 | 168-157-2 | 51.7 | -4.3 |
| blend | >= 3 | 2025-26 | 313 | 1.43 | 67.1 | 188-125-0 | 60.1 | +45.9 |
| blend | >= 4 | all | 251 | 1.87 | 74.1 | 149-100-2 | 59.8 | +35.5 |

### Spreads by month (blend, gap >= 3)

| period | n | CLV | beat close % | ATS % | units |
|---|---:|---:|---:|---:|---:|
| Nov | 886 | 0.53 | 50.5 | 52.0 | -6.5 |
| Dec | 347 | 1.41 | 68.3 | 58.7 | +40.8 |
| Jan-Mar (regular season) | 483 | 1.28 | 70.3 | 53.9 | +14.4 |
| Postseason (conf. tournaments + NCAA/NIT etc.) | 92 | 1.13 | 56.5 | 54.3 | +3.5 |

The market model at gap 4+ shows the same shape: Nov +0.64 CLV and 53.8% beat close; Dec +1.27 and 65%;
Jan-Mar +1.12 and 67%; postseason +1.35 and 63%.

### Spreads by conference group, December onward

The group is set by the higher-profile team: a game with any power-6 team counts as power 6.

| group | blend gap>=3: n | CLV | beat % | ATS % | market gap>=4: n | CLV | beat % | ATS % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| power 6 | 368 | 1.42 | 68.5 | 55.6 | 378 | 1.34 | 66.9 | 55.1 |
| mid-major | 239 | 1.41 | 69.0 | 62.0 | 343 | 1.26 | 70.3 | 56.4 |
| low-major | 315 | 1.12 | 67.2 | 51.1 | 411 | 0.99 | 61.8 | 51.5 |

Power 6 = ACC, Big East, Big Ten, Big 12, SEC, and the Pac-12 through 2023-24. Mid-major = the next 10 conferences by
average rating: Mountain West, WCC, AAC, A-10, MVC, C-USA, WAC, Ivy, Big West, Sun Belt. Low-major = the other 16.

### Spreads by gap bucket (market, all periods)

| gap | 2-3 | 3-4 | 4-5 | 5-6 | 6-8 | 8-10 |
|---|---:|---:|---:|---:|---:|---:|
| n | 3275 | 1949 | 981 | 432 | 267 | 53 |
| CLV | 0.46 | 0.57 | 0.85 | 1.15 | 1.28 | 0.76 |
| beat close % | 50.3 | 53.3 | 59.7 | 66.4 | 64.4 | 47.2 |
| ATS % | 52.4 | 53.4 | 52.8 | 54.3 | 54.9 | 50.9 |

CLV rises steadily with the gap up to 8 points. Gaps of 8+ are rare and noisy; they are more likely news (injuries)
that the model can't see.

### Totals, December onward

| model | gap | n | CLV | beat close % | ATS % | units | plays/day |
|---|---:|---:|---:|---:|---:|---:|---:|
| results (pts) | >= 6 | 714 | 1.42 | 62.9 | 54.3 | +26.7 | 2.0 |
| market | >= 5 | 2510 | 1.89 | 74.2 | 55.0 | +123.9 | 6.9 |
| market | >= 6 | 1523 | 2.19 | 77.1 | 55.8 | +98.0 | 4.2 |
| market | >= 8 | 514 | 2.86 | 83.3 | 59.1 | +65.5 | 1.4 |
| blend | >= 5 | 1041 | 2.29 | 76.8 | 56.2 | +76.8 | 2.8 |
| blend | >= 6 | 465 | 2.90 | 81.5 | 59.1 | +60.0 | 1.3 |

Every season is positive. Blend at gap 5+ by season: CLV +2.60 / +1.69 / +2.48, ATS 53.5 / 55.1 / 58.6%. Overs and
unders both work: market gap 6+ all periods gets +1.98 CLV on overs and +1.83 on unders. In November, totals CLV drops
to about +1.2 to +1.6.

## Other things worth knowing

- **When openers post:** for 2024-25 and 2025-26, the opener's timestamp is a median 10-13 hours before tip in
  Nov/Dec and about 7 hours in Jan-Mar. The most common posting time is 11 PM to midnight ET. In 2023-24 the recorded
  time was late morning on game day (median 3-4 hours before tip), so either the feed or the opener's source changed.
  The CBB betting window is the night before to the morning of the game, not CFB's early week.
- **Lines drift toward favourites and home teams** between the open and the close: about +0.2 pts in 2023-24 and
  2024-25, near zero in 2025-26. The xCLV column in tables.md removes that drift, and the market and blend models
  keep almost all their CLV after it.
- **ATS matches what CLV predicts.** One point of spread CLV is worth about 3.6% in cover rate (closing-line RMSE 11.0),
  and one point of totals CLV about 2.4% (RMSE 16.5). Market spreads at gap 4+ (December on) should go about 54.3% from
  CLV +1.19, and went 54.2%. Totals at gap 6+ should go about 55.3%, and went 55.8%. So the results look like real
  closing-line value, not luck. The close looks efficient, and the opener is where the softness is.
- **The models are not more accurate than the opener overall.** The opener's margin RMSE is lower everywhere. The
  edge is in spotting which openers will move.
- **Volume:** about 120 game days a season from December on. The busiest days have 20-50 qualifying plays (opening
  week, Saturdays in conference play).

## How it was done

- **Data:** Action Network v1 scoreboard by date (D1 feed, books 15/30/68/69/71/75/79) for the opener (30) and the
  DK/FD/BetMGM (68/69/75) closes, the same IDs `cfb/engine.py` uses. ESPN's public scoreboard supplies the game list,
  neutral-site flag, historical conference IDs and tournament notes; Action's conference labels are today's, not that
  season's. Of 28,818 final Action games, 28,794 matched ESPN games by date, score and team. Openers exist for 99.6-99.9%
  of D1-vs-D1 games; DK has 98-99%, FD 99.6-99.8% and MGM 94-95%. Raw responses are saved gzipped in `cbb/data/` (gitignored,
  117 MB) and never re-fetched.
- **Results model** (`model.py pts`): the same model as `cfb/model.py` with points only. Team points = mu + h·site +
  O[off] - D[def], with site 0 at neutral sites. The ratings are refit before each game date using earlier dates only,
  with older games decayed (half-life 60 days) and shrunk toward a preseason prior (prior weight 4). The prior is
  roughly 0.55-0.6 × last season's final rating + 0.3-0.5 × the conference average, fitted on earlier seasons.
- **Market model** (`model.py market`): the same ridge, but the target is each team's implied points from earlier
  games' closing spread and total: home = (total - spread) / 2. It uses the DK/FD/MGM median, or Action's consensus
  for 2021-23. Prior weight 1, half-life 30 days.
- **No look-ahead:** settings were picked on 2022-23 by margin RMSE against actual results, before any opener or
  CLV was examined (`tune.py`). The market model was rerun with prior weight 2 / half-life 60, 1 / no decay and 4 / 30:
  CLV at gap 4+ stayed positive in all three (+0.60 to +0.87). The chosen setting was also the best by CLV. Leaving out
  the previous day's games (an opener can post before the previous night's late games close) changes nothing:
  market gap 4+ stays at CLV +1.00 and 61.7% beat close.
- **Line hygiene:** a book whose two spread sides don't mirror is dropped, as in the CFB Oct 25 fix. An opener 10+
  points from the close (or a total 25+ away) is treated as a mislabelled line.

## Reproduce

```
python cbb/research/fetch.py 2021,2025      # ~40 min, skips files already saved
python cbb/research/build.py
python cbb/research/model.py pts 4 60
python cbb/research/model.py market 1 30
python cbb/research/evaluate.py market blend # writes tables.md
python cbb/research/opener_check.py          # CFB snapshots: book 30 opener vs DK/FD/MGM first numbers
```

## Open questions / next steps

1. The paper board measures the real gap between DK/FD/MGM's first numbers and the close. This is the one result that
   decides whether any of this is bettable.
2. A better November prior: returning minutes and transfers, from CollegeBasketballData (needs a key) or Torvik
   preseason ratings.
3. Possession-based (tempo) ratings could sharpen totals. That needs box scores (CBBD key) and is not tested here.
