"""G56: owner entegrasyonu — gözetim uykusunda TP denetimi, telemetri kancaları; izole AST derlemesi."""
import ast, json, sys, tempfile, types, unittest
from decimal import Decimal
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SRC = (ROOT / "owner_single_execution.py").read_text()
TREE = ast.parse(SRC)
FUNCS = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}
SYM = "USUSDT"


def compile_ns(names, extra):
    mod = ast.Module(body=[FUNCS[n] for n in names], type_ignores=[])
    ns = dict(extra)
    exec(compile(mod, "owner_single_execution.py", "exec"), ns)
    return ns


class FakeTime:
    def __init__(self): self.slept = []
    def sleep(self, s): self.slept.append(s)
    def time(self): return 0


class ReadOnlyGuardVenue:
    def __init__(self, qty="5", orders=(), fail=False):
        self.qty, self.orders, self.fail = qty, list(orders), fail
        self.reads = 0
    def _t(self, x): self.reads += 1; return (x, 1000)
    def read_account(self):
        if self.fail: raise TimeoutError("down")
        return self._t({"positions": [{"symbol": SYM, "positionAmt": self.qty}]})
    def read_open_algo_orders(self, s): return self._t([])
    def read_open_orders(self, s): return self._t(self.orders)
    def read_mark(self, s): return self._t({"markPrice": "1"})
    def query_algo(self, i): return {"clientAlgoId": i, "algoStatus": "NEW"}
    def now_ms(self): return 1000
    def __getattr__(self, name):
        raise AssertionError("WRITE_OR_UNKNOWN_CALL:" + name)


BUNDLE = {"entry": {"symbol": SYM, "side": "BUY"},
          "stop": {"clientAlgoId": "st"},
          "take_profits": [{"newClientOrderId": "tp1"}, {"newClientOrderId": "tp2"}]}


class SleepIntegrationTests(unittest.TestCase):
    def ns(self):
        self.ft = FakeTime()
        return compile_ns(["_tp_exposure_sleep", "_telemetry"],
                          {"time": self.ft, "_TELEMETRY_ARMED": False, "PRIVATE": Path("/nonexistent")})

    def test_unprotected_position_alarms_through_owner_alarm_channel_and_still_sleeps(self):
        ns = self.ns(); alarms = []
        v = ReadOnlyGuardVenue(qty="5", orders=[])
        sleep = ns["_tp_exposure_sleep"]((None, alarms.append, v), BUNDLE)
        sleep(5)
        self.assertEqual(alarms, ["TP_EXPOSURE_ALARM:USUSDT:5:OPEN_POSITION_WITHOUT_LIVE_TP"])
        self.assertEqual(self.ft.slept, [5])
        sleep(5)   # aralık dolmadı -> yeni okuma yok, tekrar bildirim yok
        self.assertEqual(len(alarms), 1)

    def test_protected_position_no_alarm(self):
        ns = self.ns(); alarms = []
        tp = {"symbol": SYM, "clientOrderId": "tp1", "type": "LIMIT", "side": "SELL",
              "status": "NEW", "reduceOnly": "true"}
        sleep = ns["_tp_exposure_sleep"]((None, alarms.append, ReadOnlyGuardVenue(orders=[tp])), BUNDLE)
        sleep(5)
        self.assertEqual(alarms, [])

    def test_read_failure_never_raises_and_alarms(self):
        ns = self.ns(); alarms = []
        sleep = ns["_tp_exposure_sleep"]((None, alarms.append, ReadOnlyGuardVenue(fail=True)), BUNDLE)
        sleep(5)
        self.assertEqual(self.ft.slept, [5])
        self.assertTrue(alarms and alarms[0].startswith("TP_EXPOSURE_EVIDENCE_INCOMPLETE:USUSDT"))

    def test_bad_bundle_falls_back_to_plain_sleep_with_alarm(self):
        ns = self.ns(); alarms = []
        sleep = ns["_tp_exposure_sleep"]((None, alarms.append, ReadOnlyGuardVenue()), {"entry": {}})
        self.assertEqual(sleep, self.ft.sleep)
        self.assertEqual(alarms, ["TP_EXPOSURE_AUDIT_UNAVAILABLE:KeyError"])

    def test_alarm_channel_down_never_raises(self):
        ns = self.ns()
        def down(msg): raise RuntimeError("x")
        sleep = ns["_tp_exposure_sleep"]((None, down, ReadOnlyGuardVenue()), BUNDLE)
        sleep(1)
        self.assertEqual(self.ft.slept, [1])


