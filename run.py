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
from swingbot.report import brief, notify, performance

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
    notify(brief(r, broker, cfg), cfg, "SwingBot brief")
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
    for name in ("test_rules.py", "test_rotation.py"):
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
    notify(text, cfg, "Rotation")
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


def cmd_tt_sync(args):
    """Reconcile the sandbox with both bots' paper accounts right now. `--dry` validates without sending."""
    from swingbot import rotation
    dry = "--dry" in args
    cfg, broker, eng = load()
    _swing_mirror(cfg, broker, eng, dry)
    acct = rotation.Account(os.path.join(HERE, "state_rotation", "account.json"), cfg["rotation"]["capital"])
    _, prices = rotation.load_live(set(cfg["rotation"]["universe"]) | set(acct["holdings"]))
    _rotation_mirror(cfg, acct, prices, dry)



def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "core-test":
        sys.exit(cmd_core_test())
    if cmd == "demo":
        return cmd_demo()
    if cmd == "backtest":
        return cmd_backtest(float(sys.argv[2]) if len(sys.argv) > 2 else 2.0)
    if cmd == "rotate":
        return cmd_rotate(sys.argv[2:], mirror=True)
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
