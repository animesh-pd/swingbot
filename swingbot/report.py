"""Human-readable brief, performance report, and phone alerts."""
import csv
import os
from datetime import date

import requests

from .options import describe, pos_label


def brief(r: dict, broker, cfg) -> str:
    total = cfg["capital"]["total"]
    L = [f"SwingBot brief — {r['date']}  ({cfg['mode']} mode)",
         f"Equity ${r['equity']:,.0f}  ({(r['equity'] / total - 1):+.1%} since start)"]
    if r["account_blocks"]:
        L += ["", "ENTRIES BLOCKED:"] + [f"  - {b}" for b in r["account_blocks"]]
    if r["exits"]:
        L += ["", "Exits:"] + [f"  - {e}" for e in r["exits"]]
    if r["entries"]:
        L += ["", "New entries:"]
        for o in r["entries"]:
            if o["kind"] == "share":
                L.append(f"  - BUY {o['qty']} {o['ticker']} @ ~{o['price']:.2f} | stop {o['stop']} | "
                         f"trim at {o['target']} | R:R {o['rr']}")
            else:
                c = o["contract"]
                L.append(f"  - BUY {o['qty']}x {describe(c)} | stop {o['stop']} | target {o['target']}")
            L.append(f"      why: {o['why']}")
            e = o.get("earnings")
            if o["kind"] == "share" and e and 0 <= (e - r["date"]).days <= cfg["shares"]["max_hold_days"]:
                L.append(f"      heads-up: earnings {e} ({(e - r['date']).days} days) fall inside your hold window. "
                         "A gap can jump straight past the 10% stop. Consider half size, or wait until after.")
    if r["watch"]:
        L += ["", "Close to a setup (one check short):"]
        L += [f"  - {s.ticker} ({k}, {s.direction}): {', '.join(s.reasons)}" for k, s in r["watch"][:6]]
    hot = [t for t in r["themes"] if t.risk >= 0.3]
    if hot:
        L += ["", "Geopolitical / macro risk:"]
        for t in hot:
            basis = f"{t.hot} vs typical {t.baseline:.0f}" if t.baseline is not None else f"{t.hot} escalation headlines"
            L.append(f"  - {t.theme} {t.risk:.2f} ({basis}): " + (t.headlines[0][:80] if t.headlines else ""))
    if broker.positions:
        L += ["", "Open positions:"]
        for p in broker.positions:
            name = p["ticker"] if p["kind"] == "share" else \
                pos_label(p)
            L.append(f"  - {name} x{p['qty']} entry {p['entry']} stop {p['stop']} target {p['target']}")
    if r["skipped"]:
        L += ["", "Skipped:"] + [f"  - {s}" for s in r["skipped"][:10]]
    return "\n".join(L)


def performance(journal_path: str, broker, equity: float, cfg) -> str:
    total = cfg["capital"]["total"]
    rows = []
    if os.path.exists(journal_path):
        with open(journal_path) as f:
            rows = list(csv.DictReader(f))
    month = date.today().strftime("%Y-%m")
    mtd = sum(float(r["pnl_usd"]) for r in rows if r["closed"].startswith(month))
    wins = [float(r["pnl_usd"]) for r in rows if float(r["pnl_usd"]) > 0]
    losses = [float(r["pnl_usd"]) for r in rows if float(r["pnl_usd"]) <= 0]
    L = [f"Equity ${equity:,.0f} | realized ${broker.s['realized']:+,.0f} | peak ${broker.s['peak']:,.0f}",
         f"This month realized: ${mtd:+,.0f} ({mtd / total:+.1%}) vs goal "
         f"{cfg['goals']['monthly_target_pct']}% / realistic {cfg['goals']['realistic_target_pct']}%",
         f"Closed trades: {len(rows)} | win rate {len(wins) / len(rows):.0%}" if rows else "Closed trades: 0"]
    if wins and losses:
        L.append(f"Avg win ${sum(wins) / len(wins):+.0f} | avg loss ${sum(losses) / len(losses):+.0f}")
    for kind in ("share", "option"):
        k = [float(r["pnl_usd"]) for r in rows if r["kind"] == kind]
        if k:
            L.append(f"  {kind}s: {len(k)} trades, ${sum(k):+,.0f}")
    return "\n".join(L)


def notify(text: str, cfg: dict, title: str = "SwingBot"):
    a = cfg.get("alerts", {})
    if a.get("console", True):
        print(text)
    topic = a.get("ntfy_topic")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=text.encode()[:4000],
                          headers={"Title": title}, timeout=10)
        except Exception as e:
            print(f"(phone alert failed: {e})")
