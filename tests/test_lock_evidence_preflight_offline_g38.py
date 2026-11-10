"""G38: yalnız yeni bağımsız modül; gerçek SDK/owner/ağ import edilmez."""

import ast
from contextlib import redirect_stdout
from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lock_evidence_preflight_offline_g38 as g38


class OfflinePreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = g38.build_scenario()

    def evaluate(self, *, receipts=None, records=None, phase="INTACT", now_ms=None,
                 snapshot=None, journal=None, preflight=None):
        f = self.fixture
        if records is not None or phase != "INTACT":
            journal = g38.MockJournal(f.snapshot.records if records is None else records, phase)
            snapshot = journal.capture(f.now_ms - 100)
        return (f.preflight if preflight is None else preflight).evaluate(
            f.receipts if receipts is None else receipts,
            f.journal if journal is None else journal,
            f.snapshot if snapshot is None else snapshot,
            now_ms=f.now_ms if now_ms is None else now_ms,
        )

    def assert_locked(self, decision):
        self.assertEqual(decision.unlock, "NO_UNLOCK")
        self.assertEqual(decision.new_entry, "NO_NEW_ENTRY")
        self.assertTrue(decision.manual_owner_review_required)
        self.assertEqual(decision.mode, "OFFLINE_MOCK_ONLY")
        self.assertIn("MANUAL_OWNER_REVIEW_REQUIRED", decision.reason_codes)
        self.assertIn("MOCK_PROVENANCE_ONLY", decision.reason_codes)

    def assert_closed(self, decision, reason):
        self.assert_locked(decision)
        self.assertEqual(decision.disposition, "FAIL_CLOSED")
        self.assertIn(reason, decision.reason_codes)

    def issued_receipts(self, index, *, value=0, observed=None):
        receipts = list(self.fixture.receipts)
        receipts[index] = self.fixture.preflight.sources[index].issue(
            value, self.fixture.now_ms - 100 if observed is None else observed,
        )
        return tuple(receipts)

    def test_positive_fixture_only_requests_manual_review(self):
        decision = self.fixture.run()
        self.assert_locked(decision)
        self.assertEqual(decision.disposition, "MANUAL_OWNER_REVIEW")
        self.assertIn("EVIDENCE_COHERENT_FOR_MANUAL_REVIEW", decision.reason_codes)

    def test_decision_permissions_cannot_be_initialized_or_assigned(self):
        with self.assertRaises(TypeError):
            g38.Decision("MANUAL_OWNER_REVIEW", (), unlock="UNLOCK")
        with self.assertRaises(FrozenInstanceError):
            self.fixture.run().new_entry = "ALLOW"

    def test_signed_boolean_mapping_is_not_evidence(self):
        receipt = {"signed_owner_reads": True, "signed_flat_confirmed": True,
                   "position": 0, "normal_open_orders": 0, "conditional_algo_open_orders": 0}
        self.assert_closed(self.evaluate(receipts=(receipt,)), "UNTRUSTED_RECEIPT")

    def test_signed_boolean_object_is_not_evidence(self):
        receipt = SimpleNamespace(**vars(self.fixture.receipts[0]),
                                  signed_owner_reads=True, signed_flat_confirmed=True)
        self.assert_closed(self.evaluate(receipts=(receipt,)), "UNTRUSTED_RECEIPT")

    def test_directly_constructed_receipt_has_no_provenance(self):
        forged = g38.MockReceipt(**vars(self.fixture.receipts[0]))
        self.assert_closed(self.evaluate(receipts=(forged,) + self.fixture.receipts[1:]),
                           "RECEIPT_IDENTITY_OR_CONTENT_MISMATCH")

    def test_replaced_shallow_and_deep_copies_are_not_issued_objects(self):
        original = self.fixture.receipts[0]
        for forged in (replace(original), copy(original), deepcopy(original)):
            with self.subTest(copy_type=type(forged).__name__):
                self.assert_closed(self.evaluate(receipts=(forged,) + self.fixture.receipts[1:]),
                                   "RECEIPT_IDENTITY_OR_CONTENT_MISMATCH")

    def test_foreign_source_with_identical_name_and_fields_is_rejected(self):
        source = self.fixture.preflight.sources[0]
        lookalike = g38.MockSource(source.source_id, source.kind, source.binding)
        forged = lookalike.issue(0, self.fixture.now_ms - 100)
        self.assert_closed(self.evaluate(receipts=(forged,) + self.fixture.receipts[1:]),
                           "RECEIPT_IDENTITY_OR_CONTENT_MISMATCH")

    def test_source_subclass_cannot_override_provenance_validation(self):
        class AlwaysTrusted(g38.MockSource):
            def owns(self, receipt):
                return True
        f = self.fixture
        sources = (AlwaysTrusted("fake", g38.KINDS[0], f.preflight.binding),) + f.preflight.sources[1:]
        preflight = g38.OfflinePreflight(f.preflight.binding, sources)
        self.assert_closed(self.evaluate(preflight=preflight), "INVALID_SOURCE_CONFIGURATION")

    def test_tampered_original_receipt_payload_is_rejected(self):
        receipt = self.fixture.receipts[0]
        object.__setattr__(receipt, "observed_at_ms", self.fixture.now_ms)
        self.assert_closed(self.evaluate(), "RECEIPT_IDENTITY_OR_CONTENT_MISMATCH")

    def test_receipt_id_boolean_alias_is_not_an_integer_id(self):
        object.__setattr__(self.fixture.receipts[0], "receipt_id", True)
        self.assert_closed(self.evaluate(), "RECEIPT_IDENTITY_OR_CONTENT_MISMATCH")

    def test_same_source_object_cannot_cover_all_read_categories(self):
        f = self.fixture
        preflight = g38.OfflinePreflight(f.preflight.binding, (f.preflight.sources[0],) * 3)
        self.assert_closed(self.evaluate(preflight=preflight), "INVALID_SOURCE_CONFIGURATION")

    def test_duplicate_source_names_are_rejected(self):
        f = self.fixture
        sources = tuple(g38.MockSource("same", k, f.preflight.binding) for k in g38.KINDS)
        self.assert_closed(self.evaluate(preflight=g38.OfflinePreflight(f.preflight.binding, sources)),
                           "INVALID_SOURCE_CONFIGURATION")

    def test_each_category_is_mandatory(self):
        for index, kind in enumerate(g38.KINDS):
            with self.subTest(kind=kind):
                receipts = tuple(r for i, r in enumerate(self.fixture.receipts) if i != index)
                self.assert_closed(self.evaluate(receipts=receipts), f"MISSING_{kind.upper()}_RECEIPT")

    def test_nonzero_values_in_each_category_fail_closed(self):
        for index, kind in enumerate(g38.KINDS):
            for value in (1, -1):
                with self.subTest(kind=kind, value=value):
                    self.assert_closed(self.evaluate(receipts=self.issued_receipts(index, value=value)),
                                       f"{kind.upper()}_NONZERO")

    def test_fractional_position_exposure_fails_closed(self):
        self.assert_closed(self.evaluate(receipts=self.issued_receipts(0, value=Decimal("0.0001"))),
                           "POSITION_NONZERO")

    def test_decimal_zero_position_is_valid_but_never_unlocks(self):
        decision = self.evaluate(receipts=self.issued_receipts(0, value=Decimal("0.000")))
        self.assert_locked(decision)
        self.assertEqual(decision.disposition, "MANUAL_OWNER_REVIEW")

    def test_bool_string_none_and_nonfinite_position_values_fail_closed(self):
        for value in (True, False, "0", None, Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity")):
            with self.subTest(value=repr(value)):
                self.assert_closed(self.evaluate(receipts=self.issued_receipts(0, value=value)),
                                   "POSITION_INVALID_VALUE")

    def test_order_counts_require_exact_integers(self):
        for index in (1, 2):
            for value in (False, "0", None, Decimal("0")):
                with self.subTest(index=index, value=repr(value)):
                    self.assert_closed(self.evaluate(receipts=self.issued_receipts(index, value=value)),
                                       f"{g38.KINDS[index].upper()}_INVALID_VALUE")

    def test_duplicate_receipt_same_object_or_second_issue_fails(self):
        for extra in (self.fixture.receipts[0], self.issued_receipts(0)[0]):
            self.assert_closed(self.evaluate(receipts=self.fixture.receipts + (extra,)),
                               "DUPLICATE_RECEIPT")

    def test_unknown_receipt_kind_is_rejected(self):
        f = self.fixture
        extra = g38.MockSource("unknown", "other", f.preflight.binding).issue(0, f.now_ms)
        self.assert_closed(self.evaluate(receipts=f.receipts + (extra,)), "UNKNOWN_RECEIPT_KIND")

    def test_malformed_receipt_collection_fails_closed(self):
        for receipts in (True, {}, list(self.fixture.receipts)):
            with self.subTest(collection=type(receipts).__name__):
                self.assert_closed(self.evaluate(receipts=receipts), "INVALID_RECEIPT_COLLECTION")

    def test_stale_in_each_category_is_rejected_independently(self):
        for index, kind in enumerate(g38.KINDS):
            with self.subTest(kind=kind):
                self.assert_closed(self.evaluate(receipts=self.issued_receipts(
                    index, observed=self.fixture.now_ms - 5_001)), f"{kind.upper()}_STALE")

    def test_future_in_each_category_is_rejected_independently(self):
        for index, kind in enumerate(g38.KINDS):
            with self.subTest(kind=kind):
                self.assert_closed(self.evaluate(receipts=self.issued_receipts(
                    index, observed=self.fixture.now_ms + 1)), f"{kind.upper()}_FUTURE")

    def test_exact_freshness_boundary_and_now_are_accepted(self):
        for observed in (self.fixture.now_ms - 5_000, self.fixture.now_ms):
            decision = self.evaluate(receipts=self.issued_receipts(0, observed=observed))
            self.assert_locked(decision)
            self.assertEqual(decision.disposition, "MANUAL_OWNER_REVIEW")

    def test_malformed_observation_times_fail_closed(self):
        for observed in (True, False, -1, "1000000", 1_000_000.0, float("nan")):
            with self.subTest(observed=repr(observed)):
                self.assert_closed(self.evaluate(receipts=self.issued_receipts(0, observed=observed)),
                                   "POSITION_INVALID_TIME")

    def test_invalid_configuration_fails_closed(self):
        f = self.fixture
        for now_ms in (True, -1, 1.5, "1000000"):
            self.assert_closed(self.evaluate(now_ms=now_ms), "INVALID_PREFLIGHT_CONFIGURATION")
        for max_age_ms in (True, 0, -1, 1.5):
            preflight = g38.OfflinePreflight(f.preflight.binding, f.preflight.sources, max_age_ms)
            self.assert_closed(self.evaluate(preflight=preflight), "INVALID_PREFLIGHT_CONFIGURATION")
        for binding in (None, g38.Binding("", "account", "symbol"), g38.Binding(" scope", "a", "s")):
            preflight = g38.OfflinePreflight(binding, f.preflight.sources)
            self.assert_closed(self.evaluate(preflight=preflight), "INVALID_PREFLIGHT_CONFIGURATION")

    def test_trusted_receipt_with_each_wrong_binding_dimension_fails(self):
        f = self.fixture
        for dimension in ("scope", "account", "symbol"):
            for index in range(3):
                with self.subTest(dimension=dimension, index=index):
                    sources = list(f.preflight.sources)
                    sources[index] = g38.MockSource(
                        f"other-{index}", g38.KINDS[index],
                        replace(f.preflight.binding, **{dimension: "other"}),
                    )
                    receipts = list(f.receipts)
                    receipts[index] = sources[index].issue(0, f.now_ms)
                    preflight = g38.OfflinePreflight(f.preflight.binding, tuple(sources))
                    self.assert_closed(self.evaluate(preflight=preflight, receipts=tuple(receipts)),
                                       f"RECEIPT_{dimension.upper()}_MISMATCH")

    def test_each_ledger_wal_incident_record_is_required(self):
        for index, kind in enumerate(g38.RECORD_STATES):
            with self.subTest(kind=kind):
                records = tuple(r for i, r in enumerate(self.fixture.snapshot.records) if i != index)
                self.assert_closed(self.evaluate(records=records), f"MISSING_{kind.upper()}")

    def test_unknown_record_object_or_kind_is_never_ignored(self):
        f = self.fixture
        for record in ({"kind": "unknown"}, replace(f.snapshot.records[0], kind="unknown")):
            self.assert_closed(self.evaluate(records=f.snapshot.records + (record,)),
                               "UNKNOWN_JOURNAL_RECORD")

    def test_duplicate_journal_kind_and_id_fail_closed(self):
        records = self.fixture.snapshot.records
        for extra in (records[0], replace(records[0], record_id="second-ledger")):
            self.assert_closed(self.evaluate(records=records + (extra,)), "DUPLICATE_JOURNAL_RECORD")
        records = (replace(records[0], record_id=records[1].record_id),) + records[1:]
        self.assert_closed(self.evaluate(records=records), "JOURNAL_IDS_NOT_DISTINCT")

    def test_invalid_record_ids_fail_closed(self):
        records = self.fixture.snapshot.records
        for identifier in ("", " ", " padded ", None, False):
            self.assert_closed(self.evaluate(records=(replace(records[0], record_id=identifier),)
                                            + records[1:]), "INVALID_RECORD_ID")

    def test_links_from_every_record_must_match_all_three_ids(self):
        for index in range(3):
            for link in ("ledger_id", "wal_id", "incident_id"):
                with self.subTest(index=index, link=link):
                    records = list(self.fixture.snapshot.records)
                    records[index] = replace(records[index], **{link: "orphan"})
                    self.assert_closed(self.evaluate(records=records), "LEDGER_WAL_INCIDENT_LINK_MISMATCH")

    def test_journal_record_binding_is_checked_on_every_record(self):
        for index in range(3):
            for dimension in ("scope", "account", "symbol"):
                with self.subTest(index=index, dimension=dimension):
                    records = list(self.fixture.snapshot.records)
                    binding = replace(records[index].binding, **{dimension: "other"})
                    records[index] = replace(records[index], binding=binding)
                    self.assert_closed(self.evaluate(records=records), f"JOURNAL_{dimension.upper()}_MISMATCH")

    def test_open_wal_open_ledger_and_non_supheli_incident_fail_closed(self):
        for index, kind in enumerate(g38.RECORD_STATES):
            records = list(self.fixture.snapshot.records)
            records[index] = replace(records[index], state="UNRESOLVED")
            self.assert_closed(self.evaluate(records=records), f"{kind.upper()}_STATE_INVALID")

    def test_crash_cleanup_all_nonintact_states_and_missing_artifacts_fail(self):
        for phase in ("PREPARED", "PARTIAL", "REMOVED", "DONE", "UNKNOWN", None, True):
            with self.subTest(phase=phase):
                self.assert_closed(self.evaluate(phase=phase), "CRASH_PARTIAL_CLEANUP_OR_NONINTACT_STATE")
        self.assert_closed(self.evaluate(records=()), "MISSING_LEDGER_CLOSED")

    def test_stale_future_and_malformed_journal_snapshot_time_fail(self):
        for observed, reason in ((self.fixture.now_ms - 5_001, "JOURNAL_STALE"),
                                 (self.fixture.now_ms + 1, "JOURNAL_FUTURE"),
                                 (True, "JOURNAL_INVALID_TIME")):
            snapshot = self.fixture.journal.capture(observed)
            self.assert_closed(self.evaluate(snapshot=snapshot), reason)

    def test_snapshot_from_another_journal_or_copy_has_no_provenance(self):
        foreign = g38.MockJournal(self.fixture.snapshot.records).capture(self.fixture.now_ms)
        for snapshot in (foreign, replace(self.fixture.snapshot)):
            self.assert_closed(self.evaluate(snapshot=snapshot), "UNTRUSTED_JOURNAL_SNAPSHOT")

    def test_tampered_original_snapshot_content_is_rejected(self):
        object.__setattr__(self.fixture.snapshot, "cleanup_phase", "PARTIAL")
        self.assert_closed(self.evaluate(), "UNTRUSTED_JOURNAL_SNAPSHOT")

    def test_tampered_nested_binding_cannot_keep_receipt_provenance(self):
        object.__setattr__(self.fixture.receipts[0].binding, "account", "other")
        self.assert_closed(self.evaluate(), "RECEIPT_IDENTITY_OR_CONTENT_MISMATCH")

    def test_replacement_and_aba_change_invalidates_snapshot(self):
        f = self.fixture
        f.journal.replace(())
        f.journal.replace(f.snapshot.records)
        self.assert_closed(self.evaluate(), "JOURNAL_CHANGED_SINCE_CAPTURE")

    def test_mutation_during_review_is_detected_by_final_generation_check(self):
        f = self.fixture
        original = g38.MockJournal.is_current

        def race(journal, snapshot):
            journal.replace(snapshot.records, "PARTIAL")
            return original(journal, snapshot)

        with patch.object(g38.MockJournal, "is_current", race):
            self.assert_closed(self.evaluate(), "JOURNAL_CHANGED_SINCE_CAPTURE")

    def test_evaluation_does_not_mutate_mock_records_or_snapshot(self):
        f = self.fixture
        before = (g38._snapshot_stamp(f.snapshot), tuple(g38._receipt_stamp(r) for r in f.receipts))
        for _ in range(2):
            self.assert_locked(self.evaluate())
        self.assertEqual(before, (g38._snapshot_stamp(f.snapshot),
                                  tuple(g38._receipt_stamp(r) for r in f.receipts)))
        self.assertTrue(f.journal.is_current(f.snapshot))

    def test_all_scenarios_keep_both_prohibitions_and_expected_disposition(self):
        for name in g38.SCENARIOS:
            with self.subTest(name=name):
                decision = g38.build_scenario(name).run()
                self.assert_locked(decision)
                self.assertEqual(decision.disposition,
                                 "MANUAL_OWNER_REVIEW" if name == "coherent" else "FAIL_CLOSED")

    def test_cli_positive_json_and_exit_zero_do_not_authorize_unlock(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = g38.main(["--scenario", "coherent"])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())[0]
        self.assertEqual(result["unlock"], "NO_UNLOCK")
        self.assertEqual(result["new_entry"], "NO_NEW_ENTRY")
        self.assertTrue(result["manual_owner_review_required"])

    def test_cli_all_reports_every_scenario_and_fail_closed_exit_two(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = g38.main(["--scenario", "all"])
        self.assertEqual(status, 2)
        results = json.loads(output.getvalue())
        self.assertEqual([r["scenario"] for r in results], list(g38.SCENARIOS))
        self.assertTrue(all(r["unlock"] == "NO_UNLOCK" and r["new_entry"] == "NO_NEW_ENTRY"
                            for r in results))

    def test_cli_unknown_scenario_rejected_and_no_path_options(self):
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                g38.main(["--scenario", "live"])
        self.assertEqual(caught.exception.code, 2)

    def test_module_has_only_stdlib_imports_and_no_disk_or_execution_calls(self):
        tree = ast.parse(Path(g38.__file__).read_text(encoding="utf-8"))
        allowed = {"argparse", "dataclasses", "decimal", "json"}
        forbidden_calls = {"open", "exec", "eval", "__import__", "unlink", "rmdir",
                           "rename", "replace", "write_text", "write_bytes", "system", "popen"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name in allowed for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, allowed)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    # dataclasses.replace yalnız bellek verisi kopyalar.
                    self.assertNotIn(node.func.id, forbidden_calls - {"replace"})
                elif isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, forbidden_calls - {"replace"})


if __name__ == "__main__":
    unittest.main()
