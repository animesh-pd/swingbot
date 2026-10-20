"""Tastytrade sandbox connection: credential loading and a read-only check. Places no orders.

Secrets live in Windows Credential Manager (via keyring) under the service name below, with
TT_SECRET / TT_REFRESH environment variables as a fallback. Nothing secret goes in config.yaml.
"""
import asyncio
import os

SERVICE = "swingbot-tastytrade"


class MissingCredentials(RuntimeError):
    pass


def load_credentials():
    """Return (client_secret, refresh_token) or raise MissingCredentials naming what's missing."""
    found = {}
    for name, env in (("client_secret", "TT_SECRET"), ("refresh_token", "TT_REFRESH")):
        value = os.environ.get(env)
        if not value:
            try:
                import keyring
                value = keyring.get_password(SERVICE, name)
            except Exception:
                value = None
        found[name] = value
    missing = [k for k, v in found.items() if not v]
    if missing:
        raise MissingCredentials(
            "Missing " + ", ".join(missing) + f". Store with keyring under service '{SERVICE}' "
            "(see README) or set TT_SECRET / TT_REFRESH.")
    return found["client_secret"], found["refresh_token"]


async def _check(sandbox):
    from tastytrade import Account, Session
    secret, refresh = load_credentials()
    session = Session(secret, refresh, is_test=sandbox)
    accounts = await Account.get(session)
    lines = [f"Connected to tastytrade {'SANDBOX' if sandbox else 'PRODUCTION'}: "
             f"{len(accounts)} account(s)"]
    for acct in accounts:
        bal = await acct.get_balances(session)
        lines.append(f"  {acct.account_number}: net liq {bal.net_liquidating_value}, "
                     f"cash {bal.cash_balance}, option buying power {bal.derivative_buying_power}")
    return "\n".join(lines)


def check(cfg):
    """Read-only connection test. Refuses to touch production unless the config says sandbox: false."""
    tt = cfg.get("tastytrade", {})
    sandbox = tt.get("sandbox", True)
    if not sandbox:
        raise SystemExit("config tastytrade.sandbox is false; this check is for the sandbox only.")
    from tastytrade.utils import TastytradeError
    try:
        return asyncio.run(_check(True))
    except TastytradeError as e:
        return (f"Sandbox rejected the login: {e}\n"
                "Check that the refresh token was generated for this OAuth app in the sandbox "
                "(not production) and that the client secret matches it.")


# ---------- dry-run order validation (never sends a real order) ----------
async def _dry_run_entries(entries):
    from decimal import Decimal
    from tastytrade import Account, Session
    from tastytrade.instruments import Equity, OptionType, get_option_chain
    from tastytrade.order import LimitOrder, OrderAction, OrderTimeInForce

    secret, refresh = load_credentials()
    session = Session(secret, refresh, is_test=True)
    account = (await Account.get(session))[0]
    lines = []
    for e in entries:
        try:
            if e["kind"] == "share":
                eq = await Equity.get(session, e["ticker"])
                legs = [eq.build_leg(e["qty"], OrderAction.BUY_TO_OPEN)]
                price, label = -Decimal(str(round(e["price"], 2))), f"{e['qty']} {e['ticker']} shares"
            else:
                c = e["contract"]
                chain = await get_option_chain(session, c["ticker"])
                kind = OptionType.CALL if c["right"] == "call" else OptionType.PUT
                by_strike = {o.strike_price: o for o in chain[c["expiry"]] if o.option_type == kind}
                legs = [by_strike[Decimal(str(c["strike"]))].build_leg(e["qty"], OrderAction.BUY_TO_OPEN)]
                if c.get("strike2"):
                    legs.append(by_strike[Decimal(str(c["strike2"]))].build_leg(e["qty"], OrderAction.SELL_TO_OPEN))
                price, label = -Decimal(str(c["cost"])), f"{e['qty']}x {c['ticker']} {c['expiry']} {c['strike']:g}" + \
                    (f"/{c['strike2']:g}" if c.get("strike2") else "") + f" {c['right']}"
            # LimitOrder price convention (from the SDK): negative = debit, positive = credit.
            order = LimitOrder(time_in_force=OrderTimeInForce.DAY, legs=legs, price=price)
            resp = await account.place_order(session, order, dry_run=True)   # dry_run is hard-coded on
            bp = resp.buying_power_effect
            lines.append(f"OK    {label} @ {-price}: buying power change {bp.change_in_buying_power} "
                         f"({bp.effect}), spread={bp.is_spread}; warnings: {[w.message for w in resp.warnings] or 'none'}"
                         + (f"; errors: {resp.errors}" if resp.errors else ""))
        except Exception as ex:  # report per order so one bad symbol doesn't hide the rest
            lines.append(f"FAIL  {e.get('ticker')}: {type(ex).__name__}: {ex}")
    return "\n".join(lines) or "No entries to validate."


