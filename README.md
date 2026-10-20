# SwingBot: two paper-trading bots, a backtester, and a broker-sandbox bridge

A small, readable project for learning how rule-based trading systems behave **without risking money**. It contains:

- **Swing bot**: scans a stock watchlist daily, scores technical setups, and manages positions with stops, trims and time exits.
- **Rotation bot**: once a month, holds the 3 ETFs with the best 12-month return that are above their 200-day average (cash otherwise).
- **Backtester**: replays the same rules on Yahoo Finance history, with slippage and no look-ahead.
- **tastytrade sandbox bridge**: mirrors each bot's paper positions into tastytrade's practice account.
- **Windows Task Scheduler scripts** to run it all on market days.

> **Not financial advice.** This is an educational experiment. It trades simulated money only. Backtests use history that is
> already known, and the watchlists here were picked with hindsight, so results are stress tests, not forecasts. Past results do
> not predict future returns. Do not use this with real money without understanding every rule and testing it yourself.

## What the tests found (so you do not repeat our dead ends)

| Finding | Detail |
| --- | --- |
| Options lost money in every structure | Spreads and single options, same signals: profit factor 0.26 to 0.35. Shares-only was profitable; adding options turned +12% into -30% over 2 years. Options are **off** by default here. |
| Neither bot beat SPY on return | Swing bot, 4 years: +104% on the original hindsight list (SPY +108%), only +39% to +53% on fresh lists. Its edge was smaller drawdowns (about 9% to 18%). |
| "Best" settings did not generalize | A 36-setting sweep found one that made +51.6% in 2 years; on fresh data the same idea was ordinary. Always test on a list or period you did not tune on. |
| A SPY 200-day market filter hurt | It cut the swing bot's return (+104% to +55% on one list) without lowering drawdown. Left **off**. |
| Rotation matched SPY with less pain | 2007-2026, monthly, 5 bps slippage: ~10.7%/yr vs SPY 10.6%, max drawdown 24% vs 55%, correlation to SPY 0.55. The 12-month setting is somewhat tuned (a 6-month ranking earned about half). |

The scripts behind these numbers are in [`research/`](research/README.md).

## Quick start (about 10 minutes)

1. Install Python 3.11 or newer from python.org (on Windows, tick "Add Python to PATH").
2. In a terminal in this folder:
   ```
   python -m pip install -r requirements.txt
   python tests/test_rules.py        # should end with "all tests passed"
   python tests/test_rotation.py     # should end with "all rotation tests passed"
   python run.py demo                # offline dry run on fake prices
   ```
3. First live-data run (safe, places nothing): `python run.py scan` with `mode: alerts` in `config.yaml` (the default).

> **Windows note:** if `python` opens the Microsoft Store instead of running, it is a placeholder. Use the full path to your real
> `python.exe`, or turn off the "App execution aliases" for Python in Windows Settings.

## Commands

| Command | What it does |
| --- | --- |
| `python run.py scan` | Manage positions, look for new entries, print a brief |
| `python run.py monitor` | Check stops and targets only |
| `python run.py watch` | Leave running: monitors every 15 min, scans at 3:45 PM ET |
| `python run.py rotate [--record]` | Print the monthly rotation plan; `--record` logs trades you placed by hand |
| `python run.py backtest 2` | Replay the swing bot over the last 2 years |
| `python run.py rotate-backtest` | Replay the rotation bot, 2007 to now |
| `python run.py report` / `positions` | Performance and open positions |
| `python run.py tt-check` | Read-only tastytrade sandbox connection test |
| `python run.py tt-sync [--dry]` | Make the sandbox match the paper accounts (`--dry` validates only) |
| `python run.py core-test` | Run all offline tests |

Everything is configured in [`config.yaml`](config.yaml), with comments on every setting. Each bot keeps its own simulated account in
its own folder (`state/` and `state_rotation/`, created on first run).

## Modes

- `mode: alerts` (default): reports what the bot would do and records nothing.
- `mode: paper`: records trades in a simulated account. With `tastytrade.send_orders: true` it also mirrors share positions to the
  tastytrade **sandbox** (never production; the code refuses if `tastytrade.sandbox` is false).
- There is no live-money mode in this project.

## Connecting the tastytrade sandbox (optional)

1. Register for tastytrade's developer sandbox ("cert" environment) and create an OAuth application there. Follow tastytrade's current
   developer documentation. Generate the **refresh token for the sandbox app** (a token from anywhere else is rejected).
2. Put your app's client ID in `config.yaml` (`tastytrade.client_id`). Store the two secrets in your operating system's credential
   store, not in files:
   ```
   python -m keyring set swingbot-tastytrade client_secret
   python -m keyring set swingbot-tastytrade refresh_token
   ```
   (Environment variables `TT_SECRET` and `TT_REFRESH` also work.)
3. `python run.py tt-check` should list your sandbox account. Then `python run.py tt-sync --dry`, then set `send_orders: true`.

Built-in guardrails: sandbox-only session, a per-order dollar cap (`max_order_usd`), each bot touches only its own symbols, working
orders count as already placed (re-runs never double up), and broker errors are reported without stopping the bot. Stock orders must
use `BUY_TO_OPEN` / `SELL_TO_CLOSE`.

## Scheduling on Windows

Task Scheduler runs the bots without any app open. From PowerShell in this folder:

```
powershell -ExecutionPolicy Bypass -File scheduler\install_tasks.ps1          # install
powershell -ExecutionPolicy Bypass -File scheduler\install_tasks.ps1 -Remove  # uninstall
```

Edit the times in `install_tasks.ps1` for your time zone (the defaults assume US Central). `scheduler\run_bot.ps1` skips weekends and
anything outside 9:35 AM to 3:55 PM Eastern, so a late wake-up never trades on after-hours prices, and logs to `logs\`. Set the
`SWINGBOT_PYTHON` environment variable to your `python.exe` if it is not the default path. Tasks run only while you are signed in, and
wake timers must be enabled (they are often off on battery). Market holidays are not detected.

## Project layout

```
config.yaml        every rule and setting
run.py             command line
swingbot/          signals, risk, simulated broker, backtester, rotation, tastytrade bridge
tests/             offline tests on fake prices
scheduler/         Windows Task Scheduler scripts
research/          the backtest scripts and logs behind the findings above
```

## Known limits

- Yahoo Finance data is delayed and sometimes missing (earnings dates especially). Check anything you rely on.
- Stops are checked on a schedule, not tick by tick, so gaps can jump past them.
- Option prices in the backtest are modelled, not real quotes. The news filter is keyword-based and cannot be backtested.
- Watchlists are hindsight-picked; the sandbox's fills differ from real markets.

## Author and credits

Created by **Animesh Prasad**: the idea, the strategy decisions and the live paper-trading setup. Built together with Claude
(Sonnet 5.5, by Anthropic), which wrote the code and tests, ran the backtests and drafted the documentation. Prices come from Yahoo
Finance through the `yfinance` package, and practice orders run on the tastytrade sandbox.

## License

MIT. See [LICENSE](LICENSE). Copyright (c) 2026 Animesh Prasad. The software is provided "as is", with no warranty; you are
responsible for anything you do with it.
