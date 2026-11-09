# CBB: where the edge is (Nov 4 2026, corrected Nov 8)

> **Correction, Nov 8 2026.** Three things below were overstated when this was written.
>
> 1. **The Nov 1-3 2026 "out of sample" check was not out of sample.** Those live results, split by favourite / underdog
>    and over / under, were looked at first and are what pointed to the November filters. The first real test is Nov 4 on.
> 2. **About half the November favourites' CLV is market drift.** Lines move toward favourites in November whatever the
>    model says. Blind home favourites of 16+ got CLV +1.20 and beat the close 65% in 2023-25. The rule's CLV of +1.32 is
>    +0.68 beyond drift. The Dec-Mar rules are not affected (spreads 4+ +2.03 / +1.81, totals 5+ +2.42 / +2.43).
> 3. **"Every rule profitable in every season" is partly circular.** The November filters were picked from about 50
>    cuts of the same three seasons, partly because they were positive in each.
>
> **Nov 4-7 2026, against the opener (the true test):** favourites 4+ the rule picked covered 17-25 (mean -1.02 vs the
> number), favourites it did not pick covered 65-53 (+2.18), and the picks' CLV beyond size-matched drift was +0.03.
> Overs 5+ went 27-15, CLV +2.05. Small samples, but the November favourites rule has shown no edge live.

Live November started at 107-98, -1.06u, CLV -0.44, with only 28.5% of plays beating the close. The backtest's
November was weak but not that weak (CLV about +0.5). Two model biases explain the gap, and fixing them gives a rule
set that is profitable in every backtest season and in this season's first games. Tables come from `edge.py`. ROI is
at the opener at -110: DraftKings' first number equals Action's opener 89% of the time (live Openers tab, 133 games).

## Reverse line movement (Nov 8 2026, `rlm.py`)

Graded at the closing number at -110, since the move must happen before the bet exists. Rules were fixed on 2023-25 and
then run once on 2025-26.

| rule (tickets on one side, line moved the other way) | 2023-25 | 2025-26 holdout |
|---|---|---|
| spreads, 65%+, 1+ point | 851-804, 51.4%, -1.8% ROI | 462-484, 48.8%, -6.8% |
| spreads, 80%+, 1+ point (best of 30 grid cells) | 53.2% (735 plays) | 159-158, 50.2%, -4.2% |
| totals, 65%+, 1+ point | 1025-1022, 50.1% | 736-690, 51.6%, -1.5% |

Money share above ticket share by 5-40 points: 49.5-52.9%, no edge. RLM plays where the model agreed did no better
(spreads 50.0%; totals 47.7%). Break-even is 52.4%.

Halfway between open and close the same plays win 53-56%, but that assumes knowing the line would finish moving. The
history has only open and close, so the number when the signal first fires can't be tested here. The board's RLM tiers
log exactly that number from Nov 8 (the v1 feed lost ticket shares in 2026; the engine reads them from v2).

## What went wrong live

| live Nov 1-3 (205 plays) | plays | record | CLV | beat close |
|---|---:|---|---:|---:|
| spread favourites | 26 | 15-11 | +0.85 | 62% |
| spread underdogs | 71 | 34-37 | -0.32 | 23% |
| total unders | 102 | 56-46 | -0.92 | 24% |
| total overs | 6 | 2-4 | +0.67 | 50% |

1. **Totals level.** This season's openers average about 151 points; the model averaged about 146, because it starts
   each season at last season's final scoring level. So 52% of games showed a 5+ point "under" gap, which was the
   model's error, not the market's. In 2023-25 the openers started low too, so it never showed.
2. **Compressed margins.** The ridge ratings are pulled toward priors and conference averages, so the model's margins
   run about 7% narrower than the market's (model-vs-opener slope 0.92-0.94; actual margins run about 1.08 x the
   model's). In mismatches that is a 2-3 point lean toward the underdog, so the model kept backing big underdogs.

## Fixes (no hindsight)

- Margins x k, where k is actual margin / model margin fitted on all earlier seasons (1.081 for 2023-24 ... 1.063 for 2026-27).
- Totals are measured against the market's level: subtract the average (model total - opening total) over the last
  7 days' games and the same day's slate, leaving out the game itself.

## Results, 2023-24 to 2025-26 (ROI by season in order)

| rule | raw model ROI | corrected: plays | CLV | beat close | ATS | ROI | by season | plays/day |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| Dec-Mar spreads 3+ | +6.4% | 1086 | +1.40 | 71% | 57.6% | **+9.8%** | +11.6 / +7.2 / +10.4 | 3.0 |
| Dec-Mar spreads 4+ | +14.1% | 325 | +2.03 | 82% | 62.5% | **+19.3%** | +22.9 / +11.1 / +22.1 | 0.9 |
| Dec-Mar totals 5+ | +7.4% | 729 | +2.42 | 76% | 56.3% | **+7.5%** | +3.6 / +7.1 / +11.2 | 2.0 |
| Dec-Mar totals 6+ | +12.9% | 311 | +2.86 | 79% | 57.6% | **+9.9%** | +1.5 / +24.7 / +8.4 | 0.8 |
| Nov spreads 4+, favourites only | +5.7% | 400 | +1.32 | 67% | 56.4% | **+7.7%** | +7.3 / +4.9 / +10.0 | 5.0 |
| Nov totals 5+, overs only | +8.1% | 232 | +1.82 | 67% | 56.7% | **+8.2%** | +20.6 / +2.3 / +4.6 | 2.9 |
| Nov spreads 4+, underdogs (out) | -5.6% | 201 | +0.50 | 45% | 54.7% | +4.5% | +23.0 / +8.7 / -15.5 | 2.5 |
| Nov totals 5+, unders (out) | -3.0% | 185 | +1.68 | 66% | 49.5% | -5.5% | -8.7 / -10.2 / -1.3 | 2.3 |

The scaled margins remove the raw model's one losing season (spreads 3+ in 2024-25). In November the underdogs never
beat the close (45%) and unders lost in all three seasons. Early-season ratings still compress mismatches, and early
games score more than the model's level.

**Nov 1-3 2026** (corrected model, opener vs DK/FD/MGM close; NOT out of sample, see the correction at the top):
favourites 4+ went 15-8 (+5.6u), CLV +1.35, 61% beat the close; overs 5+ went 8-8, CLV +1.38, 62.5% beat the close.
The excluded underdogs (CLV -0.35, 10% beat) and unders (CLV -4.17, 11%) were the losers.

## Caveats

- The November side filters (favourites only, overs only) were chosen by looking at 2023-25, so they are in-sample.
  The two fixes aren't. Three seasons and a few hundred plays per rule still leave ROI noisy (one standard error is
  about 5 points of ROI at 400 plays). CLV is the faster check: a rule that stops beating the close is broken
  whatever its record.
- ROI assumes -110 at the opener. A later number, or a book other than DK, gives up part of the edge: the Openers tab
  shows FanDuel and BetMGM match the opener only 40-48% of the time.
