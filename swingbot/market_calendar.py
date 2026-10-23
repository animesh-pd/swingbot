"""US stock-market calendar (NYSE): holidays, early closes, and whether a scheduled job should run now.

Rules, not a hard-coded list, so it keeps working in future years. Holidays that fall on a weekend are observed on the
nearest weekday, except New Year's Day on a Saturday (the market stays open the Friday before)."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN_ET, CLOSE_ET, EARLY_CLOSE_ET = time(9, 30), time(16, 0), time(13, 0)
WARMUP = timedelta(minutes=5)      # jobs start 5 minutes after the open and stop 5 minutes before the close


def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year, month, weekday):
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d):
    """Saturday -> Friday, Sunday -> Monday."""
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def holidays(year: int) -> dict:
    h = {_nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
         _nth_weekday(year, 2, 0, 3): "Presidents Day",
         _easter(year) - timedelta(days=2): "Good Friday",
         _last_weekday(year, 5, 0): "Memorial Day",
         _observed(date(year, 6, 19)): "Juneteenth",
         _observed(date(year, 7, 4)): "Independence Day",
         _nth_weekday(year, 9, 0, 1): "Labor Day",
         _nth_weekday(year, 11, 3, 4): "Thanksgiving",
         _observed(date(year, 12, 25)): "Christmas"}
    if date(year, 1, 1).weekday() != 5:                       # a Saturday New Year's Day is not observed
        h[_observed(date(year, 1, 1))] = "New Year's Day"
    return h


def holiday_name(d: date):
    # a holiday observed across a year boundary (Jan 1 on Sunday -> Mon Jan 2) is covered by d.year's own table
    return holidays(d.year).get(d)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and holiday_name(d) is None


def is_early_close(d: date) -> bool:
    """1:00 PM ET close: the day after Thanksgiving, Christmas Eve, and July 3rd (when July 4th is Tue-Fri)."""
    if not is_trading_day(d):
        return False
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True
    if d == date(d.year, 12, 24):
        return True
    return d == date(d.year, 7, 3) and date(d.year, 7, 4).weekday() in (1, 2, 3, 4)


def close_time(d: date) -> time:
    return EARLY_CLOSE_ET if is_early_close(d) else CLOSE_ET


def is_last_trading_day_of_week(d: date) -> bool:
    return is_trading_day(d) and not any(is_trading_day(d + timedelta(days=n)) for n in range(1, 5 - d.weekday() + 1))


def should_run(job: str, now: datetime = None):
    """(allowed, reason) for a scheduled job right now.
    scan / monitor: only while the market is open (5 min after the open to 5 min before the close).
    rotate: same, and only on days 1-5 of the month.  weekly: on the last trading day of the week, any time.
    keepawake: any time on a trading day."""
    now = (now or datetime.now(ET)).astimezone(ET)
    d = now.date()
    if not is_trading_day(d):
        why = holiday_name(d) or "weekend"
        return False, f"market closed today ({why})"
    if job == "keepawake":
        return True, "trading day"
    if job == "weekly":
        return (True, "last trading day of the week") if is_last_trading_day_of_week(d) else (False, "not the last trading day of the week")
    if job == "rotate" and d.day > 5:
        return False, "rotation only runs on days 1-5 of the month"
    start = (datetime.combine(d, OPEN_ET) + WARMUP).time()
    end = (datetime.combine(d, close_time(d)) - WARMUP).time()
    if not (start <= now.time() <= end):
        tag = " (early close today)" if is_early_close(d) else ""
        return False, f"outside market hours: ET {now:%H:%M}, window {start:%H:%M}-{end:%H:%M}{tag}"
    return True, "market open"
