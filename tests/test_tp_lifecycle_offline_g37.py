"""G37 regresyonları: yalnız mock olaylar ve TemporaryDirectory, SDK/ağ yok."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tp_lifecycle_offline_g37 as g37


class TPLifecycleG37Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="atlas-g37-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = 10_000
        self.serial = 0
        self.fresh()

    def fresh(self):
        self.serial += 1
        self.path = self.root / f"journal-{self.serial}"
        self.source = g37.MockTrustedSource()
        self.journal = g37.EventJournal(self.path)
        self.tracker = g37.TPLifecycleOffline(
            self.journal, self.source, clock_ms=lambda: self.now)
        self.tp1 = g37.Binding("TP1", "g37-scope", "MOCKUSDT", "mock-tp1", "2")
        self.tp2 = g37.Binding("TP2", "g37-scope", "MOCKUSDT", "mock-tp2", "3")

    def start(self, both=False):
        self.tracker.record_synthetic_post_intent(self.tp1)
        if both:
            self.tracker.record_synthetic_post_intent(self.tp2)

    def receipt(self, role="TP1", status="NEW", filled="0", *, binding=None,
                timestamp=None, sequence=None, source=None):
        self.now += 10
        view = self.tracker.view(role)
        return (source or self.source).issue_mock_read(
            binding or view.binding, status, filled,
            self.now if timestamp is None else timestamp,
            view.read_sequence + 1 if sequence is None else sequence)

    def read(self, role="TP1", status="NEW", filled="0", **kwargs):
        return self.tracker.observe(role, self.receipt(role, status, filled, **kwargs))

    def files(self):
        return sorted(self.path.glob("*.json"))

    def disk_bytes(self):
        return {p.name: p.read_bytes() for p in self.files()}

    def restart(self, source=None):
        return g37.TPLifecycleOffline(g37.EventJournal(self.path), source or self.source,
                                      clock_ms=lambda: self.now)

    def assert_closed(self, tracker=None):
        report = (tracker or self.tracker).report()
        self.assertFalse(report["entry_authorized"])
        self.assertFalse(report["real_signature_verified"])
        self.assertEqual(report["entry_policy"], "NO_NEW_ENTRY")
        self.assertEqual(report["post_policy"], "NO_RETRY")
        self.assertEqual(report["stop_integration"], "ABSENT")

    def corrupt_last(self, transform, *, rehash=False):
        path = self.files()[-1]
        document = json.loads(path.read_bytes())
        transform(document)
        if rehash:
            document["digest"] = g37._digest(document)
        path.write_bytes(g37._encode(document))

    def test_new_then_expired_emits_durable_owner_alarm(self):
        self.start()
        self.assertEqual(self.read().state, "NEW")
        event = self.read(status="EXPIRED")
        self.assertEqual((event.state, event.alarm), ("EXPIRED", "OWNER_ALARM"))
        self.assertEqual(self.tracker.alarms, (event,))
        self.assertEqual(self.journal.replay()[-1], event)
        self.assertTrue(self.tracker.view("TP1").no_retry)
        self.assert_closed()

    def test_tp2_partial_reconcile_then_filled_keeps_alarm_history(self):
        self.start(both=True)
        self.read("TP2")
        partial = self.read("TP2", "PARTIAL", "1")
        self.assertEqual(partial.alarm, "RECONCILE")
        self.assertEqual(self.read("TP2", "FILLED", "3").state, "FILLED")
        self.assertIn(partial, self.tracker.alarms)
        self.assertEqual(self.tracker.view("TP1").state, "INTENT_RECORDED_NO_RETRY")
        self.assert_closed()

    def test_ambiguous_post_is_unknown_and_second_intent_never_recorded(self):
        self.start()
        unknown = self.tracker.record_unknown("TP1", reason="AMBIGUOUS_POST")
        self.assertEqual((unknown.state, unknown.alarm), ("UNKNOWN_NO_RETRY", "OWNER_REVIEW"))
        for _ in range(3):
            self.tracker.record_synthetic_post_intent(self.tp1)
        self.assertEqual(self.tracker.report()["synthetic_post_intents"], 1)
        self.assert_closed()

    def test_read_timeout_preserves_fill_and_sequence_for_reconciliation(self):
        self.start()
        self.read(status="PARTIAL", filled="1")
        before = self.tracker.view("TP1")
        timeout = self.tracker.record_unknown("TP1")
        after = self.tracker.view("TP1")
        self.assertEqual(timeout.alarm, "OWNER_REVIEW")
        self.assertEqual((before.filled_qty, before.read_sequence),
                         (after.filled_qty, after.read_sequence))
        self.assertEqual(self.read(status="FILLED", filled="2").state, "FILLED")
        self.assertEqual(len(self.tracker.alarms), 2)

    def test_timeout_after_partial_cannot_regress_to_new(self):
        self.start()
        self.read(status="PARTIAL", filled="1")
        self.tracker.record_unknown("TP1")
        self.assertEqual(self.read().reason, "CONFLICTING_OBSERVATION")

    def test_two_tp_are_independent_after_one_is_quarantined(self):
        self.start(both=True)
        self.tracker.observe("TP1", {"signed": True})
        self.assertTrue(self.tracker.view("TP1").quarantined)
        self.assertEqual(self.read("TP2").state, "NEW")
        self.assertEqual(self.read("TP2", "FILLED", "3").state, "FILLED")
        self.assertTrue(self.tracker.view("TP1").quarantined)

    def test_two_tp_cannot_share_one_order_identity(self):
        self.start()
        with self.assertRaisesRegex(g37.LifecycleError, "CONFLICTING_TP_IDENTITY"):
            self.tracker.record_synthetic_post_intent(replace(self.tp2, order_id=self.tp1.order_id))
        self.assertIsNone(self.tracker.view("TP2"))
        self.assertEqual(self.tracker.report()["synthetic_post_intents"], 1)

    def test_repeated_ambiguous_post_cannot_describe_a_second_attempt(self):
        self.start()
        self.tracker.record_unknown("TP1", reason="AMBIGUOUS_POST")
        event = self.tracker.record_unknown("TP1", reason="AMBIGUOUS_POST")
        self.assertEqual(event.reason, "CONFLICTING_OBSERVATION")
        self.assertEqual(self.tracker.report()["synthetic_post_intents"], 1)

    def test_signed_true_dict_is_untrusted_and_secrets_are_not_journaled(self):
        self.start()
        event = self.tracker.observe("TP1", {
            "signed": True, "apiKey": "SECRET_API_VALUE", "signature": "SECRET_SIGNATURE",
            "headers": {"Authorization": "SECRET_HEADER"}, "exception": "SECRET_EXCEPTION",
            "status": "FILLED", "orderId": "mock-tp1"})
        self.assertEqual(event.reason, "UNTRUSTED_MOCK_RECEIPT")
        raw = b"".join(self.disk_bytes().values())
        for forbidden in (b"SECRET", b"apiKey", b"signature", b"headers", b"exception", b"signed"):
            self.assertNotIn(forbidden, raw)
        for persisted in self.journal.replay():
            self.assertEqual(set(asdict(persisted)), {field.name for field in g37.fields(g37.Event)})

    def test_receipt_from_different_source_object_is_rejected(self):
        self.start()
        foreign = self.receipt(source=g37.MockTrustedSource())
        self.assertEqual(self.tracker.observe("TP1", foreign).reason, "UNTRUSTED_MOCK_RECEIPT")

    def test_copy_of_valid_receipt_does_not_have_source_identity(self):
        self.start()
        original = self.receipt()
        copied = replace(original)
        self.assertEqual(copied.receipt_id, original.receipt_id)
        self.assertEqual(self.tracker.observe("TP1", copied).reason, "UNTRUSTED_MOCK_RECEIPT")

    def test_tampered_issued_receipt_fails_snapshot_verification(self):
        self.start()
        receipt = self.receipt()
        object.__setattr__(receipt.observation, "status", "FILLED")
        self.assertEqual(self.tracker.observe("TP1", receipt).reason, "UNTRUSTED_MOCK_RECEIPT")

    def test_all_binding_fields_are_checked(self):
        for field, value in (("role", "TP2"), ("scope", "other-scope"),
                             ("symbol", "OTHERUSDT"), ("order_id", "other-order"), ("qty", "3")):
            with self.subTest(field=field):
                self.fresh()
                self.start()
                receipt = self.receipt(binding=replace(self.tp1, **{field: value}))
                self.assertEqual(self.tracker.observe("TP1", receipt).reason, "BINDING_MISMATCH")
                self.assertEqual(self.tracker.view("TP1").filled_qty, "0")

    def test_duplicate_receipt_quarantines_without_reapplying_observation(self):
        self.start()
        receipt = self.receipt()
        accepted = self.tracker.observe("TP1", receipt)
        self.assertEqual(self.tracker.observe("TP1", receipt).reason, "DUPLICATE_RECEIPT")
        self.assertEqual(sum(e.kind == "OBSERVATION" for e in self.tracker.events), 1)
        self.assertEqual(self.tracker.view("TP1").observed_at_ms, accepted.observed_at_ms)
        self.assertEqual(self.read().reason, "TARGET_QUARANTINED")

    def test_stale_future_and_pre_intent_timestamps_fail_closed(self):
        for offset, reason in ((-6000, "STALE_OR_FUTURE"), (100, "STALE_OR_FUTURE"),
                               (-1, "REORDERED_OBSERVATION")):
            with self.subTest(offset=offset):
                self.fresh()
                self.start()
                timestamp = self.now + offset
                self.assertEqual(self.read(timestamp=timestamp).reason, reason)

    def test_reordered_sequence_or_timestamp_is_rejected(self):
        for same_sequence in (True, False):
            with self.subTest(same_sequence=same_sequence):
                self.fresh()
                self.start()
                self.read()
                view = self.tracker.view("TP1")
                kwargs = ({"sequence": view.read_sequence} if same_sequence else
                          {"timestamp": view.observed_at_ms, "sequence": view.read_sequence + 1})
                self.assertEqual(self.read(**kwargs).reason, "REORDERED_OBSERVATION")

    def test_invalid_bool_timestamps_and_sequences_are_not_integers(self):
        for key in ("timestamp", "sequence"):
            with self.subTest(key=key):
                self.fresh()
                self.start()
                self.assertEqual(self.read(**{key: True}).reason, "INVALID_OBSERVATION")

    def test_invalid_quantities_and_status_fail_closed(self):
        for filled in ("NaN", "Infinity", "-1", "1e-3", True, 0.5, "3"):
            with self.subTest(filled=filled):
                self.fresh()
                self.start()
                self.assertEqual(self.read(status="PARTIAL", filled=filled).reason,
                                 "INVALID_OBSERVATION")
        self.fresh()
        self.start()
        self.assertEqual(self.read(status="SECRET_STATUS").reason, "INVALID_OBSERVATION")
        self.assertNotIn(b"SECRET_STATUS", b"".join(self.disk_bytes().values()))

    def test_status_quantity_consistency(self):
        for status, filled in (("NEW", "1"), ("PARTIAL", "0"), ("PARTIAL", "2"),
                               ("FILLED", "1"), ("EXPIRED", "2"), ("REJECTED", "1")):
            with self.subTest(status=status, filled=filled):
                self.fresh()
                self.start()
                self.assertEqual(self.read(status=status, filled=filled).reason, "INVALID_OBSERVATION")

    def test_partial_fill_cannot_decrease(self):
        self.start()
        self.read(status="PARTIAL", filled="1.5")
        self.assertEqual(self.read(status="PARTIAL", filled="1").reason, "CONFLICTING_OBSERVATION")

    def test_terminal_statuses_cannot_be_revived(self):
        for terminal, filled in (("FILLED", "2"), ("EXPIRED", "0"),
                                 ("CANCELED", "0"), ("REJECTED", "0")):
            with self.subTest(terminal=terminal):
                self.fresh()
                self.start()
                self.read(status=terminal, filled=filled)
                self.assertEqual(self.read().reason, "CONFLICTING_OBSERVATION")

    def test_stop_and_other_roles_have_no_integration(self):
        self.start()
        before = self.disk_bytes()
        for role in ("STOP", "ENTRY", "R2", "TP3"):
            with self.subTest(role=role):
                with self.assertRaisesRegex(g37.LifecycleError, "TP_ROLE_ONLY"):
                    g37.Binding(role, "scope", "MOCKUSDT", "id", "1")
                with self.assertRaisesRegex(g37.LifecycleError, "TP_ROLE_ONLY"):
                    self.tracker.observe(role, {"signed": True})
        self.assertEqual(self.disk_bytes(), before)
        for method in ("send_order", "cancel_order", "clear_lock", "authorize_entry", "post", "get"):
            self.assertFalse(hasattr(self.tracker, method))
        self.assert_closed()

    def test_empty_startup_restart_also_denies_new_intents(self):
        restarted = self.restart()
        self.assertTrue(restarted.report()["restarted"])
        with self.assertRaisesRegex(g37.LifecycleError, "NO_NEW_ENTRY"):
            restarted.record_synthetic_post_intent(self.tp1)
        self.assertEqual(restarted.report()["synthetic_post_intents"], 0)
        self.assert_closed(restarted)

    def test_restart_replays_both_tps_and_alarms_without_new_intents(self):
        self.start(both=True)
        self.read()
        self.read(status="EXPIRED")
        self.read("TP2", "PARTIAL", "1")
        before_events, before_alarms = self.tracker.events, self.tracker.alarms
        restarted = self.restart()
        self.assertEqual(restarted.events[:-1], before_events)
        self.assertEqual(restarted.alarms, before_alarms)
        self.assertEqual(restarted.view("TP1").state, "EXPIRED")
        self.assertEqual(restarted.view("TP2").filled_qty, "1")
        with self.assertRaisesRegex(g37.LifecycleError, "NO_NEW_ENTRY"):
            restarted.record_synthetic_post_intent(self.tp2)
        self.assertEqual(restarted.report()["synthetic_post_intents"], 2)
        self.assert_closed(restarted)

    def test_restart_may_record_mock_reconciliation_without_granting_entry(self):
        self.start()
        self.tracker.record_unknown("TP1", reason="AMBIGUOUS_POST")
        new_source = g37.MockTrustedSource()
        restarted = self.restart(new_source)
        self.now += 10
        receipt = new_source.issue_mock_read(self.tp1, "NEW", "0", self.now, 1)
        self.assertEqual(restarted.observe("TP1", receipt).state, "NEW")
        self.assertEqual(restarted.report()["synthetic_post_intents"], 1)
        self.assertEqual(len(restarted.alarms), 1)
        self.assert_closed(restarted)

    def test_restart_remembers_used_receipt_and_ordering(self):
        self.start()
        receipt = self.receipt()
        self.tracker.observe("TP1", receipt)
        restarted = self.restart()
        self.assertEqual(restarted.observe("TP1", receipt).reason, "DUPLICATE_RECEIPT")

    def test_stale_writer_is_fenced_after_another_tracker_restarts(self):
        restarted = self.restart()
        before = self.disk_bytes()
        with self.assertRaises(g37.JournalError):
            self.tracker.record_synthetic_post_intent(self.tp1)
        self.assertEqual(self.disk_bytes(), before)
        self.assertEqual(self.tracker.report()["journal_health"], "FAILED")
        self.assert_closed(restarted)

    def test_events_views_and_receipts_are_immutable_values(self):
        self.start()
        receipt = self.receipt()
        event = self.tracker.observe("TP1", receipt)
        for value, field, replacement in ((event, "state", "FILLED"),
                                          (event.binding, "qty", "99"),
                                          (receipt, "receipt_id", "other"),
                                          (self.tracker.view("TP1"), "no_retry", False)):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, field, replacement)
        self.assertIsInstance(self.tracker.events, tuple)
        self.assertIsInstance(self.tracker.alarms, tuple)

    def test_append_does_not_change_any_previous_event_bytes(self):
        self.start()
        before = self.disk_bytes()
        self.read()
        self.read(status="EXPIRED")
        after = self.disk_bytes()
        for name, raw in before.items():
            self.assertEqual(after[name], raw)
        events = self.journal.replay()
        self.assertEqual(len({e.event_id for e in events}), len(events))
        for previous, following in zip(events, events[1:]):
            self.assertEqual(following.previous, previous.digest)

    def test_journal_allowlist_refuses_free_text_before_writing(self):
        before = self.disk_bytes()
        with self.assertRaises(g37.JournalError):
            self.journal.append(expected_tail=self.tracker.events[-1].digest,
                                kind="UNKNOWN", at_ms=self.now, reason="SECRET_EXCEPTION")
        with self.assertRaises(TypeError):
            self.journal.append(expected_tail=self.tracker.events[-1].digest,
                                kind="UNKNOWN", at_ms=self.now, reason="READ_TIMEOUT",
                                headers={"apiKey": "SECRET"})
        self.assertEqual(self.disk_bytes(), before)

    def test_fsync_covers_complete_file_and_directory_publication(self):
        real_fsync = g37.os.fsync
        with mock.patch.object(g37.os, "fsync", wraps=real_fsync) as sync:
            self.start()
        self.assertEqual(sync.call_count, 3)
        self.assertFalse(list(self.path.glob(".pending-*")))

    def test_fsync_failure_is_sticky_and_orphan_pending_blocks_replay(self):
        with mock.patch.object(g37.os, "fsync", side_effect=OSError("SECRET_IO_ERROR")):
            with self.assertRaisesRegex(g37.JournalError, "JOURNAL_FAILED_NO_RETRY"):
                self.start()
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.tracker.report()["journal_health"], "FAILED")
        with self.assertRaises(g37.JournalError):
            self.start()
        with self.assertRaises(g37.JournalError):
            self.restart()
        self.assertNotIn(b"SECRET_IO_ERROR", b"".join(p.read_bytes() for p in self.path.iterdir()))
        self.assert_closed()

    def test_directory_fsync_failures_never_allow_retry_after_possible_commit(self):
        for failure_call in (2, 3):
            with self.subTest(failure_call=failure_call):
                self.fresh()
                real_fsync = g37.os.fsync
                calls = 0

                def fail_at_stage(fd):
                    nonlocal calls
                    calls += 1
                    if calls == failure_call:
                        raise OSError("directory sync unavailable")
                    return real_fsync(fd)

                with mock.patch.object(g37.os, "fsync", side_effect=fail_at_stage):
                    with self.assertRaises(g37.JournalError):
                        self.start()
                self.assertEqual(len(self.files()), 2)  # Niyet diskte olabilir.
                self.assertEqual(self.tracker.report()["journal_health"], "FAILED")
                with self.assertRaises(g37.JournalError):
                    self.start()
                if failure_call == 2:
                    with self.assertRaises(g37.JournalError):
                        self.restart()  # Kalan pending dosyası belirsizliği korur.
                else:
                    restarted = self.restart()
                    self.assertEqual(restarted.report()["synthetic_post_intents"], 1)
                    with self.assertRaisesRegex(g37.LifecycleError, "NO_NEW_ENTRY"):
                        restarted.record_synthetic_post_intent(self.tp1)
                    self.assert_closed(restarted)

    def test_busy_journal_lock_fails_immediately_without_overwriting_events(self):
        before = self.disk_bytes()
        with (self.path / ".g37-lock").open("rb") as lock:
            g37.fcntl.flock(lock.fileno(), g37.fcntl.LOCK_EX | g37.fcntl.LOCK_NB)
            with self.assertRaises(g37.JournalError):
                self.start()
        self.assertEqual(self.disk_bytes(), before)
        self.assertEqual(self.tracker.report()["journal_health"], "FAILED")

    def test_publication_collision_cannot_overwrite_existing_event(self):
        before = self.disk_bytes()
        with mock.patch.object(g37.os, "link", side_effect=FileExistsError("collision")):
            with self.assertRaises(g37.JournalError):
                self.start()
        self.assertEqual(self.disk_bytes(), before)
        with self.assertRaises(g37.JournalError):
            self.restart()

    def test_duplicate_event_id_is_refused_before_publication(self):
        before = self.disk_bytes()
        with mock.patch.object(g37.uuid, "uuid4") as generator:
            generator.return_value.hex = self.tracker.events[0].event_id
            with self.assertRaises(g37.JournalError):
                self.start()
        self.assertEqual(self.disk_bytes(), before)

    def test_corrupt_or_truncated_event_blocks_replay(self):
        self.start()
        self.files()[-1].write_bytes(b'{"partial":')
        with self.assertRaises(g37.JournalError):
            self.restart()

    def test_digest_tampering_blocks_replay(self):
        self.start()
        self.corrupt_last(lambda d: d.update(state="FILLED"))
        with self.assertRaises(g37.JournalError):
            self.restart()

    def test_extra_field_even_with_rehashed_event_blocks_replay(self):
        self.start()
        self.corrupt_last(lambda d: d.update(apiKey="SECRET"), rehash=True)
        with self.assertRaises(g37.JournalError):
            self.restart()

    def test_semantically_invalid_rehashed_event_blocks_replay(self):
        self.start()
        self.read()
        self.corrupt_last(lambda d: d.update(state="FILLED"), rehash=True)
        with self.assertRaises(g37.JournalError):
            self.restart()

    def test_missing_middle_or_reordered_files_block_replay(self):
        for reorder in (False, True):
            with self.subTest(reorder=reorder):
                self.fresh()
                self.start()
                self.read()
                files = self.files()
                if reorder:
                    first, second = files[0].read_bytes(), files[1].read_bytes()
                    files[0].write_bytes(second)
                    files[1].write_bytes(first)
                else:
                    files[1].unlink()  # Sadece saldırı fixture'ı; üretim API'si silmez.
                with self.assertRaises(g37.JournalError):
                    self.restart()

    def test_symlink_directory_lock_and_event_are_refused(self):
        link = self.root / "linked-journal"
        link.symlink_to(self.path, target_is_directory=True)
        with self.assertRaises(g37.JournalError):
            g37.EventJournal(link)
        for name in (".g37-lock", self.files()[0].name):
            with self.subTest(name=name):
                self.fresh()
                target = self.path / name
                raw = target.read_bytes()
                backup = self.root / f"backup-{self.serial}"
                backup.write_bytes(raw)
                target.unlink()
                target.symlink_to(backup)
                with self.assertRaises(g37.JournalError):
                    self.restart()
                self.assertEqual(backup.read_bytes(), raw)

    def test_clock_rollback_fails_closed_and_blocks_further_writes(self):
        self.start()
        self.now -= 1
        with self.assertRaises(g37.JournalError):
            self.tracker.record_unknown("TP1")
        self.now += 100
        with self.assertRaises(g37.JournalError):
            self.tracker.record_unknown("TP1")
        self.assertEqual(self.tracker.report()["journal_health"], "FAILED")

    def test_concurrent_calls_record_exactly_one_intent_per_tp(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            events = list(pool.map(lambda _: self.tracker.record_synthetic_post_intent(self.tp1),
                                   range(12)))
        self.assertEqual(sum(event.kind == "INTENT" for event in events), 1)
        self.assertEqual(self.tracker.report()["synthetic_post_intents"], 1)
        self.assertEqual(len(self.journal.replay()), 13)
        self.assert_closed()

    def test_independent_process_writers_cannot_both_append_same_tail(self):
        code = """
