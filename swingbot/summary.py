"""Weekly summary text: what was bought and sold, P&L, and how the bots compare with SPY. Pure formatting, no network."""
from datetime import date, timedelta


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _pct(now: float, then: float) -> str:
    return f"{now / then - 1:+.1%}" if then else "n/a"


def format_weekly(d: dict) -> str:
    """d: week_start, week_end (dates); bots [{name, equity, start, prev}]; spy {week, since_start} (fractions);
    trades [str]; realized_week (float); open [{symbol, pnl, pct}]."""
    L = [f"Weekly summary {d['week_start']:%b %d} to {d['week_end']:%b %d}"]
    for b in d["bots"]:
        L.append(f"{b['name']}: ${b['equity']:,.0f}  week {_pct(b['equity'], b['prev'])}  since start {_pct(b['equity'], b['start'])}")
    eq, start, prev = (sum(b[k] for b in d["bots"]) for k in ("equity", "start", "prev"))
    L.append(f"Combined: ${eq:,.0f}  week {_pct(eq, prev)}  since start {_pct(eq, start)}")
    L.append(f"SPY: week {d['spy']['week']:+.1%}  since start {d['spy']['since_start']:+.1%}")
    L.append("")
    L += ["Trades this week:"] + [f"  {t}" for t in d["trades"]] if d["trades"] else ["No trades this week."]
    L.append(f"Realized P&L this week: ${d['realized_week']:+,.2f}")
    if d["open"]:
        unreal = sum(o["pnl"] for o in d["open"])
        L.append(f"Open positions (unrealized ${unreal:+,.2f}): " +
                 ", ".join(f"{o['symbol']} {o['pct']:+.1%}" for o in d["open"]))
    else:
        L.append("No open positions.")
    return "\n".join(L)
