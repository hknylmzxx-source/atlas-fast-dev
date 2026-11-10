"""G39: bağımsız senaryolar; yalnız mock, gerçek G37/G38 ve geçici günlük."""

import ast
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import offline_safety_chain_g39 as g39


class OfflineSafetyChainTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="atlas-g39-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "journal"
        self.owner = g39.g38.build_scenario()
        self.binding = self.owner.preflight.binding
        self.stop = g39.MockStopSnapshot(self.binding)
        self.initial_stop = self.stop
        self.now = self.owner.now_ms
        self.chain = self.new_chain()
        self.tp1 = g39.g37.Binding("TP1", self.binding.scope, self.binding.symbol, "g39-tp1", "2")
        self.tp2 = g39.g37.Binding("TP2", self.binding.scope, self.binding.symbol, "g39-tp2", "3")

    def tearDown(self):
        # Her senaryoda izinler kapalı ve STOP aynı frozen fixture olarak kalır.
        self.assert_safety(self.chain.report())
        self.assertIs(self.stop, self.initial_stop)
        self.assertTrue(self.stop.present)

    def new_chain(self, *, stop=None, preflight=None, directory=None):
        chain = g39.OfflineSafetyChain(
            self.directory if directory is None else directory,
            binding=self.binding,
            preflight=self.owner.preflight if preflight is None else preflight,
            stop=self.stop if stop is None else stop, clock_ms=lambda: self.now)
        chain.supply_owner_evidence(self.owner.receipts, self.owner.journal, self.owner.snapshot)
        return chain

    def assert_safety(self, report):
        self.assertIs(report.entry_authorized, False)
        self.assertIs(report.unlock, False)
        self.assertFalse(report.real_signature_verified)
        self.assertFalse(report.live_binance_validated)
        self.assertTrue(report.manual_owner_review_required)
        self.assertEqual(report.mode, "OFFLINE_MOCK_ONLY")
        self.assertEqual(report.post_policy, "NO_RETRY")
        self.assertEqual(report.entry_policy, "NO_NEW_ENTRY")
        self.assertEqual(report.stop_policy, "IMMUTABLE_MOCK_ONLY")

    def assert_closed(self, reason, *, chain=None):
        report = (self.chain if chain is None else chain).report()
        self.assert_safety(report)
        self.assertEqual(report.disposition, "FAIL_CLOSED")
        self.assertIn(reason, report.reason_codes)
        self.assertTrue(report.alarms)
        return report

    def start(self):
        for tp in (self.tp1, self.tp2):
            self.chain.record_synthetic_intent(tp)

    def read(self, tp, status="NEW", qty="0", *, sequence=None, observed=None,
             source=None, binding=None, chain=None):
        chain = self.chain if chain is None else chain
        self.now += 10
        view = chain.tp_view(tp.role)
        receipt = (chain.source if source is None else source).issue_mock_read(
            tp if binding is None else binding, status, qty,
            self.now if observed is None else observed,
            view.read_sequence + 1 if sequence is None else sequence)
        event = chain.observe(tp.role, receipt)
        return receipt, event

    def filled(self):
        self.start()
        self.read(self.tp1)
        self.read(self.tp2)
        self.read(self.tp1, "FILLED", "2")
        self.read(self.tp2, "FILLED", "3")

    def use_owner(self, name):
        self.owner = g39.g38.build_scenario(name)
        self.chain = self.new_chain(directory=self.directory.parent / name)
        self.filled()

    def test_normal_chain_runs_actual_modules_and_only_allows_manual_review(self):
        self.filled()
        report = self.chain.report()
        self.assert_safety(report)
        self.assertEqual(report.disposition, "MANUAL_OWNER_REVIEW")
        self.assertEqual([tp.state for tp in report.tp], ["FILLED", "FILLED"])
        self.assertEqual(report.synthetic_post_intents, 2)
        self.assertEqual(report.preflight_disposition, "MANUAL_OWNER_REVIEW")
        self.assertEqual(report.alarms, ())
        self.assertEqual(report.stop_state, "PRESENT_MOCK")
        self.assertTrue(report.stop_unchanged)
        root = Path(g39.__file__).resolve().parent
        for component in (g39.g37, g39.g38):
            self.assertEqual(Path(component.__file__).resolve().parent, root)

    def test_tp1_expired_raises_owner_alarm_without_affecting_tp2_or_stop(self):
        self.start()
        self.read(self.tp1)
        _, event = self.read(self.tp1, "EXPIRED")
        self.read(self.tp2, "FILLED", "3")
        report = self.assert_closed("TP1_EXPIRED")
        self.assertEqual(event.alarm, "OWNER_ALARM")
        self.assertEqual(self.chain.tp_view("TP2").state, "FILLED")
        self.assertTrue(any(a.event_id == event.event_id for a in report.alarms))
        self.assertTrue(any(a.event_id == event.event_id and a.kind == "OWNER_ALARM"
                            for a in report.alarms))
        self.assertTrue(report.stop_unchanged)

    def test_tp2_partial_requires_reconciliation_and_preserves_filled_qty(self):
        self.start()
        self.read(self.tp1, "FILLED", "2")
        self.read(self.tp2)
        _, event = self.read(self.tp2, "PARTIAL", "1")
        self.assertEqual(event.alarm, "RECONCILE")
        self.assertEqual(self.chain.tp_view("TP2").filled_qty, "1")
        report = self.assert_closed("TP2_PARTIAL")
        self.assertTrue(any(a.event_id == event.event_id and a.kind == "RECONCILE"
                            for a in report.alarms))

    def test_ambiguous_post_is_unknown_and_never_retried(self):
        self.start()
        event = self.chain.record_unknown("TP1")
        self.assertEqual(event.state, "UNKNOWN_NO_RETRY")
        before = sum(e.kind == "INTENT" for e in self.chain.events)
        self.chain.record_synthetic_intent(self.tp1)
        self.assertEqual(sum(e.kind == "INTENT" for e in self.chain.events), before)
        self.assertTrue(self.chain.tp_view("TP1").no_retry)
        self.assert_closed("TP1_OWNER_REVIEW_NO_RETRY")

    def test_unknown_can_reconcile_telemetry_but_alarm_history_cannot_authorize(self):
        self.start()
        unknown = self.chain.record_unknown("TP1")
        self.read(self.tp1, "FILLED", "2")
        self.read(self.tp2, "FILLED", "3")
        report = self.assert_closed("TP_ALARM_HISTORY_REQUIRES_OWNER_REVIEW")
        self.assertIn(unknown.event_id, [a.event_id for a in report.alarms])
        self.assertEqual([tp.state for tp in report.tp], ["FILLED", "FILLED"])

    def test_stop_absent_stays_absent_without_repair_even_with_flat_owner(self):
        absent = g39.MockStopSnapshot(self.binding, False)
        self.chain = self.new_chain(stop=absent, directory=self.directory.parent / "absent")
        self.filled()
        report = self.assert_closed("STOP_ABSENT_NO_REPAIR")
        self.assertEqual(report.stop_state, "ABSENT_MOCK")
        self.assertTrue(report.stop_unchanged)
        self.assertIs(self.chain.stop, absent)
        self.assertIs(absent.present, False)

    def test_stop_binding_mismatch_fails_closed_without_mutation(self):
        stop = g39.MockStopSnapshot(replace(self.binding, account="other-mock-account"))
        chain = self.new_chain(stop=stop, directory=self.directory.parent / "wrong-stop")
        self.assert_closed("STOP_CHAIN_BINDING_MISMATCH", chain=chain)
        self.assertIs(chain.stop, stop)

    def test_stop_snapshot_and_binding_cannot_be_assigned(self):
        with self.assertRaises(FrozenInstanceError):
            self.stop.present = False
        with self.assertRaises(FrozenInstanceError):
            self.stop.binding.account = "other-mock-account"
        with self.assertRaises(AttributeError):
            self.chain.stop = g39.MockStopSnapshot(self.binding, False)

    def test_stop_boolean_spoof_is_rejected(self):
        malformed = g39.MockStopSnapshot(self.binding, 1)
        chain = self.new_chain(stop=malformed, directory=self.directory.parent / "bad-stop")
        report = self.assert_closed("STOP_SNAPSHOT_INVALID_OR_CHANGED", chain=chain)
        self.assertEqual(report.stop_state, "INVALID_MOCK")

    def test_owner_flat_alone_never_unlocks_or_fills_missing_tp(self):
        report = self.assert_closed("TP1_ABSENT")
        self.assertEqual(report.preflight_disposition, "MANUAL_OWNER_REVIEW")
        self.assertEqual(report.synthetic_post_intents, 0)
        self.assertIn("TP2_ABSENT", report.reason_codes)

    def test_owner_open_gross_position_fails_closed(self):
        self.use_owner("position-open")
        self.assert_closed("POSITION_NONZERO")

    def test_preflight_stale_receipts_fail_closed_despite_filled_tps(self):
        self.use_owner("stale")
        report = self.assert_closed("POSITION_STALE")
        self.assertEqual([tp.state for tp in report.tp], ["FILLED", "FILLED"])

    def test_preflight_is_reevaluated_when_clock_advances(self):
        self.filled()
        self.assertEqual(self.chain.report().preflight_disposition, "MANUAL_OWNER_REVIEW")
        self.now += 5_001
        self.assert_closed("POSITION_STALE")

    def test_previous_owner_alarm_is_not_cleared_by_fresh_mock_evidence(self):
        self.filled()
        self.now += 5_001
        self.assert_closed("POSITION_STALE")
        receipts = tuple(s.issue(0, self.now) for s in self.owner.preflight.sources)
        snapshot = self.owner.journal.capture(self.now)
        self.chain.supply_owner_evidence(receipts, self.owner.journal, snapshot)
        report = self.assert_closed("POSITION_STALE")
        self.assertEqual(report.preflight_disposition, "MANUAL_OWNER_REVIEW")

    def test_invalid_chain_clock_configuration_returns_fail_closed_report(self):
        chain = g39.OfflineSafetyChain(self.directory.parent / "invalid-clock", binding=self.binding,
                                      preflight=self.owner.preflight, stop=self.stop, clock_ms=None)
        chain.supply_owner_evidence(self.owner.receipts, self.owner.journal, self.owner.snapshot)
        self.assert_closed("INVALID_CHAIN_CONFIGURATION", chain=chain)

    def test_preflight_evaluation_error_is_redacted_and_fail_closed(self):
        self.filled()
        with patch.object(g39.g38.OfflinePreflight, "evaluate", side_effect=OSError("SENTETIK_HATA")):
            report = self.assert_closed("OWNER_PREFLIGHT_EVALUATION_UNKNOWN")
        self.assertNotIn("SENTETIK_HATA", json.dumps(report.to_dict()))

    def test_real_g38_evaluate_is_called_for_each_report(self):
        self.filled()
        original = g39.g38.OfflinePreflight.evaluate
        with patch.object(g39.g38.OfflinePreflight, "evaluate", autospec=True,
                          side_effect=original) as evaluate:
            self.chain.report()
            self.now += 1
            self.chain.report()
        self.assertEqual(evaluate.call_count, 2)
        self.assertIs(evaluate.call_args.args[0], self.owner.preflight)
        self.assertEqual(evaluate.call_args.kwargs["now_ms"], self.now)

    def test_future_owner_receipts_fail_closed(self):
        self.use_owner("future")
        # use_owner yürüyen TP olaylarında saati ilerletir; geleceğe alınmış yeni makbuz.
        receipts = tuple(s.issue(0, self.now + 1) for s in self.owner.preflight.sources)
        self.chain.supply_owner_evidence(receipts, self.owner.journal, self.owner.snapshot)
        self.assert_closed("POSITION_FUTURE")

    def test_copied_receipt_is_not_owner_evidence(self):
        self.filled()
        forged = (replace(self.owner.receipts[0]),) + self.owner.receipts[1:]
        self.chain.supply_owner_evidence(forged, self.owner.journal, self.owner.snapshot)
        self.assert_closed("RECEIPT_IDENTITY_OR_CONTENT_MISMATCH")

    def test_signed_boolean_claim_is_not_owner_evidence(self):
        self.filled()
        forged = ({"signed_owner_reads": True, "signed_flat_confirmed": True},)
        self.chain.supply_owner_evidence(forged, self.owner.journal, self.owner.snapshot)
        self.assert_closed("UNTRUSTED_RECEIPT")

    def test_owner_account_mismatch_fails_closed(self):
        self.use_owner("account-mismatch")
        self.assert_closed("RECEIPT_ACCOUNT_MISMATCH")

    def test_owner_scope_mismatch_fails_closed(self):
        self.use_owner("scope-mismatch")
        self.assert_closed("RECEIPT_SCOPE_MISMATCH")

    def test_owner_symbol_mismatch_fails_closed(self):
        self.use_owner("symbol-mismatch")
        self.assert_closed("RECEIPT_SYMBOL_MISMATCH")

    def test_chain_and_preflight_bindings_must_match(self):
        preflight = g39.g38.OfflinePreflight(replace(self.binding, account="other"),
                                            self.owner.preflight.sources)
        chain = self.new_chain(preflight=preflight, directory=self.directory.parent / "wrong-owner")
        self.assert_closed("PREFLIGHT_CHAIN_BINDING_MISMATCH", chain=chain)

    def test_conditional_open_order_blocks_flat_owner(self):
        self.use_owner("algo-order-open")
        self.assert_closed("CONDITIONAL_ALGO_OPEN_ORDERS_NONZERO")

    def test_normal_open_order_blocks_flat_owner(self):
        self.use_owner("normal-order-open")
        self.assert_closed("NORMAL_OPEN_ORDERS_NONZERO")

    def test_missing_conditional_receipt_fails_closed(self):
        self.use_owner("missing-algo")
        self.assert_closed("MISSING_CONDITIONAL_ALGO_OPEN_ORDERS_RECEIPT")

    def test_missing_owner_evidence_does_not_accept_a_ready_decision(self):
        chain = g39.OfflineSafetyChain(self.directory.parent / "no-owner", binding=self.binding,
                                      preflight=self.owner.preflight, stop=self.stop,
                                      clock_ms=lambda: self.now)
        self.assert_closed("OWNER_EVIDENCE_ABSENT", chain=chain)
        chain.supply_owner_evidence(self.owner.run(), self.owner.journal, self.owner.snapshot)
        self.assert_closed("INVALID_RECEIPT_COLLECTION", chain=chain)

    def test_duplicate_owner_receipt_is_rejected(self):
        self.use_owner("duplicate")
        self.assert_closed("DUPLICATE_RECEIPT")

    def test_owner_partial_journal_fails_closed_without_cleanup(self):
        self.use_owner("crash-partial")
        before = self.owner.snapshot
        self.assert_closed("CRASH_PARTIAL_CLEANUP_OR_NONINTACT_STATE")
        self.assertIs(self.owner.snapshot, before)
        self.assertEqual(before.cleanup_phase, "PARTIAL")

    def test_owner_journal_generation_change_is_detected(self):
        self.filled()
        self.owner.journal.replace(self.owner.snapshot.records)
        self.assert_closed("JOURNAL_CHANGED_SINCE_CAPTURE")

    def test_owner_journal_broken_links_are_detected(self):
        self.use_owner("broken-links")
        self.assert_closed("LEDGER_WAL_INCIDENT_LINK_MISMATCH")

    def test_duplicate_tp_receipt_quarantines_only_its_tp(self):
        self.start()
        receipt, _ = self.read(self.tp1)
        event = self.chain.observe("TP1", receipt)
        self.assertEqual(event.reason, "DUPLICATE_RECEIPT")
        self.read(self.tp2, "FILLED", "3")
        self.assertTrue(self.chain.tp_view("TP1").quarantined)
        self.assertFalse(self.chain.tp_view("TP2").quarantined)
        self.assertEqual(self.chain.tp_view("TP2").state, "FILLED")
        self.assert_closed("TP1_OWNER_REVIEW_NO_RETRY")

    def test_reordered_tp_sequence_does_not_regress_filled_quantity(self):
        self.start()
        self.read(self.tp1, "PARTIAL", "1")
        _, event = self.read(self.tp1, "NEW", sequence=1)
        self.assertEqual(event.reason, "REORDERED_OBSERVATION")
        self.assertEqual(self.chain.tp_view("TP1").filled_qty, "1")
        self.assert_closed("TP1_OWNER_REVIEW_NO_RETRY")

    def test_reordered_tp_time_is_rejected(self):
        self.start()
        self.read(self.tp1)
        observed = self.chain.tp_view("TP1").observed_at_ms
        _, event = self.read(self.tp1, "PARTIAL", "1", observed=observed)
        self.assertEqual(event.reason, "REORDERED_OBSERVATION")
        self.assert_closed("TP1_OWNER_REVIEW_NO_RETRY")

    def test_foreign_tp_source_is_not_trusted(self):
        self.start()
        _, event = self.read(self.tp1, source=g39.g37.MockTrustedSource())
        self.assertEqual(event.reason, "UNTRUSTED_MOCK_RECEIPT")
        self.assert_closed("TP1_OWNER_REVIEW_NO_RETRY")

    def test_tp_chain_binding_mismatch_cannot_create_intent(self):
        self.assertIsNone(self.chain.record_synthetic_intent(replace(self.tp1, scope="different")))
        report = self.assert_closed("TP_CHAIN_BINDING_MISMATCH")
        self.assertEqual(report.synthetic_post_intents, 0)

    def test_conflicting_terminal_tp_observation_does_not_revive_order(self):
        self.filled()
        _, event = self.read(self.tp1)
        self.assertEqual(event.reason, "CONFLICTING_OBSERVATION")
        self.assertEqual(self.chain.tp_view("TP1").filled_qty, "2")
        self.assert_closed("TP1_OWNER_REVIEW_NO_RETRY")

    def test_restart_replays_unknown_and_alarm_without_new_intent(self):
        self.start()
        event = self.chain.record_unknown("TP1")
        old_bytes = {p.name: p.read_bytes() for p in self.directory.glob("*.json")}
        restarted = self.new_chain()
        self.assertIsNot(restarted.source, self.chain.source)
        self.assertEqual(restarted.tp_view("TP1").state, "UNKNOWN_NO_RETRY")
        before = len(restarted.events)
        self.assertIsNone(restarted.record_synthetic_intent(self.tp1))
        self.assertEqual(len(restarted.events), before)
        report = self.assert_closed("RESTART_REPLAY_REQUIRES_OWNER_REVIEW", chain=restarted)
        self.assertEqual(report.synthetic_post_intents, 2)
        self.assertIn(event.event_id, [a.event_id for a in report.alarms])
        for name, raw in old_bytes.items():
            self.assertEqual((self.directory / name).read_bytes(), raw)

    def test_restart_preserves_duplicate_receipt_detection(self):
        self.start()
        receipt, _ = self.read(self.tp1)
        # Aynı süreçte aynı mock kaynakla replay; disk kendi başına receipt kanıtı değildir.
        restarted = g39.OfflineSafetyChain(
            self.directory, binding=self.binding, preflight=self.owner.preflight,
            stop=self.stop, clock_ms=lambda: self.now, tp_source=self.chain.source)
        restarted.supply_owner_evidence(self.owner.receipts, self.owner.journal, self.owner.snapshot)
        event = restarted.observe("TP1", receipt)
        self.assertEqual(event.reason, "DUPLICATE_RECEIPT")
        self.assert_closed("TP1_OWNER_REVIEW_NO_RETRY", chain=restarted)

    def test_separate_process_crash_and_replay_keep_unknown_and_intent_count(self):
        # G39 için farklı geçici günlük; ilk süreç G37 fsync sonrası aniden çıkar.
        path = self.directory.parent / "process-replay"
        common = """
import json
import os
from pathlib import Path
import sys
import offline_safety_chain_g39 as g39
owner = g39.g38.build_scenario()
binding = owner.preflight.binding
chain = g39.OfflineSafetyChain(Path(sys.argv[1]), binding=binding,
    preflight=owner.preflight, stop=g39.MockStopSnapshot(binding),
    clock_ms=lambda: owner.now_ms)
chain.supply_owner_evidence(owner.receipts, owner.journal, owner.snapshot)
tp1 = g39.g37.Binding('TP1', binding.scope, binding.symbol, 'process-tp1', '2')
tp2 = g39.g37.Binding('TP2', binding.scope, binding.symbol, 'process-tp2', '3')
"""
        crash = common + """
chain.record_synthetic_intent(tp1)
chain.record_synthetic_intent(tp2)
chain.record_unknown('TP1')
print(json.dumps(chain.report().to_dict()), flush=True)
os._exit(17)
"""
        replay = common + """
chain.record_synthetic_intent(tp1)
print(json.dumps(chain.report().to_dict()))
"""
        results = []
        for code, expected in ((crash, 17), (replay, 0)):
            process = subprocess.run([sys.executable, "-B", "-c", code, str(path)],
                                     capture_output=True, text=True, timeout=20,
                                     cwd=Path(g39.__file__).resolve().parent)
            self.assertEqual(process.returncode, expected, process.stderr)
            self.assertEqual(process.stderr, "")
            results.append(json.loads(process.stdout))
        for report in results:
            self.assertIs(report["entry_authorized"], False)
            self.assertIs(report["unlock"], False)
            self.assertTrue(report["stop_unchanged"])
            self.assertEqual(report["synthetic_post_intents"], 2)
            self.assertEqual(report["tp"][0]["state"], "UNKNOWN_NO_RETRY")
        alarm_ids = [{a["event_id"] for a in r["alarms"] if a["origin"] == "G37"}
                     for r in results]
        self.assertEqual(alarm_ids[0], alarm_ids[1])
        self.assertTrue(results[1]["restarted"])
        self.assertEqual(results[1]["telemetry_health"], "REPLAY_ONLY_NO_DURABILITY_PROOF")

    def test_stale_writer_fails_closed_after_another_chain_restarts(self):
        restarted = self.new_chain()
        self.chain.record_synthetic_intent(self.tp1)
        self.assert_closed("TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY")
        self.assert_closed("RESTART_REPLAY_REQUIRES_OWNER_REVIEW", chain=restarted)

    def test_file_fsync_unknown_is_sticky_and_restart_does_not_repair_pending(self):
        self.start()
        with g39._fsync_fault(1):
            self.read(self.tp1, "PARTIAL", "1")
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        report = self.assert_closed("TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY")
        self.assertEqual(report.telemetry_health, "UNKNOWN_NO_RETRY")
        self.assertEqual(self.chain.tp_view("TP1").filled_qty, "0")
        self.assertTrue(any(a.storage == "MEMORY_ONLY" and a.code ==
                            "TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY" for a in report.alarms))
        self.assertIsNone(self.chain.record_synthetic_intent(self.tp1))
        self.assertIsNone(self.chain.record_unknown("TP2"))
        restarted = self.new_chain()
        replay_report = self.assert_closed("TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY", chain=restarted)
        self.assertIsNone(replay_report.synthetic_post_intents)
        self.assertIn("TP1_UNAVAILABLE", replay_report.reason_codes)
        self.assertEqual({p.name: p.read_bytes() for p in self.directory.iterdir()}, before)

    def test_published_directory_fsync_unknown_retains_no_retry_after_restart(self):
        self.start()
        with g39._fsync_fault(3):
            self.read(self.tp1, "PARTIAL", "1")
        self.assert_closed("TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY")
        restarted = self.new_chain()
        # Olay okunabilir; kaybolan fsync yanıtının başarısı varsayılmaz.
        self.assertEqual(restarted.tp_view("TP1").filled_qty, "1")
        self.assertTrue(restarted.tp_view("TP1").no_retry)
        self.assert_closed("RESTART_REPLAY_REQUIRES_OWNER_REVIEW", chain=restarted)
        self.assertIsNone(restarted.record_synthetic_intent(self.tp1))
        self.assertEqual(restarted.report().synthetic_post_intents, 2)

    def test_corrupt_journal_restart_alarms_without_automatic_repair(self):
        self.start()
        path = sorted(self.directory.glob("*.json"))[-1]
        path.write_bytes(b'{"synthetic_truncated":')
        before = path.read_bytes()
        restarted = self.new_chain()
        self.assert_closed("TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY", chain=restarted)
        self.assertEqual(path.read_bytes(), before)

    def test_journal_error_does_not_claim_alarm_durability_or_leak_exception(self):
        self.start()
        with patch.object(g39.g37.os, "fsync", side_effect=OSError("SENTETIK_GIZLI_HATA")):
            self.read(self.tp1, "EXPIRED")
        report = self.assert_closed("TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY")
        self.assertNotIn("SENTETIK_GIZLI_HATA", json.dumps(report.to_dict()))
        self.assertFalse(any(a.origin == "G37" for a in report.alarms))

    def test_journaled_alarm_history_survives_restart_with_identical_ids(self):
        self.start()
        self.read(self.tp1, "EXPIRED")
        self.read(self.tp2, "PARTIAL", "1")
        before = {a.event_id for a in self.chain.report().alarms if a.origin == "G37"}
        restarted = self.new_chain()
        report = self.assert_closed("TP_ALARM_HISTORY_REQUIRES_OWNER_REVIEW", chain=restarted)
        self.assertEqual({a.event_id for a in report.alarms if a.origin == "G37"}, before)
        self.assertTrue(all(a.storage == "JOURNALED_LOCAL_OUTBOX"
                            for a in report.alarms if a.origin == "G37"))

    def test_report_permissions_and_nested_alarms_are_immutable(self):
        report = self.chain.report()
        with self.assertRaises(TypeError):
            replace(report, entry_authorized=True)
        with self.assertRaises(TypeError):
            replace(report, unlock=True)
        with self.assertRaises(FrozenInstanceError):
            report.unlock = True
        with self.assertRaises(FrozenInstanceError):
            report.alarms[0].code = "ALLOW"
        copy = report.to_dict()
        copy["unlock"] = True
        self.assertFalse(self.chain.report().unlock)