import sys
from tp_lifecycle_offline_g37 import EventJournal, JournalError
try:
    EventJournal(sys.argv[1]).append(expected_tail=sys.argv[2], kind='RESTART',
        at_ms=10000, reason='RESTART_DISK_REPLAY')
except JournalError:
    raise SystemExit(3)
"""
        command = [sys.executable, "-B", "-c", code, str(self.path),
                   self.tracker.events[-1].digest]
        processes = [subprocess.Popen(command, cwd=Path(g37.__file__).parent,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(2)]
        for process in processes:
            out, err = process.communicate(timeout=10)
            self.assertEqual((out, err), (b"", b""))
        self.assertEqual(sorted(p.returncode for p in processes), [0, 3])
        self.assertEqual(len(self.journal.replay()), 2)

    def test_synthetic_demo_runs_both_tp_flows_and_disk_replay(self):
        with mock.patch("socket.create_connection", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            result = g37.synthetic_demo(self.root / "demo")
        before, after = result["before_restart"], result["after_disk_replay"]
        self.assertEqual(before["tp"], {"TP1": "EXPIRED", "TP2": "FILLED"})
        self.assertEqual(before["synthetic_post_intents"], 2)
        self.assertEqual(after["synthetic_post_intents"], 2)
        self.assertEqual(after["alarms"], before["alarms"])
        self.assertEqual({e["alarm"] for e in after["alarms"]},
                         {"OWNER_ALARM", "RECONCILE", "OWNER_REVIEW"})
        self.assertTrue(after["restarted"])
        self.assertFalse(after["entry_authorized"])
        self.assertFalse(after["real_signature_verified"])

    def test_standalone_cli_and_second_disk_run_do_not_add_post_intents(self):
        command = [sys.executable, "-B", g37.__file__, "--journal-directory", str(self.root / "cli")]
        for run in range(2):
            with self.subTest(run=run):
                process = subprocess.run(command, capture_output=True, text=True, timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr)
                report = json.loads(process.stdout)["after_disk_replay"]
                self.assertEqual(report["synthetic_post_intents"], 2)
                self.assertEqual(report["tp"], {"TP1": "EXPIRED", "TP2": "FILLED"})
                self.assertFalse(report["entry_authorized"])


if __name__ == "__main__":
    unittest.main()
