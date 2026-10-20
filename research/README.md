# Research scripts (Oct 2026)

One-off experiments behind the decisions in `../CLAUDE.md`. They import the project (they find the project root from their own location), download prices from Yahoo, and print results. They never change `config.yaml`.
Run from the project root: `python research/<script>.py` (on Windows, use the full path to your Python if plain `python` opens the Store).

| Script | Question it answered | Log/output | Runtime |
|---|---|---|---|
| `variants.py` | Does the 2-year backtest do better with shares only / single options / spreads only? (Options lost in all.) | printed | ~5 min |
| `variants2.py` | Shares only on the full $5,000 vs the $2,500 bucket, 40% vs 33% per position | printed | ~6 min |
| `sweep.py` | 36-combination grid (stop, hold days, score, position count) on the original watchlist | `sweep.csv` | ~40 min |
| `both.py` | Position count 3/5/6/8 on 2 years; out-of-sample 20-stock list and original list over 4 years | `both.log` | ~7 min (+ long tail) |
| `exp2.py` | Swing bot with the SPY 200-day market filter on vs off, on two watchlists, 4 years | `exp2.log` | ~70 min |
| `rot.py` | First sector-ETF rotation idea vs SPY, 2006-2026 | printed | <1 min |
| `bear.py` | What to hold when SPY is below its 200-day: cash, short SPY, bonds, gold (2008, 2020, 2022) | printed | <1 min |
| `exp1.py` | Multi-asset (17 ETF) trend rotation vs SPY: correlation, down-month returns | printed | <1 min |

Caveats: every watchlist was picked with hindsight; `exp2.py` and `sweep.py` are slow; results in the logs are from 2026-10-17/19 data
and will shift as new prices arrive. Treat them as stress tests, not forecasts.
