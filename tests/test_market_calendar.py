"""Market calendar tests. Run: python tests/test_market_calendar.py"""
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swingbot.market_calendar import (ET, close_time, holidays, is_early_close, is_last_trading_day_of_week,  # noqa: E402
                                      is_trading_day, should_run)


def et(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def test_holidays_match_the_published_nyse_schedule():
    assert sorted(holidays(2026)) == [date(2026, *md) for md in
        [(1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3), (9, 7), (11, 26), (12, 25)]]
    assert sorted(holidays(2027)) == [date(2027, *md) for md in
        [(1, 1), (1, 18), (2, 15), (3, 26), (5, 31), (6, 18), (7, 5), (9, 6), (11, 25), (12, 24)]]
    h28 = holidays(2028)
    assert date(2028, 1, 1) not in h28 and date(2028, 1, 3) not in h28, "Saturday New Year's Day is not observed"
    assert is_trading_day(date(2027, 12, 31)), "the Friday before a Saturday New Year's Day is a trading day"
    print("ok calendar: 2026 and 2027 holidays match the NYSE list; Saturday New Year's Day handled")


def test_early_closes():
    assert is_early_close(date(2026, 11, 27)) and is_early_close(date(2026, 12, 24))
    assert not is_early_close(date(2026, 11, 26)), "closed, not early"
    assert not is_early_close(date(2026, 7, 2)), "July 3 2026 is the observed holiday; no early close"
    assert is_early_close(date(2025, 7, 3)), "July 4 2025 is a Friday, so July 3 closes early"
    assert close_time(date(2026, 11, 27)).hour == 13 and close_time(date(2026, 11, 25)).hour == 16
    print("ok calendar: early closes (day after Thanksgiving, Dec 24, July 3) are 1 PM ET")


def test_jobs_run_only_when_the_market_is_open():
    assert should_run("scan", et(2026, 10, 19, 10, 0))[0]
    assert not should_run("scan", et(2026, 10, 19, 17, 8))[0], "after the close"
    assert not should_run("scan", et(2026, 10, 24, 11, 0))[0], "Saturday"
    ok, why = should_run("monitor", et(2026, 11, 26, 11, 0))
    assert not ok and "Thanksgiving" in why, why
    assert should_run("scan", et(2026, 11, 27, 12, 30))[0] and not should_run("scan", et(2026, 11, 27, 14, 0))[0], "half day"
    assert not should_run("scan", et(2026, 10, 19, 9, 31))[0], "wait 5 minutes after the open for real prices"
    print("ok calendar: scan/monitor skip holidays, weekends, after-hours and the post-close part of half days")


def test_rotation_and_weekly_rules():
    assert should_run("rotate", et(2026, 11, 2, 10, 35))[0]
    assert not should_run("rotate", et(2026, 11, 10, 10, 35))[0], "days 1-5 only"
    assert not should_run("rotate", et(2026, 11, 1, 10, 35))[0], "Sunday"
    assert is_last_trading_day_of_week(date(2026, 10, 23)) and not is_last_trading_day_of_week(date(2026, 10, 22))
    assert should_run("weekly", et(2026, 12, 24, 16, 10))[0], "Dec 25 is a holiday: Thursday is the last trading day"
    assert not should_run("weekly", et(2026, 12, 23, 16, 10))[0]
    assert should_run("weekly", et(2026, 11, 27, 16, 10))[0], "after-close summary on a half-day Friday"
    assert should_run("keepawake", et(2026, 10, 23, 9, 20))[0] and not should_run("keepawake", et(2026, 11, 26, 9, 20))[0]
    print("ok calendar: rotation days 1-5, weekly summary on the last trading day of the week, keep-awake on trading days")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("all calendar tests passed")
