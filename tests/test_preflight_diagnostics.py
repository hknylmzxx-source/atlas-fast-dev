"""ATLAS FAST isolated, deterministic preflight diagnostics tests (NO LIVE SDK)."""
from __future__ import annotations

import ast
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parent.parent
FILTER_FILE = ROOT / "order_filters.py"
OWNER_FILE = ROOT / "owner_single_execution.py"

spec = importlib.util.spec_from_file_location("atlas_dev_order_filters", FILTER_FILE)
filters = importlib.util.module_from_spec(spec)
spec.loader.exec_module(filters)


def exchange_info():
    return {
        "serverTime": 100,  # deliberately old; cache age is not receipt age
        "symbols": [{
            "symbol": "AINUSDT",
            "status": "TRADING",
            "contractType": "PERPETUAL",
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "pricePrecision": 3,
            "quantityPrecision": 1,
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.001",
                 "minPrice": "0.001", "maxPrice": "1000"},
                {"filterType": "LOT_SIZE", "stepSize": "0.1",
                 "minQty": "1", "maxQty": "1000"},
                {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.1",
                 "minQty": "1", "maxQty": "1000"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }],
    }


def fixture_bundle():
    return {
        "entry": {
            "symbol": "AINUSDT", "side": "BUY", "type": "MARKET",
            "positionSide": "BOTH", "quantity": "2",
        },
        "stop": {
            "symbol": "AINUSDT", "side": "SELL", "type": "STOP_MARKET",
            "positionSide": "BOTH", "algoType": "CONDITIONAL",
            "closePosition": "true", "workingType": "CONTRACT_PRICE",
            "triggerPrice": "95",
        },
        "take_profits": [{
            "symbol": "AINUSDT", "side": "SELL", "positionSide": "BOTH",
            "type": "LIMIT", "timeInForce": "GTC",
            "reduceOnly": "true", "price": price, "quantity": "1",
        } for price in ("105", "110")],
    }


def owner_isolated_namespace(worst_risk_value):
    """Compile ONLY the risk preflight function AST, not the live executor."""
    tree = ast.parse(OWNER_FILE.read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {
                "MAX_RISK", "MAX_MARGIN", "MAX_NOTIONAL", "MAX_LEVERAGE",
                "ENTRY_SLIPPAGE", "STOP_SLIPPAGE", "TAKER_FEE",
            }:
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Decimal":
                    values[target.id] = Decimal(ast.literal_eval(value.args[0]))
    assert values["MAX_RISK"] == Decimal("0.75")
    assert values["MAX_NOTIONAL"] == Decimal("600")
    assert values["MAX_LEVERAGE"] == Decimal("3")
    vals = dict(values)
    vals.update({
        "Decimal": Decimal,
        "Blocked": type("OfflineBlocked", (RuntimeError,), {}),
        "dec": lambda x: Decimal(str(x)),
        "time": SimpleNamespace(time=lambda: 1_000_000),
        "worst_risk": lambda *args: Decimal(str(worst_risk_value)),
    })
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "risk_authority")
    isolated = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(isolated)
    exec(compile(isolated, str(OWNER_FILE), "exec"), vals)
    return vals


class FakeVenue:
    def __init__(self, leverage=3):
        self.leverage = leverage
        self.calls = []

    def public(self, endpoint, symbol):
        self.calls.append(("public", endpoint))
        if endpoint == "/fapi/v1/premiumIndex":
            return {"symbol": symbol, "time": 1_000_000_000,
                    "markPrice": "100"}
        if endpoint == "/fapi/v1/exchangeInfo":
            return exchange_info()
        raise AssertionError("Unexpected public call")

    def read(self, method, **kwargs):
        self.calls.append(("signed_read", method))
        assert method == "symbol_configuration"
        return [{"symbol": "AINUSDT", "leverage": self.leverage,
                 "marginType": "ISOLATED"}]


