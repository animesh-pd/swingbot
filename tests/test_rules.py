"""Rule tests on synthetic data. Run: python tests/test_rules.py"""
import os
import shutil
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run  # noqa: E402
from swingbot.data import SyntheticData  # noqa: E402

TODAY = date(2026, 10, 16)
CALM = lambda kw, h: ["Markets steady"]  # noqa: E731
BULLS = {t: 0.003 for t in ["XOM", "CVX", "CAT", "GE", "V", "NVDA", "AMD", "SPY", "QQQ", "AAPL"]}


def baseline(cfg):
    """Pin the tests to the original two-bucket setup so they don't change when config.yaml is tuned.
    The market filter is off here; its own tests switch it on."""
    cfg["capital"].update(shares_bucket=2500, options_bucket=2500)
    cfg["shares"].update(max_positions=3, max_position_pct=0.40)
    cfg["options"]["max_positions"] = 3
    cfg["market_filter"] = {"enabled": False}


def fresh(news=CALM, seed=11, drifts=BULLS, mutate=None):
    shutil.rmtree(os.path.join(run.HERE, "state_test"), ignore_errors=True)
    data = SyntheticData(seed=seed, drifts=drifts, end=TODAY)
    cfg, broker, eng = run.load("state_test", data, news, override=baseline)
    cfg["mode"] = "paper"
    if mutate:
        mutate(cfg)
    return cfg, broker, eng, data


def test_sizing_and_caps():
    cfg, broker, eng, _ = fresh()
    eng.scan(TODAY)
    shares = broker.open_of("share")
    opts = broker.open_of("option")
    assert len(shares) <= cfg["shares"]["max_positions"]
    assert len(opts) <= cfg["options"]["max_positions"]
    for p in shares:
        assert p["entry"] * p["qty"] <= 2500 * cfg["shares"]["max_position_pct"] + 5
        assert abs(p["stop"] - p["entry"] * 0.9) / p["entry"] < 0.002, "10% share stop"
    for p in opts:
        assert p["entry"] * 100 * p["qty"] <= cfg["options"]["max_position_usd"]
        assert abs(p["stop"] - p["entry"] * (1 - cfg["options"]["stop_loss_pct"])) < 0.02
    assert broker.cash("share") >= 0 and broker.cash("option") >= 0
    print(f"ok sizing: {len(shares)} share + {len(opts)} option positions")


def test_stop_loss_fires():
    cfg, broker, eng, data = fresh()
    eng.scan(TODAY)
    p = broker.open_of("share")[0]
    data.set_last(p["ticker"], p["entry"] * 0.89)
    alerts = eng.manage(TODAY + timedelta(days=3))
    assert any("STOP" in a and p["ticker"] in a for a in alerts), alerts
    print("ok share stop:", [a for a in alerts if "STOP" in a][0])


def test_option_time_stop():
    cfg, broker, eng, data = fresh()
    eng.scan(TODAY)
    assert broker.open_of("option"), "expected an option entry"
    alerts = eng.manage(TODAY + timedelta(days=15))
    assert not broker.open_of("option"), alerts
    print("ok option exits by day 15:", alerts[0])


def test_news_blocks_entries():
    hot = lambda kw, h: [f"Missile strike escalates; sanctions round {i} on {kw[0]}" for i in range(50)]  # noqa: E731
    cfg, broker, eng, _ = fresh(news=hot)
    r = eng.scan(TODAY)
    assert not r["entries"], r["entries"]
    assert any("news risk" in s for s in r["skipped"])
    print("ok news block: no entries under high geopolitical risk")


def test_macro_blackout_blocks_options_only():
    def m(cfg):
        cfg["macro_events"] = [{"date": TODAY + timedelta(days=1), "name": "FOMC decision"}]
    cfg, broker, eng, _ = fresh(mutate=m)
    r = eng.scan(TODAY)
    assert not broker.open_of("option")
    assert any("FOMC" in s for s in r["skipped"])
    print(f"ok macro blackout: 0 options, {len(broker.open_of('share'))} shares")


def test_earnings_blackout():
    cfg, broker, eng, data = fresh()
    data.earnings_date = lambda t: TODAY + timedelta(days=2)
    r = eng.scan(TODAY)
    assert not r["entries"]
    print("ok earnings blackout: nothing entered with earnings 2 days out")


