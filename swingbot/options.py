"""Option structure selection, pricing and marking.

Structures (config `options.structure`):
  spread_first  try a vertical debit spread; if none qualifies, fall back to a single option (default)
  spread_only   spreads only
  single_only   single long calls/puts only

A vertical debit spread buys the ~0.50-delta option and sells a further-out-of-the-money option of
the same type and expiry: a bull call spread on bullish setups, a bear put spread on bearish ones.
The most you can lose is the debit you paid; the most you can make is the strike width minus the debit."""
import math
from datetime import date


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(s, k, t, vol, right, r=0.04):
    if t <= 0 or vol <= 0:
        return max(0.0, s - k) if right == "call" else max(0.0, k - s)
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if right == "call":
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def bs_delta(s, k, t, vol, right, r=0.04):
    if t <= 0 or vol <= 0:
        return 1.0 if (right == "call" and s > k) else 0.0
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    return _ncdf(d1) if right == "call" else _ncdf(d1) - 1


# ---------- labels (used by the engine, broker journal and brief) ----------
def pos_label(p: dict) -> str:
    strikes = f"{p['strike']:g}/{p['strike2']:g}" if p.get("strike2") else f"{p['strike']:g}"
    spread = " spread" if p.get("strike2") else ""
    return f"{p['ticker']} {strikes}{p['right'][0].upper()}{spread} {p['expiry']}"


def describe(c: dict) -> str:
    if c["structure"] == "spread":
        return (f"{c['ticker']} {c['expiry']} {c['strike']:g}/{c['strike2']:g} {c['right'].upper()} SPREAD "
                f"@ {c['cost']:.2f} debit (max profit {c['max_profit']:.2f}, deltas {c['delta']:.2f}/{c['delta2']:.2f})")
    return f"{c['ticker']} {c['expiry']} {c['strike']:g} {c['right'].upper()} @ {c['cost']:.2f} (delta {c['delta']:.2f})"


# ---------- selection ----------
def _liquid_rows(data, ticker: str, direction: str, today: date, cfg: dict):
    """Liquid contracts on the target expiry. Returns (ctx, None) or (None, reason)."""
    o = cfg["options"]
    right = "call" if direction == "bull" else "put"
    if right == "put" and not o["allow_puts"]:
        return None, "puts disabled in config"
    exps = [e for e in data.expirations(ticker)
            if o["target_dte_min"] <= (e - today).days <= o["target_dte_max"]]
    if not exps:
        return None, f"no expiration {o['target_dte_min']}-{o['target_dte_max']} days out"
    expiry = exps[0]
    s = data.last_price(ticker)
    t = (expiry - today).days / 365
    rows = []
    for r in data.chain(ticker, expiry, right).itertuples():
        if r.bid <= 0 or r.ask <= 0 or r.openInterest < o["min_open_interest"]:
            continue
        mid = (r.bid + r.ask) / 2
        if (r.ask - r.bid) / mid > o["max_bid_ask_spread_pct"]:
            continue
        iv = float(r.impliedVolatility or 0.35)
        rows.append({"strike": float(r.strike), "bid": float(r.bid), "ask": float(r.ask), "mid": mid,
                     "iv": iv, "delta": abs(bs_delta(s, r.strike, t, iv, right))})
    if not rows:
        return None, "no contract passed liquidity filters (open interest / spread)"
    return {"rows": rows, "expiry": expiry, "right": right, "ticker": ticker}, None


def pick_single(ctx: dict, cfg: dict):
    o = cfg["options"]
    best = min(ctx["rows"], key=lambda r: abs(r["delta"] - o["target_delta"]))
    if best["ask"] * 100 > o["max_position_usd"]:
        return None, f"1 contract costs ${best['ask'] * 100:.0f}, over the ${o['max_position_usd']} cap"
    return {"ticker": ctx["ticker"], "structure": "single", "right": ctx["right"], "strike": best["strike"],
            "expiry": ctx["expiry"], "bid": best["bid"], "ask": best["ask"], "mid": round(best["mid"], 2),
            "cost": best["ask"], "delta": round(best["delta"], 2), "iv": best["iv"]}, None


