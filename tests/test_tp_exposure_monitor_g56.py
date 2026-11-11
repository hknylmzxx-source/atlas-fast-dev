"""G56: TP'siz-pozisyon gözetim denetimi — salt-okuma, tekilleştirme, asla raise etmez."""
import ast, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tp_exposure_monitor_g56 as m
import fast_telemetry_g56 as tm

SYM = "USUSDT"


def ev(qty="10", orders=(), algo=(), errors=(), own_stop=None):
    e = {"symbol": SYM, "errors": list(errors),
         "account": ({"positions": [{"symbol": SYM, "positionAmt": qty}]}, 1),
         "open_orders": (list(orders), 1), "open_algo": (list(algo), 1)}
    if own_stop is not None:
        e["own_stop"] = ("FOUND", own_stop)
    return e


TP = {"symbol": SYM, "clientOrderId": "tp1", "type": "LIMIT", "side": "SELL",
      "status": "NEW", "reduceOnly": True, "positionSide": "BOTH"}
STOP = {"symbol": SYM, "clientAlgoId": "st", "orderType": "STOP_MARKET", "side": "SELL",
        "algoStatus": "NEW", "closePosition": True, "positionSide": "BOTH"}


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class MonitorTests(unittest.TestCase):
    def mk(self, telemetry=None, alarm=None):
        self.sent = []
        self.clock = Clock()
        return m.TpExposureMonitor(alarm=alarm or self.sent.append, symbol=SYM,
                                   own_ids={"tp1": "TP1", "st": "STOP"},
                                   telemetry=telemetry, clock_s=self.clock)

    def test_protected_position_no_alarm(self):
        mon = self.mk()
        self.assertEqual(mon.check(ev(orders=[TP], algo=[STOP])), [])
        self.assertEqual(self.sent, [])

    def test_flat_after_stop_no_alarm(self):
        mon = self.mk()
        self.assertEqual(mon.check(ev(qty="0")), [])
        self.assertEqual(self.sent, [])

    def test_open_position_without_tp_alarms_stop_is_not_tp(self):
        mon = self.mk()
        v = mon.check(ev(algo=[STOP]))
        self.assertEqual(v[0]["reason"], "OPEN_POSITION_WITHOUT_LIVE_TP")
        self.assertEqual(self.sent, ["TP_EXPOSURE_ALARM:USUSDT:10:OPEN_POSITION_WITHOUT_LIVE_TP"])

    def test_dedup_and_cooldown(self):
        mon = self.mk()
        mon.check(ev()); mon.check(ev())
        self.assertEqual(len(self.sent), 1)
        self.clock.t += m.COOLDOWN_S + 1
        mon.check(ev())
        self.assertEqual(len(self.sent), 2)

    def test_incomplete_evidence_alarms(self):
        mon = self.mk()
        e = ev(); del e["open_orders"]
        v = mon.check(e)
        self.assertEqual(v[0]["reason"], "EVIDENCE_INCOMPLETE")
        self.assertTrue(self.sent[0].startswith("TP_EXPOSURE_EVIDENCE_INCOMPLETE:USUSDT:open_orders"))

    def test_read_failure_alarms_and_never_raises(self):
        mon = self.mk()
        def boom(): raise TimeoutError("x")
        v = mon.maybe_audit(boom)
        self.assertEqual(v[0]["reason"], "AUDIT_FAILED")
        self.assertEqual(self.sent, ["TP_EXPOSURE_AUDIT_FAILED:USUSDT:TimeoutError"])

    def test_alarm_channel_failure_never_raises(self):
        def bad(msg): raise RuntimeError("down")
        mon = self.mk(alarm=bad)
        self.assertEqual(len(mon.check(ev())), 1)
        self.assertIn("ALARM_CHANNEL_FAILED:RuntimeError", mon.errors)

    def test_garbage_evidence_never_raises(self):
        mon = self.mk()
        for junk in (None, 5, {"account": "x"}, {"account": ({"positions": "x"}, 1)}):
            self.assertTrue(mon.check(junk))

    def test_interval_throttles_reads(self):
        mon = self.mk()
        calls = []
        def read(): calls.append(1); return ev(orders=[TP])
        mon.maybe_audit(read); mon.maybe_audit(read)
        self.assertEqual(len(calls), 1)
        self.clock.t += m.MIN_INTERVAL_S
        mon.maybe_audit(read)
        self.assertEqual(len(calls), 2)

    def test_lifecycle_observed_into_telemetry(self):
        with tempfile.TemporaryDirectory() as d:
            t = tm.Telemetry(Path(d) / "tel")
            mon = self.mk(telemetry=t)
            mon.check(ev(orders=[TP], algo=[STOP], own_stop=STOP))
            mon.check(ev(qty="0", algo=[], own_stop=dict(STOP, algoStatus="FINISHED")))
            import json
            rows = [json.loads(l) for p in (Path(d) / "tel").glob("*.jsonl") for l in p.read_text().splitlines()]
            kinds = [r["kind"] for r in rows]
            self.assertIn("ORDER_STATE", kinds)
            self.assertIn("ORDER_LEFT_OPEN_LIST", kinds)
            self.assertIn("FINISHED", [r.get("state") for r in rows])

    def test_module_is_read_only(self):
        tree = ast.parse((ROOT / "tp_exposure_monitor_g56.py").read_text())
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for forbidden in ("post_once", "delete_once", "post_reduce_only_market", "cancel", "new_order"):
            self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
