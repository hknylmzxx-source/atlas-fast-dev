"""Görev 36: bağımsız mock kaynak bağlaması; canlı kanıt veya işlem yetkisi yok."""

import copy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tp_observation_provenance_candidate import (
    MockProvenanceBlocked, MockReadReceipt, MockSignedReadSource,
    TpObservationProvenanceCandidate,
)


NOW = 100000
IDENTITY = dict(scope="OFFLINE-SCOPE", symbol="AINUSDT", order_id=321,
                client_order_id="OFFLINE-TP1", role="TP1")
DENIED_FLAGS = (
    "authenticated_signed_get_verified", "real_exchange_data_verified",
    "entry_authorized", "may_clear_latch", "may_release_owner_lock",
    "may_submit_order", "may_modify_stop", "may_retry_wal",
)


def sample(**changes):
    row = dict(IDENTITY, observed_at_ms=NOW - 100, status="PARTIALLY_FILLED",
               quantity="4", executed_qty="1")
    row.update(changes)
    return row


class TpObservationProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.row = sample()
        self.source = MockSignedReadSource({"read-1": self.row})
        self.reviewer = self.make_reviewer(self.source)
        self.receipt = self.source.read(request_id="read-1", received_at_ms=NOW - 50)

    def make_reviewer(self, source, **changes):
        return TpObservationProvenanceCandidate(source=source, **(IDENTITY | changes))

    def inspect(self, row=None, **changes):
        kwargs = dict(receipt=self.receipt, now_ms=NOW, normal_orders=[],
                      conditional_orders=[], stop_state="UNKNOWN")
        kwargs.update(changes)
        result = self.reviewer.inspect(self.row if row is None else row, **kwargs)
        self.assertTrue(result["mock_only"])
        for key in DENIED_FLAGS:
            self.assertIs(result[key], False, key)
        return result

    def reject(self, reason, row=None, **changes):
        result = self.inspect(row, **changes)
        self.assertFalse(result["mock_source_receipt_bound"])
        self.assertEqual(result["reasons"], [reason])
        self.assertEqual(result["decision"], "MOCK_TP_OBSERVATION_REJECTED")
        return result

    def load_sequence(self, *rows):
        self.source = MockSignedReadSource({f"read-{i}": row for i, row in enumerate(rows)})
        self.reviewer = self.make_reviewer(self.source)
        return [self.source.read(request_id=f"read-{i}", received_at_ms=NOW - 10)
                for i in range(len(rows))]

    def test_exact_source_receipt_is_only_a_mock_match(self):
        result = self.inspect()
        self.assertTrue(result["mock_source_receipt_bound"])
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["decision"], "MOCK_TP_OBSERVATION_MATCH_ONLY")

    def test_caller_signed_boolean_is_not_a_receipt(self):
        self.reject("MOCK_SOURCE_RECEIPT_NOT_BOUND", receipt=True)

    def test_signed_source_label_and_claimed_token_are_not_evidence(self):
        self.reject("MOCK_SOURCE_RECEIPT_NOT_BOUND", receipt={
            "signed": True, "source": "signed-read", "authority_token": object()})

    def test_fabricated_receipt_with_new_token_is_rejected(self):
        forged = replace(self.receipt, authority_token=object())
        self.reject("MOCK_SOURCE_RECEIPT_NOT_BOUND", receipt=forged)

    def test_receipt_from_other_source_is_rejected(self):
        other = MockSignedReadSource({"read-1": self.row})
        receipt = other.read(request_id="read-1", received_at_ms=NOW - 50)
        self.reject("MOCK_SOURCE_RECEIPT_NOT_BOUND", receipt=receipt)

    def test_stolen_token_does_not_bind_a_reconstructed_receipt(self):
        forged = MockReadReceipt(self.receipt.request_id, self.receipt.received_at_ms,
                                 self.receipt.observation, self.receipt.authority_token)
        self.reject("MOCK_SOURCE_RECEIPT_NOT_BOUND", receipt=forged)

    def test_request_id_cannot_be_relabelled(self):
        self.reject("MOCK_SOURCE_RECEIPT_NOT_BOUND",
                    receipt=replace(self.receipt, request_id="other-request"))

    def test_receipt_timestamp_cannot_be_relabelled(self):
        self.reject("MOCK_SOURCE_RECEIPT_NOT_BOUND",
                    receipt=replace(self.receipt, received_at_ms=NOW))

    def test_source_fixture_is_copied_and_receipt_is_frozen(self):
        self.row["executed_qty"] = "2"
        self.reject("MOCK_RECEIPT_OBSERVATION_CONFLICT")
        with self.assertRaises(FrozenInstanceError):
            self.receipt.received_at_ms = NOW
        self.assertIsInstance(self.receipt.observation, tuple)

    def test_source_has_no_arbitrary_read_callback(self):
        calls = []
        with self.assertRaisesRegex(MockProvenanceBlocked, "MOCK_FIXTURES_INVALID"):
            MockSignedReadSource(lambda: calls.append("called"))
        self.assertEqual(calls, [])

    def test_same_request_id_cannot_issue_another_token(self):
        with self.assertRaisesRegex(MockProvenanceBlocked, "MOCK_REQUEST_ID_REUSED"):
            self.source.read(request_id="read-1", received_at_ms=NOW)

    def test_absent_mock_request_has_no_fallback(self):
        with self.assertRaisesRegex(MockProvenanceBlocked, "MOCK_READ_NOT_FOUND"):
            self.source.read(request_id="absent", received_at_ms=NOW)

    def test_scope_mismatch_is_rejected(self):
        self.reject("SCOPE_MISMATCH", sample(scope="OTHER-SCOPE"))

    def test_symbol_mismatch_is_rejected(self):
        self.reject("SYMBOL_MISMATCH", sample(symbol="OTHERUSDT"))

    def test_order_id_mismatch_is_rejected(self):
        self.reject("ORDER_ID_MISMATCH", sample(order_id=322))

    def test_client_order_id_mismatch_is_rejected(self):
        self.reject("CLIENT_ORDER_ID_MISMATCH", sample(client_order_id="OTHER-TP"))

    def test_tp_role_mismatch_is_rejected(self):
        self.reject("ROLE_MISMATCH", sample(role="TP2"))

    def test_stop_and_entry_roles_cannot_be_tp_observations(self):
        for role in ("STOP", "ENTRY"):
            with self.subTest(role=role):
                self.reject("OBSERVATION_IDENTITY_INVALID", sample(role=role))

    def test_boolean_order_id_is_rejected(self):
        self.reject("OBSERVATION_IDENTITY_INVALID", sample(order_id=True))

    def test_observation_timestamp_must_match_source_snapshot(self):
        self.reject("MOCK_RECEIPT_OBSERVATION_CONFLICT", sample(observed_at_ms=NOW - 99))

    def test_fill_amount_must_match_source_snapshot(self):
        self.reject("MOCK_RECEIPT_OBSERVATION_CONFLICT", sample(executed_qty="2"))

    def test_status_must_match_source_snapshot(self):
        self.reject("MOCK_RECEIPT_OBSERVATION_CONFLICT", sample(status="FILLED", executed_qty="4"))

    def test_missing_field_and_signed_boolean_field_are_rejected(self):
        missing = sample()
        del missing["scope"]
        self.reject("OBSERVATION_SHAPE_INVALID", missing)
        self.reject("OBSERVATION_SHAPE_INVALID", sample(signed=True))

    def test_invalid_amounts_are_rejected(self):
        for amount in ("NaN", "Infinity", "-1", "bad", True, 1, " 1"):
            with self.subTest(amount=amount):
                self.reject("TP_AMOUNT_INVALID", sample(executed_qty=amount))

    def test_inconsistent_fill_states_are_rejected(self):
        for changes in (dict(status="NEW"), dict(executed_qty="4"),
                        dict(executed_qty="5"), dict(quantity="0"),
                        dict(status="FILLED"), dict(status="UNKNOWN")):
            with self.subTest(changes=changes):
                self.reject("TP_FILL_STATE_INVALID", sample(**changes))

    def test_boolean_and_negative_observation_timestamps_are_rejected(self):
        for timestamp in (True, -1, "99900", 99900.0):
            with self.subTest(timestamp=timestamp):
                self.reject("OBSERVATION_TIMESTAMP_INVALID", sample(observed_at_ms=timestamp))

    def test_stale_observation_is_rejected_even_with_source_token(self):
        row = sample(observed_at_ms=NOW - 5001)
        receipt, = self.load_sequence(row)
        self.reject("OBSERVATION_STALE_FUTURE_OR_READ_TIME_INVALID", row, receipt=receipt)

    def test_future_observation_is_rejected_even_with_source_token(self):
        row = sample(observed_at_ms=NOW + 1)
        receipt, = self.load_sequence(row)
        self.reject("OBSERVATION_STALE_FUTURE_OR_READ_TIME_INVALID", row, receipt=receipt)

    def test_stale_read_time_is_rejected(self):
        self.source = MockSignedReadSource({"stale-read": self.row})
        self.reviewer = self.make_reviewer(self.source)
        receipt = self.source.read(request_id="stale-read", received_at_ms=NOW - 5001)
        self.reject("OBSERVATION_STALE_FUTURE_OR_READ_TIME_INVALID", receipt=receipt)

    def test_future_read_time_is_rejected(self):
        self.source = MockSignedReadSource({"future-read": self.row})
        self.reviewer = self.make_reviewer(self.source)
        receipt = self.source.read(request_id="future-read", received_at_ms=NOW + 1)
        self.reject("OBSERVATION_STALE_FUTURE_OR_READ_TIME_INVALID", receipt=receipt)

    def test_read_cannot_precede_observation(self):
        self.source = MockSignedReadSource({"early-read": self.row})
        self.reviewer = self.make_reviewer(self.source)
        receipt = self.source.read(request_id="early-read", received_at_ms=NOW - 101)
        self.reject("OBSERVATION_STALE_FUTURE_OR_READ_TIME_INVALID", receipt=receipt)

    def test_freshness_boundary_is_inclusive(self):
        row = sample(observed_at_ms=NOW - 5000)
        receipt, = self.load_sequence(row)
        self.assertTrue(self.inspect(row, receipt=receipt)["mock_source_receipt_bound"])

    def test_invalid_review_clocks_and_age_limits_are_rejected(self):
        for changes in (dict(now_ms=True), dict(now_ms=-1), dict(now_ms="100000"),
                        dict(max_age_ms=True), dict(max_age_ms=0), dict(max_age_ms=10001)):
            with self.subTest(changes=changes):
                self.reject("MOCK_REVIEW_CLOCK_INVALID", **changes)

    def test_same_receipt_cannot_be_accepted_twice(self):
        self.assertTrue(self.inspect()["mock_source_receipt_bound"])
        self.reject("MOCK_RECEIPT_REPLAYED")

    def test_consumed_receipt_is_rejected_by_second_reviewer(self):
        self.assertTrue(self.inspect()["mock_source_receipt_bound"])
        self.reviewer = self.make_reviewer(self.source)
        self.reject("MOCK_RECEIPT_REPLAYED")

    def test_same_observation_with_distinct_request_ids_is_duplicate(self):
        first, second = self.load_sequence(self.row, self.row)
        self.assertTrue(self.inspect(receipt=first)["mock_source_receipt_bound"])
        self.reject("DUPLICATE_OBSERVATION", receipt=second)

    def test_older_observation_with_fresh_receipt_is_replay(self):
        older = sample(observed_at_ms=NOW - 200, executed_qty="0.5")
        first, second = self.load_sequence(self.row, older)
        self.assertTrue(self.inspect(receipt=first)["mock_source_receipt_bound"])
        self.reject("OBSERVATION_REPLAYED_OR_OUT_OF_ORDER", older, receipt=second)

    def test_same_time_conflicting_fill_is_rejected(self):
        conflict = sample(executed_qty="2")
        first, second = self.load_sequence(self.row, conflict)
        self.assertTrue(self.inspect(receipt=first)["mock_source_receipt_bound"])
        self.reject("CONFLICTING_OBSERVATION", conflict, receipt=second)

    def test_fill_regression_is_rejected(self):
        conflict = sample(observed_at_ms=NOW - 90, executed_qty="0.5")
        first, second = self.load_sequence(self.row, conflict)
        self.assertTrue(self.inspect(receipt=first)["mock_source_receipt_bound"])
        self.reject("CONFLICTING_OBSERVATION", conflict, receipt=second)

    def test_quantity_change_is_rejected(self):
        conflict = sample(observed_at_ms=NOW - 90, quantity="5", executed_qty="2")
        first, second = self.load_sequence(self.row, conflict)
        self.assertTrue(self.inspect(receipt=first)["mock_source_receipt_bound"])
        self.reject("CONFLICTING_OBSERVATION", conflict, receipt=second)

    def test_later_timestamp_without_fill_progress_is_duplicate(self):
        duplicate = sample(observed_at_ms=NOW - 90)
        first, second = self.load_sequence(self.row, duplicate)
        self.assertTrue(self.inspect(receipt=first)["mock_source_receipt_bound"])
        self.reject("DUPLICATE_OBSERVATION_NO_FILL_PROGRESS", duplicate, receipt=second)

    def test_filled_terminal_state_cannot_be_reopened(self):
        filled = sample(status="FILLED", executed_qty="4")
        later = sample(observed_at_ms=NOW - 90)
        first, second = self.load_sequence(filled, later)
        self.assertTrue(self.inspect(filled, receipt=first)["mock_source_receipt_bound"])
        self.reject("CONFLICTING_OBSERVATION", later, receipt=second)

    def test_new_partial_filled_progress_never_authorizes_actions(self):
        rows = [sample(status="NEW", executed_qty="0", observed_at_ms=NOW - 300),
                sample(observed_at_ms=NOW - 200),
                sample(status="FILLED", executed_qty="4", observed_at_ms=NOW - 100)]
        receipts = self.load_sequence(*rows)
        for row, receipt in zip(rows, receipts):
            with self.subTest(status=row["status"]):
                self.assertTrue(self.inspect(row, receipt=receipt)["mock_source_receipt_bound"])

    def test_rejection_neither_consumes_token_nor_changes_history(self):
        self.reject("MOCK_RECEIPT_OBSERVATION_CONFLICT", sample(executed_qty="2"))
        self.assertTrue(self.inspect()["mock_source_receipt_bound"])

    def test_normal_open_order_cannot_release_any_lock(self):
        result = self.inspect(normal_orders=[{"clientOrderId": "OFFLINE-OPEN"}])
        self.assertTrue(result["mock_source_receipt_bound"])
        self.assertIn("NORMAL_OPEN_ORDERS_PRESENT", result["safety_observations"])

    def test_conditional_open_stop_cannot_release_any_lock(self):
        result = self.inspect(conditional_orders=[{"clientAlgoId": "OFFLINE-STOP"}],
                              stop_state="OPEN")
        self.assertTrue(result["mock_source_receipt_bound"])
        self.assertIn("CONDITIONAL_OPEN_ORDERS_PRESENT", result["safety_observations"])

    def test_every_mock_stop_state_keeps_all_action_permissions_false(self):
        for state in ("OPEN", "MISSING", "FILLED", "CANCELED", "UNKNOWN", "AMBIGUOUS"):
            with self.subTest(state=state):
                source = MockSignedReadSource({"stop-state": self.row})
                self.reviewer = self.make_reviewer(source)
                receipt = source.read(request_id="stop-state", received_at_ms=NOW - 10)
                result = self.inspect(receipt=receipt, stop_state=state)
                self.assertTrue(result["mock_source_receipt_bound"])
                self.assertIn("STOP_STATE_" + state, result["safety_observations"])

    def test_missing_or_invalid_safety_context_is_rejected(self):
        for changes in (dict(normal_orders=None), dict(conditional_orders=None),
                        dict(normal_orders=[True]), dict(conditional_orders=["signed"]),
                        dict(stop_state=True), dict(stop_state="CLEARED")):
            with self.subTest(changes=changes):
                self.reject("SAFETY_CONTEXT_INVALID", **changes)

    def test_inspection_does_not_mutate_observation_or_safety_input(self):
        normal = [{"clientOrderId": "OFFLINE-OPEN"}]
        conditional = [{"clientAlgoId": "OFFLINE-STOP"}]
        before = copy.deepcopy((self.row, normal, conditional))
        self.inspect(normal_orders=normal, conditional_orders=conditional, stop_state="OPEN")
        self.assertEqual((self.row, normal, conditional), before)

    def test_tp2_has_its_own_valid_identity(self):
        row = sample(role="TP2", client_order_id="OFFLINE-TP2", order_id=322)
        source = MockSignedReadSource({"tp2": row})
        self.reviewer = self.make_reviewer(source, role="TP2", client_order_id="OFFLINE-TP2",
                                          order_id=322)
        receipt = source.read(request_id="tp2", received_at_ms=NOW - 50)
        self.assertTrue(self.inspect(row, receipt=receipt)["mock_source_receipt_bound"])


if __name__ == "__main__":
    unittest.main()