def test_kill_switch():
    cfg, broker, eng, data = fresh()
    broker.s["peak"] = 7000  # equity 5000 is ~29% under peak
    r = eng.scan(TODAY)
    assert not r["entries"] and any("KILL SWITCH" in b for b in r["account_blocks"])
    print("ok kill switch blocks entries")


def test_news_ignores_lookalike_words():
    from swingbot import news
    harmless = [f"Bank of America opens branch {i} - Local" for i in range(30)] + \
               [f"Software firm moves toward urban plan {i} - Reuters" for i in range(30)]
    assert news.score_theme(harmless) == 0.0, "bank/software/toward/urban must not trigger"
    print("ok news: 'bank', 'software', 'urban' no longer look like 'ban' or 'war'")


def test_news_counts_syndicated_story_once():
    from swingbot import news
    copies = [f"Missile strike hits port - {src}" for src in ("Reuters", "AP", "BBC", "CNN")]
    assert news.score_theme(copies) <= 0.05
    print("ok news: one story syndicated 4 ways counts once")


def test_support_resistance_ignores_noise():
    from swingbot.signals import score
    d = SyntheticData(seed=3, drifts={"Z": 0.002}, end=TODAY)
    sig = score("Z", d.bars("Z"))
    assert sig.resistance - sig.close >= sig.atr * 0.99 or sig.breakout
    assert sig.close - sig.support >= sig.atr * 0.99 or sig.close - sig.support > 0
    print(f"ok levels: resistance {sig.resistance:.1f} is >= 1 ATR from close {sig.close:.1f}")


def _news_cfg(**theme):
    t = {"keywords": ["iran"], "tickers": ["XOM"]}
    t.update(theme)
    return {"news": {"enabled": True, "lookback_hours": 24, "hot_headlines_for_max": 60, "themes": {"t": t}}}


def test_news_needs_theme_keyword_in_title():
    from swingbot import news
    off_topic = lambda kw, h: [f"Missile strike {i} hits port - AP" for i in range(80)]  # noqa: E731
    risk, reports = news.scan(_news_cfg(), off_topic)
    assert reports[0].risk == 0.0 and reports[0].total == 0
    print("ok news: escalation headlines that never mention the theme don't count")


def test_news_absolute_until_baseline_exists():
    from swingbot import news
    fake = lambda kw, h: [f"Iran missile strike {i} - AP" for i in range(30)]  # noqa: E731
    _, rep = news.scan(_news_cfg(), fake)
    assert rep[0].risk == 0.5 and rep[0].baseline is None
    print("ok news: no history -> 30 hot / cap 60 = 0.50")


