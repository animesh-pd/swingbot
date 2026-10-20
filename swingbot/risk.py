"""Risk manager: every entry passes through here. Rules come from config.yaml."""
import math
from datetime import date


class RiskManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.r = cfg["risk"]

    # ---------- sizing ----------
    def share_qty(self, price: float, bucket_cash: float) -> int:
        s = self.cfg["shares"]
        budget = min(bucket_cash, self.cfg["capital"]["shares_bucket"] * s["max_position_pct"])
        return max(0, math.floor(budget / price))

    def option_qty(self, premium: float, bucket_cash: float, multiplier: int = 100) -> int:
        o = self.cfg["options"]
        budget = min(bucket_cash, o["max_position_usd"])
        return max(0, math.floor(budget / (premium * multiplier)))

    def share_stop(self, entry: float) -> float:
        return round(entry * (1 - self.cfg["shares"]["stop_loss_pct"]), 2)

    def share_target(self, entry: float) -> float:
        return round(entry * (1 + self.cfg["shares"]["take_profit_pct"]), 2)

    def option_stop(self, premium: float) -> float:
        return round(premium * (1 - self.cfg["options"]["stop_loss_pct"]), 2)

    def option_target(self, premium: float) -> float:
        return round(premium * (1 + self.cfg["options"]["take_profit_pct"]), 2)

    # ---------- gates ----------
    def account_gate(self, equity: float, peak: float, day_start: float) -> list:
        """Account-level reasons to block ALL new entries."""
        blocks = []
        total = self.cfg["capital"]["total"]
        if day_start - equity >= total * self.r["max_daily_loss_pct"]:
            blocks.append(f"daily loss limit hit ({day_start - equity:.0f} USD)")
        if peak > 0 and (peak - equity) / peak >= self.r["max_drawdown_pct"]:
            blocks.append(f"KILL SWITCH: drawdown {(peak - equity) / peak:.0%} from peak. "
                          "Review the journal, then run `python run.py reset-killswitch`.")
        return blocks

    def ticker_gate(self, ticker: str, kind: str, today: date, open_tickers: set,
                    n_open: int, earnings: date | None, macro: list, news_risk: float) -> list:
        """Reasons to skip one ticker today. Empty list = clear to enter."""
        blocks = []
        sect = self.cfg["shares" if kind == "share" else "options"]
        if n_open >= sect["max_positions"]:
            blocks.append(f"max {sect['max_positions']} {kind} positions open")
        if ticker in open_tickers:
            blocks.append("already holding it")
        o = self.cfg["options"]
        # With exit_before_events on, a distant event just means an earlier exit; only an event
        # too close to leave `min_hold_days` of holding time blocks the entry.
        guard = kind == "option" and o.get("exit_before_events", False)
        if earnings is not None:
            days = (earnings - today).days
            # Shares held 1-2 months will often cross earnings; options must not.
            if guard:
                window = o["min_hold_days"]
            else:
                window = self.r["earnings_blackout_days"] if kind == "share" else o["max_hold_days"]
            if 0 <= days <= window:
                blocks.append(f"earnings in {days} days ({earnings})")
        if kind == "option":
            window = o["min_hold_days"] if guard else self.r["macro_blackout_days"]
            for ev in macro:
                d = (ev["date"] - today).days
                if 0 <= d <= window:
                    blocks.append(f"{ev['name']} on {ev['date']}")
        if news_risk >= self.cfg["news"]["block_threshold"]:
            blocks.append(f"geopolitical/news risk {news_risk:.2f}")
        return blocks

    def pdt_ok(self, entry_date: date, today: date) -> bool:
        """Under the pattern-day-trader rule, avoid closing a position the day it was opened.
        Hard stops still fire; this only blocks discretionary same-day exits."""
        return not (self.r["pdt_guard"] and entry_date == today)
