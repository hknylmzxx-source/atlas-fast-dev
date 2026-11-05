"""Offline tests of source-only market-last preflight acquisition.

Only selected AST functions are compiled; main executor and SDK never import.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "owner_single_execution.py"
FILTERS = ROOT / "order_filters.py"
owner_ast = ast.parse(SRC.read_text())
selected = [
    n for n in owner_ast.body
    if isinstance(n, ast.FunctionDef) and n.name in {
        "_policy_inputs_market_last", "_policy_timing_suffix"
    }
]
assert len(selected) == 2
mod = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))

class OfflineBlocked(RuntimeError):
    pass

spec = importlib.util.spec_from_file_location("atlas_dev_filters_marketlast", FILTERS)
filter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(filter_mod)

class FakeInputs:
    def __init__(self, record, market, exception=None):
        self.record = record
        self.market = market
        self.exception = exception
    def signed_income_envelope(self, venue):
        self.record.append("income")
        if self.exception is not None:
            raise self.exception
        return {"schema": "offline-synthetic-income"}
    def policy_market(self, venue, bundle, account):
        self.record.append("market")
        return self.market

def source_functions(clock):
    fn = iter(clock)
    ns = {
        "Blocked": OfflineBlocked,
        "_now_ms": lambda: next(fn),
    }
    exec(compile(mod, str(SRC), "exec"), ns)
    return ns

class MarketLastPreflightTests(unittest.TestCase):
    def test_long_income_read_does_not_age_market_before_policy(self):
        """360s income phase, 90ms market phase: record actual age, not fabricated."""
        call_order = []
        ns = source_functions([1_000_000, 1_360_000, 1_360_090])
        inputs = FakeInputs(call_order, {
            "exchange_info_received_ms": 1_360_050,
            "mark_time_ms": 1_360_000,
        })
        market, envelope, timing = ns["_policy_inputs_market_last"](inputs, None, {}, {})
        self.assertEqual(call_order, ["income", "market"])
        self.assertEqual(timing, {
            "income_ms": 360_000, "market_ms": 90,
            "exchange_info_age_ms": 40, "mark_age_ms": 90,
        })
        self.assertEqual(envelope, {"schema": "offline-synthetic-income"})
        # The real PolicyGate independently validates both envelope and market.
        self.assertIs(market, inputs.market)

    def test_income_error_fails_closed_and_skips_market(self):
        calls = []
        ns = source_functions([1, 2, 3])
        fake = FakeInputs(calls, {
            "exchange_info_received_ms": 1,
            "mark_time_ms": 1,
        }, exception=OfflineBlocked("INCOME_HISTORY_INCOMPLETE"))
        with self.assertRaisesRegex(OfflineBlocked, "INCOME_HISTORY_INCOMPLETE"):
            ns["_policy_inputs_market_last"](fake, None, {}, {})
        self.assertEqual(calls, ["income"])

    def test_missing_market_timestamp_fails_closed(self):
        calls = []
        ns = source_functions([100, 150, 200])
        fake = FakeInputs(calls, {"exchange_info_received_ms": 199})
        with self.assertRaisesRegex(OfflineBlocked, "POLICY_SNAPSHOT_MARKET_TIMES_MISSING"):
            ns["_policy_inputs_market_last"](fake, None, {}, {})
        self.assertEqual(calls, ["income", "market"])

    def test_clock_moves_backwards_fails_closed(self):
        calls = []
        ns = source_functions([1000, 900, 990])
        fake = FakeInputs(calls, {
            "exchange_info_received_ms": 900,
            "mark_time_ms": 800,
        })
        with self.assertRaisesRegex(OfflineBlocked, "POLICY_SNAPSHOT_LOCAL_CLOCK_MOVED_BACKWARD"):
            ns["_policy_inputs_market_last"](fake, None, {}, {})

    def test_time_code_preserves_age_when_market_is_old(self):
        f = filter_mod
        # No bypass: reordering does not authorize stale market usage.
        fixture = {
            "serverTime": 42,
            "symbols": [],
        }
        with self.assertRaisesRegex(f.FilterBlocked, r"EXCHANGE_INFO_TOO_OLD:AGE_MS=300001:LIMIT_MS=300000"):
            f.symbol_rules(fixture, "AINUSDT", received_at_ms=500_000, now_ms=800_001)

    def test_timing_suffix_only_contains_durations(self):
        ns = source_functions([])
        suffix = ns["_policy_timing_suffix"]("PREFLIGHT", {
            "income_ms": 301_000, "market_ms": 71,
            "exchange_info_age_ms": 30, "mark_age_ms": 45,
        })
        self.assertEqual(suffix,
            ";PREFLIGHT_TIMING_MS:INCOME=301000,MARKET=71,INFO_AGE=30,MARK_AGE=45")
        self.assertNotIn("client", suffix.lower())

    def test_both_source_sites_use_market_last_helper(self):
        run = next(n for n in owner_ast.body
                   if isinstance(n, ast.FunctionDef) and n.name == "run_once")
        calls = [
            n for n in ast.walk(run)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_policy_inputs_market_last"
        ]
        self.assertEqual(len(calls), 2)
        # There should be no remaining direct market or income calls in run_once.
        direct = [
            n for n in ast.walk(run)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("policy_market", "signed_income_envelope")
        ]
        self.assertFalse(direct)

if __name__ == "__main__":
    unittest.main()
