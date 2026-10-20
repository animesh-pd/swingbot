"""Strategy C: multi-asset trend rotation. A slow, monthly bot with its own account, separate from the swing bot.

Rule (the one that was backtested): once a month, hold the top N ETFs by 12-month momentum, but only those
trading above their 200-day average. Any slot with no qualifying ETF stays in cash. Equal weight.

This module is pure rules + a small JSON account. Prices are passed in, so it runs offline in tests."""
import json
import math
import os
from datetime import date

import pandas as pd

SLIPPAGE = 0.0005   # 5 bps on every fill, paper or backtest


def pick(closes: dict, cfg: dict) -> list:
    """Symbols to hold, best first. `closes` maps symbol -> adjusted daily closes up to and including today."""
    lb, sma = int(cfg["momentum_days"]), int(cfg["sma_days"])
    score = {}
    for sym in cfg["universe"]:
        c = closes.get(sym)
        if c is None:
            continue
        c = c.dropna()
        if len(c) <= max(lb, sma):
            continue   # not enough history to judge
        if c.iloc[-1] > c.tail(sma).mean():
            score[sym] = c.iloc[-1] / c.iloc[-1 - lb] - 1
    return sorted(score, key=score.get, reverse=True)[: int(cfg["top_n"])]


def plan(account: dict, prices: dict, targets: list, cfg: dict) -> list:
    """Orders (sells first) that move the account to equal-weight `targets`. Each: {side, symbol, qty, price}."""
    n = int(cfg["top_n"])
    held = account["holdings"]
    equity = account["cash"] + sum(h["qty"] * prices[s] for s, h in held.items())
    slot = equity / n                      # empty slots stay in cash
    orders, cash = [], account["cash"]
    for sym, h in held.items():            # sell what dropped out, and trim what grew past its slot
        want = 0 if sym not in targets else math.floor(slot / prices[sym])
        sell = h["qty"] - want
        if sell > 0 and (sym not in targets or sell * prices[sym] >= cfg["min_trade_usd"]):
            orders.append({"side": "sell", "symbol": sym, "qty": sell, "price": prices[sym]})
            cash += sell * prices[sym] * (1 - SLIPPAGE)
    for sym in targets:                    # then buy what is missing, as far as cash allows
        have = held.get(sym, {}).get("qty", 0)
        qty = min(math.floor(slot / prices[sym]) - have, math.floor(cash / (prices[sym] * (1 + SLIPPAGE))))
        if qty > 0 and qty * prices[sym] >= cfg["min_trade_usd"]:
            orders.append({"side": "buy", "symbol": sym, "qty": qty, "price": prices[sym]})
            cash -= qty * prices[sym] * (1 + SLIPPAGE)
    return orders


def is_due(account: dict, today: date) -> bool:
    """True on the first run of each calendar month."""
    return account.get("last_rebalance") != today.strftime("%Y-%m")


class Account:
    """The rotation bot's own $ account, kept in state_dir/account.json."""

    def __init__(self, path: str, capital: float):
        self.path = path
        if os.path.exists(path):
            with open(path) as f:
                self.s = json.load(f)
        else:
            self.s = {"cash": float(capital), "holdings": {}, "last_rebalance": None, "log": []}

    def __getitem__(self, k):
        return self.s[k]

    def get(self, k, default=None):
        return self.s.get(k, default)

    def equity(self, prices: dict) -> float:
        return self.s["cash"] + sum(h["qty"] * prices[sym] for sym, h in self.s["holdings"].items())

    def apply(self, orders: list, today: date):
        """Record fills (with slippage) as if you made these trades."""
        for o in orders:
            sym, qty = o["symbol"], o["qty"]
            if o["side"] == "buy":
                fill = o["price"] * (1 + SLIPPAGE)
                h = self.s["holdings"].setdefault(sym, {"qty": 0, "cost": 0.0})
                h["cost"] = (h["cost"] * h["qty"] + fill * qty) / (h["qty"] + qty)
                h["qty"] += qty
                self.s["cash"] -= fill * qty
            else:
                fill = o["price"] * (1 - SLIPPAGE)
                h = self.s["holdings"][sym]
                self.s["cash"] += fill * qty
                pnl = (fill - h["cost"]) * qty
                h["qty"] -= qty
                if h["qty"] == 0:
                    del self.s["holdings"][sym]
                o["pnl"] = round(pnl, 2)
            self.s["log"].append({"date": today.isoformat(), **{k: o[k] for k in ("side", "symbol", "qty")},
                                  "fill": round(fill, 4), "pnl": o.get("pnl")})
        self.s["last_rebalance"] = today.strftime("%Y-%m")

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.s, f, indent=1)


def describe(orders: list, targets: list, account: dict, prices: dict, cfg: dict) -> str:
    eq = account["cash"] + sum(h["qty"] * prices[s] for s, h in account["holdings"].items())
    L = [f"ROTATION  equity ${eq:,.0f} | hold up to {cfg['top_n']} of {len(cfg['universe'])} ETFs",
         "Qualifying (above 200-day), best momentum first: " + (", ".join(targets) or "none -> stay in cash")]
    if not orders:
        L.append("No trades needed this month.")
    for o in orders:
        L.append(f"  {o['side'].upper():4} {o['qty']:>4} {o['symbol']:5} @ ~{o['price']:.2f}  (${o['qty'] * o['price']:,.0f})")
    return "\n".join(L)


def load_live(symbols: list):
    """Yahoo data on your laptop. Returns (adjusted closes per symbol, latest real price per symbol)."""
    import logging
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    adj = yf.download(list(symbols), period="3y", auto_adjust=True, progress=False)["Close"]
    raw = yf.download(list(symbols), period="10d", auto_adjust=False, progress=False)["Close"]
    closes = {s: adj[s].dropna() for s in adj.columns if adj[s].dropna().size}
    prices = {s: float(raw[s].dropna().iloc[-1]) for s in raw.columns if raw[s].dropna().size}
    return closes, prices


def backtest(prices: pd.DataFrame, cfg: dict, start=None) -> pd.Series:
    """Daily returns of the same rules. Signal on the last trading day of each month; the new weights earn
    from the next session on. Costs: SLIPPAGE on each unit of turnover. `prices` = adjusted closes, one column/ETF."""
    uni = [s for s in cfg["universe"] if s in prices.columns]
    ret = prices[uni].pct_change().fillna(0)
    need = max(int(cfg["momentum_days"]), int(cfg["sma_days"]))
    month_end = prices.index.to_series().groupby(prices.index.to_period("M")).last()
    w = pd.DataFrame(0.0, index=prices.index, columns=uni)
    cur = pd.Series(0.0, index=uni)
    for day in month_end.values:
        i = prices.index.get_loc(day)
        if i >= need:
            window = {s: prices[s].iloc[: i + 1] for s in uni}
            held = pick(window, {**cfg, "universe": uni})
            cur = pd.Series(0.0, index=uni)
            for s in held:
                cur[s] = 1 / int(cfg["top_n"])
        w.iloc[i:] = cur.values            # in force from this close; earns via shift(1) below
    port = (w.shift(1) * ret).sum(axis=1)
    turnover = w.diff().abs().sum(axis=1).shift(1).fillna(0)
    port = port - turnover * SLIPPAGE
    first = prices.index[need]
    return port.loc[first:] if start is None else port.loc[max(pd.Timestamp(start), first):]
