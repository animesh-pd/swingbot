"""Backtester: replays the real Engine day by day over past prices.

What is real:   share prices, the 5 technical checks, S/R levels, sizing, stops, trims, time stops,
                risk gates (daily loss, drawdown), earnings blackout (Yahoo's past earnings dates).
What is MODELLED: option prices (Black-Scholes from realized volatility + a premium, with bid/ask
                gaps). Free historical option chains do not exist, so option results are an estimate.
What is MISSING: the news/geopolitics filter (headlines cannot be replayed), dividends, early
                assignment, intraday option moves. FOMC dates are real; CPI/jobs dates are approximate.

No look-ahead: every call returns data up to and including the simulated day only."""
import copy
import os
import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .broker import PaperBroker, SHARE_SLIPPAGE
from .engine import Engine
from .options import bs_price

FOMC = [  # decision days (second day of each meeting)
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
KILL_PAUSE_DAYS = 10   # trading days the kill switch stays on before the backtest "resets" it
IV_PREMIUM = 1.10      # implied vol usually sits above realized
MIN_IV = 0.18


def macro_events(start: date, end: date) -> list:
    """FOMC (real) + approximate jobs report (first Friday) and CPI (~12th, moved to a weekday)."""
    ev = [{"date": date.fromisoformat(d), "name": "FOMC decision"} for d in FOMC]
    d = date(start.year, start.month, 1)
    while d <= end:
        first = d
        while first.weekday() != 4:
            first += timedelta(days=1)
        ev.append({"date": first, "name": "Jobs report (approx)"})
        cpi = date(d.year, d.month, 12)
        while cpi.weekday() > 4:
            cpi += timedelta(days=1)
        ev.append({"date": cpi, "name": "CPI (approx)"})
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return [e for e in ev if start - timedelta(days=5) <= e["date"] <= end + timedelta(days=60)]


class HistoricalData:
    """Serves the same interface as LiveData, but frozen at `self.today`."""

    def __init__(self, bars: dict, earnings: dict | None = None):
        self.full = bars
        self.earn = {k: sorted(v) for k, v in (earnings or {}).items()}
        self.today = None
        self._cache = {}

    def set_day(self, d: date):
        self.today, self._cache = d, {}

    def bars(self, ticker, days=400):
        if ticker not in self._cache:
            df = self.full[ticker]
            df = df[df.index.date <= self.today]
            if df.empty:
                raise ValueError(f"no data for {ticker} on {self.today}")
            self._cache[ticker] = df.tail(days)
        return self._cache[ticker]

    def last_price(self, ticker):
        return float(self.bars(ticker)["Close"].iloc[-1])

    def earnings_date(self, ticker):
        for d in self.earn.get(ticker, []):
            if d >= self.today:
                return d
        return None

    def expirations(self, ticker):
        return [self.today + timedelta(days=n) for n in range(1, 75)
                if (self.today + timedelta(days=n)).weekday() == 4]

    def _vol(self, ticker):
        c = self.bars(ticker)["Close"].tail(31)
        r = np.log(c / c.shift(1)).dropna()
        rv = float(r.std() * np.sqrt(252)) if len(r) > 5 else 0.3
        return max(rv * IV_PREMIUM, MIN_IV)

    def chain(self, ticker, expiry, right):
        s, iv = self.last_price(ticker), self._vol(ticker)
        t = max((expiry - self.today).days, 1) / 365
        step = 0.5 if s < 50 else 1.0 if s < 200 else 2.5 if s < 500 else 5.0
        rows = []
        for k in np.arange(round(s * 0.75 / step) * step, s * 1.25, step):
            mid = bs_price(s, k, t, iv, right)
            half = max(0.015 * mid, 0.02)
            rows.append({"strike": float(k), "bid": round(max(mid - half, 0.0), 2), "ask": round(mid + half, 2),
                         "openInterest": 2000, "impliedVolatility": iv})
        return pd.DataFrame(rows)


def load_history(tickers, years=2.0, yf=None):
    """Download prices (and past earnings dates) via yfinance. Needs internet: run on your laptop."""
    if yf is None:
        import logging
        import yfinance as yf
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    days = int(years * 365) + 600   # extra ~400 trading days of warm-up before the first simulated day
    bars, earn = {}, {}
    for t in sorted(set(tickers)):
        try:
            df = yf.Ticker(t).history(period=f"{days}d", interval="1d", auto_adjust=False)
            if not df.empty:
                df.index = pd.DatetimeIndex(df.index).tz_localize(None)
                bars[t] = df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            pass
        try:
            ed = yf.Ticker(t).get_earnings_dates(limit=40)
            if ed is not None and len(ed):
                earn[t] = [pd.Timestamp(x).date() for x in ed.index]
        except Exception:
            pass
    return bars, earn


def _intraday_stops(eng, data, today):
    """Shares: if the day's low touched the stop, fill at the stop (or the open if it gapped below).
    The live bot checks every 15 minutes, so this is the realistic fill; close-only checks would flatter it."""
    for p in list(eng.broker.positions):
        if p["kind"] != "share" or p["entry_date"] == today:
            continue
        row = data.bars(p["ticker"]).iloc[-1]
        if row["Low"] <= p["stop"]:
            eng.broker.sell(p, min(p["stop"], float(row["Open"])), p["qty"], today, "stop loss (intraday)")


def run_backtest(cfg, bars, earnings=None, years=2.0, workdir=None, progress=None):
    """Returns a dict with the equity curve, trades and benchmark numbers."""
    cfg = copy.deepcopy(cfg)
    cfg["mode"] = "paper"
    tickers = sorted(set(cfg["shares"]["watchlist"]) | set(cfg["options"]["watchlist"]))
    tickers = [t for t in tickers if t in bars]
    ref = "SPY" if "SPY" in bars else tickers[0]
    days = [d.date() for d in bars[ref].index]
    start = days[-1] - timedelta(days=int(years * 365))
    sim = [d for d in days if d >= start]
    cfg["macro_events"] = macro_events(sim[0], sim[-1])
    cfg["news"]["enabled"] = False

    tmp = workdir or tempfile.mkdtemp(prefix="swingbot_bt_")
    broker = PaperBroker(os.path.join(tmp, "account.json"), os.path.join(tmp, "journal.csv"), cfg)
    data = HistoricalData({t: bars[t] for t in tickers}, earnings)
    eng = Engine(cfg, data, broker, news_fetch=lambda kw, h: [])
    eng.news_history = os.path.join(tmp, "news_history.json")

    curve, blocked_days, trips, halted_for = [], 0, 0, 0
    for i, d in enumerate(sim):
        data.set_day(d)
        _intraday_stops(eng, data, d)
        res = eng.scan(d)
        blocked_days += bool(res["account_blocks"])
        if any("KILL SWITCH" in b for b in res["account_blocks"]):
            halted_for += 1
            if halted_for == 1:
                trips += 1
            if halted_for >= KILL_PAUSE_DAYS:   # stand-in for you reviewing the journal and resetting
                broker.reset_killswitch(broker.equity(eng.marks(d)))
                halted_for = 0
        else:
            halted_for = 0
        curve.append((d, broker.equity(eng.marks(d))))
        if progress and i % 50 == 0:
            progress(f"  {d}  equity ${curve[-1][1]:,.0f}")
    # close everything at the end so open trades are counted
    data.set_day(sim[-1])
    for p in list(broker.positions):
        broker.sell(p, eng.marks(sim[-1])[p["id"]], p["qty"], sim[-1], "end of backtest")
    curve[-1] = (sim[-1], broker.equity({}))

    trades = pd.read_csv(broker.journal_path) if os.path.exists(broker.journal_path) else pd.DataFrame()
    bench = {}
    for name, ts in (("SPY buy&hold", [ref]), ("Watchlist equal-weight buy&hold", tickers)):
        rets = []
        for t in ts:
            c = bars[t]["Close"]
            c = c[(c.index.date >= sim[0]) & (c.index.date <= sim[-1])]
            if len(c) > 1:
                rets.append(c.iloc[-1] / c.iloc[0] - 1)
        bench[name] = float(np.mean(rets)) if rets else float("nan")
    return {"curve": curve, "trades": trades, "bench": bench, "blocked_days": blocked_days, "kill_trips": trips,
            "start": sim[0], "end": sim[-1], "capital": cfg["capital"]["total"], "cfg": cfg,
            "earnings_coverage": sum(1 for t in tickers if earnings and earnings.get(t))}


def _stats(tr: pd.DataFrame):
    if tr.empty:
        return "no trades"
    w, l = tr[tr.pnl_usd > 0], tr[tr.pnl_usd <= 0]
    gross_l = -l.pnl_usd.sum()
    pf = f"{w.pnl_usd.sum() / gross_l:.2f}" if gross_l > 0 else "n/a"
    return (f"{len(tr)} trades | win rate {len(w) / len(tr):.0%} | avg win ${w.pnl_usd.mean() if len(w) else 0:,.0f} "
            f"| avg loss ${l.pnl_usd.mean() if len(l) else 0:,.0f} | profit factor {pf} | net ${tr.pnl_usd.sum():+,.0f}")


def format_report(r) -> str:
    cap = r["capital"]
    eq = pd.Series({d: v for d, v in r["curve"]})
    final = eq.iloc[-1]
    dd = float(((eq.cummax() - eq) / eq.cummax()).max())
    yrs = max((r["end"] - r["start"]).days / 365, 0.01)
    months = eq.groupby([pd.Timestamp(d).to_period("M") for d in eq.index]).last()
    mret = months.pct_change().dropna()
    tr = r["trades"]
    lines = [f"BACKTEST {r['start']} to {r['end']}  (${cap:,.0f} start, news filter OFF, options MODELLED)",
             f"Final equity ${final:,.0f}  ({final / cap - 1:+.1%} total, {(final / cap) ** (1 / yrs) - 1:+.1%}/yr)",
             f"Max drawdown {dd:.1%} | days with entries halted: {r['blocked_days']} | "
             f"kill-switch trips: {r['kill_trips']} (auto-reset after {KILL_PAUSE_DAYS} trading days)",
             f"Months up: {(mret > 0).sum()} of {len(mret)} | best {mret.max():+.1%} | worst {mret.min():+.1%} | "
             f"months at 20%+: {(mret >= 0.20).sum()}" if len(mret) else "Too short for monthly stats"]
    for name, v in r["bench"].items():
        lines.append(f"Benchmark {name}: {v:+.1%} over the same period")
    lines.append("")
    if not tr.empty:
        lines.append("Shares:  " + _stats(tr[tr.kind == "share"]))
        lines.append("Options: " + _stats(tr[tr.kind == "option"]))
        lines.append("  of which spreads: " + _stats(tr[tr.position.str.contains("spread")]))
        lines.append("  of which single options: " + _stats(tr[(tr.kind == "option") & ~tr.position.str.contains("spread")]))
        lines.append("\nBy exit reason (count, net $):")
        tr = tr.assign(reason=tr.why_out.str.replace(r"\d+d|\d+-day", "N", regex=True).str.split(":").str[0])
        for k, g in tr.groupby("reason"):
            lines.append(f"  {k:28} {len(g):3}  {g.pnl_usd.sum():+9,.0f}")
        lines.append("\nBest and worst tickers (net $):")
        by = tr.assign(t=tr.position.str.split().str[0]).groupby("t").pnl_usd.sum().sort_values()
        lines.append("  worst: " + ", ".join(f"{k} {v:+,.0f}" for k, v in by.head(3).items()))
        lines.append("  best:  " + ", ".join(f"{k} {v:+,.0f}" for k, v in by.tail(3)[::-1].items()))
    lines.append(f"\nEarnings dates found for {r['earnings_coverage']} tickers "
                 "(missing dates mean the earnings blackout could not apply).")
    lines.append("CAUTION: the watchlist was chosen with hindsight (survivorship bias), option prices are modelled, "
                 "and the news filter is off. Treat this as a stress test of the rules, not a forecast.")
    return "\n".join(lines)
