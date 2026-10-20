"""Paper broker: simulated account saved to a JSON file. No real money, no credentials.

A live broker (Tastytrade) will implement the same methods later, so the engine won't change."""
import csv
import json
import os
from datetime import date

from .options import pos_label

SHARE_SLIPPAGE = 0.0005   # 0.05% worse than the last price on every share fill
OPTION_EXIT_HAIRCUT = 0.02  # options sell ~2% under mid (you buy at the ask)
SPREAD_EXIT_HAIRCUT = 0.04  # a spread crosses two bid/ask gaps on the way out


def _ser(o):
    return o.isoformat() if isinstance(o, date) else o


def _de(p):
    for k in ("entry_date", "expiry"):
        if isinstance(p.get(k), str):
            p[k] = date.fromisoformat(p[k])
    return p


class PaperBroker:
    def __init__(self, state_path: str, journal_path: str, cfg: dict):
        self.path, self.journal_path = state_path, journal_path
        if os.path.exists(state_path):
            with open(state_path) as f:
                s = json.load(f)
            s["positions"] = [_de(p) for p in s["positions"]]
        else:
            c = cfg["capital"]
            s = {"cash": {"share": c["shares_bucket"], "option": c["options_bucket"]},
                 "positions": [], "next_id": 1, "peak": c["total"],
                 "day_start": {"date": None, "equity": c["total"]}, "realized": 0.0}
        self.s = s

    # ---------- state ----------
    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.s, f, indent=2, default=_ser)

    @property
    def positions(self):
        return self.s["positions"]

    def open_of(self, kind):
        return [p for p in self.positions if p["kind"] == kind]

    def cash(self, kind):
        return self.s["cash"][kind]

    # ---------- orders ----------
    def buy_shares(self, ticker, qty, last, stop, target, today, reason):
        price = round(last * (1 + SHARE_SLIPPAGE), 2)
        cost = price * qty
        if qty <= 0 or cost > self.cash("share"):
            return None
        self.s["cash"]["share"] -= cost
        pos = {"id": self.s["next_id"], "kind": "share", "ticker": ticker, "qty": qty,
               "entry": price, "entry_date": today, "stop": stop, "target": target,
               "high": price, "trimmed": False, "reason": reason}
        self.s["next_id"] += 1
        self.positions.append(pos)
        return pos

    def buy_option(self, c, qty, today, stop, target, reason):
        price = c["cost"]   # ask for a single option, net debit for a spread
        cost = price * 100 * qty
        if qty <= 0 or cost > self.cash("option"):
            return None
        self.s["cash"]["option"] -= cost
        pos = {"id": self.s["next_id"], "kind": "option", "ticker": c["ticker"], "qty": qty,
               "right": c["right"], "strike": c["strike"], "expiry": c["expiry"], "iv": c["iv"], "strike2": c.get("strike2"),
               "entry": price, "entry_date": today, "stop": stop, "target": target,
               "high": price, "trimmed": False, "reason": reason}
        self.s["next_id"] += 1
        self.positions.append(pos)
        return pos

    def sell(self, pos, mark, qty, today, why):
        """Sell `qty` of a position at `mark` (share price or option mid). Returns realized P&L."""
        qty = min(qty, pos["qty"])
        if pos["kind"] == "share":
            px, mult = round(mark * (1 - SHARE_SLIPPAGE), 2), 1
        else:
            px, mult = round(mark * (1 - (SPREAD_EXIT_HAIRCUT if pos.get('strike2') else OPTION_EXIT_HAIRCUT)), 2), 100
        proceeds = px * qty * mult
        pnl = (px - pos["entry"]) * qty * mult
        self.s["cash"][pos["kind"]] += proceeds
        self.s["realized"] += pnl
        pos["qty"] -= qty
        if pos["qty"] == 0:
            self.positions.remove(pos)
        self._journal(pos, qty, px, pnl, today, why)
        return pnl

    # ---------- valuation ----------
    def equity(self, marks: dict) -> float:
        """marks: {position id: current price/mid}"""
        eq = self.s["cash"]["share"] + self.s["cash"]["option"]
        for p in self.positions:
            eq += marks.get(p["id"], p["entry"]) * p["qty"] * (100 if p["kind"] == "option" else 1)
        return round(eq, 2)

    def roll_day(self, today, equity):
        if self.s["day_start"]["date"] != today.isoformat():
            self.s["day_start"] = {"date": today.isoformat(), "equity": equity}
        self.s["peak"] = max(self.s["peak"], equity)

    def reset_killswitch(self, equity):
        self.s["peak"] = equity

    # ---------- journal ----------
    def _journal(self, pos, qty, exit_px, pnl, today, why):
        new = not os.path.exists(self.journal_path)
        os.makedirs(os.path.dirname(self.journal_path) or ".", exist_ok=True)
        desc = pos["ticker"] if pos["kind"] == "share" else \
            pos_label(pos)
        mult = 100 if pos["kind"] == "option" else 1
        with open(self.journal_path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["opened", "closed", "kind", "position", "qty", "entry", "exit",
                            "pnl_usd", "pnl_pct", "why_in", "why_out"])
            w.writerow([_ser(pos["entry_date"]), today.isoformat(), pos["kind"], desc, qty,
                        pos["entry"], exit_px, round(pnl, 2),
                        round(100 * pnl / (pos["entry"] * qty * mult), 1), pos["reason"], why])
