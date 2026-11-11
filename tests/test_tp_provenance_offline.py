"""TP kaynak eşleşmesi: yalnız sentetik olaylar ve bağımsız mock okumalar."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tp_provenance_offline import TpEvidenceBlocked, TpProvenanceReplay

WIRE = {
    "symbol": "AINUSDT", "side": "SELL", "positionSide": "BOTH",
    "type": "LIMIT", "timeInForce": "GTC", "quantity": "2", "price": "0.35",
    "reduceOnly": "true", "newClientOrderId": "OFFLINE-TP1-36",
}
NOW = 1_000_000


def event_and_receipt(*, timestamp=NOW - 10, status="NEW", filled="0", order_id=36):
    event = {
        "e": "ORDER_TRADE_UPDATE", "E": timestamp, "T": timestamp,
        "o": {"s": WIRE["symbol"], "c": WIRE["newClientOrderId"],
              "S": WIRE["side"], "ps": "BOTH", "o": "LIMIT", "f": "GTC",
              "q": "2", "p": "0.35", "R": True, "i": order_id,
              "X": status, "x": "NEW" if status == "NEW" else "TRADE",
              "z": filled, "T": timestamp},
    }
    receipt = {
        "symbol": WIRE["symbol"], "clientOrderId": WIRE["newClientOrderId"],
        "side": WIRE["side"], "positionSide": "BOTH", "type": "LIMIT",
        "timeInForce": "GTC", "origQty": "2", "price": "0.35",
        "reduceOnly": True, "orderId": order_id, "status": status,
        "executedQty": filled, "updateTime": timestamp,
    }
    return event, receipt


class MockOrderRead:
    def __init__(self, receipt, received_at_ms=NOW):
        self.receipt, self.received_at_ms = receipt, received_at_ms
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return deepcopy(self.receipt), self.received_at_ms


def verifier(**kwargs):
    return TpProvenanceReplay(scope="OFFLINE-SCOPE-36", role="TP1", wire=WIRE, **kwargs)


class TpProvenanceTests(unittest.TestCase):
    def assert_no_authority(self, result):
        for field in ("authenticated_signed_get_verified", "entry_authorized",
                      "may_clear_latch", "may_release_owner_lock"):
            self.assertIs(result[field], False, field)

    def inspect(self, event, receipt, **kwargs):
        reader = MockOrderRead(receipt)
        result = verifier().inspect(event, read_order=reader, now_ms=NOW, **kwargs)
        self.assert_no_authority(result)
        return result, reader

    def test_matching_event_requires_separate_exact_order_read(self):
        event, receipt = event_and_receipt()
        result, reader = self.inspect(event, receipt)
        self.assertTrue(result["synthetic_tp_consistent"])
        self.assertEqual(reader.calls, [{"symbol": WIRE["symbol"],
            "orig_client_order_id": WIRE["newClientOrderId"], "recv_window": 5000}])

    def test_buy_tp2_and_equivalent_decimal_strings(self):
        wire = dict(WIRE, side="BUY", price="0.350", quantity="2.00")
        replay = TpProvenanceReplay(scope="OFFLINE-SHORT", role="TP2", wire=wire)
        event, receipt = event_and_receipt()
        event["o"]["S"] = receipt["side"] = "BUY"
        result = replay.inspect(event, read_order=MockOrderRead(receipt), now_ms=NOW)
        self.assertTrue(result["synthetic_tp_consistent"])
        self.assert_no_authority(result)

    def test_untrusted_labels_and_fake_signature_do_not_replace_read(self):
        event, receipt = event_and_receipt()
        event.update(source="SIGNED_BINANCE_GET", authenticated=True,
                     owner_approved=True, signature="OFFLINE-FORGED", receipt=receipt)
        result = verifier().inspect(event, read_order=None, now_ms=NOW)
        self.assertFalse(result["synthetic_tp_consistent"])
        self.assertEqual(result["reasons"], ["INDEPENDENT_ORDER_READ_FAILED"])
        self.assert_no_authority(result)

    def test_foreign_or_incomplete_events_rejected_before_read(self):
        event, receipt = event_and_receipt()
        mutations = {"s": "OTHERUSDT", "c": "FOREIGN-TP", "S": "BUY",
                     "ps": "LONG", "o": "MARKET", "f": "IOC", "R": False,
                     "q": "3", "p": "0.36", "i": True, "x": "CANCELED"}
        for key, value in mutations.items():
            with self.subTest(key=key):
                fake = deepcopy(event)
                fake["o"][key] = value
                result, reader = self.inspect(fake, receipt)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assertEqual(reader.calls, [])
        for fake in (None, {}, {"e": "TRADE", "o": event["o"]},
                     dict(event, o=[])):
            with self.subTest(fake=fake):
                result, reader = self.inspect(fake, receipt)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assertEqual(reader.calls, [])

    def test_stale_future_or_boolean_event_clocks_rejected(self):
        for timestamp in (NOW - 5001, NOW + 1, True, -1):
            with self.subTest(timestamp=timestamp):
                event, receipt = event_and_receipt(timestamp=timestamp)
                result, reader = self.inspect(event, receipt)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assertEqual(reader.calls, [])
        event, receipt = event_and_receipt()
        for key, value in (("T", True), ("T", NOW - 11), ("E", NOW - 11)):
            with self.subTest(key=key, value=value):
                result, reader = self.inspect(dict(event, **{key: value}), receipt)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assertEqual(reader.calls, [])

    def test_malformed_and_nonfinite_amounts_rejected(self):
        for field in ("q", "p", "z"):
            for value in ("NaN", "sNaN", "Infinity", "-1", "", "bad", True, 2, " 2"):
                with self.subTest(field=field, value=value):
                    event, receipt = event_and_receipt()
                    event["o"][field] = value
                    result, reader = self.inspect(event, receipt)
                    self.assertFalse(result["synthetic_tp_consistent"])
                    self.assertEqual(reader.calls, [])

    def test_fill_states_must_match_quantity(self):
        for status, filled in (("NEW", "1"), ("PARTIALLY_FILLED", "0"),
                               ("PARTIALLY_FILLED", "2"), ("FILLED", "1"),
                               ("FILLED", "3"), ("CANCELED", "0")):
            with self.subTest(status=status, filled=filled):
                event, receipt = event_and_receipt(status=status, filled=filled)
                result, reader = self.inspect(event, receipt)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assertEqual(reader.calls, [])

    def test_forged_or_mismatched_read_cannot_confirm_event(self):
        mutations = {"symbol": "OTHERUSDT", "clientOrderId": "FOREIGN-TP",
                     "side": "BUY", "positionSide": "SHORT", "type": "MARKET",
                     "timeInForce": "IOC", "orderId": 37, "updateTime": NOW - 9,
                     "status": "FILLED", "executedQty": "2", "reduceOnly": 1,
                     "origQty": "3", "price": "0.36"}
        for key, value in mutations.items():
            with self.subTest(key=key):
                event, receipt = event_and_receipt()
                receipt[key] = value
                result, reader = self.inspect(event, receipt)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assertEqual(len(reader.calls), 1)

    def test_read_shape_clock_timeout_and_absence_fail_closed(self):
        event, receipt = event_and_receipt()
        for observation in (None, receipt, (receipt,), ([], NOW), (receipt, True),
                            (receipt, NOW + 1), (receipt, NOW - 5001),
                            (receipt, NOW - 11)):
            with self.subTest(observation=observation):
                result = verifier().inspect(event, read_order=lambda **_: observation, now_ms=NOW)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assert_no_authority(result)
        for error in (TimeoutError, LookupError):
            with self.subTest(error=error):
                def failed_read(**kwargs):
                    raise error("OFFLINE_READ_FAILED")
                result = verifier().inspect(event, read_order=failed_read, now_ms=NOW)
                self.assertEqual(result["reasons"], ["INDEPENDENT_ORDER_READ_FAILED"])

    def test_new_partial_and_filled_progress_monotonically(self):
        replay = verifier()
        for offset, status, filled in ((30, "NEW", "0"), (20, "PARTIALLY_FILLED", "1"),
                                       (10, "FILLED", "2")):
            event, receipt = event_and_receipt(timestamp=NOW - offset, status=status, filled=filled)
            result = replay.inspect(event, read_order=MockOrderRead(receipt), now_ms=NOW)
            self.assertTrue(result["synthetic_tp_consistent"])
            self.assert_no_authority(result)

    def test_read_captured_before_event_publication_is_rejected(self):
        replay = verifier()
        event, receipt = event_and_receipt(timestamp=NOW - 10)
        event["E"] = NOW - 2
        old_read = MockOrderRead(receipt, received_at_ms=NOW - 5)
        result = replay.inspect(event, read_order=old_read, now_ms=NOW)
        self.assertFalse(result["synthetic_tp_consistent"])
        self.assertEqual(result["reasons"], ["TP_READ_EVENT_MISMATCH"])
        # Eski okuma olayı tüketmez; yayım sonrasındaki mock okuma geçebilir.
        fresh_read = MockOrderRead(receipt, received_at_ms=NOW - 1)
        self.assertTrue(replay.inspect(event, read_order=fresh_read,
                                      now_ms=NOW)["synthetic_tp_consistent"])

    def test_replay_regression_and_order_id_rebinding_rejected_before_read(self):
        replay = verifier()
        event, receipt = event_and_receipt(status="PARTIALLY_FILLED", filled="1")
        self.assertTrue(replay.inspect(event, read_order=MockOrderRead(receipt),
                                      now_ms=NOW)["synthetic_tp_consistent"])
        for timestamp, status, filled, order_id in (
                (NOW - 10, "PARTIALLY_FILLED", "1", 36),
                (NOW - 11, "NEW", "0", 36), (NOW - 9, "NEW", "0", 36),
                (NOW - 9, "PARTIALLY_FILLED", "1", 36), (NOW - 9, "FILLED", "2", 37)):
            with self.subTest(timestamp=timestamp, status=status, order_id=order_id):
                event, receipt = event_and_receipt(timestamp=timestamp, status=status,
                                                  filled=filled, order_id=order_id)
                reader = MockOrderRead(receipt)
                result = replay.inspect(event, read_order=reader, now_ms=NOW)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assertEqual(reader.calls, [])

    def test_terminal_fill_cannot_be_reopened(self):
        replay = verifier()
        event, receipt = event_and_receipt(status="FILLED", filled="2")
        self.assertTrue(replay.inspect(event, read_order=MockOrderRead(receipt),
                                      now_ms=NOW)["synthetic_tp_consistent"])
        event, receipt = event_and_receipt(timestamp=NOW - 9, status="PARTIALLY_FILLED", filled="1")
        reader = MockOrderRead(receipt)
        self.assertFalse(replay.inspect(event, read_order=reader, now_ms=NOW)["synthetic_tp_consistent"])
        self.assertEqual(reader.calls, [])

    def test_failed_read_or_forgery_does_not_consume_valid_event(self):
        replay = verifier()
        event, receipt = event_and_receipt()
        bad_read = MockOrderRead(dict(receipt, orderId=37))
        self.assertFalse(replay.inspect(event, read_order=bad_read, now_ms=NOW)["synthetic_tp_consistent"])
        forged = deepcopy(event)
        forged["o"]["c"] = "FOREIGN-TP"
        self.assertFalse(replay.inspect(forged, read_order=bad_read, now_ms=NOW)["synthetic_tp_consistent"])
        self.assertTrue(replay.inspect(event, read_order=MockOrderRead(receipt),
                                      now_ms=NOW)["synthetic_tp_consistent"])

    def test_caller_mutation_during_read_cannot_rebind_captured_event(self):
        replay = verifier()
        event, receipt = event_and_receipt()
        def mutate(**kwargs):
            event["o"].update(i=999, c="FOREIGN-TP")
            return receipt, NOW
        self.assertTrue(replay.inspect(event, read_order=mutate, now_ms=NOW)["synthetic_tp_consistent"])
        # Kabul, okuma öncesi yakalanmış immutable olaya aittir; değişmiş olay reddedilir.
        self.assertFalse(replay.inspect(event, read_order=MockOrderRead(receipt),
                                       now_ms=NOW)["synthetic_tp_consistent"])

    def test_plan_is_copied_and_invalid_plan_is_refused(self):
        wire = dict(WIRE)
        replay = TpProvenanceReplay(scope="OFFLINE", role="TP1", wire=wire)
        wire["newClientOrderId"] = "FOREIGN-TP"
        event, receipt = event_and_receipt()
        self.assertTrue(replay.inspect(event, read_order=MockOrderRead(receipt),
                                      now_ms=NOW)["synthetic_tp_consistent"])
        for patch in ({"quantity": "0"}, {"price": "NaN"}, {"reduceOnly": True},
                      {"newClientOrderId": ""}, {"positionSide": "LONG"}, {"extra": True}):
            with self.subTest(patch=patch), self.assertRaises(TpEvidenceBlocked):
                TpProvenanceReplay(scope="OFFLINE", role="TP1", wire=dict(WIRE, **patch))
        for role in ("ENTRY", "STOP", "TP3"):
            with self.subTest(role=role), self.assertRaises(TpEvidenceBlocked):
                TpProvenanceReplay(scope="OFFLINE", role=role, wire=WIRE)

    def test_invalid_review_limits_do_not_read(self):
        event, receipt = event_and_receipt()
        for now_ms, max_age in ((True, 5000), (-1, 5000), (NOW, True),
                                (NOW, 0), (NOW, 10001)):
            with self.subTest(now_ms=now_ms, max_age=max_age):
                reader = MockOrderRead(receipt)
                result = verifier().inspect(event, read_order=reader, now_ms=now_ms, max_age_ms=max_age)
                self.assertFalse(result["synthetic_tp_consistent"])
                self.assertEqual(reader.calls, [])


if __name__ == "__main__":
    unittest.main()
