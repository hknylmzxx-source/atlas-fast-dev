"""G42 çevrimdışı callback regresyonları; eski modüller/SDK import edilmez."""

from contextlib import contextmanager
from dataclasses import dataclass, replace
import fcntl
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from protective_deadline_patch_candidate_g42 import (
    Callbacks, Decision, FenceBusy, FenceGrant, FsyncAck, Intent, Lease,
    ProtectiveDeadlinePatchCandidate,
)


class ManualClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass(frozen=True)
class StopObservation:
    identity: str = "OFFLINE_STOP_OBSERVATION"
    present: bool = True


class MockStorage:
    """Mock callback'ler gerçek liste/dict yan etkisi yapar; soyut geçiş modeli değil."""

    def __init__(self, clock):
        self.clock = clock
        self.current = Lease("SCOPE_G42", "MOCK_OWNER", 1, 7, 200.0)
        self.stop = StopObservation()
        self.rows = {}
        self.writes = []
        self.events = []
        self.held = False
        self.busy_count = 0
        self.on_enter = None
        self.on_lookup = None
        self.on_append = None
        self.on_exit = None
        self.grant_override = None
        self.acquire = Mock(side_effect=self._acquire)
        self.lease_is_current = Mock(side_effect=lambda lease: lease is self.current)
        self.lookup = Mock(side_effect=self._lookup)
        self.append_once = Mock(side_effect=self._append_once)
        self.fsync_file = Mock(side_effect=self._fsync_file)
        self.fsync_directory = Mock(side_effect=self._fsync_directory)

    def callbacks(self):
        return Callbacks(self.acquire, self.lease_is_current, self.lookup,
                         self.append_once, self.fsync_file, self.fsync_directory)

    @contextmanager
    def _acquire(self, lease):
        self.events.append("acquire")
        if self.busy_count:
            self.busy_count -= 1
            raise FenceBusy("SYNTHETIC_BUSY")
        if self.held:
            raise FenceBusy("SYNTHETIC_HELD")
        self.held = True
        try:
            if self.on_enter:
                self.on_enter()
            yield self.grant_override or FenceGrant(
                lease.scope, lease.owner, lease.epoch, lease.generation)
        finally:
            self.held = False
            self.events.append("exit")
            if self.on_exit:
                self.on_exit()

    def _lookup(self, intent):
        assert self.held, "lookup fence altında olmalı"
        self.events.append("lookup")
        if self.on_lookup:
            self.on_lookup()
        return self.rows.get((intent.scope, intent.role))

    def _append_once(self, intent):
        assert self.held, "append fence altında olmalı"
        if self.on_append:
            self.on_append()
        key = (intent.scope, intent.role)
        if key in self.rows:
            raise FileExistsError("SYNTHETIC_DUPLICATE")
        self.rows[key] = intent
        self.writes.append((intent, self.clock.now))
        self.events.append("append")

    def _fsync_file(self, intent):
        assert self.held, "file fsync fence altında olmalı"
        assert self.rows[(intent.scope, intent.role)] == intent
        self.events.append("file_fsync")
        return FsyncAck.OK

    def _fsync_directory(self):
        assert self.held, "directory fsync fence altında olmalı"
        self.events.append("directory_fsync")
        return FsyncAck.OK


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.storage = MockStorage(self.clock)
        self.intent = Intent("SCOPE_G42", "TP1", "G42_ID", "MOCKUSDT", "G42_CLIENT")
        self.lease = self.storage.current
        self.candidate = self.new_candidate()
        self.stop = self.storage.stop

    def new_candidate(self, **kwargs):
        return ProtectiveDeadlinePatchCandidate(
            self.storage.callbacks(), replay_only=kwargs.pop("replay_only", False),
            clock=kwargs.pop("clock", self.clock), sleep=kwargs.pop("sleep", self.clock.sleep),
            **kwargs)

    def run_candidate(self, **kwargs):
        result = self.candidate.run(self.intent, self.lease, **kwargs)
        self.assert_closed(result)
        return result

    def assert_closed(self, result):
        self.assertIs(result.entry_authorized, False)
        self.assertIs(result.unlock, False)
        self.assertIs(result.stop_proven, False)
        self.assertEqual(result.retry_policy, "NO_RETRY")
        self.assertIs(self.storage.stop, self.stop)
        self.assertEqual(self.storage.stop, StopObservation())

    def assert_no_write(self):
        self.storage.append_once.assert_not_called()
        self.storage.fsync_file.assert_not_called()
        self.storage.fsync_directory.assert_not_called()
        self.assertEqual(self.storage.rows, {})

    def revoke(self):
        self.storage.current = replace(self.lease, generation=self.lease.generation + 1)

    def test_positive_success_runs_real_callback_sequence_under_fence(self):
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual(result.status, "ACKED")
        self.assertTrue(result.durability_confirmed)
        self.assertEqual(self.storage.events,
                         ["acquire", "lookup", "append", "file_fsync", "directory_fsync", "exit"])
        self.assertEqual(self.storage.writes, [(self.intent, 100.0)])
        self.assertFalse(self.storage.held)

    def test_positive_deadline_checked_before_first_acquisition(self):
        self.candidate = self.new_candidate(clock=Mock(side_effect=[100.0, 100.25]))
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual((result.status, result.attempts), ("DEADLINE", 0))
        self.storage.acquire.assert_not_called()
        self.assert_no_write()

    def test_unrepresentable_positive_deadline_is_rejected(self):
        self.candidate = self.new_candidate(clock=lambda: 1e20)
        result = self.run_candidate(timeout_s=0.01)
        self.assertEqual(result.reason, "DEADLINE_NOT_REPRESENTABLE")
        self.storage.acquire.assert_not_called()
        self.assert_no_write()

    def test_preemption_during_acquisition_rejects_before_lookup(self):
        self.storage.on_enter = lambda: setattr(self.clock, "now", 100.25)
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual(result.status, "DEADLINE")
        self.storage.lookup.assert_not_called()
        self.assertFalse(self.storage.held)
        self.assert_no_write()

    def test_deadline_equality_after_lookup_rejects_before_mutation(self):
        self.storage.on_lookup = lambda: setattr(self.clock, "now", 100.25)
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual(result.status, "DEADLINE")
        self.storage.lookup.assert_called_once_with(self.intent)
        self.assert_no_write()

    def test_oversleep_and_late_release_never_attempt_second_fence(self):
        self.storage.busy_count = 1
        sleeps = []
        def oversleep(seconds):
            sleeps.append(seconds)
            self.clock.now = 100.5
        self.candidate = self.new_candidate(sleep=oversleep)
        result = self.run_candidate(timeout_s=0.25, poll_s=0.005)
        self.assertEqual(result.status, "DEADLINE")
        self.assertEqual(sleeps, [0.005])
        self.storage.acquire.assert_called_once()
        self.assert_no_write()

    def test_oversleep_while_still_busy_also_stops_before_second_fence(self):
        self.storage.busy_count = 10
        self.candidate = self.new_candidate(sleep=lambda seconds: setattr(self.clock, "now", 101.0))
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual(result.status, "DEADLINE")
        self.assertEqual(result.attempts, 1)
        self.assert_no_write()

    def test_contention_retry_within_budget_writes_once(self):
        self.storage.busy_count = 2
        result = self.run_candidate(timeout_s=0.25, poll_s=0.005)
        self.assertEqual((result.status, result.attempts), ("ACKED", 3))
        self.assertEqual(self.clock.sleeps, [0.005, 0.005])
        self.storage.append_once.assert_called_once_with(self.intent)

    def test_sleep_is_clipped_to_remaining_budget(self):
        self.storage.busy_count = 100
        result = self.run_candidate(timeout_s=0.015625, poll_s=0.05)
        self.assertEqual(result.status, "DEADLINE")
        self.assertEqual(self.clock.sleeps, [0.015625])
        self.assertEqual(result.attempts, 1)
        self.assert_no_write()

    def test_timeout_result_is_terminal_even_after_lock_release(self):
        self.storage.busy_count = 1
        result = self.run_candidate(timeout_s=0.015625, poll_s=0.05)
        again = self.run_candidate(timeout_s=0.25)
        self.assertIs(again, result)
        self.storage.acquire.assert_called_once()
        self.assert_no_write()

    def test_zero_timeout_free_fence_is_one_attempt_without_time_budget(self):
        self.storage.on_enter = lambda: setattr(self.clock, "now", 101.0)
        self.storage.fsync_directory.side_effect = lambda: (
            setattr(self.clock, "now", 102.0) or FsyncAck.OK)
        result = self.run_candidate(timeout_s=0)
        self.assertEqual((result.status, result.attempts), ("ACKED", 1))
        self.assertEqual(self.clock.sleeps, [])
        self.storage.append_once.assert_called_once()

    def test_zero_timeout_busy_is_terminal_and_never_sleeps(self):
        self.storage.busy_count = 1
        result = self.run_candidate(timeout_s=0)
        self.assertEqual(result.reason, "FENCE_BUSY_NONBLOCKING")
        self.assertIs(self.run_candidate(timeout_s=0), result)
        self.storage.acquire.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])
        self.assert_no_write()

    def test_zero_timeout_still_rejects_lease_expiry_before_mutation(self):
        self.storage.on_lookup = lambda: setattr(self.clock, "now", 200.0)
        result = self.run_candidate(timeout_s=0)
        self.assertEqual(result.reason, "LEASE_EXPIRED")
        self.assert_no_write()

    def test_expired_lease_rejected_at_start(self):
        self.clock.now = 200.0
        result = self.run_candidate()
        self.assertEqual(result.reason, "LEASE_EXPIRED")
        self.storage.acquire.assert_not_called()
        self.assert_no_write()

    def test_lease_expiry_after_lookup_rejected_under_fence(self):
        self.lease = replace(self.lease, expires_at=100.125)
        self.storage.current = self.lease
        self.storage.on_lookup = lambda: setattr(self.clock, "now", 100.125)
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual(result.reason, "LEASE_EXPIRED")
        self.assert_no_write()

    def test_stale_lease_before_fence_rejected(self):
        self.revoke()
        result = self.run_candidate()
        self.assertEqual(result.reason, "LEASE_STALE")
        self.storage.acquire.assert_not_called()
        self.assert_no_write()

    def test_lease_revoked_during_acquisition_rejected(self):
        self.storage.on_enter = self.revoke
        result = self.run_candidate()
        self.assertEqual(result.reason, "LEASE_STALE")
        self.assert_no_write()

    def test_same_owner_new_generation_during_lookup_rejected(self):
        self.storage.on_lookup = self.revoke
        result = self.run_candidate()
        self.assertEqual(result.reason, "LEASE_STALE")
        self.assert_no_write()

    def test_stale_or_malformed_fence_grants_are_rejected(self):
        valid = FenceGrant(self.lease.scope, self.lease.owner, 1, 7)
        for grant in [replace(valid, generation=6), replace(valid, epoch=0),
                      replace(valid, owner="OTHER"), replace(valid, scope="OTHER"),
                      replace(valid, generation=True), replace(valid, epoch=True), True]:
            with self.subTest(grant=grant):
                self.storage.grant_override = grant
                result = self.new_candidate().run(self.intent, self.lease)
                self.assert_closed(result)
                self.assertEqual(result.reason, "FENCE_GRANT_STALE_OR_INVALID")
                self.assert_no_write()

    def test_lease_copy_with_equal_fields_is_not_current_authority(self):
        self.lease = replace(self.lease)
        result = self.run_candidate()
        self.assertEqual(result.reason, "LEASE_STALE")
        self.assert_no_write()

    def test_readiness_bool_and_invalid_lease_shapes_are_not_authority(self):
        for lease in [True, None, replace(self.lease, scope="OTHER"),
                      replace(self.lease, epoch=True), replace(self.lease, generation=0),
                      replace(self.lease, expires_at=math.inf)]:
            with self.subTest(lease=lease):
                result = self.new_candidate().run(self.intent, lease)
                self.assert_closed(result)
                self.assertEqual(result.reason, "LEASE_INVALID")
                self.assert_no_write()

    def test_truthy_current_callback_is_not_true_authority(self):
        self.storage.lease_is_current.side_effect = None
        self.storage.lease_is_current.return_value = 1
        result = self.run_candidate()
        self.assertEqual(result.reason, "LEASE_STALE")
        self.assert_no_write()

    def test_file_and_directory_fsync_unknown_or_exception_never_retry(self):
        for field in ("fsync_file", "fsync_directory"):
            for failure in (FsyncAck.UNKNOWN, None, OSError("SYNTHETIC_IO"), FenceBusy()):
                with self.subTest(field=field, failure=type(failure).__name__):
                    clock = ManualClock()
                    backend = MockStorage(clock)
                    stop = backend.stop
                    callback = getattr(backend, field)
                    if isinstance(failure, Exception):
                        callback.side_effect = failure
                    else:
                        callback.side_effect = None
                        callback.return_value = failure
                    candidate = ProtectiveDeadlinePatchCandidate(
                        backend.callbacks(), replay_only=False, clock=clock, sleep=clock.sleep)
                    result = candidate.run(self.intent, backend.current)
                    self.assert_closed(result)
                    self.assertEqual(result.status, "UNKNOWN")
                    self.assertTrue(result.append_attempted)
                    self.assertFalse(result.durability_confirmed)
                    self.assertIs(candidate.run(self.intent, backend.current), result)
                    backend.append_once.assert_called_once()
                    backend.acquire.assert_called_once()
                    self.assertEqual(len(backend.rows), 1)
                    self.assertEqual(clock.sleeps, [])
                    self.assertIs(backend.stop, stop)
                    self.assertEqual(backend.stop, StopObservation())

    def test_late_fsync_ack_is_unknown_even_when_record_exists(self):
        self.storage.fsync_directory.side_effect = lambda: (
            setattr(self.clock, "now", 100.25) or FsyncAck.OK)
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "DEADLINE_EXPIRED"))
        self.assertEqual(len(self.storage.rows), 1)
        self.assertTrue(result.append_attempted)
        self.assertFalse(result.durability_confirmed)
        self.assertIs(self.run_candidate(), result)
        self.storage.append_once.assert_called_once()

    def test_append_exception_after_side_effect_is_unknown(self):
        def partial_append(intent):
            self.storage._append_once(intent)
            raise OSError("SYNTHETIC_AFTER_WRITE")
        self.storage.append_once.side_effect = partial_append
        result = self.run_candidate()
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(len(self.storage.rows), 1)
        self.storage.fsync_file.assert_not_called()
        self.assertIs(self.run_candidate(), result)
        self.storage.append_once.assert_called_once()

    def test_append_fencebusy_is_unknown_and_not_contention_retry(self):
        self.storage.append_once.side_effect = FenceBusy("BODY_NOT_ACQUIRE")
        result = self.run_candidate()
        self.assertEqual(result.status, "UNKNOWN")
        self.assertTrue(result.append_attempted)
        self.assertEqual(self.clock.sleeps, [])
        self.storage.acquire.assert_called_once()

    def test_nonbusy_acquire_error_never_retries(self):
        self.storage.acquire.side_effect = OSError("SYNTHETIC_FENCE")
        result = self.run_candidate()
        self.assertEqual(result.status, "BLOCKED")
        self.storage.acquire.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])
        self.assert_no_write()

    def test_lookup_fencebusy_is_not_acquire_contention(self):
        self.storage.lookup.side_effect = FenceBusy("SYNTHETIC_LOOKUP")
        result = self.run_candidate()
        self.assertEqual(result.status, "BLOCKED")
        self.storage.acquire.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])
        self.assert_no_write()

    def test_identical_intent_returns_cached_ack_without_append_or_fsync(self):
        first = self.run_candidate()
        self.clock.now = 300.0
        second = self.run_candidate()
        self.assertIs(second, first)
        self.storage.acquire.assert_called_once()
        self.storage.append_once.assert_called_once()
        self.storage.fsync_file.assert_called_once()
        self.storage.fsync_directory.assert_called_once()

    def test_changed_identity_cannot_overwrite_same_scope_role(self):
        self.run_candidate()
        result = self.candidate.run(replace(self.intent, identity="OTHER"), self.lease)
        self.assert_closed(result)
        self.assertEqual(result.reason, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.storage.rows[(self.intent.scope, self.intent.role)], self.intent)
        self.storage.append_once.assert_called_once()

    def test_second_instance_sees_record_without_claiming_known_durability(self):
        self.run_candidate()
        result = self.new_candidate().run(self.intent, self.lease)
        self.assert_closed(result)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.reason, "PREEXISTING_RECORD_DURABILITY_UNKNOWN")
        self.assertFalse(result.append_attempted)
        self.storage.append_once.assert_called_once()

    def test_preexisting_conflicting_record_is_never_overwritten(self):
        existing = replace(self.intent, client_id="OTHER")
        self.storage.rows[(self.intent.scope, self.intent.role)] = existing
        result = self.run_candidate()
        self.assertEqual(result.reason, "IDEMPOTENCY_CONFLICT")
        self.storage.append_once.assert_not_called()
        self.assertEqual(self.storage.rows[(self.intent.scope, self.intent.role)], existing)

    def test_unknown_quarantines_scope_even_for_new_identity_and_role(self):
        self.storage.fsync_file.side_effect = None
        self.storage.fsync_file.return_value = FsyncAck.UNKNOWN
        first = self.run_candidate()
        result = self.candidate.run(replace(self.intent, role="TP2", identity="NEW"), self.lease)
        self.assert_closed(result)
        self.assertEqual((first.status, result.reason), ("UNKNOWN", "SCOPE_QUARANTINED_NO_RETRY"))
        self.storage.acquire.assert_called_once()
        self.storage.append_once.assert_called_once()

    def test_unknown_does_not_block_unrelated_scope_or_change_stop(self):
        self.storage.fsync_file.side_effect = None
        self.storage.fsync_file.return_value = FsyncAck.UNKNOWN
        self.run_candidate()
        self.storage.fsync_file.side_effect = self.storage._fsync_file
        intent = replace(self.intent, scope="OTHER_SCOPE")
        lease = replace(self.lease, scope=intent.scope)
        self.storage.current = lease
        result = self.candidate.run(intent, lease)
        self.assert_closed(result)
        self.assertEqual(result.status, "ACKED")
        self.assertEqual(len(self.storage.rows), 2)

    def test_tp1_tp2_and_stop_role_wal_do_not_prove_or_modify_stop(self):
        for role in ("TP1", "TP2", "STOP"):
            result = self.candidate.run(replace(self.intent, role=role), self.lease)
            self.assert_closed(result)
            self.assertEqual(result.status, "ACKED")
        self.assertEqual(len(self.storage.rows), 3)

    def test_restart_instance_is_replay_only_even_when_no_row_visible(self):
        restarted = self.new_candidate(replay_only=True)
        result = restarted.run(self.intent, self.lease)
        self.assert_closed(result)
        self.assertEqual(result.status, "REPLAY_ONLY")
        self.storage.acquire.assert_not_called()
        self.assert_no_write()

    def test_restart_does_not_reuse_cached_success_as_new_permission(self):
        self.run_candidate()
        self.candidate.mark_restart()
        result = self.run_candidate()
        self.assertEqual(result.status, "REPLAY_ONLY")
        self.storage.append_once.assert_called_once()
        self.assertIs(self.storage.stop, self.stop)

    def test_restart_during_lookup_prevents_append(self):
        self.storage.on_lookup = self.candidate.mark_restart
        result = self.run_candidate()
        self.assertEqual(result.status, "REPLAY_ONLY")
        self.assert_no_write()

    def test_restart_during_append_is_unknown_and_never_cleans_record(self):
        self.storage.on_append = self.candidate.mark_restart
        result = self.run_candidate()
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(len(self.storage.rows), 1)
        self.assertEqual(self.run_candidate().status, "REPLAY_ONLY")
        self.storage.append_once.assert_called_once()

    def test_clock_regression_before_acquire_rejects(self):
        self.candidate = self.new_candidate(clock=Mock(side_effect=[100.0, 99.0]))
        result = self.run_candidate()
        self.assertEqual(result.reason, "CLOCK_INVALID_OR_REGRESSED")
        self.storage.acquire.assert_not_called()
        self.assert_no_write()

    def test_clock_regression_after_append_is_unknown(self):
        self.storage.fsync_directory.side_effect = lambda: (
            setattr(self.clock, "now", 99.0) or FsyncAck.OK)
        result = self.run_candidate()
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "CLOCK_INVALID_OR_REGRESSED"))
        self.assertEqual(len(self.storage.rows), 1)

    def test_invalid_clock_values_fail_closed(self):
        for now in (math.nan, math.inf, -math.inf, True, "100"):
            with self.subTest(now=now):
                result = self.new_candidate(clock=lambda: now).run(self.intent, self.lease)
                self.assert_closed(result)
                self.assertEqual(result.status, "BLOCKED")
                self.assert_no_write()

    def test_invalid_timeout_and_poll_are_rejected_without_callbacks(self):
        for field, values in {
            "timeout_s": (-1, 0.50001, math.nan, math.inf, True, "0.1"),
            "poll_s": (0, -1, 0.05001, math.nan, math.inf, True, "0.01"),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        self.candidate.run(self.intent, self.lease, **{field: value})
        self.storage.acquire.assert_not_called()
        self.storage.lease_is_current.assert_not_called()
        self.assert_no_write()

    def test_invalid_intent_rejected_without_callbacks(self):
        for intent in (True, replace(self.intent, role="ENTRY"),
                       replace(self.intent, identity="../escape"),
                       replace(self.intent, scope=""), replace(self.intent, client_id=True)):
            with self.subTest(intent=intent):
                with self.assertRaises(ValueError):
                    self.candidate.run(intent, self.lease)
        self.storage.acquire.assert_not_called()
        self.assert_no_write()

    def test_fence_exit_error_after_append_is_unknown(self):
        def exit_error():
            raise FenceBusy("EXIT_NOT_ACQUIRE")
        self.storage.on_exit = exit_error
        result = self.run_candidate()
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(len(self.storage.rows), 1)
        self.assertEqual(self.clock.sleeps, [])
        self.storage.acquire.assert_called_once()

    def test_suppressed_append_exception_cannot_become_success(self):
        @contextmanager
        def swallowing_fence(lease):
            self.storage.held = True
            try:
                yield FenceGrant(lease.scope, lease.owner, lease.epoch, lease.generation)
            except OSError:
                pass
            finally:
                self.storage.held = False
        self.storage.acquire.side_effect = swallowing_fence
        self.storage.append_once.side_effect = OSError("SYNTHETIC_APPEND")
        result = self.run_candidate()
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "FENCE_SUPPRESSED_FAILURE"))

    def test_reentrant_call_is_rejected_without_second_mutation(self):
        nested = []
        self.storage.on_lookup = lambda: nested.append(
            self.candidate.run(replace(self.intent, role="TP2"), self.lease))
        result = self.run_candidate()
        self.assertEqual(result.status, "ACKED")
        self.assertEqual(nested[0].reason, "CALL_IN_PROGRESS_NO_RETRY")
        self.assert_closed(nested[0])
        self.storage.append_once.assert_called_once()

    def test_toctou_deadline_inside_append_can_still_allow_late_write(self):
        # Bilerek sınırı gösterir: son check ile storage mutation atomik değildir.
        self.storage.on_append = lambda: setattr(self.clock, "now", 101.0)
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(self.storage.writes, [(self.intent, 101.0)])
        self.assertIs(self.run_candidate(), result)
        self.storage.append_once.assert_called_once()

    def test_toctou_generation_change_inside_append_does_not_erase_write(self):
        self.storage.on_append = self.revoke
        result = self.run_candidate()
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "LEASE_STALE"))
        self.assertEqual(len(self.storage.rows), 1)
        self.assertIs(self.run_candidate(), result)

    def test_late_fence_exit_cannot_return_ack(self):
        self.storage.on_exit = lambda: setattr(self.clock, "now", 101.0)
        result = self.run_candidate(timeout_s=0.25)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(len(self.storage.rows), 1)

    def test_revocation_during_fence_exit_cannot_return_ack(self):
        self.storage.on_exit = self.revoke
        result = self.run_candidate()
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "LEASE_STALE"))
        self.assertEqual(len(self.storage.rows), 1)

    def test_decision_authority_flags_cannot_be_enabled_by_constructor(self):
        result = self.run_candidate()
        for name in ("entry_authorized", "unlock", "stop_proven", "retry_policy"):
            with self.subTest(name=name):
                with self.assertRaises((TypeError, ValueError)):
                    replace(result, **{name: True})
        with self.assertRaises((TypeError, ValueError)):
            Decision("ACKED", "MOCK", entry_authorized=True)


