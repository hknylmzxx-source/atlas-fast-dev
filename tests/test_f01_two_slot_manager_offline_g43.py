"""G43 hedefli testler: sentetik saat/veri, ortak bellek store'u; SDK/ağ yok."""
import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
from decimal import Decimal, localcontext
from pathlib import Path
import sys
from threading import Barrier
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import f01_two_slot_manager_offline_g43 as g43


class TwoSlotManagerG43Tests(unittest.TestCase):
    def setUp(self):
        self.now = 1_000
        self.configure()

    def configure(self, **changes):
        defaults = dict(global_risk="20", slot_risk="12", global_margin="100",
                        slot_margin="70", global_leverage=8, slot_leverage=10,
                        funds=(g43.MockFunds("ACCOUNT_A", "100"),
                               g43.MockFunds("ACCOUNT_B", "100")))
        defaults.update(changes)
        self.limits = g43.MockLimits(**defaults)
        self.store = g43.MockSlotStore(self.limits)
        self.manager = g43.TwoSlotManagerOffline(self.store)
        self.requests = {}
        self.intents = {}
        self.leases = {}

    def closed(self, result):
        self.assertIs(result.entry_authorized, False)
        self.assertIs(result.unlock, False)
        self.assertEqual(result.retry_policy, "NO_RETRY")
        self.assertEqual(result.mode, "OFFLINE_MOCK_ONLY")
        return result

    def request(self, number=1, **changes):
        defaults = dict(slot_id=f"slot-{number}",
                        binding=g43.Binding(f"scope-{number}", "ACCOUNT_A",
                                            f"MOCK{number}USDT", "LONG"),
                        quantity="2", price="100", risk="8", leverage=5)
        defaults.update(changes)
        return g43.SlotRequest(**defaults)

    def entry(self, request, **changes):
        defaults = dict(binding=request.binding, role="ENTRY",
                        client_id=request.slot_id + "-entry-client",
                        order_id=request.slot_id + "-entry-order",
                        order_kind="NORMAL", quantity=request.quantity, reduce_only=False)
        defaults.update(changes)
        return g43.OrderIntent(**defaults)

    def lease(self, request, *, manager=None, **changes):
        defaults = dict(owner="mock-process-A", now_ms=self.now, ttl_ms=10_000)
        defaults.update(changes)
        decision = self.closed((manager or self.manager).mock_issue_lease(
            request.slot_id, request.binding, **defaults))
        self.assertEqual(decision.status, "MOCK_LEASE")
        self.leases[request.slot_id] = decision.lease
        return decision.lease

    def reserve(self, number=1, *, request=None, entry=None, lease=None):
        request = request or self.request(number)
        entry = entry or self.entry(request)
        lease = lease or self.lease(request)
        result = self.closed(self.manager.reserve_slot(request, entry, lease, now_ms=self.now))
        if result.status == "RESERVED":
            self.requests[request.slot_id] = request
            self.intents[(request.slot_id, "ENTRY")] = entry
        return result

    def protective(self, role="STOP", slot_id="slot-1", **changes):
        request = self.requests[slot_id]
        defaults = dict(binding=request.binding, role=role,
                        client_id=slot_id + "-" + role + "-client",
                        order_id=slot_id + "-" + role + "-order",
                        order_kind="ALGO", quantity=request.quantity if role == "STOP" else "1",
                        reduce_only=True)
        defaults.update(changes)
        return g43.OrderIntent(**defaults)

    def add(self, role="STOP", slot_id="slot-1", **changes):
        intent = self.protective(role, slot_id, **changes)
        result = self.closed(self.manager.reserve_protective_intent(
            slot_id, intent, self.leases[slot_id], now_ms=self.now))
        if result.status == "RESERVED":
            self.intents[(slot_id, role)] = intent
        return result

    def report(self, *, now=None, manager=None):
        result = self.closed((manager or self.manager).report(
            now_ms=self.now if now is None else now))
        for view in result.slots:
            self.closed(view)
        self.assertLessEqual(result.active_slots, 2)
        return result

    def view(self, slot_id="slot-1"):
        return next(v for v in self.report().slots if v.record.request.slot_id == slot_id)

    def read(self, role="ENTRY", status="NEW", filled="0", slot_id="slot-1", **changes):
        self.now += 10
        record = self.view(slot_id).record.find(role)
        defaults = dict(intent=self.intents[(slot_id, role)], status=status,
                        cumulative_filled=filled, observed_at_ms=self.now,
                        sequence=1 if record.last_observation is None else
                        record.last_observation.sequence + 1)
        defaults.update(changes)
        return self.closed(self.manager.observe(slot_id, g43.MockObservation(**defaults),
                                               now_ms=self.now))

    def codes(self, origin=None, slot_id=None):
        return {a.code for a in self.report().alarms
                if (origin is None or a.origin == origin)
                and (slot_id is None or a.slot_id == slot_id)}

    def test_two_slots_reserve_real_model_records_and_third_is_denied(self):
        self.assertEqual(self.reserve().status, "RESERVED")
        self.assertEqual(self.reserve(2).status, "RESERVED")
        self.assertEqual(self.reserve(3).reason, "THIRD_ACTIVE_SLOT")
        report = self.report()
        self.assertEqual((report.active_slots, len(report.slots)), (2, 2))
        self.assertEqual(Decimal(report.reserved_margin), 80)
        self.assertEqual(Decimal(report.reserved_risk), 16)

    def test_limit_of_two_is_global_across_accounts_and_scopes(self):
        self.reserve()
        second = self.request(2, binding=g43.Binding("scope-B", "ACCOUNT_B", "OTHERUSDT", "SHORT"))
        self.assertEqual(self.reserve(request=second).status, "RESERVED")
        self.assertEqual(self.reserve(3).reason, "THIRD_ACTIVE_SLOT")

    def test_duplicate_slot_does_not_double_charge_or_append_entry(self):
        request = self.request()
        self.reserve(request=request)
        before = self.report()
        for _ in range(3):
            result = self.manager.reserve_slot(request, self.entry(request), self.leases[request.slot_id],
                                               now_ms=self.now)
            self.assertEqual(self.closed(result).reason, "DUPLICATE_SLOT_NO_RETRY")
        self.assertEqual(self.report(), before)

    def test_same_slot_changed_request_is_replay_conflict(self):
        self.reserve()
        changed = replace(self.requests["slot-1"], risk="9")
        result = self.manager.reserve_slot(changed, self.entry(changed), self.leases["slot-1"],
                                           now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "SLOT_REPLAY_CONFLICT")
        self.assertEqual(Decimal(self.report().reserved_risk), 8)

    def test_entry_binding_and_quantity_must_match_slot(self):
        request = self.request()
        lease = self.lease(request)
        for intent in (replace(self.entry(request), quantity="1"),
                       replace(self.entry(request), binding=self.request(2).binding),
                       self.protective_from_request(request)):
            with self.subTest(intent=intent.role):
                result = self.manager.reserve_slot(request, intent, lease, now_ms=self.now)
                self.assertEqual(self.closed(result).reason, "ENTRY_BINDING_OR_QUANTITY_MISMATCH")
        self.assertEqual(self.report().active_slots, 0)

    @staticmethod
    def protective_from_request(request):
        return g43.OrderIntent(request.binding, "STOP", "stop-c", "stop-o", "ALGO", "2", True)

    def test_same_exposure_cannot_be_duplicated_under_another_slot(self):
        self.reserve()
        duplicate = self.request(2, binding=self.requests["slot-1"].binding)
        self.assertEqual(self.reserve(request=duplicate).reason, "EXPOSURE_BINDING_ALREADY_ACTIVE")

    def test_same_physical_exposure_in_another_scope_is_denied(self):
        self.reserve()
        binding = replace(self.requests["slot-1"].binding, scope="other-scope")
        self.assertEqual(self.reserve(request=self.request(2, binding=binding)).reason,
                         "EXPOSURE_BINDING_ALREADY_ACTIVE")

    def test_same_symbol_long_short_are_separate_gross_reservations(self):
        self.reserve()
        binding = replace(self.requests["slot-1"].binding, position_side="SHORT")
        self.assertEqual(self.reserve(request=self.request(2, binding=binding)).status, "RESERVED")
        self.assertEqual(Decimal(self.report().gross_notional), 400)
        self.assertEqual(Decimal(self.report().reserved_margin), 80)

    def test_both_side_cannot_overlap_existing_long_or_short(self):
        self.reserve()
        binding = replace(self.requests["slot-1"].binding, position_side="BOTH")
        self.assertEqual(self.reserve(request=self.request(2, binding=binding)).reason,
                         "EXPOSURE_BINDING_ALREADY_ACTIVE")

    def test_slot_risk_limit(self):
        self.assertEqual(self.reserve(request=self.request(risk="12.00000001")).reason,
                         "SLOT_RISK_LIMIT")

    def test_global_risk_limit_counts_both_slots(self):
        self.configure(global_risk="15")
        self.reserve()
        self.assertEqual(self.reserve(2).reason, "GLOBAL_RISK_LIMIT")

    def test_slot_margin_limit(self):
        self.assertEqual(self.reserve(request=self.request(price="176")).reason, "SLOT_MARGIN_LIMIT")

    def test_global_margin_limit_counts_both_slots(self):
        self.configure(global_margin="79.99999999")
        self.reserve()
        self.assertEqual(self.reserve(2).reason, "GLOBAL_MARGIN_LIMIT")

    def test_slot_leverage_limit(self):
        self.configure(global_leverage=12, slot_leverage=5)
        self.assertEqual(self.reserve(request=self.request(leverage=6)).reason, "SLOT_LEVERAGE_LIMIT")

    def test_global_leverage_limit(self):
        self.assertEqual(self.reserve(request=self.request(leverage=9)).reason, "GLOBAL_LEVERAGE_LIMIT")

    def test_exact_mock_risk_margin_and_leverage_boundaries(self):
        self.configure(global_risk="16", slot_risk="8", global_margin="80", slot_margin="40",
                       global_leverage=5, slot_leverage=5,
                       funds=(g43.MockFunds("ACCOUNT_A", "80"),))
        self.assertEqual(self.reserve().status, "RESERVED")
        self.assertEqual(self.reserve(2).status, "RESERVED")

    def test_margin_rounds_up_instead_of_under_reserving(self):
        self.configure(global_margin="1", slot_margin="1",
                       funds=(g43.MockFunds("ACCOUNT_A", "0.33333333"),))
        request = self.request(quantity="1", price="1", leverage=3)
        self.assertEqual(request.margin, Decimal("0.33333334"))
        self.assertEqual(self.reserve(request=request).reason, "ACCOUNT_FUNDS_EXHAUSTED")

    def test_same_account_funds_are_shared_across_scopes(self):
        self.configure(funds=(g43.MockFunds("ACCOUNT_A", "79"),))
        self.reserve()
        self.assertEqual(self.reserve(2).reason, "ACCOUNT_FUNDS_EXHAUSTED")

    def test_account_funds_cannot_be_borrowed_from_another_account(self):
        self.configure(funds=(g43.MockFunds("ACCOUNT_A", "39"),
                              g43.MockFunds("ACCOUNT_B", "1000")))
        self.assertEqual(self.reserve().reason, "ACCOUNT_FUNDS_EXHAUSTED")
        self.assertEqual(self.report().active_slots, 0)

    def test_unknown_account_is_denied(self):
        binding = replace(self.request().binding, account="UNKNOWN_ACCOUNT")
        self.assertEqual(self.reserve(request=self.request(binding=binding)).reason, "ACCOUNT_FUNDS_UNKNOWN")

    def test_funds_exhaustion_does_not_leave_partial_slot_or_intent(self):
        self.configure(funds=(g43.MockFunds("ACCOUNT_A", "0"),))
        self.reserve()
        report = self.report()
        self.assertEqual(report.slots, ())
        self.assertEqual(Decimal(report.reserved_risk), 0)
        self.assertEqual(Decimal(report.reserved_margin), 0)

    def test_generation_rotation_fences_old_token_at_reservation(self):
        request = self.request()
        old = self.lease(request)
        new = self.lease(request, owner="mock-process-B")
        self.assertEqual(new.generation, old.generation + 1)
        result = self.manager.reserve_slot(request, self.entry(request), old, now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "LEASE_UNTRUSTED_OR_STALE")
        self.assertEqual(self.reserve(request=request, lease=new).status, "RESERVED")

    def test_copied_and_foreign_store_tokens_are_not_leases(self):
        request = self.request()
        token = self.lease(request)
        other = g43.TwoSlotManagerOffline(g43.MockSlotStore(self.limits))
        foreign = other.mock_issue_lease(request.slot_id, request.binding, owner="mock-process-A",
                                         now_ms=self.now, ttl_ms=10_000).lease
        for untrusted in (replace(token), foreign, None, {"generation": token.generation}):
            with self.subTest(token_type=type(untrusted).__name__):
                result = self.manager.reserve_slot(request, self.entry(request), untrusted, now_ms=self.now)
                self.assertEqual(self.closed(result).reason, "LEASE_UNTRUSTED_OR_STALE")
        self.assertEqual(self.report().active_slots, 0)

    def test_lease_expiry_is_exclusive(self):
        request = self.request()
        token = self.lease(request, ttl_ms=1)
        self.now += 1
        self.assertEqual(self.reserve(request=request, lease=token).reason, "LEASE_EXPIRED")

    def test_slot_binding_cannot_be_changed_by_lease_rotation(self):
        request = self.request()
        self.lease(request)
        decision = self.manager.mock_issue_lease(request.slot_id, self.request(2).binding,
                                                owner="B", now_ms=self.now, ttl_ms=1_000)
        self.assertEqual(self.closed(decision).reason, "SLOT_BINDING_CONFLICT")

    def test_lease_for_other_slot_cannot_reserve_or_append(self):
        token = self.lease(self.request(2))
        request = self.request()
        result = self.manager.reserve_slot(request, self.entry(request), token, now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "LEASE_UNTRUSTED_OR_STALE")
        self.reserve()
        result = self.manager.reserve_protective_intent("slot-1", self.protective(), token, now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "LEASE_UNTRUSTED_OR_STALE")

    def test_generation_rotation_fences_protective_reservation(self):
        self.reserve()
        old = self.leases["slot-1"]
        self.lease(self.requests["slot-1"])
        result = self.manager.reserve_protective_intent("slot-1", self.protective(), old, now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "LEASE_UNTRUSTED_OR_STALE")
        self.assertIsNone(self.view().record.find("STOP"))

    def test_two_threads_same_slot_reserve_exactly_one_intent(self):
        request = self.request()
        token = self.lease(request)
        barrier = Barrier(2)
        facades = (self.manager, g43.TwoSlotManagerOffline(self.store))
        def run(manager):
            barrier.wait(timeout=3)
            return manager.reserve_slot(request, self.entry(request), token, now_ms=self.now)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, facades))
        self.assertEqual(sorted(self.closed(r).status for r in results), ["DENY", "RESERVED"])
        self.assertEqual({r.reason for r in results},
                         {"SYNTHETIC_ENTRY_INTENT_ONLY", "DUPLICATE_SLOT_NO_RETRY"})
        report = self.report()
        self.assertEqual((report.active_slots, len(report.slots[0].record.intents)), (1, 1))
        self.assertEqual(Decimal(report.reserved_margin), 40)

    def test_two_threads_compete_for_final_slot_atomically(self):
        self.reserve()
        requests = (self.request(2), self.request(3))
        tokens = tuple(self.lease(r) for r in requests)
        barrier = Barrier(2)
        def run(index):
            barrier.wait(timeout=3)
            request = requests[index]
            return g43.TwoSlotManagerOffline(self.store).reserve_slot(
                request, self.entry(request), tokens[index], now_ms=self.now)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))
        self.assertEqual(sorted(self.closed(r).status for r in results), ["DENY", "RESERVED"])
        self.assertIn("THIRD_ACTIVE_SLOT", {r.reason for r in results})
        self.assertEqual(self.report().active_slots, 2)

    def test_two_threads_cannot_overspend_last_account_funds(self):
        self.configure(funds=(g43.MockFunds("ACCOUNT_A", "60"),))
        requests = (self.request(1), self.request(2))
        tokens = tuple(self.lease(r) for r in requests)
        barrier = Barrier(2)
        def run(index):
            barrier.wait(timeout=3)
            request = requests[index]
            return g43.TwoSlotManagerOffline(self.store).reserve_slot(
                request, self.entry(request), tokens[index], now_ms=self.now)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))
        self.assertEqual(sorted(self.closed(r).status for r in results), ["DENY", "RESERVED"])
        self.assertIn("ACCOUNT_FUNDS_EXHAUSTED", {r.reason for r in results})
        self.assertEqual(Decimal(self.report().reserved_margin), 40)

    def test_two_threads_distinct_slots_can_both_reserve_without_contamination(self):
        requests = (self.request(1), self.request(2))
        tokens = tuple(self.lease(r) for r in requests)
        barrier = Barrier(2)
        def run(index):
            barrier.wait(timeout=3)
            request = requests[index]
            return g43.TwoSlotManagerOffline(self.store).reserve_slot(
                request, self.entry(request), tokens[index], now_ms=self.now)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))
        self.assertEqual([self.closed(r).status for r in results], ["RESERVED", "RESERVED"])
        report = self.report()
        self.assertEqual({v.record.request for v in report.slots}, set(requests))
        self.assertEqual(Decimal(report.reserved_margin), 80)
        self.assertTrue(all(len(v.record.intents) == 1 for v in report.slots))

    def test_unknown_in_one_slot_does_not_freeze_other_slot_or_rotate_its_lease(self):
        self.reserve()
        self.reserve(2)
        token_a = self.leases["slot-1"]
        self.read(status="FILLED", filled="2", slot_id="slot-2")
        self.read(status="UNKNOWN")
        self.assertEqual(self.add(slot_id="slot-2").status, "RESERVED")
        self.read("STOP", slot_id="slot-2")
        self.lease(self.requests["slot-1"])
        self.assertEqual(self.leases["slot-1"].generation, token_a.generation + 1)
        self.assertEqual(self.add("TP1", slot_id="slot-2").status, "RESERVED")
        self.assertEqual(self.view("slot-2").stop_state, "MOCK_STOP_OBSERVED")
        self.assertTrue(self.view().record.find("ENTRY").uncertain)
        self.assertFalse(self.view("slot-2").record.find("ENTRY").uncertain)
        self.assertEqual(Decimal(self.report().reserved_margin), 80)

    def test_two_simulated_processes_share_authority_and_restart_gate(self):
        process_a = self.manager
        process_b = g43.TwoSlotManagerOffline(self.store)
        request = self.request()
        old = self.lease(request, manager=process_a)
        new = self.lease(request, manager=process_b, owner="mock-process-B")
        self.assertEqual(process_a.reserve_slot(request, self.entry(request), old,
                                               now_ms=self.now).reason, "LEASE_UNTRUSTED_OR_STALE")
        self.assertEqual(process_b.reserve_slot(request, self.entry(request), new,
                                               now_ms=self.now).status, "RESERVED")
        self.closed(process_b.mock_restart(now_ms=self.now))
        self.assertEqual(self.closed(process_a.reserve_slot(request, self.entry(request), old,
                                                            now_ms=self.now)).reason,
                         "RESTART_MANUAL_REVIEW")
        self.assertEqual(self.report(manager=process_a), self.report(manager=process_b))

    def test_entry_stop_and_two_tp_intents_have_separate_observations(self):
        self.reserve()
        for role in ("STOP", "TP1", "TP2"):
            self.assertEqual(self.add(role).status, "RESERVED")
        self.read(status="PARTIAL", filled="1")
        self.read("STOP")
        self.read("TP1")
        records = {r.intent.role: r for r in self.view().record.intents}
        self.assertEqual(records["ENTRY"].known_observation.status, "PARTIAL")
        self.assertEqual(records["STOP"].known_observation.status, "NEW")
        self.assertEqual(records["TP1"].known_observation.status, "NEW")
        self.assertIsNone(records["TP2"].known_observation)

    def test_partial_entry_keeps_full_budget_and_missing_stop_is_danger(self):
        self.reserve()
        self.read(status="PARTIAL", filled="0.25")
        self.assertIn("STOP_MISSING_DANGER", self.codes("STOP"))
        self.assertEqual(self.report().status, "DANGER")
        self.assertEqual(Decimal(self.report().reserved_margin), 40)
        self.assertEqual(Decimal(self.report().reserved_risk), 8)

    def test_unobserved_entry_has_possible_exposure_and_missing_stop_danger(self):
        self.reserve()
        self.assertIn("STOP_MISSING_DANGER", self.codes("STOP"))
        self.assertIn("ENTRY_UNKNOWN_OR_STALE_NO_RETRY", self.codes("ENTRY"))

    def test_stop_intent_without_observation_is_not_protection(self):
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add()
        self.assertEqual(self.view().stop_state, "STOP_MISSING_DANGER")

    def test_fresh_algo_stop_observation_is_mock_coverage_only(self):
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add()
        self.assertEqual(self.read("STOP").status, "OBSERVED")
        self.assertEqual(self.view().stop_state, "MOCK_STOP_OBSERVED")
        self.assertEqual(self.codes("STOP"), set())
        self.closed(self.report())

    def test_stop_canceled_or_missing_or_unknown_keeps_danger(self):
        for status in ("CANCELED", "EXPIRED", "REJECTED", "MISSING", "UNKNOWN"):
            with self.subTest(status=status):
                self.configure()
                self.reserve()
                self.read(status="FILLED", filled="2")
                self.add()
                self.read("STOP", status)
                self.assertTrue(self.view().stop_state.endswith("DANGER"))
                self.assertTrue(self.codes("STOP"))

    def test_stale_stop_report_rechecks_age_without_new_observation(self):
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add()
        self.read("STOP")
        self.now += self.limits.max_observation_age_ms + 1
        self.assertEqual(self.view().stop_state, "STOP_STALE_DANGER")

    def test_stop_and_tp_alarms_are_independent(self):
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add("TP1")
        self.read("TP1", "UNKNOWN")
        self.assertTrue(self.codes("STOP"))
        self.assertIn("TP_UNKNOWN_NO_RETRY", self.codes("TP1"))
        # STOP intent de belirsizlik öncesinde rezerve edilmiş olmalıdır.
        self.assertEqual(self.add("STOP").reason, "SLOT_UNCERTAIN_NO_RETRY")
        self.configure()
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add("STOP")
        self.add("TP1")
        self.read("TP1", "UNKNOWN")
        self.read("STOP")
        self.assertEqual(self.codes("STOP"), set())
        self.assertIn("TP_UNKNOWN_NO_RETRY", self.codes("TP1"))
        self.read("STOP", "CANCELED")
        self.assertTrue(self.codes("STOP"))
        self.assertIn("TP_UNKNOWN_NO_RETRY", self.codes("TP1"))

    def test_tp_filled_never_manufactures_stop_evidence(self):
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add("TP1")
        self.read("TP1", "FILLED", "1")
        self.assertEqual(Decimal(self.view().exposure), 1)
        self.assertEqual(self.view().stop_state, "STOP_MISSING_DANGER")
        self.assertIsNone(self.view().record.find("STOP"))

    def test_unknown_tp_stays_no_retry_after_fresh_reconciliation(self):
        self.reserve()
        self.add("TP1")
        self.read("TP1", "UNKNOWN")
        before = self.view().record
        for _ in range(2):
            self.assertEqual(self.add("TP1").reason, "DUPLICATE_INTENT_NO_RETRY")
        self.assertEqual(self.view().record, before)
        self.assertEqual(self.read("TP1").status, "MANUAL_REVIEW")
        self.assertIn("TP_UNKNOWN_NO_RETRY", self.codes("TP1"))
        self.assertEqual(self.add("TP2").reason, "SLOT_UNCERTAIN_NO_RETRY")

    def test_partial_tp_preserves_full_risk_and_margin_and_own_alarm(self):
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add("TP1")
        self.read("TP1", "PARTIAL", "0.5")
        self.assertEqual(Decimal(self.view().exposure), Decimal("1.5"))
        self.assertIn("TP_PARTIAL", self.codes("TP1"))
        self.assertEqual(Decimal(self.report().reserved_margin), 40)
        self.assertEqual(Decimal(self.report().reserved_risk), 8)

    def test_partial_stop_fill_and_tp_fill_are_not_netted_between_slots(self):
        self.reserve()
        self.reserve(2)
        self.read(status="FILLED", filled="2")
        self.read(status="FILLED", filled="2", slot_id="slot-2")
        self.add("STOP")
        self.read("STOP", "PARTIAL", "0.5")
        self.add("TP1")
        self.read("TP1", "FILLED", "1")
        self.assertEqual(Decimal(self.view().exposure), Decimal("0.5"))
        self.assertEqual(Decimal(self.view("slot-2").exposure), 2)
        self.assertEqual(self.view("slot-2").stop_state, "STOP_MISSING_DANGER")

    def test_protective_quantities_and_reduce_only_are_enforced(self):
        self.reserve()
        self.assertEqual(self.add("STOP", quantity="1").reason, "PROTECTIVE_QUANTITY_LIMIT")
        self.assertEqual(self.add("TP1", quantity="2").status, "RESERVED")
        self.assertEqual(self.add("TP2").reason, "PROTECTIVE_QUANTITY_LIMIT")
        with self.assertRaisesRegex(g43.ModelInputError, "INVALID_ORDER_INTENT"):
            self.protective(reduce_only=False)

    def test_duplicate_protective_and_changed_identity_cannot_retry(self):
        self.reserve()
        self.add()
        before = self.view().record
        self.assertEqual(self.add().reason, "DUPLICATE_INTENT_NO_RETRY")
        self.assertEqual(self.add(order_id="new-order").reason, "ROLE_INTENT_CONFLICT_NO_RETRY")
        self.assertEqual(self.view().record, before)

    def test_client_ids_are_unique_across_slots_and_roles_in_same_account(self):
        self.reserve()
        client_id = self.intents[("slot-1", "ENTRY")].client_id
        request = self.request(2)
        self.assertEqual(self.reserve(request=request, entry=self.entry(request, client_id=client_id)).reason,
                         "CLIENT_ID_REPLAY_OR_CONFLICT")
        self.assertEqual(self.add(client_id=client_id).reason, "CLIENT_ID_REPLAY_OR_CONFLICT")

    def test_order_ids_are_unique_in_account_and_order_kind(self):
        self.reserve()
        order_id = self.intents[("slot-1", "ENTRY")].order_id
        request = self.request(2)
        self.assertEqual(self.reserve(request=request, entry=self.entry(request, order_id=order_id)).reason,
                         "ORDER_ID_REPLAY_OR_CONFLICT")
        # Aynı sayısal/metinsel kimlik ALGO ad alanında farklı bir mock emirdir.
        self.assertEqual(self.add(order_id=order_id, order_kind="ALGO").status, "RESERVED")
        self.assertEqual(self.add("TP1", order_id=order_id).reason, "ORDER_ID_REPLAY_OR_CONFLICT")

    def test_other_account_may_use_same_client_and_order_ids_without_contamination(self):
        self.reserve()
        first = self.intents[("slot-1", "ENTRY")]
        binding = replace(self.request(2).binding, account="ACCOUNT_B")
        request = self.request(2, binding=binding)
        entry = self.entry(request, client_id=first.client_id, order_id=first.order_id)
        self.assertEqual(self.reserve(request=request, entry=entry).status, "RESERVED")
        self.read(status="PARTIAL", filled="1", slot_id="slot-2")
        self.assertEqual(self.view().record.find("ENTRY").filled, 0)
        self.assertEqual(self.view("slot-2").record.find("ENTRY").filled, 1)

    def test_observation_checks_every_identity_dimension(self):
        self.reserve()
        original = self.intents[("slot-1", "ENTRY")]
        bindings = [replace(original.binding, **{field: value}) for field, value in (
            ("scope", "different"), ("account", "ACCOUNT_B"),
            ("symbol", "OTHERUSDT"), ("position_side", "SHORT"))]
        intents = [replace(original, binding=b) for b in bindings] + [
            replace(original, client_id="other-client"), replace(original, order_id="other-order"),
            replace(original, order_kind="ALGO"), replace(original, quantity="1"),
            self.protective_from_request(self.requests["slot-1"])]
        before = self.view().record
        for intent in intents:
            with self.subTest(intent=intent):
                self.assertEqual(self.read(intent=intent).reason, "EXACT_INTENT_BINDING_REQUIRED")
                self.assertEqual(self.view().record, before)

    def test_other_slot_observation_does_not_mutate_either_slot(self):
        self.reserve()
        self.reserve(2)
        before = tuple(v.record for v in self.report().slots)
        result = self.read(intent=self.intents[("slot-2", "ENTRY")], status="FILLED", filled="2")
        self.assertEqual(result.reason, "EXACT_INTENT_BINDING_REQUIRED")
        self.assertEqual(tuple(v.record for v in self.report().slots), before)

    def test_future_stale_and_pre_intent_observations_are_rejected(self):
        for timestamp, reason in ((2_000, "FUTURE_OBSERVATION"),
                                  (0, "STALE_OBSERVATION"), (999, "PRE_INTENT_OBSERVATION")):
            with self.subTest(timestamp=timestamp):
                self.configure()
                self.reserve()
                self.assertEqual(self.read(observed_at_ms=timestamp).reason, reason)
                self.assertIsNone(self.view().record.find("ENTRY").last_observation)

    def test_observation_exact_age_boundary_is_accepted(self):
        self.reserve()
        timestamp = self.now
        self.now += self.limits.max_observation_age_ms - 10
        self.assertEqual(self.read(observed_at_ms=timestamp).status, "OBSERVED")

    def test_sequence_and_timestamp_are_monotone_per_intent(self):
        self.reserve()
        self.read(sequence=10)
        before = self.view().record.find("ENTRY")
        self.assertEqual(self.read(sequence=9).reason, "OUT_OF_ORDER_OR_CONFLICTING_OBSERVATION")
        self.assertEqual(self.read(sequence=11, observed_at_ms=1_005).reason,
                         "OUT_OF_ORDER_OR_CONFLICTING_OBSERVATION")
        self.assertEqual(self.view().record.find("ENTRY"), before)

    def test_identical_observation_replay_never_reapplies_fill(self):
        self.reserve()
        self.read(status="PARTIAL", filled="1")
        record = self.view().record.find("ENTRY")
        result = self.manager.observe("slot-1", record.last_observation, now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "DUPLICATE_OBSERVATION_NO_RETRY")
        self.assertEqual(self.view().record.find("ENTRY"), record)

    def test_same_sequence_with_changed_data_is_conflict(self):
        self.reserve()
        self.read(sequence=7)
        self.assertEqual(self.read(sequence=7, status="PARTIAL", filled="1").reason,
                         "OUT_OF_ORDER_OR_CONFLICTING_OBSERVATION")
        self.assertEqual(self.view().record.find("ENTRY").filled, 0)

    def test_fill_regression_and_overfill_rejected(self):
        self.reserve()
        self.read(status="PARTIAL", filled="1")
        for filled in ("0.5", "2.1"):
            with self.subTest(filled=filled):
                self.assertEqual(self.read(status="PARTIAL", filled=filled).reason,
                                 "FILL_REGRESSION_OR_OVERFILL")
        self.assertEqual(self.view().record.find("ENTRY").filled, 1)

    def test_invalid_status_fill_combinations_are_rejected(self):
        self.reserve()
        for status, filled in (("NEW", "1"), ("PARTIAL", "0"), ("PARTIAL", "2"),
                               ("FILLED", "1"), ("REJECTED", "1")):
            with self.subTest(status=status, filled=filled):
                self.assertEqual(self.read(status=status, filled=filled).reason, "STATUS_FILL_CONFLICT")
        self.assertEqual(self.view().record.find("ENTRY").filled, 0)

    def test_terminal_status_cannot_return_to_open_even_with_later_sequence(self):
        self.reserve()
        self.read(status="CANCELED")
        self.assertEqual(self.read().reason, "TERMINAL_STATE_CONFLICT")
        self.assertEqual(self.read(status="FILLED", filled="2").reason, "TERMINAL_STATE_CONFLICT")

    def test_unknown_cannot_introduce_unconfirmed_fill(self):
        self.reserve()
        self.assertEqual(self.read(status="UNKNOWN", filled="1").reason, "UNCERTAIN_FILL_NOT_PROOF")
        self.read(status="PARTIAL", filled="1")
        self.assertEqual(self.read(status="UNKNOWN", filled="1").status, "MANUAL_REVIEW")
        self.assertEqual(self.view().record.find("ENTRY").filled, 1)

    def test_tp_or_stop_exit_fill_cannot_exceed_known_entry(self):
        self.reserve()
        self.add("STOP")
        self.add("TP1")
        self.assertEqual(self.read("TP1", "FILLED", "1").reason, "EXIT_FILLS_EXCEED_KNOWN_ENTRY")
        self.read(status="PARTIAL", filled="1")
        self.read("TP1", "FILLED", "1")
        self.assertEqual(self.read("STOP", "PARTIAL", "0.5").reason,
                         "EXIT_FILLS_EXCEED_KNOWN_ENTRY")
        self.assertEqual(Decimal(self.view().exposure), 0)

    def test_partial_fill_then_cancel_does_not_release_budget_or_hide_exposure(self):
        self.reserve()
        self.read(status="PARTIAL", filled="1")
        self.read(status="CANCELED", filled="1")
        result = self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "EXPOSURE_REMAINS")
        self.assertEqual(Decimal(self.report().reserved_margin), 40)
        self.assertTrue(self.codes("STOP"))

    def test_active_algo_order_blocks_retirement_after_tp_flat(self):
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add("STOP")
        self.add("TP1", quantity="2")
        self.read("STOP")
        self.read("TP1", "FILLED", "2")
        result = self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "ORDERS_NOT_FRESH_TERMINAL_NO_RETRY")
        self.assertEqual(self.report().active_slots, 1)
        self.read("STOP", "CANCELED")
        result = self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now)
        self.assertEqual(self.closed(result).status, "MODEL_RETIRED")
        self.assertEqual(Decimal(self.report().reserved_margin), 0)

    def test_canceled_zero_fill_slot_can_retire_but_historical_ids_stay_reserved(self):
        self.reserve()
        old = self.intents[("slot-1", "ENTRY")]
        self.read(status="CANCELED")
        result = self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now)
        self.assertEqual(self.closed(result).status, "MODEL_RETIRED")
        self.assertEqual(self.report().active_slots, 0)
        self.assertEqual(len(self.report().slots), 1)
        request = self.request(2)
        self.assertEqual(self.reserve(request=request, entry=self.entry(request, client_id=old.client_id)).reason,
                         "CLIENT_ID_REPLAY_OR_CONFLICT")
        result = self.manager.mock_issue_lease("slot-1", old.binding, owner="A", now_ms=self.now, ttl_ms=100)
        self.assertEqual(self.closed(result).reason, "RETIRED_SLOT_NO_RETRY")
        self.assertEqual(self.reserve(2).status, "RESERVED")

    def test_stale_terminal_evidence_cannot_retire_slot(self):
        self.reserve()
        self.read(status="CANCELED")
        self.now += self.limits.max_observation_age_ms + 1
        result = self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "ORDERS_NOT_FRESH_TERMINAL_NO_RETRY")

    def test_retiring_one_of_two_slots_allows_a_fresh_third_identity_only(self):
        self.reserve()
        self.reserve(2)
        self.assertEqual(self.reserve(3).reason, "THIRD_ACTIVE_SLOT")
        self.read(status="CANCELED")
        result = self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now)
        self.assertEqual(self.closed(result).status, "MODEL_RETIRED")
        self.assertEqual(self.reserve(3).status, "RESERVED")
        report = self.report()
        self.assertEqual((report.active_slots, len(report.slots)), (2, 3))
        self.assertEqual(Decimal(report.reserved_margin), 80)
        active = {v.record.request.slot_id for v in report.slots if v.record.active}
        self.assertEqual(active, {"slot-2", "slot-3"})

    def test_retired_order_id_tombstone_is_not_lost_when_budget_is_released(self):
        self.reserve()
        old = self.intents[("slot-1", "ENTRY")]
        self.read(status="CANCELED")
        self.closed(self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now))
        request = self.request(2)
        result = self.reserve(request=request, entry=self.entry(request, order_id=old.order_id))
        self.assertEqual(result.reason, "ORDER_ID_REPLAY_OR_CONFLICT")
        self.assertEqual(self.report().active_slots, 0)

    def test_unknown_order_remains_manual_even_after_terminal_observation(self):
        self.reserve()
        self.read(status="UNKNOWN")
        self.read(status="CANCELED")
        result = self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "ORDERS_NOT_FRESH_TERMINAL_NO_RETRY")
        self.assertEqual(self.report().active_slots, 1)

    def test_crash_after_reservation_keeps_budget_and_entry_tombstone(self):
        self.reserve()
        before = self.view().record
        self.assertEqual(self.closed(self.manager.mock_restart(now_ms=self.now)).status, "MANUAL_REVIEW")
        report = self.report()
        self.assertEqual(report.status, "MANUAL_REVIEW")
        self.assertEqual(report.active_slots, 1)
        self.assertEqual(Decimal(report.reserved_margin), 40)
        self.assertEqual(report.slots[0].record, before)
        self.assertTrue(report.slots[0].manual_review)
        self.assertEqual(report.epoch, 2)
        result = self.manager.reserve_slot(before.request, before.find("ENTRY").intent,
                                           self.leases["slot-1"], now_ms=self.now)
        self.assertEqual(self.closed(result).reason, "RESTART_MANUAL_REVIEW")

    def test_restart_before_reservation_cannot_be_used_as_fresh_start(self):
        request = self.request()
        token = self.lease(request)
        self.closed(self.manager.mock_restart(now_ms=self.now))
        self.assertEqual(self.reserve(request=request, lease=token).reason, "RESTART_MANUAL_REVIEW")
        self.assertEqual(self.report().active_slots, 0)

    def test_restart_denies_lease_protective_intent_and_retirement(self):
        self.reserve()
        stop = self.protective()
        self.closed(self.manager.mock_restart(now_ms=self.now))
        request = self.requests["slot-1"]
        calls = (
            self.manager.mock_issue_lease(request.slot_id, request.binding, owner="B", now_ms=self.now, ttl_ms=5),
            self.manager.reserve_protective_intent("slot-1", stop, self.leases["slot-1"], now_ms=self.now),
            self.manager.mock_retire_slot("slot-1", self.leases["slot-1"], now_ms=self.now))
        for result in calls:
            self.assertEqual(self.closed(result).reason, "RESTART_MANUAL_REVIEW")

    def test_restart_can_only_refine_observations_without_reopening(self):
        self.reserve()
        self.add("STOP")
        self.add("TP1")
        self.read(status="FILLED", filled="2")
        self.read("TP1", "UNKNOWN")
        self.closed(self.manager.mock_restart(now_ms=self.now))
        self.assertEqual(self.read("STOP").status, "MANUAL_REVIEW")
        self.assertEqual(self.view().stop_state, "MOCK_STOP_OBSERVED")
        self.assertIn("TP_UNKNOWN_NO_RETRY", self.codes("TP1"))
        self.assertEqual(self.report().status, "MANUAL_REVIEW")
        self.closed(self.manager.mock_restart(now_ms=self.now))
        self.assertEqual(self.report().epoch, 2)

    def test_invalid_restart_clock_still_closes_all_facades(self):
        self.reserve()
        result = self.closed(self.manager.mock_restart(now_ms=-1))
        self.assertEqual((result.status, result.reason), ("MANUAL_REVIEW", "CLOCK_INVALID"))
        self.assertTrue(self.report().restarted)

    def test_clock_regression_and_invalid_clock_do_not_reserve(self):
        request = self.request()
        token = self.lease(request)
        for now, reason in ((999, "CLOCK_REGRESSION"), (True, "CLOCK_INVALID"),
                            (float("nan"), "CLOCK_INVALID"), (-1, "CLOCK_INVALID")):
            with self.subTest(now=now):
                result = self.manager.reserve_slot(request, self.entry(request), token, now_ms=now)
                self.assertEqual(self.closed(result).reason, reason)
        self.assertEqual(self.report().active_slots, 0)

    def test_bad_report_clock_does_not_claim_stop_coverage(self):
        self.reserve()
        self.read(status="FILLED", filled="2")
        self.add()
        self.read("STOP")
        report = self.report(now=self.now - 1)
        self.assertEqual(report.status, "MANUAL_REVIEW")
        self.assertEqual(report.slots[0].stop_state, "STOP_CLOCK_UNTRUSTED_DANGER")

    def test_raw_dict_and_malformed_observations_cannot_change_model(self):
        self.reserve()
        before = self.view().record
        raw = {"signed": True, "status": "FILLED", "untrusted": "SYNTHETIC_ONLY"}
        self.assertEqual(self.closed(self.manager.observe("slot-1", raw, now_ms=self.now)).reason,
                         "MOCK_OBSERVATION_REQUIRED")
        for changes in (dict(sequence=True), dict(sequence=0), dict(status="INVALID"),
                        dict(cumulative_filled="NaN"), dict(cumulative_filled=1.0),
                        dict(observed_at_ms=True)):
            with self.subTest(changes=changes):
                self.assertEqual(self.read(**changes).reason, "INVALID_OBSERVATION")
        self.assertEqual(self.view().record, before)
        self.assertNotIn("SYNTHETIC_ONLY", str(asdict(self.report())))

    def test_decimal_inputs_are_strict_and_no_float_or_nonfinite_limits(self):
        for value in ("NaN", "Infinity", "-1", "1e2", " 1", "01", "0.000000001", True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(g43.ModelInputError):
                    self.request(risk=value)
        for value in ("0", "-1"):
            with self.assertRaises(g43.ModelInputError):
                replace(self.limits, global_margin=value)
        with self.assertRaises(g43.ModelInputError):
            self.request(leverage=True)
        with self.assertRaises(g43.ModelInputError):
            replace(self.limits, funds=self.limits.funds + (self.limits.funds[0],))

    def test_invalid_ids_and_position_side_are_rejected(self):
        for binding in (("../scope", "ACCOUNT_A", "MOCKUSDT", "LONG"),
                        ("scope", "ACCOUNT_A", "mockusdt", "LONG"),
                        ("scope", "ACCOUNT_A", "MOCKUSDT", "INVALID")):
            with self.assertRaises(g43.ModelInputError):
                g43.Binding(*binding)
        with self.assertRaises(g43.ModelInputError):
            self.request(slot_id="")
        with self.assertRaises(g43.ModelInputError):
            self.entry(self.request(), client_id="bad/client")

    def test_report_and_decision_authority_flags_cannot_be_enabled_by_constructor(self):
        for constructor, kwargs in ((g43.Decision, dict(status="RESERVED", reason="MODEL_ONLY")),):
            with self.assertRaises(TypeError):
                constructor(**kwargs, entry_authorized=True)
            with self.assertRaises(TypeError):
                constructor(**kwargs, unlock=True)
        result = self.reserve()
        with self.assertRaises(FrozenInstanceError):
            result.entry_authorized = True
        report = self.report()
        with self.assertRaises(FrozenInstanceError):
            report.unlock = True
        with self.assertRaises(FrozenInstanceError):
            report.slots[0].record.active = False

    def test_large_decimal_notional_is_exact_despite_callers_decimal_context(self):
        self.configure(global_margin="999999999999", slot_margin="999999999999",
                       global_leverage=10**18, slot_leverage=10**18,
                       funds=(g43.MockFunds("ACCOUNT_A", "999999999999"),))
        request = self.request(quantity="999999999999.99999999", price="999999999999.99999999",
                               leverage=10**18)
        with localcontext() as context:
            context.prec = 64
            expected = Decimal(request.quantity) * Decimal(request.price)
        with localcontext() as context:
            context.prec = 8
            self.assertEqual(self.reserve(request=request).status, "RESERVED")
            self.assertEqual(Decimal(self.report().gross_notional), expected)

    def test_standalone_demo_returns_two_mock_slots_third_deny_and_closed_results(self):
        result = g43.demo()
        self.assertEqual([d["status"] for d in result["decisions"]], ["RESERVED", "RESERVED", "DENY"])
        self.assertEqual(result["decisions"][2]["reason"], "THIRD_ACTIVE_SLOT")
        for item in result["decisions"] + [result, result["report"]] + list(result["report"]["slots"]):
            self.assertIs(item["entry_authorized"], False)
            self.assertIs(item["unlock"], False)

    def test_module_imports_only_allowlisted_stdlib_and_has_no_external_adapters(self):
        tree = ast.parse(Path(g43.__file__).read_text())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                modules.add(node.module.split(".")[0])
        self.assertLessEqual(modules, {"__future__", "dataclasses", "decimal", "functools",
                                       "json", "re", "threading"})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "eval", "exec", "__import__", "compile"})


if __name__ == "__main__":
    unittest.main()
