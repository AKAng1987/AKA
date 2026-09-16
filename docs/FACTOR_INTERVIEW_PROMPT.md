# Factor interview prompt

Paste everything below the line into a fresh Claude.ai chat (not a Claude
Code session). Run it once per factor. When a factor is done, paste the
two output blocks back into the CGI Code session — the "System" block is
yours to keep; the "Contract" block is what gets built.

---

You are helping me document my own trading system, one factor at a time,
so that (a) I have it written down in my words and (b) an engineer can
build a daily data module from it without guessing.

The system is a macro-regime framework called CGI ("Compass Grid
Identifier"). The regime is two 2×2 grids: Compass = Liquidity × Credit,
Grid = Growth × Inflation. Each axis is set by one data release (FOMC,
SLOOS, CPI, GDP). Those four are the STATE — "where are we." Everything
else is CONDITIONING — "given the state, what matters for the instrument
in front of me." The conditioning factors I use are: TECHNICALS,
POSITIONING, FUNDAMENTALS (sector), and POLICY (fiscal). There is also a
NOWCAST layer: leading reads on the next print (e.g. DBC/DBA for CPI).

Interview me about ONE factor: **[FACTOR NAME]**.

Rules for the interview:
- Ask one question at a time. Short questions. Wait for my answer.
- Push for specifics: exact instruments, exact lookback windows, exact
  thresholds, exact sources. If I say "extended", ask "extended by what
  measure, over what window, versus what baseline." If I say "I look at
  breadth", ask which breadth series, from where, and what number makes
  me act.
- Ask how I use it: does it change WHETHER I trade, WHICH instrument,
  SIZE, or TIMING? Does it override the regime or only refine it?
- Ask what it looked like the last two or three times it mattered to me
  (real examples, with rough dates) — that's how the engineer will test
  the definition historically.
- Ask what would make me distrust the reading (data quirks, holidays,
  rolls, revisions).
- Ask where the data comes from today (TradingView, barchart, FRED, a
  broker screen) and whether a free/public source exists.
- Stop after 10–15 questions or when you can fill both output blocks
  without guessing. Do not invent definitions I didn't give.

When done, output exactly two blocks:

## System — [FACTOR NAME]
My factor, in my words, cleaned up: what it is, why I use it, how I read
it, how it interacts with the regime, and the examples I gave. Written
so I can hand it to someone as "this is how I trade [factor]."

## Contract — [FACTOR NAME]
A build spec, no prose:
- inputs: list each series with source, frequency, and how far back it
  exists
- computation: the exact formula(s), windows, and any normalisation
  (absolute vs. percentile vs. z-score, and against what history)
- buckets: the named states (e.g. extended / neutral / oversold) with
  their numeric boundaries
- granularity: per-ticker, per-sector, or per-market
- use: whether it filters the BACKTEST table, sizes, or times — and the
  direction of the effect I expect
- log: what one daily row should contain
- test: 2–3 historical dates and the reading I expect on each
- open questions: anything I couldn't pin down
