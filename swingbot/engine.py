"""The trading loop: manage open positions first, then look for new entries."""
import os
from datetime import date

from . import news as newsmod
from .options import mark as option_mark, pick_contract, pos_label
from .risk import RiskManager
from .signals import score


def _macro(cfg):
    out = []
    for ev in cfg.get("macro_events", []):
        d = ev["date"] if isinstance(ev["date"], date) else date.fromisoformat(str(ev["date"]))
        out.append({"date": d, "name": ev["name"]})
    return out


class Engine:
    def __init__(self, cfg, data, broker, news_fetch=None):
        self.cfg, self.data, self.broker = cfg, data, broker
        self.risk = RiskManager(cfg)
        self.news_fetch = news_fetch or newsmod.fetch_headlines
        self._earn_cache = {}
        self.news_history = os.path.join(os.path.dirname(broker.path), "news_history.json")

    # ---------- marks & equity ----------
    def marks(self, today):
        m = {}
        for p in self.broker.positions:
            try:
                m[p["id"]] = (self.data.last_price(p["ticker"]) if p["kind"] == "share"
                              else option_mark(self.data, p, today))
            except Exception:
                m[p["id"]] = p["entry"]
        return m

    # ---------- event awareness ----------
    def _earnings(self, ticker, today):
        key = (ticker, today)
        if key not in self._earn_cache:
            try:
                self._earn_cache[key] = self.data.earnings_date(ticker)
            except Exception:
                self._earn_cache[key] = None
        return self._earn_cache[key]

    def _event_ahead(self, ticker, today):
        """An earnings/macro event on the next session (through Monday when today is Friday), else None."""
        look = 3 if today.weekday() == 4 else 1
        for ev in _macro(self.cfg):
            if 0 <= (ev["date"] - today).days <= look:
                return f"{ev['name']} {ev['date']}"
        e = self._earnings(ticker, today)
        if e is not None and 0 <= (e - today).days <= look:
            return f"earnings {e}"
        return None

    # ---------- exits ----------
    def manage(self, today) -> list[str]:
        """Apply stops, targets, trailing stops and time stops. Returns alert lines."""
        alerts, marks = [], self.marks(today)
        sc, oc = self.cfg["shares"], self.cfg["options"]
        for p in list(self.broker.positions):
            px = marks[p["id"]]
            held = (today - p["entry_date"]).days
            p["high"] = max(p["high"], px)
            label = p["ticker"] if p["kind"] == "share" else \
                pos_label(p)
            same_day_ok = self.risk.pdt_ok(p["entry_date"], today)

            if px <= p["stop"]:  # hard stops always fire, PDT or not
                pnl = self.broker.sell(p, px, p["qty"], today, "stop loss")
                alerts.append(f"STOP {label} at {px:.2f} (P&L {pnl:+.0f})")
                continue
            if not same_day_ok:
                continue

            if p["kind"] == "share":
                if not p["trimmed"] and px >= p["target"]:
                    half = p["qty"] // 2
                    if half > 0:
                        pnl = self.broker.sell(p, px, half, today, "target: trimmed half")
                        alerts.append(f"TRIM {label} {half} sh at {px:.2f} (P&L {pnl:+.0f}); trailing the rest")
                    p["trimmed"] = True
                if p["trimmed"] and p in self.broker.positions:
                    p["stop"] = max(p["stop"], round(p["high"] * (1 - sc["trail_pct"]), 2), p["entry"])
                if held >= sc["max_hold_days"] and p in self.broker.positions:
                    pnl = self.broker.sell(p, px, p["qty"], today, f"time stop {held}d")
                    alerts.append(f"TIME EXIT {label} at {px:.2f} (P&L {pnl:+.0f})")
            else:
                dte = (p["expiry"] - today).days
                ev = self._event_ahead(p["ticker"], today) if oc.get("exit_before_events") else None
                why = ("profit target" if px >= p["target"] else
                       f"{held}-day hold limit" if held >= oc["max_hold_days"] else
                       f"{dte} days to expiry" if dte <= 7 else
                       f"event risk: {ev}" if ev else None)
                if why:
                    pnl = self.broker.sell(p, px, p["qty"], today, why)
                    alerts.append(f"EXIT {label} at {px:.2f}: {why} (P&L {pnl:+.0f})")
        self.broker.s["peak"] = max(self.broker.s["peak"], self.broker.equity(self.marks(today)))
        return alerts

    # ---------- entries ----------
    def scan(self, today, place_orders=True) -> dict:
        exits = self.manage(today)
        marks = self.marks(today)
        equity = self.broker.equity(marks)
        self.broker.roll_day(today, equity)
        acct_blocks = self.risk.account_gate(equity, self.broker.s["peak"],
                                             self.broker.s["day_start"]["equity"])
        ticker_risk, themes = newsmod.scan(self.cfg, self.news_fetch,
                                           history_path=self.news_history, today=today)
        macro = _macro(self.cfg)
        result = {"date": today, "equity": equity, "exits": exits, "account_blocks": acct_blocks,
                  "themes": themes, "entries": [], "watch": [], "skipped": []}
        market_block = self._market_block()
        if market_block:
            result["skipped"].append("no new bullish entries: " + market_block)

        for kind, key in (("share", "shares"), ("option", "options")):
            sect = self.cfg[key]
            cands = []
            for t in sect["watchlist"]:
                try:
                    sig = score(t, self.data.bars(t))
                except Exception as e:
                    result["skipped"].append(f"{t}: {e}")
                    continue
                if kind == "share" and sig.direction != "bull":
                    continue  # shares are long-only
                if sig.direction == "none" or (market_block and sig.direction == "bull"):
                    continue
                cands.append(sig)
            cands.sort(key=lambda s: (s.score, s.reward_risk), reverse=True)

            for sig in cands:
                open_now = self.broker.open_of(kind)
                if sig.score >= sect["min_signal_score"] and sig.reward_risk < self.cfg["risk"]["min_reward_risk"]:
                    result["skipped"].append(f"{sig.ticker} ({kind}): reward:risk {sig.reward_risk} below "
                                             f"{self.cfg['risk']['min_reward_risk']} (too close to the next level)")
                    continue
                if sig.score < sect["min_signal_score"]:
                    if sig.score == sect["min_signal_score"] - 1:
                        result["watch"].append((kind, sig))
                    continue
                blocks = acct_blocks + self.risk.ticker_gate(
                    sig.ticker, kind, today, {p["ticker"] for p in open_now}, len(open_now),
                    self.data.earnings_date(sig.ticker), macro, ticker_risk.get(sig.ticker, 0.0))
                if blocks:
                    result["skipped"].append(f"{sig.ticker} ({kind}, score {sig.score}): " + "; ".join(blocks))
                    continue
                order = self._order(kind, sig, today, place_orders)
                if isinstance(order, str):
                    result["skipped"].append(f"{sig.ticker} ({kind}): {order}")
                else:
                    result["entries"].append(order)
        self.broker.save()
        return result

    def _market_block(self) -> str:
        """Market trend filter: '' when entries are allowed, else why bullish entries are paused.
        Fails open (allows entries) if the index data can't be read."""
        mf = self.cfg.get("market_filter") or {}
        if not mf.get("enabled"):
            return ""
        sym, n = mf.get("symbol", "SPY"), int(mf.get("sma_days", 200))
        try:
            close = self.data.bars(sym)["Close"]
        except Exception:
            return ""
        if len(close) < n:
            return ""
        sma = float(close.tail(n).mean())
        last = float(close.iloc[-1])
        return f"{sym} {last:.2f} is below its {n}-day average {sma:.2f}" if last < sma else ""

    def _order(self, kind, sig, today, place):
        why = f"score {sig.score}/5: " + ", ".join(sig.reasons)
        if kind == "share":
            qty = self.risk.share_qty(sig.close, self.broker.cash("share"))
            if qty == 0:
                return f"${sig.close:.0f}/share doesn't fit the position cap"
            stop, target = self.risk.share_stop(sig.close), self.risk.share_target(sig.close)
            order = {"kind": "share", "ticker": sig.ticker, "qty": qty, "price": sig.close,
                     "stop": stop, "target": target, "why": why, "rr": sig.reward_risk,
                     "earnings": self.data.earnings_date(sig.ticker)}
            if place and not self.broker.buy_shares(sig.ticker, qty, sig.close, stop, target, today, why):
                return "not enough cash in the shares bucket"
            return order
        c, err = pick_contract(self.data, sig.ticker, sig.direction, today, self.cfg)
        if err:
            return err
        qty = self.risk.option_qty(c["cost"], self.broker.cash("option"))
        if qty == 0:
            return "not enough cash in the options bucket"
        stop, target = self.risk.option_stop(c["cost"]), self.risk.option_target(c["cost"])
        if c["structure"] == "spread":   # a spread can never be worth more than its width
            target = round(min(target, 0.9 * c["width"]), 2)
        order = {"kind": "option", "ticker": sig.ticker, "qty": qty, "contract": c,
                 "price": c["cost"], "stop": stop, "target": target, "why": why, "rr": sig.reward_risk}
        if place and not self.broker.buy_option(c, qty, today, stop, target, why):
            return "not enough cash in the options bucket"
        return order