class OfflineSafetyChainCliTests(unittest.TestCase):
    def test_every_cli_scenario_keeps_both_permissions_false_and_stop_unchanged(self):
        for name in g39.SCENARIOS:
            with self.subTest(scenario=name):
                with patch("socket.create_connection", side_effect=AssertionError("AG_YASAK")):
                    result = g39.run_scenario(name)
                for report in (result["before_restart"], result["result"]):
                    if report is not None:
                        self.assertIs(report["entry_authorized"], False)
                        self.assertIs(report["unlock"], False)
                        self.assertFalse(report["live_binance_validated"])
                        self.assertTrue(report["stop_unchanged"])
                self.assertEqual(result["result"]["disposition"],
                                 "MANUAL_OWNER_REVIEW" if name in ("normal", "owner-flat")
                                 else "FAIL_CLOSED")

    def test_standalone_cli_normal_and_failure_exit_codes_and_json(self):
        for name, code in (("normal", 0), ("stop-absent", 2), ("crash-restart", 2),
                           ("journal-fsync-unknown", 2), ("all", 2)):
            with self.subTest(scenario=name):
                process = subprocess.run([sys.executable, "-B", g39.__file__, "--scenario", name],
                                         capture_output=True, text=True, timeout=20,
                                         cwd=Path(g39.__file__).resolve().parent)
                self.assertEqual(process.returncode, code, process.stderr)
                self.assertEqual(process.stderr, "")
                reports = json.loads(process.stdout)
                self.assertEqual(len(reports), len(g39.SCENARIOS) if name == "all" else 1)
                for report in reports:
                    self.assertIs(report["result"]["entry_authorized"], False)
                    self.assertIs(report["result"]["unlock"], False)

    def test_main_all_and_unknown_scenario_are_explicit(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = g39.main(["--scenario", "all"])
        self.assertEqual(code, 2)
        self.assertEqual(len(json.loads(output.getvalue())), len(g39.SCENARIOS))
        with self.assertRaises(ValueError):
            g39.run_scenario("gercek-islem")

    def test_imports_are_stdlib_and_only_two_real_local_offline_modules(self):
        tree = ast.parse(Path(g39.__file__).read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module.split(".")[0])
        self.assertEqual(imports - set(sys.stdlib_module_names),
                         {"tp_lifecycle_offline_g37", "lock_evidence_preflight_offline_g38"})


if __name__ == "__main__":
    unittest.main()