_CHILD = r'''
import fcntl
import os
import select
import sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("LOCKED", flush=True)
    if not select.select([sys.stdin], [], [], 10.0)[0]:
        raise SystemExit(3)
    if sys.stdin.readline() != "RELEASE\n":
        raise SystemExit(4)
    fcntl.flock(fd, fcntl.LOCK_UN)
    print("RELEASED", flush=True)
finally:
    os.close(fd)
'''


class HeldChildFence:
    """Yalnız temp fixture çocuğu; zaman bazlı normal kilit bırakma yok."""

    def __init__(self, path):
        self.path = path
        self.proc = None
        self.buffer = b""

    def read_line(self):
        deadline = time.monotonic() + 5.0
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise AssertionError("G42_FIXTURE_ACK_TIMEOUT")
            chunk = os.read(self.proc.stdout.fileno(), 256)
            if not chunk:
                raise AssertionError("G42_FIXTURE_ACK_EOF")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line.decode("ascii")

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", _CHILD, str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            if self.read_line() != "LOCKED":
                raise AssertionError("G42_FIXTURE_BAD_LOCK_ACK")
            # Readiness sonrasında hâlâ tutulan gerçek advisory fence doğrulanır.
            fd = os.open(self.path, os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError("G42_FIXTURE_NOT_HELD")
            finally:
                os.close(fd)
            return self
        except BaseException:
            self.close()
            raise

    def release(self):
        self.proc.stdin.write(b"RELEASE\n")
        self.proc.stdin.flush()
        if self.read_line() != "RELEASED" or self.proc.wait(timeout=5.0) != 0:
            raise AssertionError("G42_FIXTURE_RELEASE_FAILED")

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5.0)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()

    def __exit__(self, exc_type, exc, tb):
        self.close()