class TelemetryHookTests(unittest.TestCase):
    def test_unarmed_telemetry_is_none_so_tests_never_touch_owner_dir(self):
        ns = compile_ns(["_telemetry"], {"_TELEMETRY_ARMED": False, "PRIVATE": Path("/nonexistent")})
        self.assertIsNone(ns["_telemetry"]())

    def test_armed_hooks_write_receipt_and_never_raise(self):
        with tempfile.TemporaryDirectory() as d:
            import fast_telemetry_g56 as tm
            tm._DEFAULT.clear()
            import hashlib
            ns = compile_ns(["_telemetry", "_telemetry_observe", "fp"],
                            {"_TELEMETRY_ARMED": True, "PRIVATE": Path(d), "hashlib": hashlib, "json": json})
            # expected_uid=501 Mac'te sahibin uid'i; test ortamı farklıysa yazma reddedilir -> yine raise yok
            tel = ns["_telemetry"]()
            tel.expected_uid = None
            ns["_telemetry_observe"]("TP1", {"newClientOrderId": "tp1"},
                                     {"clientOrderId": "tp1", "status": "EXPIRED", "reduceOnly": True}, "SIGNED_READBACK")
            ns["_telemetry_observe"]("STOP", {"clientAlgoId": "st"}, None, "SIGNED_READBACK")
            rows = [json.loads(l) for p in (Path(d) / tm.DIRNAME).glob("*.jsonl")
                    for l in p.read_text().splitlines()]
            kinds = [r["kind"] for r in rows]
            self.assertEqual(kinds[:2], ["ORDER_STATE", "ORDER_ERROR"])  # FAZ3C canli varyant: PRE_ENTRY kancasi yok
            self.assertEqual(rows[0]["state"], "EXPIRED")
            tm._DEFAULT.clear()


class StaticGuaranteeTests(unittest.TestCase):
    def calls(self, fn):
        return [n for n in ast.walk(FUNCS[fn]) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]

    def test_hook_call_sites_are_wrapped_in_try_except(self):
        for fn, hook in (("checked", "_telemetry_observe"),
                         ("supervise_until_flat", "_tp_exposure_sleep")):
            tries = [n for n in ast.walk(FUNCS[fn]) if isinstance(n, ast.Try)]
            wrapped = any(any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == hook
                              for b in t.body for c in ast.walk(b))
                          and any(h.type is not None and getattr(h.type, "id", "") == "Exception" for h in t.handlers)
                          for t in tries)
            self.assertTrue(wrapped, fn)

    def test_telemetry_armed_only_in_main_after_owner_only(self):
        main = FUNCS["main"]
        order = [getattr(c.func, "id", None) for c in ast.walk(main) if isinstance(c, ast.Call)]
        self.assertIn("owner_only", order)
        assigns = [n for n in ast.walk(main) if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", "") == "_TELEMETRY_ARMED" for t in n.targets)]
        self.assertEqual(len(assigns), 1)
        self.assertGreater(assigns[0].lineno, next(c.lineno for c in ast.walk(main)
                           if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "owner_only"))
        top = [n for n in TREE.body if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "_TELEMETRY_ARMED" for t in n.targets)]
        self.assertEqual(len(top), 1)
        self.assertIs(top[0].value.value, False)

    def test_new_helpers_never_send_or_cancel(self):
        for fn in ("_telemetry", "_telemetry_observe", "_tp_exposure_sleep"):
            attrs = {n.attr for n in ast.walk(FUNCS[fn]) if isinstance(n, ast.Attribute)}
            names = {n.id for n in ast.walk(FUNCS[fn]) if isinstance(n, ast.Name)}
            for bad in ("post_once", "delete_once", "one_write", "place_stop_write", "emergency_flatten",
                        "journal", "post_reduce_only_market"):
                self.assertNotIn(bad, attrs | names, fn)


if __name__ == "__main__":
    unittest.main()