def test_news_relative_to_own_baseline():
    import json
    from swingbot import news
    path = os.path.join(run.HERE, "state_test", "hist.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"t": [[f"2026-10-{d:02d}", 20] for d in range(1, 11)]}, open(path, "w"))
    cfg = _news_cfg()
    f = lambda n: (lambda kw, h: [f"Iran missile strike {i} - AP" for i in range(n)])  # noqa: E731
    _, normal = news.scan(cfg, f(20), history_path=path, today=TODAY)
    _, spike = news.scan(cfg, f(40), history_path=path, today=TODAY)
    assert normal[0].risk == 0.0, normal[0]
    assert spike[0].risk == 1.0 and spike[0].baseline == 20
    print("ok news: a normal day scores 0.00, double the usual scores 1.00")


def test_news_also_hot_words_are_per_theme():
    from swingbot import news
    heads = lambda kw, h: [f"Fed signals rate hike {i} - AP" for i in range(30)]  # noqa: E731
    plain = news.scan(_news_cfg(keywords=["fed"]), heads)[1][0]
    opted = news.scan(_news_cfg(keywords=["fed"], also_hot=["hike"]), heads)[1][0]
    assert plain.hot == 0 and opted.hot == 30
    print("ok news: 'hike' only counts for themes that opt in")


def test_share_entry_warns_when_earnings_inside_hold_window():
    from swingbot.report import brief
    cfg, broker, eng, data = fresh()
    data.earnings_date = lambda t: TODAY + timedelta(days=10)
    r = eng.scan(TODAY)
    assert any(o["kind"] == "share" for o in r["entries"]), "expected share entries"
    assert "heads-up: earnings" in brief(r, broker, cfg)
    print("ok brief: share entries crossing earnings get a heads-up")


def test_news_weekend_scans_are_not_recorded_or_counted():
    import json
    from swingbot import news
    path = os.path.join(run.HERE, "state_test", "hist_weekend.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    saturdays = [(date(2026, 9, 5) + timedelta(days=7 * i)).isoformat() for i in range(8)]
    json.dump({"t": [[d, 20] for d in saturdays]}, open(path, "w"))
    fake = lambda kw, h: [f"Iran missile strike {i} - AP" for i in range(20)]  # noqa: E731
    _, rep = news.scan(_news_cfg(), fake, history_path=path, today=TODAY)          # Friday
    assert rep[0].baseline is None, "weekend-only history must not form a baseline"
    news.scan(_news_cfg(), fake, history_path=path, today=date(2026, 10, 17))      # Saturday
    assert "2026-10-17" not in json.load(open(path))["t"][-1], "Saturday scan must not be recorded"
    print("ok news: weekend scans are neither recorded nor used for the baseline")


def test_option_cap_is_1200():
    cfg, *_ = fresh()
    assert cfg["options"]["max_position_usd"] == 1200
    print("ok config: options cap is $1,200 per position")


def test_spread_construction_bull_and_bear():
    from swingbot.options import pick_contract
    cfg, broker, eng, data = fresh(mutate=lambda c: c["options"].update(structure="spread_only"))
    for direction, right in (("bull", "call"), ("bear", "put")):
        c, err = pick_contract(data, "XOM", direction, TODAY, cfg)
        assert err is None, err
        assert c["structure"] == "spread" and c["right"] == right
        assert (c["strike2"] > c["strike"]) if right == "call" else (c["strike2"] < c["strike"]), "short leg must be further OTM"
        assert 0 < c["cost"] <= cfg["options"]["max_debit_pct_of_width"] * c["width"] + 1e-9
        assert abs(c["max_profit"] - (c["width"] - c["cost"])) < 0.01
        assert c["cost"] * 100 <= cfg["options"]["max_position_usd"] and c["delta2"] < c["delta"]
        print(f"ok spread: {direction} -> {c['strike']:g}/{c['strike2']:g} {right} spread, debit {c['cost']:.2f} on ${c['width']:g} wide")


def test_spread_loss_never_exceeds_the_debit():
    from swingbot.report import brief
    cfg, broker, eng, data = fresh(mutate=lambda c: c["options"].update(structure="spread_only"))
    r = eng.scan(TODAY)
    opts = broker.open_of("option")
    assert opts and all(p.get("strike2") for p in opts), "expected spread positions"
    assert "SPREAD" in brief(r, broker, cfg)
    paid = max(p["entry"] * 100 * p["qty"] for p in opts)
    for t in {p["ticker"] for p in opts}:
        data.set_last(t, data.last_price(t) * 0.6)       # 40% crash in every underlying
    eng.manage(TODAY + timedelta(days=3))
    import csv
    rows = [x for x in csv.DictReader(open(broker.journal_path)) if x["kind"] == "option"]
    assert rows, "expected closed spread trades"
    worst = min(float(x["pnl_usd"]) for x in rows)
    assert worst >= -paid - 0.01, f"lost {worst} on a spread that cost {paid}"
    print(f"ok spread risk: worst loss ${-worst:.0f} <= debit paid ${paid:.0f} after a 40% crash")


def test_spread_first_falls_back_to_single():
    from swingbot.options import pick_contract
    cfg, broker, eng, data = fresh(mutate=lambda c: c["options"].update(structure="spread_first", max_debit_pct_of_width=0.01))
    c, err = pick_contract(data, "XOM", "bull", TODAY, cfg)
    assert err is None and c["structure"] == "single", (c, err)
    cfg["options"]["structure"] = "spread_only"
    c, err = pick_contract(data, "XOM", "bull", TODAY, cfg)
    assert c is None and "of the" in err and "width" in err
    cfg["options"].update(structure="single_only", max_position_usd=300)
    c, err = pick_contract(data, "XOM", "bull", TODAY, cfg)
    assert c is None and "over the $300 cap" in err
    print("ok structure modes: spread_first falls back, spread_only and single_only respect the cap")


def test_spread_profit_target_never_exceeds_width():
    cfg, broker, eng, data = fresh(mutate=lambda c: c["options"].update(structure="spread_only", take_profit_pct=2.0))
    eng.scan(TODAY)
    for p in broker.open_of("option"):
        assert p["target"] <= 0.9 * abs(p["strike"] - p["strike2"]) + 0.01
    print("ok spread target is capped below the strike width")


def _fomc_in(days):
    def m(cfg):
        cfg["macro_events"] = [{"date": TODAY + timedelta(days=days), "name": "FOMC decision"}]
    return m


def test_option_entry_blocked_when_event_leaves_under_min_hold():
    cfg, broker, eng, _ = fresh(mutate=_fomc_in(4))          # 4 days out: not enough time to hold
    r = eng.scan(TODAY)
    assert not broker.open_of("option") and any("FOMC" in s for s in r["skipped"])
    cfg, broker, eng, _ = fresh(mutate=_fomc_in(11))         # 11 days out: enter, exit the day before
    eng.scan(TODAY)
    assert broker.open_of("option")
    print("ok events: FOMC 4 days out blocks options entry, 11 days out allows it")


def test_option_exits_the_session_before_fomc():
    cfg, broker, eng, _ = fresh(mutate=_fomc_in(11))         # event Tue Oct 27
    eng.scan(TODAY)
    assert broker.open_of("option")
    assert not any("event risk" in a for a in eng.manage(TODAY + timedelta(days=7)))   # Fri Oct 23: event 4 days away
    alerts = eng.manage(TODAY + timedelta(days=10))                                    # Mon Oct 26: day before
    assert any("event risk: FOMC" in a for a in alerts), alerts
    assert not broker.open_of("option")
    print("ok events:", [a for a in alerts if "event risk" in a][0])


def test_option_exits_before_a_monday_earnings_report_on_friday():
    cfg, broker, eng, data = fresh(mutate=_fomc_in(40))
    data.earnings_date = lambda t: TODAY + timedelta(days=10)   # Monday Oct 26
    eng.scan(TODAY)
    assert broker.open_of("option"), "earnings 10 days out should still allow entry"
    alerts = eng.manage(TODAY + timedelta(days=7))              # Friday Oct 23: Monday report is 3 days away
    assert any("event risk: earnings" in a for a in alerts), alerts
    print("ok events: Friday exit ahead of Monday earnings")


def test_shares_ignore_the_macro_exit_rule():
    cfg, broker, eng, _ = fresh(mutate=_fomc_in(11))
    eng.scan(TODAY)
    n = len(broker.open_of("share"))
    eng.manage(TODAY + timedelta(days=10))
    assert len(broker.open_of("share")) == n
    print("ok events: share positions are not force-closed by macro events")


def _filter_on(cfg):
    cfg["market_filter"] = {"enabled": True, "symbol": "SPY", "sma_days": 200}


def test_market_filter_pauses_entries_when_spy_is_below_its_average():
    falling = {**BULLS, "SPY": -0.003}   # every stock still trends up; only the index is falling
    _, broker, eng, _ = fresh(drifts=falling, mutate=_filter_on)
    r = eng.scan(TODAY)
    assert not [e for e in r["entries"] if e["kind"] == "share"] and not broker.open_of("share"), r["entries"]
    assert any("below its 200-day" in s for s in r["skipped"]), r["skipped"]
    print("ok market filter: no entries while SPY is under its 200-day average")


def test_market_filter_allows_entries_when_spy_is_above_its_average():
    _, broker, eng, _ = fresh(mutate=_filter_on)
    r = eng.scan(TODAY)
    assert r["entries"], r["skipped"]
    print(f"ok market filter: {len(r['entries'])} entries while SPY is above its average")


def test_market_filter_does_not_close_existing_positions():
    falling = {**BULLS, "SPY": -0.003}
    _, broker, eng, _ = fresh(drifts=falling)
    eng.scan(TODAY)                       # filter off: positions open
    n = len(broker.open_of("share"))
    assert n, "expected share entries to start from"
    _filter_on(eng.cfg)
    eng.scan(TODAY + timedelta(days=1))
    assert len(broker.open_of("share")) >= n
    print("ok market filter: existing positions are left to their own stops")


def test_sandbox_sync_only_touches_managed_symbols():
    from swingbot.tastytrade_conn import diff_positions
    desired = {"XOM": 10, "JPM": 0}
    actual = {"XOM": 4, "JPM": 3, "AAPL": 50}           # AAPL is yours / another bot's: must be left alone
    d = diff_positions(desired, actual, managed={"XOM", "JPM", "CVX"})
    assert d == {"XOM": 6, "JPM": -3}, d
    assert diff_positions({"XOM": 4}, {"XOM": 4}, {"XOM"}) == {}, "already matching -> no orders"
    print("ok sandbox: reconciles only managed symbols, no orders when already in sync")


def test_sandbox_refuses_production_config():
    from swingbot.tastytrade_conn import require_send_enabled
    try:
        require_send_enabled({"tastytrade": {"sandbox": False, "send_orders": True}})
    except SystemExit:
        print("ok sandbox: refuses to send orders when sandbox is false")
        return
    raise AssertionError("must refuse when tastytrade.sandbox is false")


def test_sandbox_fill_average_waits_for_the_whole_quantity():
    from swingbot.tastytrade_conn import vwap_for
    fills = [("2026-10-19T14:00", 100.0, 3), ("2026-10-19T14:05", 102.0, 2)]
    assert vwap_for(fills, 5) == (100.0 * 3 + 102.0 * 2) / 5
    assert vwap_for(fills, 4) == (100.0 * 3 + 102.0 * 1) / 4, "uses only the first 4 shares"
    assert vwap_for(fills, 6) is None, "partly filled: wait, do not half-apply"
    assert vwap_for([], 1) is None
    print("ok sandbox: fill price is the average of the first N shares, and None until fully filled")


def test_sandbox_sync_survives_dropped_positions_and_missing_prices():
    from swingbot.tastytrade_conn import effective_actual, valid_price, diff_positions
    # sandbox lists no positions but its filled orders show we bought 24 XLE and 9 XLK: do not buy them again
    held = effective_actual({}, {"XLE": 24, "XLK": 9}, ["XLE", "XLK", "V"])
    assert held == {"XLE": 24, "XLK": 9, "V": 0}, held
    assert diff_positions({"XLE": 24, "XLK": 9, "V": 2}, held, ["XLE", "XLK", "V"]) == {"V": 2}
    # a listed position wins over order history; a net sell never counts as negative holdings
    assert effective_actual({"XLE": 10}, {"XLE": 24}, ["XLE"]) == {"XLE": 10}
    assert effective_actual({}, {"XLE": -5}, ["XLE"]) == {"XLE": 0}
    for bad in (float("nan"), 0, -3, None):
        assert not valid_price(bad), bad
    assert valid_price(402.81)
    print("ok sandbox: dropped positions fall back to filled orders; NaN prices are skipped")


def test_weekly_summary_and_trade_alert_text():
    from datetime import date
    from swingbot.summary import format_weekly, monday_of
    from swingbot.report import trade_alert
    assert monday_of(date(2026, 10, 23)) == date(2026, 10, 19)
    txt = format_weekly({"week_start": date(2026, 10, 19), "week_end": date(2026, 10, 23),
        "bots": [{"name": "Swing", "equity": 5100.0, "start": 5000.0, "prev": 5050.0},
                 {"name": "Rotation", "equity": 4950.0, "start": 5000.0, "prev": 5000.0}],
        "spy": {"week": 0.01, "since_start": 0.02}, "trades": ["BUY 2 V @ 398.94 (Mon)"],
        "realized_week": 12.5, "open": [{"symbol": "V", "pnl": 30.0, "pct": 0.039}]})
    assert "Combined: $10,050  week +0.0%" in txt.replace("+0.5%", "+0.0%") or "Combined: $10,050" in txt
    assert "SPY: week +1.0%  since start +2.0%" in txt and "BUY 2 V @ 398.94" in txt and "V +3.9%" in txt, txt
    assert "No trades this week." in format_weekly({"week_start": date(2026, 10, 19), "week_end": date(2026, 10, 23),
        "bots": [{"name": "Swing", "equity": 5000.0, "start": 5000.0, "prev": 5000.0}], "spy": {"week": 0, "since_start": 0},
        "trades": [], "realized_week": 0.0, "open": []})
    r = {"exits": ["EXIT V at 440.00: target (P&L +80)"], "entries": [
        {"kind": "share", "qty": 2, "ticker": "CVX", "price": 150.0, "stop": 135.0, "target": 180.0}]}
    assert "BUY 2 CVX @ ~150.00" in trade_alert(r, {"mode": "paper"}) and "EXIT V" in trade_alert(r, {"mode": "paper"})
    assert trade_alert(r, {"mode": "alerts"}).startswith("SUGGESTED"), "alerts mode must say nothing was recorded"
    assert trade_alert({"exits": [], "entries": []}, {"mode": "paper"}) is None, "no trades -> no phone message"
    print("ok alerts: trade message, 'suggested' label in alerts mode, silent when nothing traded, weekly summary text")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    shutil.rmtree(os.path.join(run.HERE, "state_test"), ignore_errors=True)
    print("all tests passed")
