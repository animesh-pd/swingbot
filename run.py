"""SwingBot command line.

  python run.py scan        manage positions, then look for entries (paper orders in paper mode)
  python run.py monitor     check stops/targets on open positions only
  python run.py watch       run all day: monitor every 15 min, scan at 3:45 PM ET, brief at close
  python run.py news        show every theme's headline counts and top headlines
  python run.py report      performance vs. your monthly goal
  python run.py positions   list open positions
  python run.py backtest [years]  replay the rules on past prices (needs internet; default 2 years)
  python run.py rotate [--record]  Strategy C: monthly ETF trend-rotation plan (own $5k account, state_rotation/)
  python run.py rotate-backtest    replay Strategy C on 2007-now (needs internet)
  python run.py tt-sync [--dry]    match the tastytrade SANDBOX to both bots' paper accounts (--dry = validate only)
  python run.py weekly [--dry]     Friday summary: trades, P&L, both bots vs SPY (pushed to your phone)
  python run.py can-run <job>      would this job run right now? (scan|monitor|rotate|weekly|keepawake; checks holidays and market hours)
  python run.py notify-test        send one test message to your phone (ntfy)
  python run.py tt-fills [--dry]   copy real sandbox fill prices into the paper accounts (also runs after scan / rotate / tt-sync)
  python run.py tt-check   read-only tastytrade sandbox connection test (places no orders)
  python run.py tt-dryrun   build today's scan entries as sandbox orders and validate them (dry run only)
  python run.py core-test   run the offline unit tests (tests/test_rules.py and tests/test_rotation.py)
  python run.py demo        offline dry run on fake prices (no internet needed)
  python run.py reset-killswitch
"""
import os
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import yaml

from swingbot.broker import PaperBroker
from swingbot.engine import Engine
from swingbot.report import brief, notify, performance, trade_alert

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo("America/New_York")


def load(state_dir="state", data=None, news_fetch=None, override=None):
    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    if override:
        override(cfg)   # applied before the broker reads its starting cash
    sd = os.path.join(HERE, state_dir)
    broker = PaperBroker(os.path.join(sd, "account.json"), os.path.join(sd, "journal.csv"), cfg)
    if data is None:
        from swingbot.data import LiveData
        data = LiveData()
    return cfg, broker, Engine(cfg, data, broker, news_fetch)


def cmd_scan(cfg, broker, eng, today=None):
    today = today or datetime.now(ET).date()
    r = eng.scan(today, place_orders=(cfg["mode"] == "paper"))
    notify(brief(r, broker, cfg), cfg, "SwingBot brief", push=False)   # long brief: console only
    alert = trade_alert(r, cfg)
    if alert:
        notify(alert, cfg, "SwingBot trade", console=False)
    return r


def cmd_monitor(cfg, broker, eng, today=None):
    today = today or datetime.now(ET).date()
    alerts = eng.manage(today)
    broker.save()
    if alerts:
        notify("\n".join(alerts), cfg, "SwingBot exit")
    return alerts


def cmd_watch(cfg, broker, eng):
    print("Watching. Ctrl+C to stop. (Market holidays aren't detected; it just finds no new prices.)")
    scanned_on = None
    while True:
        now = datetime.now(ET)
        open_ = now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)
        if open_:
            eng.data._cache = {}  # refresh prices
            if cmd_monitor(cfg, broker, eng, now.date()):
                _swing_mirror(cfg, broker, eng)
            if (now.hour, now.minute) >= (15, 45) and scanned_on != now.date():
                cmd_scan(cfg, broker, eng, now.date())
                _swing_mirror(cfg, broker, eng)
                scanned_on = now.date()
        time.sleep(15 * 60 if open_ else 5 * 60)


