"""Strategy C (trend rotation) rule tests on synthetic prices. Run: python tests/test_rotation.py"""
import os
import shutil
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swingbot import rotation  # noqa: E402

CFG = {"top_n": 3, "momentum_days": 252, "sma_days": 200, "min_trade_usd": 100,
       "universe": ["UP1", "UP2", "UP3", "UP4", "DOWN1", "DOWN2"]}
N = 400
IDX = pd.bdate_range(end=date(2026, 10, 16), periods=N)


def series(daily_drift, seed):
    noise = np.random.default_rng(seed).normal(0, 0.001, N)
    return pd.Series(100 * np.exp(np.cumsum(daily_drift + noise)), index=IDX)


def closes(**drifts):
    return {s: series(d, i) for i, (s, d) in enumerate(drifts.items())}


def test_pick_holds_only_uptrends_best_first():
    c = closes(UP1=0.0010, UP2=0.0020, UP3=0.0015, UP4=0.0005, DOWN1=-0.001, DOWN2=-0.002)
    got = rotation.pick(c, CFG)
    assert got == ["UP2", "UP3", "UP1"], got           # top 3 by 12-month return; UP4 is 4th, downtrends excluded
    print("ok pick: strongest 3 uptrends, downtrends excluded:", got)


def test_pick_stays_in_cash_when_nothing_trends_up():
    c = closes(UP1=-0.001, UP2=-0.002, UP3=-0.0015, UP4=-0.0005, DOWN1=-0.001, DOWN2=-0.002)
    assert rotation.pick(c, CFG) == []
    c = closes(UP1=0.002, UP2=-0.002, UP3=-0.0015, UP4=-0.0005, DOWN1=-0.001, DOWN2=-0.002)
    assert rotation.pick(c, CFG) == ["UP1"]            # a lone qualifier gets one slot; the other two stay cash
    print("ok pick: all-down -> cash; one qualifier -> one slot (rest cash)")


def test_pick_needs_enough_history():
    short = {s: v.tail(150) for s, v in closes(UP1=0.002).items()}
    assert rotation.pick(short, CFG) == []
    print("ok pick: skips ETFs without 252 days of history")


def test_plan_sizes_positions_to_equal_slots():
    acct = {"cash": 5000.0, "holdings": {}}
    prices = {"A": 100.0, "B": 250.0, "C": 40.0}
    orders = rotation.plan(acct, prices, ["A", "B", "C"], CFG)
    assert [o["side"] for o in orders] == ["buy"] * 3
    spent = sum(o["qty"] * o["price"] for o in orders)
    assert spent <= 5000
    for o in orders:
        assert o["qty"] * o["price"] <= 5000 / 3 + 1e-9, o
    print(f"ok plan: 3 equal slots, ${spent:,.0f} of $5,000 invested")


def test_plan_sells_dropped_etfs_and_funds_the_new_one():
    acct = {"cash": 100.0, "holdings": {"OLD": {"qty": 30, "cost": 50.0}, "KEEP": {"qty": 15, "cost": 100.0}}}
    prices = {"OLD": 60.0, "KEEP": 110.0, "NEW": 50.0}
    orders = rotation.plan(acct, prices, ["KEEP", "NEW"], CFG)
    assert orders[0] == {"side": "sell", "symbol": "OLD", "qty": 30, "price": 60.0}, orders
    assert any(o["side"] == "buy" and o["symbol"] == "NEW" for o in orders), orders
    cash = 100.0 + 30 * 60.0 * (1 - rotation.SLIPPAGE)
    for o in orders:
        if o["side"] == "buy":
            cash -= o["qty"] * o["price"] * (1 + rotation.SLIPPAGE)
    assert cash >= 0, "never buys with money it does not have"
    print("ok plan: sells the ETF that dropped out, buys the new one within cash")


def test_account_round_trip_and_monthly_gate():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rot_test", "account.json")
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    a = rotation.Account(path, 5000)
    assert rotation.is_due(a, date(2026, 10, 1))
    a.apply([{"side": "buy", "symbol": "GLD", "qty": 10, "price": 200.0}], date(2026, 10, 1))
    assert a["holdings"]["GLD"]["qty"] == 10 and a["cash"] < 3000
    assert not rotation.is_due(a, date(2026, 10, 20)), "same month: not due again"
    assert rotation.is_due(a, date(2026, 11, 2)), "new month: due"
    sell = {"side": "sell", "symbol": "GLD", "qty": 10, "price": 220.0}
    a.apply([sell], date(2026, 11, 2))
    assert "GLD" not in a["holdings"] and sell["pnl"] > 0
    a.save()
    assert rotation.Account(path, 5000)["cash"] == a["cash"], "state reloads from disk"
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    print(f"ok account: buy/sell with slippage, P&L {sell['pnl']:+.0f}, monthly gate, save/reload")


def test_backtest_has_no_lookahead_and_goes_to_cash_in_a_crash():
    up = series(0.002, 1)
    crash = up.copy()
    crash.iloc[-60:] = crash.iloc[-60] * np.exp(np.cumsum(np.full(60, -0.02)))   # collapses in the last 60 days
    cfg = {**CFG, "universe": ["X"]}
    r_up = rotation.backtest(pd.DataFrame({"X": up}), cfg)
    r_crash = rotation.backtest(pd.DataFrame({"X": crash}), cfg)
    cut = r_up.index[-61]
    assert np.allclose(r_up.loc[:cut], r_crash.loc[:cut]), "an event after day T must not change returns before T"
    assert (1 + r_crash).prod() > 0.5 * (1 + r_up.iloc[:-60]).prod(), "trend filter moved to cash during the crash"
    print("ok backtest: no look-ahead, and the trend filter limited a crash")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("all rotation tests passed")
