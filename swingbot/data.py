"""Market data. LiveData uses Yahoo Finance (free, delayed ~15 min) on your laptop.
SyntheticData generates fake prices so the whole bot can be tested offline."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd


ETFS = {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "SOXX", "SMH", "VTI", "VOO"}


class LiveData:
    def __init__(self):
        import logging
        import yfinance as yf  # installed on the laptop via requirements.txt
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # it logs harmless 404s loudly
        self.yf = yf
        self._cache = {}

    def bars(self, ticker: str, days: int = 400) -> pd.DataFrame:
        if ticker not in self._cache:
            df = self.yf.Ticker(ticker).history(period=f"{days}d", interval="1d", auto_adjust=False)
            if df.empty:
                raise ValueError(f"no price data for {ticker}")
            self._cache[ticker] = df[["Open", "High", "Low", "Close", "Volume"]]
        return self._cache[ticker]

    def last_price(self, ticker: str) -> float:
        return float(self.bars(ticker)["Close"].iloc[-1])

    def earnings_date(self, ticker: str) -> date | None:
        if ticker.upper() in ETFS:
            return None  # funds don't report earnings; Yahoo answers 404 for them
        try:
            cal = self.yf.Ticker(ticker).calendar
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if dates:
                d = dates[0]
                return d if isinstance(d, date) else pd.Timestamp(d).date()
        except Exception:
            pass
        return None  # ETFs have none; unknown is treated as "no earnings soon", so check manually

    def expirations(self, ticker: str) -> list[date]:
        return [datetime.strptime(e, "%Y-%m-%d").date() for e in self.yf.Ticker(ticker).options]

    def chain(self, ticker: str, expiry: date, right: str) -> pd.DataFrame:
        """Columns: strike, bid, ask, openInterest, impliedVolatility."""
        oc = self.yf.Ticker(ticker).option_chain(expiry.isoformat())
        df = (oc.calls if right == "call" else oc.puts).copy()
        return df[["strike", "bid", "ask", "openInterest", "impliedVolatility"]].fillna(0)


class SyntheticData:
    """Random-walk prices with a configurable drift per ticker. For tests and demos only."""

    def __init__(self, seed: int = 7, drifts: dict | None = None, end: date | None = None):
        self.rng = np.random.default_rng(seed)
        self.drifts = drifts or {}
        self.end = end or date.today()
        self._bars = {}

    def bars(self, ticker: str, days: int = 300) -> pd.DataFrame:
        if ticker not in self._bars:
            drift = self.drifts.get(ticker, 0.0005)
            idx = pd.bdate_range(end=self.end, periods=days)
            rets = self.rng.normal(drift, 0.018, days)
            close = 100 * np.exp(np.cumsum(rets))
            spread = np.abs(self.rng.normal(0, 0.008, days)) * close
            self._bars[ticker] = pd.DataFrame({
                "Open": close * (1 + self.rng.normal(0, 0.004, days)),
                "High": close + spread, "Low": close - spread, "Close": close,
                "Volume": self.rng.integers(5e6, 2e7, days).astype(float)}, index=idx)
        return self._bars[ticker]

    def set_last(self, ticker: str, price: float):
        """Move the latest close (used by tests to trigger stops and targets)."""
        df = self.bars(ticker)
        df.iloc[-1, df.columns.get_loc("Close")] = price
        df.iloc[-1, df.columns.get_loc("High")] = max(price, df["High"].iloc[-1])
        df.iloc[-1, df.columns.get_loc("Low")] = min(price, df["Low"].iloc[-1])

    def last_price(self, ticker: str) -> float:
        return float(self.bars(ticker)["Close"].iloc[-1])

    def earnings_date(self, ticker: str):
        return None

    def expirations(self, ticker: str) -> list[date]:
        days = (self.end + timedelta(days=n) for n in range(1, 70))
        return [d for d in days if d.weekday() == 4]  # weekly Friday expirations

    def chain(self, ticker: str, expiry: date, right: str) -> pd.DataFrame:
        from .options import bs_price
        s = self.last_price(ticker)
        t = max((expiry - self.end).days, 1) / 365
        rows = []
        for k in np.arange(round(s * 0.8), round(s * 1.2), 1.0):
            mid = bs_price(s, k, t, 0.35, right)
            rows.append({"strike": k, "bid": round(mid * 0.98, 2), "ask": round(mid * 1.02, 2),
                         "openInterest": 2000, "impliedVolatility": 0.35})
        return pd.DataFrame(rows)