def cmd_demo():
    """Full dry run on synthetic data so you can see the bot work without internet."""
    import shutil
    from datetime import timedelta
    from swingbot.data import SyntheticData

    shutil.rmtree(os.path.join(HERE, "state_demo"), ignore_errors=True)
    start = date.today()
    fake_news = lambda kw, h: ["Markets steady as traders await data"] * 3
    data = SyntheticData(seed=11, drifts={"XOM": 0.003, "CAT": 0.003, "NVDA": 0.003,
                                          "AMD": 0.003, "SOFI": -0.003}, end=start)
    cfg, broker, eng = load("state_demo", data, fake_news)
    cfg["mode"] = "paper"
    print("=== Day 1: scan ===")
    cmd_scan(cfg, broker, eng, start)
    print("\n=== Day 5: prices move; monitor applies stops and targets ===")
    tickers = sorted({p["ticker"] for p in broker.positions})
    for t in tickers:  # everything rallies 22%: calls and shares win, the bearish put gets stopped out
        data.set_last(t, data.last_price(t) * 1.22)
    cmd_monitor(cfg, broker, eng, start + timedelta(days=4))
    print("\n=== Report ===")
    print(performance(broker.journal_path, broker, broker.equity(eng.marks(start)), cfg))


def cmd_core_test():
    import subprocess
    failed = 0
    for name in ("test_rules.py", "test_rotation.py", "test_market_calendar.py"):
        print(f"=== {name} ===")
        if subprocess.call([sys.executable, os.path.join(HERE, "tests", name)], cwd=HERE) != 0:
            failed += 1
    print("\nAll core tests passed." if not failed else f"\n{failed} test file(s) FAILED.")
    return 1 if failed else 0


def cmd_backtest(years):
    from swingbot.backtest import format_report, load_history, run_backtest
    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    tickers = set(cfg["shares"]["watchlist"]) | set(cfg["options"]["watchlist"])
    print(f"Downloading history for {len(tickers)} tickers (takes a minute)...")
    bars, earn = load_history(tickers, years)
    r = run_backtest(cfg, bars, earn, years, progress=print)
    text = format_report(r)
    print("\n" + text)
    os.makedirs(os.path.join(HERE, "state"), exist_ok=True)
    r["trades"].to_csv(os.path.join(HERE, "state", "backtest_trades.csv"), index=False)
    with open(os.path.join(HERE, "state", "backtest_report.txt"), "w") as f:
        f.write(text)
    print("\nSaved state/backtest_report.txt and state/backtest_trades.csv")


def cmd_rotate(args, today=None, closes=None, prices=None, mirror=False):
    """Strategy C monthly rotation. `closes`/`prices` can be passed in (tests); otherwise Yahoo is used."""
    from swingbot import rotation
    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    rc = cfg["rotation"]
    today = today or datetime.now(ET).date()
    acct = rotation.Account(os.path.join(HERE, "state_rotation", "account.json"), rc["capital"])
    if closes is None:
        closes, prices = rotation.load_live(set(rc["universe"]) | set(acct["holdings"]))
    missing = [s for s in rc["universe"] if s not in closes]
    if missing:
        print(f"(no data for {', '.join(missing)}; skipped)")
    targets = rotation.pick(closes, rc)
    orders = rotation.plan(acct, prices, targets, rc)
    due = rotation.is_due(acct, today)
    text = rotation.describe(orders, targets, acct, prices, rc)
    if "--record" in args or (rc["mode"] == "paper" and due):
        acct.apply(orders, today)
        acct.save()
        text += "\nRecorded to state_rotation/account.json."
    elif due:
        text += "\nThis is the monthly rebalance. After you place the trades, run: python run.py rotate --record"
    else:
        text += f"\nNot a rebalance day (last one: {acct.get('last_rebalance')}). Plan above is informational; trade monthly."
    notify(text, cfg, "Rotation", push=bool(orders) and due)
    if mirror:
        _rotation_mirror(cfg, acct, prices)
    return orders


