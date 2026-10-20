"""Geopolitical and macro headline monitor.

Per theme, pulls recent headlines from Google News RSS and counts UNIQUE headlines that
(a) mention one of the theme's keywords in the TITLE and (b) use escalation language.

Scoring, 0-1:
  * Until a theme has 5+ days of history: hot count / `hot_headlines_for_max` (a rough fallback).
  * After that: relative to the theme's own typical (median) count over the last ~20 days.
    0 at or below normal, 1.0 at twice normal. Ordinary news flow reads as quiet; a genuine
    spike reads as hot. A crisis that drags on gradually becomes the new normal, by design.

News never triggers a trade; it only blocks or flags entries.
Run `python run.py news` to see the raw counts."""
import json
import os
import re
import statistics
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

import requests

# Whole words only ("bank" must not match "ban"). Ambiguous words (hike, threat, tension,
# emergency, curbs) are left out here; a theme can opt in via `also_hot` in config.yaml.
ESCALATION = re.compile(
    r"\b(war|wars|attack\w*|strikes?|struck|escalat\w*|sanction\w*|bans?|banned|banning|"
    r"blockade\w*|missile\w*|invasion|invad\w*|retaliat\w*|crash\w*|plunge\w*|plummet\w*|"
    r"selloff|sell-off|rout|default\w*|shutdown|embargo\w*|crackdown)\b", re.I)

HOT_FOR_MAX = 60       # fallback cap before a baseline exists
MIN_HISTORY_DAYS = 5
HISTORY_KEEP = 30


@dataclass
class ThemeReport:
    theme: str
    risk: float
    total: int = 0            # unique on-topic headlines (theme keyword in the title)
    hot: int = 0              # of those, how many use escalation language
    headlines: list = field(default_factory=list)
    baseline: float | None = None   # typical hot count, once enough history exists
    days: int = 0                   # days of history so far


def _words(words):
    if not words:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")(?:s|es)?\b", re.I)


def _clean(title: str) -> str:
    """Drop the ' - Publisher' suffix and normalize, so syndicated copies count once."""
    t = re.sub(r"\s+[-|–]\s+[^-|–]{2,40}$", "", title).lower()
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def fetch_headlines(keywords: list, hours: int = 24, timeout: int = 10) -> list[str]:
    q = " OR ".join(f'"{k}"' for k in keywords) + f" when:{max(1, hours // 24)}d"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 swingbot"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return [item.findtext("title", "") for item in root.iter("item")]


def unique(headlines: list[str]) -> list[str]:
    seen, out = set(), []
    for h in headlines:
        key = _clean(h)
        if key and key not in seen:
            seen.add(key)
            out.append(h)
    return out


def _is_hot(h: str, extra) -> bool:
    return bool(ESCALATION.search(h) or (extra and extra.search(h)))


def _risk(hot: int, cap: int, baseline: float | None) -> float:
    if baseline is None:
        return round(min(hot / cap, 1.0), 2)
    return round(max(0.0, min(hot / max(baseline, 8) - 1.0, 1.0)), 2)


def score_theme(headlines, hot_for_max=HOT_FOR_MAX, keywords=None, extra=None, baseline=None) -> float:
    """Score a list of headlines. `keywords` (optional) = theme words that must be in the title."""
    kw, ex = _words(keywords), _words(extra)
    hot = [h for h in unique(headlines) if (kw is None or kw.search(h)) and _is_hot(h, ex)]
    return _risk(len(hot), hot_for_max, baseline)


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path, hist):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(hist, f)
    except Exception:
        pass  # history is a nice-to-have; never crash a scan over it


def _baseline(entries, today_iso):
    prior = [c for d, c in entries if d != today_iso and date.fromisoformat(d).weekday() < 5][-20:]
    return (statistics.median(prior) if len(prior) >= MIN_HISTORY_DAYS else None), len(prior)


def scan(cfg: dict, fetch=fetch_headlines, history_path: str | None = None, today: date | None = None):
    """Returns ({ticker: risk}, [ThemeReport]). `fetch` is swappable for tests."""
    ncfg = cfg["news"]
    cap = ncfg.get("hot_headlines_for_max", HOT_FOR_MAX)
    day = today or date.today()
    today_iso, record = day.isoformat(), day.weekday() < 5   # weekend news volume is lower: don't record it
    hist = _load(history_path) if history_path else {}
    reports, ticker_risk = [], {}
    if not ncfg.get("enabled"):
        return ticker_risk, reports
    for name, theme in ncfg["themes"].items():
        try:
            heads = unique(fetch(theme["keywords"], ncfg["lookback_hours"]))
        except Exception as e:
            reports.append(ThemeReport(name, 0.0, 0, 0, [f"(news fetch failed: {e})"]))
            continue  # a failed fetch is not recorded as a quiet day
        kw, ex = _words(theme["keywords"]), _words(theme.get("also_hot"))
        on_topic = [h for h in heads if kw.search(h)]
        hot = [h for h in on_topic if _is_hot(h, ex)]
        entries = hist.get(name, [])
        base, ndays = _baseline(entries, today_iso)
        risk = _risk(len(hot), cap, base)
        reports.append(ThemeReport(name, risk, len(on_topic), len(hot), (hot or on_topic)[:3], base, ndays))
        if record:
            hist[name] = ([e for e in entries if e[0] != today_iso] + [[today_iso, len(hot)]])[-HISTORY_KEEP:]
        for t in theme["tickers"]:
            ticker_risk[t] = max(ticker_risk.get(t, 0.0), risk)
    if history_path:
        _save(history_path, hist)
    reports.sort(key=lambda r: -r.risk)
    return ticker_risk, reports
