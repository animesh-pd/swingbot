"""Technical signal scoring. Technicals drive decisions; fundamentals are not used."""
from dataclasses import dataclass, field

import pandas as pd

from .indicators import enrich, support_resistance


@dataclass
class Signal:
    ticker: str
    direction: str            # "bull" | "bear" | "none"
    score: int                # checks passed, 0-5
    close: float
    support: float
    resistance: float
    atr: float
    rsi: float
    reasons: list = field(default_factory=list)
    breakout: bool = False

    @property
    def reward_risk(self) -> float:
        """Distance to the next level in the trade direction vs. distance to the opposite level."""
        if self.direction == "bear":
            reward, risk = self.close - self.support, self.resistance - self.close
        else:
            reward, risk = self.resistance - self.close, self.close - self.support
        return round(reward / risk, 2) if risk > 0 else 0.0


def _checks(d: pd.DataFrame, bull: bool):
    last, prev = d.iloc[-1], d.iloc[-2]
    sign = 1 if bull else -1
    checks = [
        (sign * (last.Close - last.SMA50) > 0, "price above 50-day" if bull else "price below 50-day"),
        (sign * (last.SMA20 - last.SMA50) > 0, "20-day above 50-day" if bull else "20-day below 50-day"),
        ((40 <= last.RSI <= 65) if bull else (35 <= last.RSI <= 60),
         f"RSI {last.RSI:.0f} has room to run"),
        (sign * last.MACDhist > 0 and sign * (last.MACDhist - prev.MACDhist) > 0,
         "MACD momentum building"),
        (last.Volume > last.VolAvg20 and sign * (last.Close - prev.Close) > 0,
         "volume confirms the move"),
    ]
    passed = [why for ok, why in checks if ok]
    return len(passed), passed


def score(ticker: str, bars: pd.DataFrame) -> Signal:
    if len(bars) < 60:
        raise ValueError(f"{ticker}: need at least 60 daily bars, got {len(bars)}")
    d = enrich(bars)
    last = d.iloc[-1]
    support, resistance, breakout = support_resistance(d)
    bull_n, bull_why = _checks(d, True)
    bear_n, bear_why = _checks(d, False)
    if bull_n >= bear_n and bull_n > 0:
        direction, n, why = "bull", bull_n, bull_why
    elif bear_n > 0:
        direction, n, why = "bear", bear_n, bear_why
    else:
        direction, n, why = "none", 0, []
    return Signal(ticker, direction, n, float(last.Close), support, resistance,
                  float(last.ATR), float(last.RSI), why, breakout)