def cmd_rotate_backtest(args):
    from swingbot import rotation
    with open(os.path.join(HERE, "config.yaml")) as f:
        rc = yaml.safe_load(f)["rotation"]
    import yfinance as yf
    print("Downloading ETF history since 2006...")
    px = yf.download(rc["universe"] + ["SPY"], start="2006-06-01", auto_adjust=True, progress=False)["Close"].dropna()
    r = rotation.backtest(px, rc)
    spy = px["SPY"].pct_change().loc[r.index]
    rows = []
    for name, s in (("Rotation", r), ("SPY buy&hold", spy)):
        eq = (1 + s).cumprod()
        m = (1 + s).groupby(s.index.to_period("M")).prod() - 1
        rows.append((name, eq.iloc[-1] ** (252 / len(s)) - 1, (1 - eq / eq.cummax()).max(), (m > 0).mean(), m))
    sm = rows[1][4]
    dn = sm < 0
    for name, cagr, dd, up, m in rows:
        print(f"{name:14} CAGR {cagr:+.1%} | max drawdown {dd:.0%} | months up {up:.0%} | "
              f"avg return in SPY-down months {m[dn].mean():+.1%}")
    print(f"Correlation of monthly returns to SPY: {rows[0][4].corr(sm):.2f}   ({r.index[0].date()} to {r.index[-1].date()})")
    print("CAUTION: ETF universe chosen with hindsight; results are a stress test, not a forecast.")


def _swing_mirror(cfg, broker, eng, dry_run=False):
    """Copy the swing bot's paper SHARE positions to the tastytrade sandbox (no-op unless send_orders is on)."""
    from swingbot import tastytrade_conn as tt
    if cfg.get("mode") != "paper" or not (tt.require_send_enabled(cfg) or dry_run):
        return
    desired = {}
    for p in broker.positions:
        if p["kind"] == "share":
            desired[p["ticker"]] = desired.get(p["ticker"], 0) + p["qty"]
    managed = set(cfg["shares"]["watchlist"]) | set(desired)
    if managed & set(cfg["rotation"]["universe"]):
        raise SystemExit("swing watchlist and rotation universe overlap; they would fight over the same sandbox positions.")
    prices = {s: eng.data.last_price(s) for s in managed}
    try:
        print("Sandbox:", tt.sync_positions(cfg, desired, managed, prices, dry_run))
    except tt.MissingCredentials as e:
        print(e)
    except Exception as e:   # a broker hiccup must never stop the bot itself
        print(f"Sandbox sync failed: {type(e).__name__}: {e}")


def _rotation_mirror(cfg, acct, prices, dry_run=False):
    from swingbot import tastytrade_conn as tt
    if cfg["rotation"]["mode"] != "paper" or not (tt.require_send_enabled(cfg) or dry_run):
        return
    desired = {s: h["qty"] for s, h in acct["holdings"].items()}
    managed = set(cfg["rotation"]["universe"]) | set(desired)
    try:
        print("Sandbox:", tt.sync_positions(cfg, desired, managed, prices, dry_run))
    except tt.MissingCredentials as e:
        print(e)
    except Exception as e:
        print(f"Sandbox sync failed: {type(e).__name__}: {e}")


