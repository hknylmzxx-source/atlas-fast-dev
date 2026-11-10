"""G41 saf model regresyonları: sentetik saat ve sıralanmış yarış adımları.

Gerçek sleep, flock, dosya yazımı, SDK, ağ veya emir işlemi içermez.
"""
from dataclasses import FrozenInstanceError, replace
from itertools import permutations
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deadline_fence_model_offline_g41 import (
    DeadlineFenceModel, FsyncOutcome, MockLeaseToken, MockStopSnapshot, Phase,
)


class DeadlineFenceModelOfflineG41Tests(unittest.TestCase):
    def setUp(self):
        self.model = DeadlineFenceModel(stop=MockStopSnapshot(False))
        self.accept(self.model.mock_issue_lease(
            owner="MOCK_OWNER", now_s=100.0, ttl_s=10.0))
        self.token = self.model.lease

    def assert_closed(self, decision):
        self.assertEqual(decision.entry_policy, "NO_NEW_ENTRY")
        self.assertEqual(decision.unlock_policy, "NO_UNLOCK")
        self.assertIs(decision.entry_authorized, False)
        self.assertIs(decision.unlock, False)
        self.assertIs(decision.stop_proven, False)
        if decision.status != "WAIT":
            self.assertEqual(decision.retry_policy, "NO_RETRY")

    def accept(self, transition):
        self.assert_closed(transition.decision)
        self.model = transition.model
        return transition.decision

    def begin(self, **changes):
        args = dict(request_id="r1", role="STOP", token=self.token,
                    timeout_s=0.025, poll_s=0.005, now_s=100.0)
        args.update(changes)
        return self.accept(self.model.begin(**args))

    def fence(self, now=100.0, request_id="r1"):
        return self.accept(self.model.try_fence(request_id, now_s=now))

    def mutate(self, now=100.0, request_id="r1"):
        return self.accept(self.model.atomic_mutate(request_id, now_s=now))

    def sync(self, now=100.0, request_id="r1", file=FsyncOutcome.OK,
             directory=FsyncOutcome.OK):
        return self.accept(self.model.finish_fsync(
            request_id, file_outcome=file, directory_outcome=directory, now_s=now))

    def busy(self, value, now=100.0):
        return self.accept(self.model.mock_contention(busy=value, now_s=now))

    def staged(self):
        self.assertEqual(self.begin().status, "PENDING")
        self.assertEqual(self.fence().status, "FENCED")
        self.assertEqual(self.mutate().status, "MUTATED")

    def test_normal_wal_is_durable_without_entry_unlock_or_stop_claim(self):
        initial = self.model
        self.staged()
        self.assertEqual(self.sync(100.01).status, "DURABLE")
        self.assertEqual(len(self.model.wal), 1)
        self.assertTrue(self.model.wal[0].durable)
        self.assertIsNone(self.model.fence_owner)
        self.assertIs(self.model.stop, initial.stop)
        self.assertEqual(initial.wal, ())
        self.assertEqual(initial.attempts, ())

    def test_old_ready_signal_does_not_preserve_remaining_lock_lifetime(self):
        # G40 race 1: geçmiş LOCKED, ebeveyn çağrısında kilit tutuluyor demek değil.
        self.busy(True)
        self.busy(False, 100.15)
        self.assertEqual(self.begin(now_s=100.15).status, "PENDING")
        self.assertAlmostEqual(self.model.attempts[0].deadline, 100.175)
        self.assertEqual(self.fence(100.15).status, "FENCED")
        self.assertEqual(self.mutate(100.15).status, "MUTATED")
        self.assertEqual(self.sync(100.15).status, "DURABLE")

    def test_ready_boolean_is_never_a_lease_token(self):
        self.assertEqual(self.begin(token=True).reason, "LEASE_UNTRUSTED_OR_STALE")
        self.assertEqual(self.model.wal, ())

    def test_token_copy_with_identical_fields_is_untrusted(self):
        cloned = replace(self.token)
        self.assertIsNot(cloned, self.token)
        self.assertEqual(cloned.generation, self.token.generation)
        self.assertEqual(self.begin(token=cloned).status, "REJECTED")
        self.assertEqual(self.model.wal, ())

    def test_token_from_different_authority_is_untrusted(self):
        foreign = DeadlineFenceModel().mock_issue_lease(
            owner="MOCK_OWNER", now_s=100.0, ttl_s=10.0).model.lease
        self.assertEqual(foreign.scope, self.token.scope)
        self.assertEqual(foreign.generation, self.token.generation)
        self.assertEqual(self.begin(token=foreign).reason, "LEASE_UNTRUSTED_OR_STALE")

    def test_missing_or_wrong_scope_token_is_untrusted(self):
        for token in (None, 1, object(), MockLeaseToken(
                "OTHER_SCOPE", "MOCK_OWNER", 1, 1, 110.0)):
            with self.subTest(token_type=type(token).__name__):
                self.setUp()
                self.assertEqual(self.begin(token=token).status, "REJECTED")
                self.assertEqual(self.model.wal, ())

    def test_same_owner_generation_change_invalidates_stale_ready_token(self):
        self.accept(self.model.mock_issue_lease(
            owner="MOCK_OWNER", now_s=100.0, ttl_s=10.0))
        self.assertEqual(self.model.generation, self.token.generation + 1)
        self.assertEqual(self.begin().reason, "LEASE_UNTRUSTED_OR_STALE")
        self.assertEqual(self.model.wal, ())

    def test_generation_is_rechecked_before_fence_acquisition(self):
        self.begin()
        self.accept(self.model.mock_issue_lease(
            owner="NEXT_OWNER", now_s=100.001, ttl_s=10.0))
        self.assertEqual(self.fence(100.002).reason, "LEASE_UNTRUSTED_OR_STALE")
        self.assertEqual(self.model.attempts[0].fence_attempts, 0)

    def test_generation_is_rechecked_under_fence_before_mutation(self):
        self.begin()
        self.fence()
        self.accept(self.model.mock_issue_lease(
            owner="NEXT_OWNER", now_s=100.001, ttl_s=10.0))
        self.assertEqual(self.mutate(100.002).reason, "LEASE_UNTRUSTED_OR_STALE")
        self.assertEqual(self.model.wal, ())
        self.assertIsNone(self.model.fence_owner)

    def test_lease_expiration_boundary_blocks_mutation(self):
        self.accept(self.model.mock_issue_lease(
            owner="MOCK_OWNER", now_s=100.0, ttl_s=0.01))
        self.token = self.model.lease
        self.begin()
        self.fence()
        self.assertEqual(self.mutate(100.01).reason, "LEASE_EXPIRED")
        self.assertEqual(self.model.wal, ())

    def test_positive_timeout_equality_is_expired_before_first_fence(self):
        self.begin()
        self.assertEqual(self.fence(100.025).reason, "DEADLINE_BEFORE_MUTATION")
        self.assertEqual(self.model.attempts[0].fence_attempts, 0)
        self.assertEqual(self.model.wal, ())

    def test_deadline_is_rechecked_after_fence_preemption(self):
        self.begin()
        self.fence(100.001)
        self.assertEqual(self.mutate(100.025).reason, "DEADLINE_BEFORE_MUTATION")
        self.assertEqual(self.model.wal, ())

    def test_last_representable_instant_before_deadline_can_mutate(self):
        self.begin()
        self.fence()
        now = math.nextafter(self.model.attempts[0].deadline, -math.inf)
        self.assertEqual(self.mutate(now).status, "MUTATED")
        self.assertEqual(self.sync(now).status, "DURABLE")

    def test_busy_can_retry_only_before_mutation_and_before_deadline(self):
        self.busy(True)
        self.begin()
        wait = self.fence()
        self.assertEqual(wait.status, "WAIT")
        self.assertEqual(wait.retry_policy, "WAIT_PRE_MUTATION_ONLY")
        self.assertAlmostEqual(wait.wait_s, 0.005)
        self.busy(False, 100.005)
        self.assertEqual(self.fence(100.005).status, "FENCED")
        self.assertEqual(self.mutate(100.006).status, "MUTATED")
        self.assertEqual(self.sync(100.007).status, "DURABLE")
        self.assertEqual(self.model.attempts[0].fence_attempts, 2)

    def test_scheduler_oversleep_refuses_even_when_fence_has_become_free(self):
        # G40 race 2: 5 ms bekleme önerisi, 150 ms sonra dönüş, kilit artık boş.
        self.busy(True)
        self.begin()
        self.assertEqual(self.fence().wait_s, 0.005)
        self.busy(False, 100.15)
        self.assertEqual(self.fence(100.15).status, "REJECTED")
        self.assertEqual(self.model.attempts[0].fence_attempts, 1)
        self.assertEqual(self.model.wal, ())

    def test_scheduler_oversleep_refuses_while_fence_is_still_busy(self):
        self.busy(True)
        self.begin()
        self.fence()
        self.assertEqual(self.fence(100.15).reason, "DEADLINE_BEFORE_MUTATION")
        self.assertEqual(self.model.attempts[0].fence_attempts, 1)
        self.assertEqual(self.model.wal, ())

    def test_releasing_fence_after_timeout_cannot_resurrect_same_request(self):
        self.busy(True)
        self.begin()
        self.fence()
        self.fence(100.025)
        self.busy(False, 100.03)
        for result in (self.fence(100.03), self.mutate(100.03), self.sync(100.03)):
            self.assertEqual(result.status, "REJECTED")
        self.assertEqual(self.model.wal, ())
        self.assertEqual(self.begin(now_s=100.03).reason,
                         "REQUEST_ALREADY_EXISTS_NO_RETRY")

    def test_wait_hint_is_capped_by_remaining_budget(self):
        self.busy(True)
        self.begin(timeout_s=0.003, poll_s=0.05)
        self.assertAlmostEqual(self.fence().wait_s, 0.003)

    def test_zero_timeout_allows_one_immediate_nonblocking_mutation(self):
        self.begin(timeout_s=0)
        self.assertEqual(self.fence().status, "FENCED")
        self.assertEqual(self.mutate().status, "MUTATED")
        self.assertEqual(self.sync().status, "DURABLE")
        self.assertEqual(self.model.attempts[0].fence_attempts, 1)

    def test_zero_timeout_busy_is_terminal_without_wait(self):
        self.busy(True)
        self.begin(timeout_s=0)
        result = self.fence()
        self.assertEqual(result.reason, "ZERO_BUDGET_BUSY")
        self.assertIsNone(result.wait_s)
        self.busy(False)
        self.assertEqual(self.fence().status, "REJECTED")
        self.assertEqual(self.model.attempts[0].fence_attempts, 1)
        self.assertEqual(self.model.wal, ())

    def test_zero_timeout_preempted_before_first_try_cannot_acquire(self):
        self.begin(timeout_s=0)
        self.assertEqual(self.fence(math.nextafter(100.0, math.inf)).status, "REJECTED")
        self.assertEqual(self.model.attempts[0].fence_attempts, 0)

    def test_zero_timeout_preempted_after_fence_cannot_mutate(self):
        self.begin(timeout_s=0)
        self.fence()
        self.assertEqual(self.mutate(math.nextafter(100.0, math.inf)).status, "REJECTED")
        self.assertEqual(self.model.wal, ())

    def test_fsync_file_or_directory_error_is_unknown_and_never_retried(self):
        for file, directory in ((FsyncOutcome.ERROR, FsyncOutcome.OK),
                                (FsyncOutcome.OK, FsyncOutcome.ERROR),
                                (FsyncOutcome.UNKNOWN, FsyncOutcome.OK),
                                (FsyncOutcome.OK, FsyncOutcome.UNKNOWN)):
            with self.subTest(file=file, directory=directory):
                self.setUp()
                self.staged()
                self.assertEqual(self.sync(file=file, directory=directory).status, "UNKNOWN")
                self.assertTrue(self.model.quarantined)
                self.assertIsNone(self.model.wal[0].durable)
                self.assertEqual(self.sync().status, "UNKNOWN")
                self.assertEqual(self.mutate().status, "UNKNOWN")
                self.assertEqual(self.begin(request_id="r2").status, "REJECTED")
                self.assertEqual(len(self.model.wal), 1)

    def test_untrusted_fsync_boolean_is_unknown_not_durable(self):
        self.staged()
        self.assertEqual(self.sync(file=True).status, "UNKNOWN")
        self.assertIsNone(self.model.wal[0].durable)

    def test_known_fsync_ack_after_deadline_preserves_fact_but_is_unknown(self):
        self.staged()
        result = self.sync(100.15)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.retry_policy, "NO_RETRY")
        self.assertTrue(self.model.wal[0].durable)
        self.assertEqual(len(self.model.wal), 1)

    def test_lease_expires_after_mutation_without_zero_write_claim(self):
        self.accept(self.model.mock_issue_lease(
            owner="MOCK_OWNER", now_s=100.0, ttl_s=0.01))
        self.token = self.model.lease
        self.staged()
        self.assertEqual(self.sync(100.01).status, "UNKNOWN")
        self.assertEqual(len(self.model.wal), 1)
        self.assertTrue(self.model.wal[0].durable)

    def test_duplicate_mutation_call_is_unknown_without_second_write(self):
        self.staged()
        self.assertEqual(self.mutate().status, "UNKNOWN")
        self.assertEqual(self.sync().status, "UNKNOWN")
        self.assertEqual(len(self.model.wal), 1)

    def test_acquisition_retry_after_mutation_is_unknown_without_new_attempt(self):
        self.staged()
        self.assertEqual(self.fence().status, "UNKNOWN")
        self.assertEqual(self.model.attempts[0].fence_attempts, 1)
        self.assertEqual(self.sync().status, "UNKNOWN")
        self.assertEqual(len(self.model.wal), 1)

    def test_crash_before_mutation_does_not_resume_or_regrant(self):
        self.begin()
        self.fence()
        self.accept(self.model.crash_restart(now_s=100.01))
        self.assertEqual(self.model.epoch, self.token.epoch + 1)
        self.assertIsNone(self.model.lease)
        self.assertEqual(self.model.wal, ())
        self.assertEqual(self.mutate(100.01).status, "REJECTED")
        self.assertEqual(self.begin(request_id="r2", now_s=100.01).reason,
                         "RESTART_REPLAY_ONLY")
        self.assertEqual(self.accept(self.model.mock_issue_lease(
            owner="MOCK_OWNER", now_s=100.01, ttl_s=10)).status, "REJECTED")

    def test_crash_after_mutation_preserves_unknown_no_retry(self):
        self.staged()
        self.accept(self.model.crash_restart(now_s=100.01))
        self.assertEqual(self.model.attempts[0].phase, Phase.UNKNOWN)
        self.assertIsNone(self.model.wal[0].durable)
        self.assertEqual(self.sync(100.01).status, "UNKNOWN")
        self.assertEqual(self.mutate(100.01).status, "UNKNOWN")
        self.accept(self.model.crash_restart(now_s=100.02))
        self.assertEqual(len(self.model.wal), 1)
        self.assertEqual(self.model.attempts[0].phase, Phase.UNKNOWN)

    def test_crash_after_durable_wal_is_still_replay_only(self):
        self.staged()
        self.sync()
        self.accept(self.model.crash_restart(now_s=100.01))
        self.assertTrue(self.model.wal[0].durable)
        self.assertEqual(self.begin(request_id="r2", now_s=100.01).status, "REJECTED")
        self.assertTrue(self.model.replay_only)

    def test_two_serialized_writers_compete_for_same_role_without_duplicate(self):
        self.begin()
        self.begin(request_id="r2")
        self.fence()
        self.assertEqual(self.fence(request_id="r2").status, "WAIT")
        self.mutate()
        self.sync()
        self.assertEqual(self.fence(request_id="r2").status, "FENCED")
        self.assertEqual(self.mutate(request_id="r2").reason, "DUPLICATE_WAL_NO_RETRY")
        self.assertEqual([row.request_id for row in self.model.wal], ["r1"])

    def test_expired_writer_cannot_mutate_after_later_writer_acquires(self):
        self.begin()
        self.fence()
        self.begin(request_id="r2", now_s=100.01)
        self.assertEqual(self.fence(100.01, "r2").status, "WAIT")
        self.assertEqual(self.mutate(100.025).status, "REJECTED")
        self.assertEqual(self.fence(100.026, "r2").status, "FENCED")
        self.assertEqual(self.mutate(100.026, "r2").status, "MUTATED")
        self.assertEqual(self.mutate(100.026, "r1").status, "REJECTED")
        self.assertEqual(self.sync(100.026, "r2").status, "DURABLE")
        self.assertEqual([row.request_id for row in self.model.wal], ["r2"])

    def test_external_mock_contention_cannot_steal_held_model_fence(self):
        self.begin()
        self.fence()
        self.assertEqual(self.busy(True).reason, "MODEL_FENCE_ALREADY_HELD")
        self.assertEqual(self.model.fence_owner, "r1")
        self.assertFalse(self.model.external_busy)
        self.assertEqual(self.mutate().status, "MUTATED")

    def test_tp_wal_neither_changes_stop_observation_nor_claims_stop(self):
        for observed in (True, False, None):
            with self.subTest(stop_present=observed):
                self.setUp()
                self.model = replace(self.model, stop=MockStopSnapshot(observed))
                stop = self.model.stop
                self.begin(role="TP1")
                self.fence()
                self.mutate()
                self.sync()
                self.begin(request_id="r2", role="TP2")
                self.fence(request_id="r2")
                self.mutate(request_id="r2")
                self.sync(request_id="r2")
                self.assertIs(self.model.stop, stop)
                self.assertEqual(len(self.model.wal), 2)
                self.begin(request_id="r3", role="STOP")
                self.fence(request_id="r3")
                self.mutate(request_id="r3")
                self.sync(request_id="r3")
                self.assertIs(self.model.stop, stop)
                self.assertEqual(len(self.model.wal), 3)

    def test_unknown_tp_and_restart_leave_stop_snapshot_unchanged(self):
        stop = self.model.stop
        self.begin(role="TP1")
        self.fence()
        self.mutate()
        self.sync(directory=FsyncOutcome.UNKNOWN)
        self.accept(self.model.crash_restart(now_s=100.01))
        self.assertIs(self.model.stop, stop)
        self.assertIs(self.model.stop.present, False)

    def test_mutation_without_fence_and_fsync_without_mutation_are_rejected(self):
        self.begin()
        self.assertEqual(self.mutate().reason, "MODEL_FENCE_NOT_OWNED")
        self.assertEqual(self.model.wal, ())
        self.begin(request_id="r2")
        self.fence(request_id="r2")
        self.assertEqual(self.sync(request_id="r2").reason, "FSYNC_WITHOUT_MUTATION")
        self.assertEqual(self.model.wal, ())

    def test_monotonic_regression_before_mutation_is_terminal(self):
        self.begin()
        self.fence(100.01)
        self.assertEqual(self.mutate(100.005).reason, "CLOCK_REGRESSION")
        self.assertEqual(self.model.last_now, 100.01)
        self.assertEqual(self.model.wal, ())

    def test_invalid_clock_after_mutation_is_unknown(self):
        for bad in (99.0, math.nan, math.inf, True, "100"):
            with self.subTest(bad=bad):
                self.setUp()
                self.staged()
                self.assertEqual(self.sync(bad).status, "UNKNOWN")
                self.assertEqual(len(self.model.wal), 1)

    def test_invalid_clock_before_mutation_never_reaches_wal(self):
        for bad in (math.nan, math.inf, -1, True, "100", 10 ** 1000):
            with self.subTest(bad_type=type(bad).__name__):
                self.setUp()
                self.begin()
                self.fence()
                self.assertEqual(self.mutate(bad).status, "REJECTED")
                self.assertEqual(self.model.wal, ())

    def test_invalid_wait_bounds_fail_before_any_fence_or_wal(self):
        for field, bad in (("timeout_s", -1), ("timeout_s", True),
                           ("timeout_s", math.nan), ("timeout_s", math.inf),
                           ("timeout_s", 0.501), ("poll_s", 0),
                           ("poll_s", True), ("poll_s", 0.051),
                           ("poll_s", math.nan), ("poll_s", "0.01")):
            with self.subTest(field=field, bad=bad):
                self.setUp()
                self.assertEqual(self.begin(**{field: bad}).status, "REJECTED")
                self.assertEqual(self.model.attempts, ())
                self.assertEqual(self.model.wal, ())

    def test_nonrepresentable_positive_budget_is_rejected(self):
        self.model = DeadlineFenceModel()
        self.accept(self.model.mock_issue_lease(owner="MOCK_OWNER", now_s=1e20, ttl_s=1e6))
        self.token = self.model.lease
        self.assertEqual(self.begin(now_s=1e20).reason, "WAIT_BOUND_INVALID")
        self.assertEqual(self.model.wal, ())

    def test_generation_and_deadline_interleavings_all_refuse_stale_mutation(self):
        # Üç bağımsız olayın altı sıralaması; mutation daima son doğrulamayı yapar.
        for events in permutations(("rotate", "expire", "free")):
            with self.subTest(events=events):
                self.setUp()
                self.busy(True)
                self.begin()
                self.fence()
                now = 100.0
                for event in events:
                    if event == "rotate":
                        self.accept(self.model.mock_issue_lease(
                            owner="NEXT_OWNER", now_s=now, ttl_s=10))
                    elif event == "expire":
                        now = 100.15
                    else:
                        self.busy(False, now)
                self.assertEqual(self.fence(now).status, "REJECTED")
                self.assertEqual(self.model.wal, ())

    def test_immutable_snapshot_forks_are_predictions_not_cross_process_locking(self):
        # Sınırın çalışır örneği: eski snapshot iki harness'e verilirse fiziksel
        # arbitration yoktur. Yalnız tek sıralı state zinciri model sözleşmesidir.
        original = self.model
        for request_id in ("fork_a", "fork_b"):
            self.model = original
            self.begin(request_id=request_id)
            self.fence(request_id=request_id)
            self.assertEqual(self.mutate(request_id=request_id).status, "MUTATED")
            self.assertEqual(len(self.model.wal), 1)
        self.assertEqual(original.wal, ())

    def test_decision_and_stop_fields_cannot_be_changed_through_normal_api(self):
        decision = self.begin()
        with self.assertRaises(FrozenInstanceError):
            decision.unlock = True
        # dataclasses.replace, Python 3.13'te TypeError; eski sürümde ValueError.
        with self.assertRaises((TypeError, ValueError)):
            replace(decision, entry_policy="ALLOW")
        with self.assertRaises(FrozenInstanceError):
            self.model.stop.present = True


if __name__ == "__main__":
    unittest.main()
