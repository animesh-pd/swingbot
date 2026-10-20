"""Technical indicators on a daily OHLCV DataFrame (columns: Open, High, Low, Close, Volume)."""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - prev).abs(),
                    (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def support_resistance(df: pd.DataFrame, lookback: int = 90, k: int = 5):
    """(support, resistance, breakout) from the last `lookback` bars.

    A level is a pivot: the highest high / lowest low within k bars on each side, so small
    wiggles don't count. Levels closer than 1 ATR to the close are ignored (noise).
    With no overhead resistance (stock at highs) the target is projected 3 ATR up and
    `breakout` is True; with no support below, support is projected 2 ATR down."""
    w = df.tail(lookback).reset_index(drop=True)
    close = float(w["Close"].iloc[-1])
    a = float(df["ATR"].iloc[-1]) if "ATR" in df else float(atr(df).iloc[-1])
    highs, lows = [], []
    for i in range(k, len(w) - k):
        if w["High"][i] >= w["High"][i - k:i + k + 1].max():
            highs.append(float(w["High"][i]))
        if w["Low"][i] <= w["Low"][i - k:i + k + 1].min():
            lows.append(float(w["Low"][i]))
    highs.append(float(w["High"].tail(k).max()))   # recent highs/lows can't be confirmed yet
    lows.append(float(w["Low"].tail(k).min()))
    above = [h for h in highs if h >= close + a]
    below = [l for l in lows if l <= close - a]
    breakout = not above
    resistance = min(above) if above else close + 3 * a
    support = max(below) if below else close - 2 * a
    return support, resistance, breakout


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = out["Close"]
    out["SMA20"] = sma(c, 20)
    out["SMA50"] = sma(c, 50)
    out["SMA200"] = sma(c, 200)
    out["RSI"] = rsi(c)
    out["MACD"], out["MACDsig"], out["MACDhist"] = macd(c)
    out["ATR"] = atr(out)
    out["VolAvg20"] = out["Volume"].rolling(20).mean()
    return out