def _apply_fills(cfg, dry_run=False):
    """Replace paper entry prices with the sandbox's real fill prices, once per position.
    Only buys, and only when the fills cover the whole position. Cash, stop and target move with the entry."""
    from datetime import timedelta
    from swingbot import rotation, tastytrade_conn as tt
    tt.require_send_enabled(cfg)   # refuses unless sandbox mode; reading fills places no orders, so it also runs while send_orders is off
    broker = PaperBroker(os.path.join(HERE, "state", "account.json"), os.path.join(HERE, "state", "journal.csv"), cfg)
    acct = rotation.Account(os.path.join(HERE, "state_rotation", "account.json"), cfg["rotation"]["capital"])
    swing = [p for p in broker.positions if p["kind"] == "share" and not p.get("fill_synced")]
    rot = {s: h for s, h in acct["holdings"].items() if not h.get("fill_synced")}
    if not swing and not rot:
        return
    first = [p["entry_date"] for p in swing] + [date.fromisoformat(l["date"]) for l in acct["log"] if l["side"] == "buy"]
    try:
        fills = tt.buy_fills(cfg, {p["ticker"] for p in swing} | set(rot), min(first) - timedelta(days=1))
    except tt.MissingCredentials as e:
        return print(e)
    except Exception as e:
        return print(f"Fill sync failed: {type(e).__name__}: {e}")
    lines, changed = [], False
    for p in swing:
        px = tt.vwap_for(fills.get(p["ticker"], []), p["qty"])
        if px is None:
            continue
        px, old = round(px, 2), p["entry"]
        lines.append(f"{p['ticker']}: paper entry {old:.2f} -> sandbox fill {px:.2f} ({(px - old) * p['qty']:+.2f} on {p['qty']} shares)")
        if not dry_run:
            broker.s["cash"]["share"] += (old - px) * p["qty"]
            p["stop"], p["target"] = round(p["stop"] * px / old, 2), round(p["target"] * px / old, 2)
            p.update(entry=px, high=px, fill_synced=True, paper_entry=old)
            changed = True
    for s_, h in rot.items():
        px = tt.vwap_for(fills.get(s_, []), h["qty"])
        if px is None:
            continue
        old = h["cost"]
        lines.append(f"{s_}: paper cost {old:.2f} -> sandbox fill {px:.2f} ({(px - old) * h['qty']:+.2f} on {h['qty']} shares)")
        if not dry_run:
            acct.s["cash"] += (old - px) * h["qty"]
            h.update(cost=px, fill_synced=True, paper_cost=old)
            for l in acct["log"]:   # keep the trade log in step with the real fill
                if l["symbol"] == s_ and l["side"] == "buy" and l["qty"] == h["qty"]:
                    l["fill"] = round(px, 4)
            changed = True
    if lines:
        print("Fill sync" + (" (dry run)" if dry_run else "") + ":")
        for line in lines:
            print("  " + line)
    if changed:
        broker.save()
        acct.save()


def cmd_tt_sync(args):
    """Reconcile the sandbox with both bots' paper accounts right now. `--dry` validates without sending."""
    from swingbot import rotation
    dry = "--dry" in args
    cfg, broker, eng = load()
    _swing_mirror(cfg, broker, eng, dry)
    acct = rotation.Account(os.path.join(HERE, "state_rotation", "account.json"), cfg["rotation"]["capital"])
    _, prices = rotation.load_live(set(cfg["rotation"]["universe"]) | set(acct["holdings"]))
    _rotation_mirror(cfg, acct, prices, dry)
    _apply_fills(cfg, dry)


def _closes(symbols):
    """Recent unadjusted daily closes per symbol (Yahoo)."""
    import logging
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    df = yf.download(sorted(symbols), period="2y", auto_adjust=False, progress=False)["Close"]   # long enough for any start date
    return {s_: df[s_].dropna() for s_ in df.columns if df[s_].dropna().size}