class ExchangeInfoTimeTests(unittest.TestCase):
    def test_cached_server_time_with_fresh_receipt_is_allowed(self):
        got = filters.symbol_rules(exchange_info(), "AINUSDT",
                                   received_at_ms=1_000_000,
                                   now_ms=1_000_100)
        self.assertEqual(got["tick"], Decimal("0.001"))

    def test_exact_five_minutes_is_allowed(self):
        self.assertEqual(filters.symbol_rules(exchange_info(), "AINUSDT",
                         received_at_ms=1_000_000,
                         now_ms=1_300_000)["min_notional"], Decimal("5"))

    def test_over_five_minutes_is_rejected(self):
        with self.assertRaisesRegex(filters.FilterBlocked, "^EXCHANGE_INFO_TOO_OLD:AGE_MS=300001:LIMIT_MS=300000$"):
            filters.symbol_rules(exchange_info(), "AINUSDT",
                                 received_at_ms=1_000_000,
                                 now_ms=1_300_001)

    def test_receipt_in_future_is_rejected(self):
        with self.assertRaisesRegex(filters.FilterBlocked, "^EXCHANGE_INFO_RECEIPT_IN_FUTURE_CLOCK_MISMATCH:OFFSET_MS=1$"):
            filters.symbol_rules(exchange_info(), "AINUSDT",
                                 received_at_ms=1_000_001,
                                 now_ms=1_000_000)

    def test_missing_timestamp_remains_blocked(self):
        with self.assertRaisesRegex(filters.FilterBlocked, "^EXCHANGE_INFO_TIME_MISSING$"):
            filters.symbol_rules(exchange_info(), "AINUSDT",
                                 received_at_ms="1000000", now_ms=1_000_000)

    def test_actual_bundle_risk_failure_names_current_ceiling(self):
        rules = filters.symbol_rules(exchange_info(), "AINUSDT",
                                     received_at_ms=1_000_000, now_ms=1_000_000)
        with self.assertRaisesRegex(filters.FilterBlocked,
            "^RISK_ABOVE_OWNER_LIMIT_2\\.70_USDT_INCLUDING_SLIPPAGE_AND_FEES$"):
            filters.validate_bundle(
                fixture_bundle(), rules, mark="100", last_price="100",
                mark_time_ms=1_000_000, now_ms=1_000_000,
                dual_side_position=False, entry_slippage_bps="5",
                stop_slippage_bps="10", taker_fee_rate="0.0005", leverage=3)


class OwnerRiskMessageTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fixture_bundle()
        self.account = {"positions": [], "availableBalance": "1000"}

    def test_over_limit_reports_configured_075_and_blocks(self):
        ns = owner_isolated_namespace("0.75000001")
        with self.assertRaisesRegex(ns["Blocked"], "^STOP_RISK_EXCEEDS_OWNER_LIMIT_0\\.75_USDT_INCL_COSTS$"):
            ns["risk_authority"](FakeVenue(), self.bundle, self.account)

    def test_at_limit_is_allowed_when_leverage_correct(self):
        ns = owner_isolated_namespace("0.75")
        mark = ns["risk_authority"](FakeVenue(leverage=3), self.bundle, self.account)
        self.assertEqual(mark, Decimal("100"))

    def test_wrong_leverage_reports_required_3x(self):
        ns = owner_isolated_namespace("0.5")
        with self.assertRaisesRegex(ns["Blocked"], "^SIGNED_SYMBOL_LEVERAGE_NOT_OWNER_3X$"):
            ns["risk_authority"](FakeVenue(leverage=1), self.bundle, self.account)

    def test_wrong_margin_type_remains_blocked(self):
        ns = owner_isolated_namespace("0.5")
        venue = FakeVenue(leverage=3)
        def wrong_margin(method, **kwargs):
            return [{"symbol": "AINUSDT", "leverage": 3,
                     "marginType": "CROSSED"}]
        venue.read = wrong_margin
        with self.assertRaisesRegex(ns["Blocked"], "^SIGNED_SYMBOL_NOT_ISOLATED_MARGIN$"):
            ns["risk_authority"](venue, self.bundle, self.account)


if __name__ == "__main__":
    unittest.main()