def dry_run(entries):
    return asyncio.run(_dry_run_entries(entries))


# ---------- sandbox position mirroring (SANDBOX ONLY: the session below is hard-coded is_test=True) ----------
def diff_positions(desired: dict, actual: dict, managed) -> dict:
    """Share changes the sandbox account needs so it matches `desired`. Only `managed` symbols are touched,
    so one bot never trades another bot's (or your own) positions. Positive = buy, negative = sell."""
    out = {}
    for sym in managed:
        delta = int(desired.get(sym, 0)) - int(actual.get(sym, 0))
        if delta:
            out[sym] = delta
    return out


def require_send_enabled(cfg):
    tt = cfg.get("tastytrade", {})
    if not tt.get("sandbox", True):
        raise SystemExit("config tastytrade.sandbox is false; order sending is sandbox-only.")
    return bool(tt.get("send_orders", False))


WORKING = {"Received", "Routed", "In Flight", "Live", "Contingent"}


async def _sync(desired, managed, prices, max_order_usd, dry_run):
    from decimal import Decimal
    from tastytrade import Account, Session
    from tastytrade.instruments import Equity
    from tastytrade.order import LimitOrder, OrderAction, OrderTimeInForce

    secret, refresh = load_credentials()
    session = Session(secret, refresh, is_test=True)          # never production
    account = (await Account.get(session))[0]
    actual = {}
    for p in await account.get_positions(session):
        if str(getattr(p.instrument_type, "value", p.instrument_type)) == "Equity" and p.symbol in managed:
            q = int(p.quantity)
            actual[p.symbol] = -q if "short" in str(getattr(p.quantity_direction, "value", p.quantity_direction)).lower() else q
    # Orders still working count as already placed, so a re-run never doubles up.
    for o in await account.get_live_orders(session):
        if str(getattr(o.status, "value", o.status)) not in WORKING:
            continue
        for leg in o.legs:
            if leg.symbol in managed:
                rem = int(leg.remaining_quantity if leg.remaining_quantity is not None else leg.quantity)
                actual[leg.symbol] = actual.get(leg.symbol, 0) + (rem if "buy" in str(leg.action.value).lower() else -rem)
    lines = []
    for sym, delta in diff_positions(desired, actual, managed).items():
        px = prices[sym]
        notional = abs(delta) * px
        if notional > max_order_usd:
            lines.append(f"SKIP  {sym}: {delta:+d} shares (${notional:,.0f}) is over the ${max_order_usd:,.0f} per-order cap")
            continue
        buy = delta > 0
        try:
            eq = await Equity.get(session, sym)
            leg = eq.build_leg(abs(delta), OrderAction.BUY_TO_OPEN if buy else OrderAction.SELL_TO_CLOSE)
            # marketable limit, 0.5% through the last price. SDK convention: negative = debit, positive = credit.
            limit = round(px * (1.005 if buy else 0.995), 2)
            order = LimitOrder(time_in_force=OrderTimeInForce.DAY, legs=[leg], price=Decimal(str(-limit if buy else limit)))
            resp = await account.place_order(session, order, dry_run=dry_run)
            tag = "DRY " if dry_run else "SENT"
            oid = getattr(getattr(resp, "order", None), "id", None)
            lines.append(f"{tag}  {'BUY' if buy else 'SELL'} {abs(delta)} {sym} @ limit {limit}"
                         + (f" (order id {oid})" if oid and not dry_run else "")
                         + (f"; errors: {resp.errors}" if getattr(resp, "errors", None) else ""))
        except Exception as ex:
            lines.append(f"FAIL  {sym}: {type(ex).__name__}: {ex}")
    return "\n".join(lines) or "Sandbox already matches the paper account; no orders needed."


def sync_positions(cfg, desired: dict, managed, prices: dict, dry_run=False) -> str:
    """Bring the tastytrade SANDBOX account's positions in `managed` symbols in line with `desired`."""
    if not require_send_enabled(cfg) and not dry_run:
        return "tastytrade.send_orders is false: sandbox orders are off."
    cap = float(cfg.get("tastytrade", {}).get("max_order_usd", 2500))
    return asyncio.run(_sync(desired, set(managed), prices, cap, dry_run))