def cmd_weekly(args, today=None):
    """Friday summary: trades, realized and open P&L, both bots vs SPY. Pushed to the phone. `--dry` saves nothing."""
    import csv
    import json
    from swingbot import rotation, summary
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    today = today or datetime.now(ET).date()
    ws = summary.monday_of(today)
    in_week = lambda iso: ws <= date.fromisoformat(str(iso)[:10]) <= today   # noqa: E731
    broker = PaperBroker(os.path.join(HERE, "state", "account.json"), os.path.join(HERE, "state", "journal.csv"), cfg)
    acct = rotation.Account(os.path.join(HERE, "state_rotation", "account.json"), cfg["rotation"]["capital"])
    closes = _closes({p["ticker"] for p in broker.positions} | set(acct["holdings"]) | {"SPY"})
    last = {s_: float(c.iloc[-1]) for s_, c in closes.items()}
    spy = closes["SPY"]
    snap_path = os.path.join(HERE, "state", "weekly_snapshots.json")
    snaps = json.load(open(snap_path)) if os.path.exists(snap_path) else {}
    # baseline for "since start": config summary.start_date, else the date of the first weekly summary, else today
    start_day = date.fromisoformat(str((cfg.get("summary") or {}).get("start_date") or (min(snaps) if snaps else today.isoformat())))
    before_start = spy[spy.index.date <= start_day]
    spy_start = float(before_start.iloc[-1] if len(before_start) else spy.iloc[0])
    spy_prev = float(spy[spy.index.date < ws].iloc[-1])

    swing_eq = broker.equity({p["id"]: last.get(p["ticker"], p["entry"]) for p in broker.positions})
    rot_eq = acct.equity({s_: last.get(s_, h["cost"]) for s_, h in acct["holdings"].items()})
    before = [k for k in snaps if k < today.isoformat()]
    swing_start = cfg["capital"]["shares_bucket"] + cfg["capital"]["options_bucket"]   # the swing bot's own $5,000
    prev = snaps[max(before)] if before else {"swing": swing_start, "rotation": cfg["rotation"]["capital"]}

    trades, realized, rows = [], 0.0, []
    if os.path.exists(broker.journal_path):
        rows = list(csv.DictReader(open(broker.journal_path)))
    for p in broker.positions:   # still open, bought this week
        if p["kind"] == "share" and in_week(p["entry_date"]):
            trades.append(f"BUY {p['qty']} {p['ticker']} @ {p['entry']:.2f} ({date.fromisoformat(str(p['entry_date'])[:10]):%a})")
    for r_ in rows:
        if in_week(r_["opened"]):
            trades.append(f"BUY {r_['qty']} {r_['position']} @ {float(r_['entry']):.2f} ({date.fromisoformat(r_['opened']):%a})")
        if in_week(r_["closed"]):
            realized += float(r_["pnl_usd"])
            trades.append(f"SELL {r_['qty']} {r_['position']} @ {float(r_['exit']):.2f} P&L ${float(r_['pnl_usd']):+,.2f} ({r_['why_out'][:30]})")
    for l in acct["log"]:
        if in_week(l["date"]):
            extra = f" P&L ${l['pnl']:+,.2f}" if l.get("pnl") is not None else ""
            if l.get("pnl") is not None:
                realized += l["pnl"]
            trades.append(f"{l['side'].upper()} {l['qty']} {l['symbol']} @ {l['fill']:.2f}{extra} ({date.fromisoformat(l['date']):%a}, rotation)")
    open_ = [{"symbol": p["ticker"], "pnl": (last.get(p["ticker"], p["entry"]) - p["entry"]) * p["qty"],
              "pct": last.get(p["ticker"], p["entry"]) / p["entry"] - 1} for p in broker.positions if p["kind"] == "share"]
    open_ += [{"symbol": s_, "pnl": (last.get(s_, h["cost"]) - h["cost"]) * h["qty"],
               "pct": last.get(s_, h["cost"]) / h["cost"] - 1} for s_, h in acct["holdings"].items()]
    text = summary.format_weekly({
        "week_start": ws, "week_end": today,
        "bots": [{"name": "Swing", "equity": swing_eq, "start": swing_start, "prev": prev["swing"]},
                 {"name": "Rotation", "equity": rot_eq, "start": cfg["rotation"]["capital"], "prev": prev["rotation"]}],
        "spy": {"week": last["SPY"] / spy_prev - 1, "since_start": last["SPY"] / spy_start - 1},
        "trades": trades, "realized_week": realized, "open": open_})
    if "--dry" not in args:
        snaps[today.isoformat()] = {"swing": swing_eq, "rotation": rot_eq, "spy": last["SPY"]}
        os.makedirs(os.path.dirname(snap_path), exist_ok=True)
        json.dump(snaps, open(snap_path, "w"), indent=1)
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        open(os.path.join(HERE, "logs", f"weekly-{today.isoformat()}.txt"), "w", encoding="utf-8").write(text)
    notify(text, cfg, "SwingBot weekly summary", push="--dry" not in args)


