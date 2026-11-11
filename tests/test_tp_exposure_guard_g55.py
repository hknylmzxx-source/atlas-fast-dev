"""G55/Faz 3: pozisyon bağlamlı TP alarmı + TP'siz açık pozisyon denetimi (offline, I/O'suz)."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tp_lifecycle_offline_g37 as g37
import tp_exposure_guard_offline_g55 as g55


def tp(symbol="USUSDT", side="SELL", status="NEW", otype="TAKE_PROFIT_MARKET",
       reduce_only=True, **extra):
    order = {"symbol": symbol, "side": side, "status": status, "type": otype,
             "reduceOnly": reduce_only}
    order.update(extra)
    return order


class ClassifyTests(unittest.TestCase):
    def test_flat_terminal_is_info(self):
        for status in ("EXPIRED", "CANCELED", "REJECTED"):
            self.assertEqual(g55.classify_tp_terminal(status, g55.ExposureContext("0", False)),
                             g55.CTX_FLAT_EXPIRY)

    def test_open_without_tp_is_alarm(self):
        self.assertEqual(g55.classify_tp_terminal("CANCELED", g55.ExposureContext("5", False)),
                         g55.CTX_EXPOSED_UNPROTECTED)
        self.assertEqual(g55.classify_tp_terminal("EXPIRED", g55.ExposureContext("-3", False)),
                         g55.CTX_EXPOSED_UNPROTECTED)

    def test_open_with_other_tp_is_protected(self):
        self.assertEqual(g55.classify_tp_terminal("CANCELED", g55.ExposureContext("5", True)),
                         g55.CTX_EXPOSED_PROTECTED)

    def test_missing_context_fails_closed(self):
        self.assertEqual(g55.classify_tp_terminal("EXPIRED", None), g55.CTX_EXPOSED_UNPROTECTED)

    def test_bad_context_rejected(self):
        with self.assertRaises(ValueError):
            g55.ExposureContext("abc", False)
        with self.assertRaises(ValueError):
            g55.ExposureContext("1", "yes")
        with self.assertRaises(ValueError):
            g55.ExposureContext("NaN", False)

    def test_non_terminal_rejected(self):
        with self.assertRaises(ValueError):
            g55.classify_tp_terminal("FILLED", g55.ExposureContext("0", False))


class UnprotectedPositionsTests(unittest.TestCase):
    def test_flat_has_no_violation(self):
        self.assertEqual(g55.unprotected_positions({"USUSDT": "0"}, []), [])

    def test_open_position_without_tp_violates(self):
        v = g55.unprotected_positions({"USUSDT": "10"}, [])
        self.assertEqual(v, [{"symbol": "USUSDT", "position_qty": "10",
                              "reason": "OPEN_POSITION_WITHOUT_LIVE_TP"}])

    def test_reduce_only_tp_protects_long(self):
        self.assertEqual(g55.unprotected_positions({"USUSDT": "10"}, [tp()]), [])

    def test_close_position_tp_protects(self):
        self.assertEqual(g55.unprotected_positions(
            {"USUSDT": "10"}, [tp(reduce_only=False, closePosition=True)]), [])

    def test_algo_order_shape_protects_short(self):
        order = {"symbol": "USUSDT", "side": "BUY", "algoStatus": "NEW",
                 "orderType": "TAKE_PROFIT_MARKET", "reduceOnly": "true"}
        self.assertEqual(g55.unprotected_positions({"USUSDT": "-4"}, [order]), [])

    def test_wrong_side_or_not_reduce_only_does_not_protect(self):
        for order in (tp(side="BUY"), tp(reduce_only=False), tp(status="CANCELED"),
                      tp(status="EXPIRED"), tp(otype="STOP_MARKET"), tp(symbol="BTCUSDT"),
                      tp(positionSide="SHORT"), "garbage", None):
            with self.subTest(order=order):
                self.assertEqual(len(g55.unprotected_positions({"USUSDT": "10"}, [order])), 1)

    def test_partial_tp_fill_remaining_position_without_tp_violates(self):
        orders = [tp(status="FILLED")]  # TP1 doldu, TP2 düştü, kalan pozisyon korumasız
        v = g55.unprotected_positions({"USUSDT": "3"}, orders)
        self.assertEqual(v[0]["reason"], "OPEN_POSITION_WITHOUT_LIVE_TP")

    def test_stop_only_does_not_count_as_tp(self):
        stop = tp(otype="STOP_MARKET")
        self.assertEqual(len(g55.unprotected_positions({"USUSDT": "1"}, [stop])), 1)

    def test_unreadable_inputs_fail_closed(self):
        self.assertEqual(g55.unprotected_positions(None, [])[0]["reason"], "UNREADABLE_POSITIONS")
        self.assertEqual(g55.unprotected_positions({"USUSDT": "x"}, [])[0]["reason"],
                         "UNREADABLE_POSITION")
        self.assertEqual(g55.unprotected_positions({"bad sym": "1"}, [])[0]["reason"],
                         "UNREADABLE_POSITION")
        self.assertEqual(g55.unprotected_positions({"USUSDT": True}, [])[0]["reason"],
                         "UNREADABLE_POSITION")

    def test_multiple_symbols_sorted(self):
        v = g55.unprotected_positions({"ZZZUSDT": "1", "AAAUSDT": "2", "BBBUSDT": "0"}, [])
        self.assertEqual([x["symbol"] for x in v], ["AAAUSDT", "ZZZUSDT"])


class G37ContextAlarmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="atlas-g55-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal"
        self.now = 10_000
        self.source = g37.MockTrustedSource()
        self.tracker = g37.TPLifecycleOffline(g37.EventJournal(self.path), self.source,
                                              clock_ms=lambda: self.now)
        self.tp1 = g37.Binding("TP1", "g55-scope", "USUSDT", "mock-tp1", "2")
        self.tp2 = g37.Binding("TP2", "g55-scope", "USUSDT", "mock-tp2", "3")
        self.tracker.record_synthetic_post_intent(self.tp1)
        self.tracker.record_synthetic_post_intent(self.tp2)

    def read(self, role, status, filled="0", exposure=None):
        self.now += 10
        view = self.tracker.view(role)
        receipt = self.source.issue_mock_read(view.binding, status, filled, self.now,
                                              view.read_sequence + 1)
        return self.tracker.observe(role, receipt, exposure=exposure)

    def test_stop_out_then_expired_no_alarm_but_logged(self):
        self.read("TP1", "NEW"); self.read("TP2", "NEW")
        e1 = self.read("TP1", "EXPIRED", exposure=g55.ExposureContext("0", False))
        e2 = self.read("TP2", "EXPIRED", exposure=g55.ExposureContext("0", False))
        self.assertEqual((e1.alarm, e1.reason), ("NONE", "MOCK_READ_ACCEPTED_FLAT_TERMINAL"))
        self.assertEqual(e2.alarm, "NONE")
        self.assertEqual(self.tracker.alarms, ())
        self.assertIn(e1, self.tracker.events)  # bilgi kaydı olarak duruyor

    def test_tp_canceled_while_position_open_alarms(self):
        self.read("TP1", "NEW"); self.read("TP2", "NEW")
        e = self.read("TP1", "CANCELED", exposure=g55.ExposureContext("5", False))
        self.assertEqual((e.alarm, e.reason),
                         ("OWNER_ALARM", "MOCK_READ_ACCEPTED_EXPOSED_UNPROTECTED"))
        self.assertEqual(self.tracker.alarms, (e,))

    def test_tp_canceled_but_other_tp_live_no_alarm(self):
        self.read("TP1", "NEW"); self.read("TP2", "NEW")
        e = self.read("TP1", "CANCELED", exposure=g55.ExposureContext("5", True))
        self.assertEqual(e.alarm, "NONE")

    def test_partial_fill_then_remaining_without_tp_alarms(self):
        self.read("TP1", "NEW"); self.read("TP2", "NEW")
        self.read("TP1", "FILLED", "2")
        e = self.read("TP2", "EXPIRED", exposure=g55.ExposureContext("3", False))
        self.assertEqual(e.alarm, "OWNER_ALARM")

    def test_without_context_keeps_old_fail_closed_alarm(self):
        self.read("TP1", "NEW")
        e = self.read("TP1", "EXPIRED")
        self.assertEqual((e.alarm, e.reason), ("OWNER_ALARM", "MOCK_READ_ACCEPTED"))

    def test_context_ignored_for_non_terminal(self):
        e = self.read("TP1", "NEW", exposure=g55.ExposureContext("0", False))
        self.assertEqual((e.alarm, e.reason), ("NONE", "MOCK_READ_ACCEPTED"))

    def test_restart_replays_context_events(self):
        self.read("TP1", "NEW"); self.read("TP2", "NEW")
        self.read("TP1", "EXPIRED", exposure=g55.ExposureContext("0", False))
        self.read("TP2", "CANCELED", exposure=g55.ExposureContext("2", False))
        before = self.tracker.alarms
        restarted = g37.TPLifecycleOffline(g37.EventJournal(self.path), g37.MockTrustedSource(),
                                           clock_ms=lambda: self.now)
        self.assertEqual(restarted.alarms, before)
        self.assertEqual(len(before), 1)

    def test_tampered_context_alarm_rejected_on_replay(self):
        import json
        self.read("TP1", "NEW")
        self.read("TP1", "CANCELED", exposure=g55.ExposureContext("5", False))
        last = sorted(self.path.glob("*.json"))[-1]
        doc = json.loads(last.read_bytes())
        doc["alarm"] = "NONE"  # alarmı sessizce susturma denemesi
        doc["digest"] = g37._digest(doc)
        last.write_bytes(g37._encode(doc))
        with self.assertRaises(g37.LifecycleError):
            g37.TPLifecycleOffline(g37.EventJournal(self.path), g37.MockTrustedSource(),
                                   clock_ms=lambda: self.now)


if __name__ == "__main__":
    unittest.main()