class ProcessFenceTests(unittest.TestCase):
    def fixture(self, backend, path):
        @contextmanager
        def acquire(lease):
            fd = os.open(path, os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise FenceBusy("TEMP_FIXTURE_BUSY") from error
                backend.held = True
                try:
                    yield FenceGrant(lease.scope, lease.owner, lease.epoch, lease.generation)
                finally:
                    backend.held = False
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        return acquire

    def test_real_child_late_release_after_oversleep_cannot_trigger_append(self):
        clock = ManualClock()
        backend = MockStorage(clock)
        intent = Intent("SCOPE_G42", "TP1", "G42_ID", "MOCKUSDT", "G42_CLIENT")
        with tempfile.TemporaryDirectory(prefix="g42-fence-") as temp:
            with HeldChildFence(Path(temp) / "fixture.lock") as child:
                backend.acquire.side_effect = self.fixture(backend, child.path)
                def oversleep(seconds):
                    self.assertEqual(seconds, 0.005)
                    child.release()
                    clock.now = 100.5
                candidate = ProtectiveDeadlinePatchCandidate(
                    backend.callbacks(), replay_only=False, clock=clock, sleep=oversleep)
                result = candidate.run(intent, backend.current, timeout_s=0.25, poll_s=0.005)
                self.assertEqual(result.status, "DEADLINE")
                self.assertFalse(result.entry_authorized)
                self.assertFalse(result.unlock)
                self.assertIs(candidate.run(intent, backend.current), result)
                backend.acquire.assert_called_once()
                backend.append_once.assert_not_called()
                self.assertEqual(backend.rows, {})
                self.assertEqual(child.proc.returncode, 0)

    def test_old_readiness_after_child_release_does_not_define_new_call_deadline(self):
        clock = ManualClock()
        backend = MockStorage(clock)
        intent = Intent("SCOPE_G42", "TP1", "G42_ID", "MOCKUSDT", "G42_CLIENT")
        with tempfile.TemporaryDirectory(prefix="g42-fence-") as temp:
            with HeldChildFence(Path(temp) / "fixture.lock") as child:
                child.release()
                clock.now = 100.5
                backend.acquire.side_effect = self.fixture(backend, child.path)
                candidate = ProtectiveDeadlinePatchCandidate(
                    backend.callbacks(), replay_only=False, clock=clock, sleep=clock.sleep)
                result = candidate.run(intent, backend.current, timeout_s=0.25)
                self.assertEqual(result.status, "ACKED")
                self.assertFalse(result.entry_authorized)
                self.assertFalse(result.unlock)
                backend.append_once.assert_called_once_with(intent)
                self.assertEqual(backend.writes, [(intent, 100.5)])


if __name__ == "__main__":
    unittest.main()
