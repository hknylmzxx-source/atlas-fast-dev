#!/usr/bin/env python3
"""ATLAS FAST — Binance USD-M exchangeInfo filter / precision validator (CANDIDATE).

Claude, 18.10.2026. Offline review candidate; owner install name would be
`order_filters.py`. Pure functions: no network, no SDK, never sends orders.
Symbol rules are NEVER hardcoded; they come from the exchangeInfo document
passed in (fixtures in tests). Owner-approved ceilings are hardcoded and can
only be tightened by the caller:

    leverage 3x · filter ceiling 285 USDT notional · filter risk ceiling 2.70 USDT

validate_bundle() checks, and raises FilterBlocked on any doubt:
  * exchangeInfo freshness, single TRADING USDT perpetual entry, one-way mode,
    unique & complete filters (PRICE_FILTER, LOT_SIZE, MARKET_LOT_SIZE,
    MIN_NOTIONAL), internal consistency (min<=max, minQty on step, tick vs
    pricePrecision, step vs quantityPrecision), numbers as strings only;
  * ENTRY MARKET qty on BOTH LOT_SIZE and MARKET_LOT_SIZE (step/min/max);
  * STOP STOP_MARKET closePosition: trigger on tick, inside price band,
    protective side of the reference price chosen by workingType
    (MARK_PRICE -> mark, CONTRACT_PRICE -> worse of mark/last);
  * TPs: LIMIT GTC reduceOnly, opposite side, on tick, PERCENT_PRICE band,
    LOT_SIZE step/min, quantities sum EXACTLY to entry qty, beyond entry;
  * MIN_NOTIONAL for entry (and TPs unless the caller explicitly exempts
    reduce-only orders after verifying Binance's current rule);
  * notional <= configurable ceiling (at most 285 USDT) at worst-case entry;
  * risk <= configurable ceiling (at most 2.70 USDT) = qty x |worst entry - stop| + stop slippage + taker
    fees on entry and stop. Rounding can only make risk SMALLER (helpers
    round toward safety); the check is done on the final rounded values.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

MAX_NOTIONAL_CEILING = Decimal("285")   # 047 live pilot (owner 23.10.2026)
MAX_RISK_CEILING = Decimal("2.70")      # 047
LEVERAGE_REQUIRED = 3                   # 047
REQUIRED_FILTERS = ("PRICE_FILTER", "LOT_SIZE", "MARKET_LOT_SIZE", "MIN_NOTIONAL")
WORKING_TYPES = ("MARK_PRICE", "CONTRACT_PRICE")


class FilterBlocked(ValueError):
    pass


def D(v, what, *, positive=True, allow_zero=False):
    if isinstance(v, (bool, float)) or not isinstance(v, (str, int, Decimal)):
        raise FilterBlocked("NUMBER_NOT_STRING_OR_INT_" + what)
    try:
        x = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise FilterBlocked("NUMBER_INVALID_" + what) from None
    if not x.is_finite():
        raise FilterBlocked("NUMBER_NOT_FINITE_" + what)
    if positive and (x < 0 or (x == 0 and not allow_zero)):
        raise FilterBlocked("NUMBER_NOT_POSITIVE_" + what)
    return x


def _decimals(x):
    return max(0, -x.normalize().as_tuple().exponent)


def _on_step(value, step, base=Decimal("0")):
    return (value - base) % step == 0


# ---- exchangeInfo rules ---------------------------------------------------

def symbol_rules(exchange_info, symbol, *, received_at_ms, now_ms, max_age_ms=300_000):
    if type(exchange_info) is not dict or type(exchange_info.get("symbols")) is not list:
        raise FilterBlocked("EXCHANGE_INFO_MISSING")
    server = exchange_info.get("serverTime")
    if type(server) is not int or type(received_at_ms) is not int or type(now_ms) is not int:
        raise FilterBlocked("EXCHANGE_INFO_TIME_MISSING")
    # Validate freshness from local receipt time, not exchangeInfo.serverTime:
    # Binance may return a cached serverTime. Remain fail-closed at 5 minutes.
    age_ms = now_ms - received_at_ms
    if age_ms < 0:
        raise FilterBlocked("EXCHANGE_INFO_RECEIPT_IN_FUTURE_CLOCK_MISMATCH")
    if age_ms > max_age_ms:
        raise FilterBlocked("EXCHANGE_INFO_TOO_OLD")
    rows = [r for r in exchange_info["symbols"] if type(r) is dict and r.get("symbol") == symbol]
    if len(rows) != 1:
        raise FilterBlocked("SYMBOL_RULES_MISSING_OR_DUPLICATE")
    r = rows[0]
    if (r.get("status") != "TRADING" or r.get("contractType") != "PERPETUAL"
            or r.get("quoteAsset") != "USDT" or r.get("marginAsset") != "USDT"):
        raise FilterBlocked("SYMBOL_NOT_TRADING_USDT_PERPETUAL")
    if type(r.get("filters")) is not list:
        raise FilterBlocked("FILTERS_MISSING")
    filters = {}
    for f in r["filters"]:
        if type(f) is not dict or type(f.get("filterType")) is not str:
            raise FilterBlocked("FILTER_MALFORMED")
        if f["filterType"] in filters:
            raise FilterBlocked("FILTER_DUPLICATE_CONFLICT_" + f["filterType"])
        filters[f["filterType"]] = f
    for need in REQUIRED_FILTERS:
        if need not in filters:
            raise FilterBlocked("FILTER_MISSING_" + need)
    pf, ls, ml, mn = (filters[k] for k in REQUIRED_FILTERS)
    rules = {
        "tick": D(pf.get("tickSize"), "TICK"),
        "min_price": D(pf.get("minPrice"), "MIN_PRICE"),
        "max_price": D(pf.get("maxPrice"), "MAX_PRICE"),
        "step": D(ls.get("stepSize"), "STEP"),
        "min_qty": D(ls.get("minQty"), "MIN_QTY"),
        "max_qty": D(ls.get("maxQty"), "MAX_QTY"),
        "m_step": D(ml.get("stepSize"), "MARKET_STEP"),
        "m_min_qty": D(ml.get("minQty"), "MARKET_MIN_QTY"),
        "m_max_qty": D(ml.get("maxQty"), "MARKET_MAX_QTY"),
        "min_notional": D(mn.get("notional"), "MIN_NOTIONAL"),
        "percent": None,
    }
    if "PERCENT_PRICE" in filters:
        pp = filters["PERCENT_PRICE"]
        rules["percent"] = (D(pp.get("multiplierDown"), "PCT_DOWN"), D(pp.get("multiplierUp"), "PCT_UP"))
        if not rules["percent"][0] < 1 < rules["percent"][1]:
            raise FilterBlocked("PERCENT_PRICE_CONTRADICTORY")
    if (rules["min_price"] >= rules["max_price"] or rules["min_qty"] > rules["max_qty"]
            or rules["m_min_qty"] > rules["m_max_qty"]
            or not _on_step(rules["min_qty"], rules["step"])
            or not _on_step(rules["m_min_qty"], rules["m_step"])):
        raise FilterBlocked("FILTERS_CONTRADICTORY")
    pp_, qp_ = r.get("pricePrecision"), r.get("quantityPrecision")
    if (type(pp_) is not int or type(qp_) is not int
            or _decimals(rules["tick"]) > pp_ or _decimals(rules["step"]) > qp_):
        raise FilterBlocked("PRECISION_CONTRADICTS_FILTERS")
    return rules


# ---- safe rounding helpers ----------------------------------------------

def floor_qty(qty, rules):
    step = max(rules["step"], rules["m_step"])
    return (D(qty, "QTY") / step).to_integral_value(ROUND_FLOOR) * step


def round_stop(price, entry_side, rules):
    """Round the STOP toward the entry: BUY stop (below) up, SELL stop down."""
    t = rules["tick"]
    mode = ROUND_CEILING if entry_side == "BUY" else ROUND_FLOOR
    return (D(price, "STOP") / t).to_integral_value(mode) * t


def round_tp(price, entry_side, rules):
    """Round a TP toward the entry (more likely to fill, never further away)."""
    t = rules["tick"]
    mode = ROUND_FLOOR if entry_side == "BUY" else ROUND_CEILING
    return (D(price, "TP") / t).to_integral_value(mode) * t


# ---- full bundle validation -----------------------------------------------

def _qty_ok(q, step, lo, hi, what):
    if not _on_step(q, step):
        raise FilterBlocked("QTY_OFF_STEP_" + what)
    if q < lo:
        raise FilterBlocked("QTY_BELOW_MIN_" + what)
    if q > hi:
        raise FilterBlocked("QTY_ABOVE_MAX_" + what)


def _price_ok(p, rules, what):
    if not _on_step(p, rules["tick"]):
        raise FilterBlocked("PRICE_OFF_TICK_" + what)
    if not rules["min_price"] <= p <= rules["max_price"]:
        raise FilterBlocked("PRICE_OUTSIDE_FILTER_BAND_" + what)


def validate_bundle(bundle, rules, *, mark, last_price, mark_time_ms, now_ms,
                    dual_side_position, entry_slippage_bps, stop_slippage_bps,
                    taker_fee_rate, max_notional=MAX_NOTIONAL_CEILING,
                    max_risk=MAX_RISK_CEILING, leverage=LEVERAGE_REQUIRED,
                    tp_reduce_only_min_notional_exempt=False, max_mark_age_ms=10_000):
    max_notional = D(max_notional, "MAX_NOTIONAL")
    max_risk = D(max_risk, "MAX_RISK")
    if max_notional > MAX_NOTIONAL_CEILING or max_risk > MAX_RISK_CEILING:
        raise FilterBlocked("CALLER_TRIED_TO_LOOSEN_OWNER_CEILING")
    if leverage != LEVERAGE_REQUIRED:
        raise FilterBlocked("LEVERAGE_MUST_BE_3X")
    if dual_side_position is not False:
        raise FilterBlocked("ACCOUNT_NOT_ONE_WAY")
    mark = D(mark, "MARK"); last = D(last_price, "LAST")
    if type(mark_time_ms) is not int or not 0 <= now_ms - mark_time_ms <= max_mark_age_ms:
        raise FilterBlocked("MARK_PRICE_STALE")
    slip_in = D(entry_slippage_bps, "ENTRY_SLIP", allow_zero=True) / 10_000
    slip_stop = D(stop_slippage_bps, "STOP_SLIP", allow_zero=True) / 10_000
    fee = D(taker_fee_rate, "FEE", allow_zero=True)
    if fee >= Decimal("0.01") or slip_in >= Decimal("0.05") or slip_stop >= Decimal("0.05"):
        raise FilterBlocked("COST_ASSUMPTION_IMPLAUSIBLE")

    if type(bundle) is not dict or set(bundle) != {"entry", "stop", "take_profits"}:
        raise FilterBlocked("BUNDLE_SHAPE_INVALID")
    e, s, tps = bundle["entry"], bundle["stop"], bundle["take_profits"]
    if type(tps) is not list or len(tps) != 2:
        raise FilterBlocked("BUNDLE_SHAPE_INVALID")
    side = e.get("side")
    if side not in ("BUY", "SELL"):
        raise FilterBlocked("ENTRY_SIDE_INVALID")
    exit_side = "SELL" if side == "BUY" else "BUY"
    sym = e.get("symbol")
    if any(o.get("positionSide") != "BOTH" for o in (e, s, *tps)):
        raise FilterBlocked("POSITION_SIDE_NOT_ONE_WAY_BOTH")
    if any(o.get("symbol") != sym for o in (s, *tps)):
        raise FilterBlocked("SYMBOL_MISMATCH")

    # ENTRY
    if e.get("type") != "MARKET" or "reduceOnly" in e:
        raise FilterBlocked("ENTRY_NOT_PLAIN_MARKET")
    qty = D(e.get("quantity"), "ENTRY_QTY")
    _qty_ok(qty, rules["step"], rules["min_qty"], rules["max_qty"], "ENTRY_LOT")
    _qty_ok(qty, rules["m_step"], rules["m_min_qty"], rules["m_max_qty"], "ENTRY_MARKET_LOT")
    worst_entry = mark * (1 + slip_in) if side == "BUY" else mark * (1 - slip_in)
    if qty * mark < rules["min_notional"]:
        raise FilterBlocked("ENTRY_BELOW_MIN_NOTIONAL")
    notional = qty * max(mark, worst_entry)
    if notional > max_notional:
        raise FilterBlocked("ENTRY_NOTIONAL_ABOVE_CAP")
    margin = notional / leverage

    # STOP
    if (s.get("type") != "STOP_MARKET" or s.get("closePosition") != "true"
            or s.get("algoType") != "CONDITIONAL" or s.get("side") != exit_side
            or "quantity" in s or "reduceOnly" in s):
        raise FilterBlocked("STOP_NOT_CLOSE_POSITION_STOP_MARKET")
    wt = s.get("workingType")
    if wt not in WORKING_TYPES:
        raise FilterBlocked("STOP_WORKING_TYPE_INVALID")
    stop = D(s.get("triggerPrice"), "STOP_TRIGGER")
    _price_ok(stop, rules, "STOP")
    refs = (mark,) if wt == "MARK_PRICE" else (mark, last)
    if side == "BUY" and not all(stop < r for r in refs + (worst_entry,)):
        raise FilterBlocked("STOP_NOT_PROTECTIVE_FOR_REFERENCE_PRICE")
    if side == "SELL" and not all(stop > r for r in refs + (worst_entry,)):
        raise FilterBlocked("STOP_NOT_PROTECTIVE_FOR_REFERENCE_PRICE")

    # TAKE PROFITS
    tp_qty, prev = Decimal("0"), None
    for i, t in enumerate(tps, 1):
        if (t.get("type") != "LIMIT" or t.get("timeInForce") != "GTC"
                or t.get("reduceOnly") != "true" or t.get("side") != exit_side):
            raise FilterBlocked("TP%d_NOT_REDUCE_ONLY_GTC_LIMIT" % i)
        p = D(t.get("price"), "TP%d_PRICE" % i)
        q = D(t.get("quantity"), "TP%d_QTY" % i)
        _price_ok(p, rules, "TP%d" % i)
        _qty_ok(q, rules["step"], rules["min_qty"], rules["max_qty"], "TP%d" % i)
        if rules["percent"]:
            lo, hi = rules["percent"]
            if not mark * lo <= p <= mark * hi:
                raise FilterBlocked("TP%d_OUTSIDE_PERCENT_PRICE" % i)
        if not tp_reduce_only_min_notional_exempt and p * q < rules["min_notional"]:
            raise FilterBlocked("TP%d_BELOW_MIN_NOTIONAL" % i)
        beyond = (p > worst_entry) if side == "BUY" else (p < worst_entry)
        if not beyond:
            raise FilterBlocked("TP%d_NOT_BEYOND_ENTRY" % i)
        if prev is not None and ((p <= prev) if side == "BUY" else (p >= prev)):
            raise FilterBlocked("TP_ORDER_INVALID")
        prev = p
        tp_qty += q
    if tp_qty != qty:
        raise FilterBlocked("TP_QUANTITIES_NOT_EQUAL_ENTRY")

    # RISK on final rounded values
    stop_fill = stop * (1 - slip_stop) if side == "BUY" else stop * (1 + slip_stop)
    price_risk = qty * abs(worst_entry - stop_fill)
    fees = qty * worst_entry * fee + qty * stop_fill * fee
    risk = price_risk + fees
    if risk > max_risk:
        raise FilterBlocked(f"RISK_ABOVE_OWNER_LIMIT_{max_risk}_USDT_INCLUDING_SLIPPAGE_AND_FEES")
    return {"symbol": sym, "qty": str(qty), "notional_worst_usdt": str(notional),
            "margin_1x_usdt": str(margin), "risk_worst_usdt": str(risk),
            "risk_price_usdt": str(price_risk), "fees_usdt": str(fees),
            "stop_reference": wt}