def pick_spread(ctx: dict, cfg: dict):
    o = cfg["options"]
    right = ctx["right"]
    long = min(ctx["rows"], key=lambda r: abs(r["delta"] - o["target_delta"]))
    further = (lambda r: r["strike"] > long["strike"]) if right == "call" else (lambda r: r["strike"] < long["strike"])
    shorts = [r for r in ctx["rows"] if further(r) and 0.08 <= r["delta"] <= long["delta"] - 0.10]
    if not shorts:
        return None, "no liquid short strike further out of the money"
    first_reason = None
    for short in sorted(shorts, key=lambda r: abs(r["delta"] - o["spread_short_delta"])):
        width = abs(short["strike"] - long["strike"])
        debit = long["ask"] - short["bid"]          # natural price: pay the ask, sell at the bid
        if debit <= 0:
            reason = "spread would pay a credit (bad quotes)"
        elif debit > o["max_debit_pct_of_width"] * width:
            reason = (f"debit {debit:.2f} is {debit / width:.0%} of the ${width:g} width "
                      f"(max {o['max_debit_pct_of_width']:.0%})")
        elif debit * 100 > o["max_position_usd"]:
            reason = f"spread costs ${debit * 100:.0f}, over the ${o['max_position_usd']} cap"
        else:
            return {"ticker": ctx["ticker"], "structure": "spread", "right": right,
                    "strike": long["strike"], "strike2": short["strike"], "expiry": ctx["expiry"],
                    "bid": long["bid"], "ask": long["ask"], "mid": round(long["mid"] - short["mid"], 2),
                    "cost": round(debit, 2), "width": width, "max_profit": round(width - debit, 2),
                    "delta": round(long["delta"], 2), "delta2": round(short["delta"], 2),
                    "iv": long["iv"]}, None
        first_reason = first_reason or reason
    return None, first_reason


def pick_contract(data, ticker: str, direction: str, today: date, cfg: dict):
    """Return (contract_dict, None) or (None, reason). Calls/call-spreads on bull setups, puts/put-spreads on bear."""
    mode = cfg["options"].get("structure", "spread_first")
    ctx, err = _liquid_rows(data, ticker, direction, today, cfg)
    if err:
        return None, err
    reasons = []
    if mode in ("spread_first", "spread_only"):
        c, why = pick_spread(ctx, cfg)
        if c:
            return c, None
        reasons.append(f"spread: {why}")
        if mode == "spread_only":
            return None, reasons[0]
    c, why = pick_single(ctx, cfg)
    if c:
        return c, None
    reasons.append(f"single: {why}")
    return None, "; ".join(reasons)


# ---------- marking ----------
def _leg_mid(chain, strike):
    row = chain[(chain["strike"] - strike).abs() < 0.01]      # exact contract only
    if len(row) and row["bid"].iloc[0] > 0 and row["ask"].iloc[0] > 0:
        return (row["bid"].iloc[0] + row["ask"].iloc[0]) / 2
    return None


def mark(data, pos: dict, today: date) -> float:
    """Current mid value of an open option position (single option or spread net value)."""
    k2 = pos.get("strike2")
    try:
        ch = data.chain(pos["ticker"], pos["expiry"], pos["right"])
        a = _leg_mid(ch, pos["strike"])
        b = _leg_mid(ch, k2) if k2 else 0.0
        if a is not None and b is not None:
            v = a - b
            return round(max(min(v, abs(pos["strike"] - k2)) if k2 else v, 0.0), 2)
    except Exception:
        pass
    # Fallback: theoretical value from the IV at entry
    t = max((pos["expiry"] - today).days, 0) / 365
    s, iv = data.last_price(pos["ticker"]), pos.get("iv") or 0.35
    v = bs_price(s, pos["strike"], t, iv, pos["right"])
    if k2:
        v = min(max(v - bs_price(s, k2, t, iv, pos["right"]), 0.0), abs(pos["strike"] - k2))
    return round(v, 2)