def cmd_notify_test():
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    topic = cfg.get("alerts", {}).get("ntfy_topic")
    notify("SwingBot test: if you can read this on your phone, trade alerts and the weekly summary will reach you.",
           cfg, "SwingBot test")
    print("Pushed to ntfy." if topic else "No alerts.ntfy_topic set in config.yaml, so nothing was pushed.")



def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "core-test":
        sys.exit(cmd_core_test())
    if cmd == "demo":
        return cmd_demo()
    if cmd == "backtest":
        return cmd_backtest(float(sys.argv[2]) if len(sys.argv) > 2 else 2.0)
    if cmd == "can-run":   # used by the scheduler scripts: exit 0 = go, exit 1 = skip (reason printed)
        from swingbot import market_calendar
        ok, why = market_calendar.should_run(sys.argv[2] if len(sys.argv) > 2 else "scan")
        print(why)
        sys.exit(0 if ok else 1)
    if cmd == "weekly":
        return cmd_weekly(sys.argv[2:])
    if cmd == "notify-test":
        return cmd_notify_test()
    if cmd == "rotate":
        cmd_rotate(sys.argv[2:], mirror=True)
        return _apply_fills(yaml.safe_load(open(os.path.join(HERE, "config.yaml"))))
    if cmd == "tt-fills":
        return _apply_fills(yaml.safe_load(open(os.path.join(HERE, "config.yaml"))), "--dry" in sys.argv)
    if cmd == "tt-sync":
        return cmd_tt_sync(sys.argv[2:])
    if cmd == "rotate-backtest":
        return cmd_rotate_backtest(sys.argv[2:])
    if cmd == "tt-check":
        from swingbot import tastytrade_conn
        with open(os.path.join(HERE, "config.yaml")) as f:
            tt_cfg = yaml.safe_load(f)
        try:
            return print(tastytrade_conn.check(tt_cfg))
        except tastytrade_conn.MissingCredentials as e:
            return print(e)
    if cmd == "tt-dryrun":
        from swingbot import tastytrade_conn
        cfg, broker, eng = load()
        r = eng.scan(datetime.now(ET).date(), place_orders=False)   # never touches the paper account
        print(f"{len(r['entries'])} entries from the scan; validating each on the tastytrade sandbox (dry run):")
        try:
            return print(tastytrade_conn.dry_run(r["entries"]))
        except tastytrade_conn.MissingCredentials as e:
            return print(e)
    cfg, broker, eng = load()
    if cmd == "scan":
        cmd_scan(cfg, broker, eng)
        _swing_mirror(cfg, broker, eng)
        _apply_fills(cfg)
    elif cmd == "monitor":
        if cmd_monitor(cfg, broker, eng):
            _swing_mirror(cfg, broker, eng)
    elif cmd == "watch":
        cmd_watch(cfg, broker, eng)
    elif cmd == "news":
        from swingbot import news as newsmod
        risk, themes = newsmod.scan(cfg, eng.news_fetch, history_path=eng.news_history,
                                   today=datetime.now(ET).date())
        for t in themes:
            basis = (f"typical {t.baseline:.0f}" if t.baseline is not None else
                     f"building baseline {t.days}/5 days; fallback cap {cfg['news'].get('hot_headlines_for_max', 60)}")
            print(f"{t.theme:16} risk {t.risk:.2f}  ({t.hot} escalation / {t.total} on-topic; {basis})")
            for h in t.headlines[:3]:
                print(f"    - {h[:110]}")
        print("\nTicker risk:", {k: v for k, v in sorted(risk.items(), key=lambda x: -x[1])})
    elif cmd == "report":
        today = datetime.now(ET).date()
        print(performance(broker.journal_path, broker, broker.equity(eng.marks(today)), cfg))
    elif cmd == "positions":
        for p in broker.positions:
            print(p)
        print(f"cash: {broker.s['cash']}") if broker.positions else print("No open positions.")
    elif cmd == "reset-killswitch":
        broker.reset_killswitch(broker.equity(eng.marks(datetime.now(ET).date())))
        broker.save()
        print("Kill switch reset. Peak equity set to current equity.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
