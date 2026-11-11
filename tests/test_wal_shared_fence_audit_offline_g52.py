"""G52: negatif karşı örnekler ve olumlu sentetik sınırlar; owner çalıştırılmaz."""
import ast
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wal_shared_fence_audit_offline_g52 as g52


def assert_closed(test, value):
    if isinstance(value, g52.Decision):
        value = value.to_dict()
    if isinstance(value, dict):
        if "status" in value:
            for name in ("unlock", "entry", "resolve"):
                test.assertIs(value[name], False)
        for item in value.values():
            assert_closed(test, item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_closed(test, item)


class StaticAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "owner_single_execution.py").read_text(encoding="utf-8")
        cls.report = g52.audit_source(cls.source)

    def test_actual_marker_is_one_unconditional_blocked_raise(self):
        self.assertTrue(self.report["marker_strict_block_observed"])
        self.assertEqual(self.report["decision"]["status"], "UNPROVEN")

    def test_zero_entry_terminal_block_remains_observed(self):
        self.assertTrue(self.report["zero_entry_terminal_block_observed"])

    def test_all_three_marker_callers_remain_visible(self):
        self.assertEqual({c["caller"] for c in self.report["marker_callers"]},
                         {"risk_ledger_entry_gate", "_restart_one", "run_once"})

    def test_all_writer_families_are_separate_unproven_rows(self):
        writers = self.report["writers"]
        self.assertEqual({r["role"] for r in writers},
                         {"ENTRY", "STOP", "TP1", "TP2", "CANCEL_STOP", "CANCEL_TP",
                          "EMERGENCY", "POLICY_RESERVE", "incident"})
        for row in writers:
            self.assertTrue(row["wal_sites"], row["role"])
            self.assertEqual(row["decision"]["status"], "UNPROVEN")
            self.assertTrue(all(site["line"] > 0 for site in row["wal_sites"]))
        assert_closed(self, writers)

    def test_normal_lease_precedes_journal_in_lexical_source_only(self):
        calls = [c for c in self.report["calls"] if c["caller"] == "one_write"]
        lines = {name: min(c["line"] for c in calls if c["callee"] == name)
                 for name in ("venue.acquire_mutation_lease", "journal", "venue.post_once")}
        self.assertLess(lines["venue.acquire_mutation_lease"], lines["journal"])
        self.assertLess(lines["journal"], lines["venue.post_once"])
        self.assertFalse(self.report["runtime_filelock_proven"])

    def test_cancel_journal_precedes_delete_without_proving_wrapper_scope(self):
        rows = {r["role"]: r for r in self.report["writers"]}
        for role in ("CANCEL_STOP", "CANCEL_TP"):
            row = rows[role]
            writes = [x["line"] for x in row["related_call_signals"]]
            self.assertTrue(any(line > row["wal_sites"][0]["line"] for line in writes))
            self.assertEqual(row["decision"]["status"], "UNPROVEN")

    def test_emergency_journal_precedes_post_signal(self):
        row = next(r for r in self.report["writers"] if r["role"] == "EMERGENCY")
        self.assertLess(row["wal_sites"][0]["line"], row["related_call_signals"][0]["line"])

    def test_policy_and_incident_are_not_inferred_from_normal_lease(self):
        rows = {r["role"]: r for r in self.report["writers"]}
        for role in ("POLICY_RESERVE", "incident"):
            self.assertTrue(rows[role]["wal_sites"])
            self.assertEqual(rows[role]["decision"]["status"], "UNPROVEN")

    def test_readonly_directory_open_is_not_mislabeled_as_wal_writer(self):
        row = next(r for r in self.report["writers"] if r["role"] == "POLICY_RESERVE")
        # FAZ2: G54 sonrası yazım adapter üzerinden; owner'da doğrudan writable open kalmamalı.
        self.assertEqual(len(row["wal_sites"]), 1)
        self.assertEqual(row["wal_sites"][0]["callee"], "append_reservation")
        direct = [c for c in self.report["calls"] if c["caller"] == "write_policy_reserve_record"
                  and c["callee"] in ("os.open", "os.fsync")]
        self.assertEqual(direct, [])

    def test_global_lock_mutation_lease_and_writer_fence_are_distinct(self):
        rows = self.report["distinctions"]
        self.assertEqual([r["kind"] for r in rows],
                         ["GLOBAL_OWNER_LOCK", "MUTATION_LEASE", "SHARED_WRITER_FENCE"])
        self.assertTrue(rows[0]["signals"])
        self.assertTrue(rows[1]["signals"])
        self.assertFalse(self.report["runtime_filelock_proven"])

    def test_call_graph_contains_entry_stop_and_dynamic_tp_routes(self):
        calls = self.report["calls"]
        self.assertTrue(any(c["caller"] == "run_once" and c["callee"] == "one_write"
                            and "'ENTRY'" in c["arguments"] for c in calls))
        self.assertTrue(any(c["caller"] == "place_stop_write" and c["callee"] == "one_write"
                            and "'STOP'" in c["arguments"] for c in calls))
        self.assertTrue(any(c["caller"] == "run_once" and c["callee"] == "one_write"
                            and "role" in c["arguments"] for c in calls))
        self.assertIn('("TP1","TP2")', self.source)

    def test_fence_import_is_a_source_signal_not_a_proof(self):
        self.assertTrue(any(r["module"] == "owner_mutation_fence" for r in self.report["imports"]))
        self.assertEqual(self.report["decision"]["status"], "UNPROVEN")

    def test_syntax_error_is_unproven_never_a_clean_report(self):
        report = g52.audit_source("def malformed(:")
        self.assertEqual(report["decision"]["status"], "UNPROVEN")
        self.assertEqual(report["writers"], [])
        assert_closed(self, report)

    def test_pass_marker_is_a_barrier_failure(self):
        report = g52.audit_source("def mark_scope_resolved():\n    pass\n")
        self.assertFalse(report["marker_strict_block_observed"])
        self.assertEqual(report["decision"]["status"], "FAIL")

    def test_conditional_raise_does_not_prove_blocking(self):
        report = g52.audit_source("def mark_scope_resolved():\n    if condition:\n        raise Blocked("
                                  + repr(g52.MARKER_REASON) + ")\n")
        self.assertFalse(report["marker_strict_block_observed"])

    def test_barrier_string_in_comment_or_docstring_proves_nothing(self):
        report = g52.audit_source("# " + g52.MARKER_REASON + "\ndef mark_scope_resolved():\n    "
                                  + repr(g52.MARKER_REASON) + "\n    return True\n")
        self.assertFalse(report["marker_strict_block_observed"])

    def test_wrong_exception_and_wrong_reason_are_rejected(self):
        for exception, reason in (("ValueError", g52.MARKER_REASON), ("Blocked", "other")):
            with self.subTest(exception=exception):
                report = g52.audit_source("def mark_scope_resolved():\n    raise " + exception
                                          + "(" + repr(reason) + ")\n")
                self.assertFalse(report["marker_strict_block_observed"])

    def test_duplicate_definition_cannot_hide_reopened_marker(self):
        source = "def mark_scope_resolved():\n    raise Blocked(" + repr(g52.MARKER_REASON)
        report = g52.audit_source(source + ")\ndef mark_scope_resolved():\n    return True\n")
        self.assertFalse(report["marker_strict_block_observed"])
        self.assertIn("mark_scope_resolved", report["duplicate_definitions"])

    def test_decorator_can_replace_barrier_so_shape_is_not_strict(self):
        report = g52.audit_source("@wrapper\ndef mark_scope_resolved():\n    raise Blocked("
                                  + repr(g52.MARKER_REASON) + ")\n")
        self.assertFalse(report["marker_strict_block_observed"])

    def test_nested_calls_are_not_attributed_to_outer_function(self):
        report = g52.audit_source("def outer():\n    def inner():\n        journal('ENTRY')\n    pass\n")
        self.assertEqual(report["calls"], [])
        self.assertEqual(report["decision"]["status"], "FAIL")

    def test_dynamic_import_remains_unproven_and_is_never_invoked(self):
        report = g52.audit_source("x = __import__('owner_mutation_fence')\n")
        self.assertIn("DYNAMIC_IMPORTS_UNRESOLVED", report["limitations"])
        self.assertTrue(any(s["callee"] == "__import__" for s in report["dynamic_signals"]))

    def test_monkey_patch_remains_unproven(self):
        report = g52.audit_source("venue.acquire_mutation_lease = unsafe\nsetattr(venue,'journal',unsafe)\n")
        self.assertIn("MONKEY_PATCH_UNRESOLVED", report["limitations"])
        self.assertTrue(any(s["callee"] == "POSSIBLE_REBINDING" for s in report["dynamic_signals"]))

    def test_wrapper_false_negative_is_explicitly_unproven(self):
        report = g52.audit_source("def one_write(venue, role):\n    wrapped_writer(role)\n")
        row = next(r for r in report["writers"] if r["role"] == "ENTRY")
        self.assertEqual(row["wal_sites"], [])
        self.assertEqual(row["decision"]["status"], "UNPROVEN")
        self.assertIn("WRAPPER_DISPATCH_UNRESOLVED", report["limitations"])

    def test_flock_spelling_does_not_claim_real_os_filelock(self):
        report = g52.audit_source("def global_one_trade_lock():\n    fcntl.flock(1,2)\n")
        self.assertFalse(report["runtime_filelock_proven"])
        self.assertIn("STATIC_NOT_CLOSED_WORLD", report["limitations"])

    def test_source_is_parsed_without_executing_imports_or_file_actions(self):
        source = "import owner_single_execution\nraise RuntimeError('must never execute')\n"
        with patch.object(Path, "open", side_effect=AssertionError("no file IO")):
            report = g52.audit_source(source)
        self.assertEqual(report["evidence_kind"], "STATIC_AST_ONLY")
        self.assertNotIn("owner_single_execution", sys.modules)

    def test_new_module_has_only_stdlib_imports_and_no_ast_execution(self):
        tree = ast.parse(Path(g52.__file__).read_text(encoding="utf-8"))
        imports = {alias.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                   for alias in n.names}
        imports.update(n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom))
        self.assertLessEqual(imports, {"__future__", "argparse", "ast", "dataclasses", "hashlib",
                                       "json", "os", "pathlib", "tempfile"})
        direct = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)}
        self.assertTrue(direct.isdisjoint({"exec", "eval", "compile", "__import__"}))

    def test_source_digest_changes_when_source_changes(self):
        self.assertNotEqual(self.report["source_sha256"],
                            g52.audit_source(self.source + "\n# synthetic\n")["source_sha256"])
        assert_closed(self, self.report)


class AcceptanceMatrixTests(unittest.TestCase):
    def claims(self, value=True):
        return {key: value for key, _ in g52.CRITERIA}

    def test_all_positive_claims_are_mock_only_and_cannot_authorize(self):
        report = g52.acceptance_matrix(self.claims())
        self.assertEqual(report["decision"]["status"], "PASS_SYNTHETIC_ONLY")
        self.assertEqual(len(report["rows"]), 10)
        assert_closed(self, report)

    def test_each_missing_safety_claim_keeps_its_own_row_unproven(self):
        for key, _ in g52.CRITERIA:
            with self.subTest(key=key):
                claims = self.claims(); claims[key] = None
                report = g52.acceptance_matrix(claims)
                row = next(r for r in report["rows"] if r["criterion"] == key)
                self.assertEqual(row["decision"]["status"], "UNPROVEN")
                self.assertEqual(report["decision"]["status"], "UNPROVEN")
                assert_closed(self, report)

    def test_each_negative_safety_claim_fails_closed_individually(self):
        for key, _ in g52.CRITERIA:
            with self.subTest(key=key):
                claims = self.claims(); claims[key] = False
                report = g52.acceptance_matrix(claims)
                self.assertEqual(report["decision"]["status"], "FAIL")
                self.assertEqual(sum(r["decision"]["status"] == "FAIL" for r in report["rows"]), 1)
                assert_closed(self, report)

    def test_global_lock_claim_cannot_replace_writer_fence(self):
        claims = self.claims(); claims["one_authority"] = False
        self.assertEqual(g52.acceptance_matrix(claims)["decision"]["status"], "FAIL")

    def test_even_all_unknown_matrix_has_no_permission(self):
        report = g52.acceptance_matrix(self.claims(None))
        self.assertEqual(report["decision"]["status"], "UNPROVEN")
        assert_closed(self, report)

    def test_missing_extra_non_dict_and_truthy_types_fail_schema(self):
        missing = self.claims(); missing.pop("no_aba")
        extra = self.claims(); extra["global_lock"] = True
        for value in (missing, extra, None, [], "True", dict(self.claims(), no_aba=1),
                      dict(self.claims(), all_writers="YES")):
            with self.subTest(value=value):
                report = g52.acceptance_matrix(value)
                self.assertEqual(report["decision"]["status"], "FAIL")
                assert_closed(self, report)

    def test_decision_flags_are_immutable_and_not_constructor_parameters(self):
        result = g52.decision("PASS_SYNTHETIC_ONLY", "test")
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.resolve = True
        with self.assertRaises(TypeError):
            g52.Decision("PASS_SYNTHETIC_ONLY", (), entry=True)
        for status in ("PASS_SYNTHETIC_ONLY", "UNPROVEN", "FAIL", "UNKNOWN"):
            assert_closed(self, g52.decision(status, "all closed"))


class SyntheticLabTests(unittest.TestCase):
    def setUp(self):
        self.lab = g52.SyntheticLab()
        self.addCleanup(self.lab.__exit__)
        self.a, self.b = self.lab.actors

    def acquire(self, role="ENTRY", actor=None):
        token, result = self.lab.acquire(actor or self.a, role)
        self.assertEqual(result.status, "PASS_SYNTHETIC_ONLY")
        assert_closed(self, result)
        return token

    def append(self, role="ENTRY", **fault):
        token = self.acquire(role)
        result = self.lab.commit(token, role, **fault)
        self.lab.release(token)
        assert_closed(self, result)
        return result

    def row_path(self, index=1):
        return self.lab.root / ("mock_%04d.json" % index)

    def edit_row(self, index, **updates):
        path = self.row_path(index)
        row = json.loads(path.read_bytes())
        row.pop("digest")
        row.update(updates)
        row["digest"] = g52._hash(row)
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    def test_context_owns_and_cleans_its_temp_directory(self):
        with g52.SyntheticLab() as lab:
            root = lab.root
            self.assertTrue(root.is_dir())
            self.assertTrue(root.name.startswith("g52-synthetic-"))
            self.assertNotEqual(root, ROOT)
        self.assertFalse(root.exists())

    def test_all_ten_writer_roles_append_with_mock_tokens_only(self):
        for role in g52.ROLES:
            self.assertEqual(self.append(role).status, "PASS_SYNTHETIC_ONLY")
        self.assertEqual(self.lab.count, 10)
        self.assertEqual(self.lab.replay((10, self.lab.tail)).status, "PASS_SYNTHETIC_ONLY")
        self.assertFalse(any(p.name.startswith("resolved_") for p in self.lab.root.iterdir()))

    def test_two_fake_actors_contend_on_single_model_authority(self):
        token = self.acquire()
        other, result = self.lab.acquire(self.b, "incident")
        self.assertIsNone(other)
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(self.lab.commit(token, "ENTRY").status, "PASS_SYNTHETIC_ONLY")
        self.assertEqual(len(self.lab.snapshot()), 1)

    def test_second_actor_can_write_after_model_release(self):
        self.append()
        token = self.acquire("incident", self.b)
        self.assertEqual(self.lab.commit(token, "incident").status, "PASS_SYNTHETIC_ONLY")
        self.assertEqual(self.lab.count, 2)

    def test_unknown_actor_role_and_global_authority_are_refused(self):
        for actor, role, authority in ((g52.FakeProcess("stranger"), "ENTRY", "MOCK_SHARED"),
                                       (self.a, "unknown", "MOCK_SHARED"),
                                       (self.a, "ENTRY", "GLOBAL_OWNER_LOCK"),
                                       (self.a, "ENTRY", "MUTATION_LEASE")):
            token, result = self.lab.acquire(actor, role, authority)
            self.assertIsNone(token)
            self.assertEqual(result.status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_missing_token_cannot_append(self):
        result = self.lab.commit(None, "ENTRY")
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_wrong_role_token_cannot_write_stop(self):
        token = self.acquire()
        self.assertEqual(self.lab.commit(token, "STOP").status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_resolver_token_cannot_be_used_as_writer_token(self):
        token = self.acquire("RESOLVER")
        self.assertEqual(self.lab.commit(token, "ENTRY").status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_resolver_grant_never_creates_resolved_marker(self):
        self.acquire("RESOLVER")
        result = self.lab.resolve_request()
        self.assertEqual(result.status, "UNPROVEN")
        assert_closed(self, result)
        self.assertEqual(list(self.lab.root.iterdir()), [])

    def test_stale_late_writer_after_successful_precheck_is_rejected(self):
        token = self.acquire()
        self.assertTrue(self.lab.current(token))
        self.lab.release(token)
        self.acquire("STOP", self.b)
        result = self.lab.commit(token, "ENTRY")
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_forged_authority_epoch_generation_holder_and_role_fail(self):
        token = self.acquire()
        for change in (dict(authority="GLOBAL_OWNER_LOCK"), dict(epoch=0), dict(generation=999),
                       dict(actor="fake-B"), dict(role="STOP")):
            with self.subTest(change=change):
                result = self.lab.commit(replace(token, **change), "ENTRY")
                self.assertEqual(result.status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_generation_changes_when_same_holder_reacquires(self):
        old = self.acquire()
        self.lab.release(old)
        new = self.acquire()
        self.assertGreater(new.generation, old.generation)
        self.assertEqual(self.lab.commit(old, "ENTRY").status, "FAIL")
        self.assertEqual(self.lab.commit(new, "ENTRY").status, "PASS_SYNTHETIC_ONLY")

    def test_bool_and_float_cannot_impersonate_integer_epoch_or_generation(self):
        token = self.acquire()
        for changes in (dict(epoch=True), dict(epoch=1.0), dict(generation=True),
                        dict(generation=1.0)):
            self.assertFalse(self.lab.current(replace(token, **changes)))
            self.assertEqual(self.lab.commit(replace(token, **changes), "ENTRY").status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_identical_local_counters_from_other_authority_cannot_write(self):
        self.acquire()
        with g52.SyntheticLab() as other:
            foreign, _ = other.acquire(other.actors[0], "ENTRY")
            self.assertEqual(foreign.epoch, self.lab.held.epoch)
            self.assertEqual(foreign.generation, self.lab.held.generation)
            self.assertNotEqual(foreign.authority, self.lab.held.authority)
            self.assertEqual(self.lab.commit(foreign, "ENTRY").status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_same_grant_cannot_append_twice_even_after_mock_ack(self):
        token = self.acquire()
        self.assertEqual(self.lab.commit(token, "ENTRY").status, "PASS_SYNTHETIC_ONLY")
        before = self.lab.snapshot()
        self.assertEqual(self.lab.commit(token, "ENTRY").status, "FAIL")
        self.assertEqual(self.lab.snapshot(), before)

    def test_same_grant_cannot_retry_after_unknown_append(self):
        token = self.acquire()
        self.assertEqual(self.lab.commit(token, "ENTRY", crash="after_write").status, "UNPROVEN")
        before = self.lab.snapshot()
        self.assertEqual(self.lab.commit(token, "ENTRY").status, "FAIL")
        self.assertEqual(self.lab.snapshot(), before)

    def test_restart_aba_empty_snapshot_does_not_validate_old_token(self):
        old = self.acquire()
        before = self.lab.snapshot()
        self.assertEqual(self.lab.restart().status, "UNPROVEN")
        new = self.acquire()
        self.assertEqual(before, self.lab.snapshot())
        self.assertGreater(new.epoch, old.epoch)
        self.assertEqual(self.lab.commit(old, "ENTRY").status, "FAIL")
        self.assertEqual(self.lab.compare_snapshots(before, self.lab.snapshot()).status, "UNPROVEN")

    def test_stale_release_cannot_clear_another_actors_grant(self):
        old = self.acquire()
        self.lab.release(old)
        new = self.acquire("incident", self.b)
        self.assertEqual(self.lab.release(old).status, "FAIL")
        self.assertTrue(self.lab.current(new))

    def test_equal_double_read_still_leaves_late_writer_window(self):
        before = self.lab.snapshot()
        after = self.lab.snapshot()
        self.assertEqual(self.lab.compare_snapshots(before, after).status, "UNPROVEN")
        self.lab.rogue_append("incident")
        self.assertNotEqual(before, self.lab.snapshot())
        assert_closed(self, self.lab.resolve_request())

    def test_nonparticipant_incident_writes_even_while_mock_fence_is_held(self):
        token = self.acquire("RESOLVER")
        before = self.lab.snapshot()
        result = self.lab.rogue_append("incident")
        self.assertEqual(result.status, "PASS_SYNTHETIC_ONLY")
        self.assertTrue(self.lab.current(token))
        self.assertEqual(self.lab.compare_snapshots(before, self.lab.snapshot()).status, "FAIL")
        assert_closed(self, self.lab.resolve_request())

    def test_each_nonparticipating_family_can_invalidate_snapshot(self):
        for role in g52.ROLES:
            before = self.lab.snapshot()
            self.lab.rogue_append(role)
            self.assertEqual(self.lab.compare_snapshots(before, self.lab.snapshot()).status, "FAIL")

    def test_invalid_fault_point_is_refused_before_disk_write(self):
        token = self.acquire()
        for fault in (dict(crash="invalid"), dict(fsync_error="invalid")):
            self.assertEqual(self.lab.commit(token, "ENTRY", **fault).status, "FAIL")
        self.assertEqual(self.lab.snapshot(), ())

    def test_before_append_crash_has_no_record_but_no_resolve_permission(self):
        result = self.append(crash="before_append")
        self.assertEqual(result.status, "UNPROVEN")
        self.assertEqual(self.lab.snapshot(), ())
        self.assertEqual(self.lab.replay().status, "UNPROVEN")
        assert_closed(self, self.lab.resolve_request())

    def test_torn_write_replay_is_corrupt_and_keeps_evidence(self):
        self.assertEqual(self.append(crash="torn").status, "UNPROVEN")
        before = self.row_path().read_bytes()
        result = self.lab.replay()
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", result.reasons)
        self.assertEqual(before, self.row_path().read_bytes())

    def test_after_write_crash_leaves_parseable_bytes_without_durability_proof(self):
        result = self.append(crash="after_write")
        self.assertEqual(result.status, "UNPROVEN")
        self.assertEqual(self.lab.events, ["WRITE"])
        self.assertEqual(json.loads(self.row_path().read_bytes())["role"], "ENTRY")
        self.assertEqual(self.lab.replay().status, "UNPROVEN")

    def test_file_fsync_crash_has_no_directory_fsync_or_ack(self):
        self.assertEqual(self.append(crash="after_file_fsync").status, "UNPROVEN")
        self.assertEqual(self.lab.events, ["WRITE", "FILE_FSYNC"])

    def test_directory_fsync_crash_does_not_turn_into_ack(self):
        result = self.append(crash="after_directory_fsync")
        self.assertEqual(result.status, "UNPROVEN")
        self.assertEqual(self.lab.events, ["WRITE", "FILE_FSYNC", "DIRECTORY_FSYNC"])
        self.assertEqual(self.lab.count, 1)
        assert_closed(self, self.lab.replay((1, self.lab.tail)))

    def test_before_ack_crash_still_unknown_despite_all_fsync_calls(self):
        self.assertEqual(self.append(crash="before_ack").status, "UNPROVEN")
        self.assertNotIn("ACK", self.lab.events)
        self.assertTrue(self.row_path().exists())

    def test_file_fsync_failure_never_claims_no_write(self):
        result = self.append(fsync_error="file")
        self.assertEqual(result.status, "UNPROVEN")
        self.assertTrue(self.row_path().exists())
        self.assertEqual(self.lab.events, ["WRITE"])

    def test_directory_fsync_failure_never_claims_durable_ack(self):
        result = self.append(fsync_error="directory")
        self.assertEqual(result.status, "UNPROVEN")
        self.assertEqual(self.lab.events, ["WRITE", "FILE_FSYNC"])
        self.assertTrue(self.row_path().exists())

    def test_normal_mock_fsync_order_is_file_then_directory_then_ack(self):
        self.assertEqual(self.append().status, "PASS_SYNTHETIC_ONLY")
        self.assertEqual(self.lab.events, ["WRITE", "FILE_FSYNC", "DIRECTORY_FSYNC", "ACK"])

    def test_unknown_write_is_not_retried_or_overwritten(self):
        self.append(crash="after_write")
        before = self.row_path().read_bytes()
        result = self.append()
        self.assertEqual(result.status, "FAIL")
        self.assertIn("DUPLICATE_MOCK_RECORD_NO_RETRY", result.reasons)
        self.assertEqual(self.row_path().read_bytes(), before)

    def test_empty_replay_is_unproven_even_with_empty_anchor(self):
        self.assertEqual(self.lab.replay((0, "0" * 64)).status, "UNPROVEN")

    def test_valid_replay_without_anchor_cannot_detect_suffix_loss(self):
        self.append()
        result = self.lab.replay()
        self.assertEqual(result.status, "UNPROVEN")
        self.assertIn("VALID_CHAIN_WITHOUT_EXTERNAL_ANCHOR_SUFFIX_LOSS_UNPROVEN", result.reasons)

    def test_valid_mock_anchor_replay_is_only_positive_model_result(self):
        self.append(); self.append("STOP")
        result = self.lab.replay((2, self.lab.tail))
        self.assertEqual(result.status, "PASS_SYNTHETIC_ONLY")
        self.assertIn("MOCK_ANCHOR_NOT_EXTERNAL_TRUST_ROOT_REPLAY_ONLY", result.reasons)
        assert_closed(self, result)

    def test_wrong_anchor_count_hash_and_type_are_rejected(self):
        self.append()
        for anchor in ((2, self.lab.tail), (1, "f" * 64), [1, self.lab.tail], (True, self.lab.tail)):
            self.assertEqual(self.lab.replay(anchor).status, "FAIL")

    def test_suffix_deletion_needs_anchor_and_never_looks_clean(self):
        self.append(); self.append("STOP")
        anchor = (2, self.lab.tail)
        self.row_path(2).unlink()
        self.assertEqual(self.lab.replay(anchor).status, "FAIL")
        self.assertEqual(self.lab.replay().status, "UNPROVEN")

    def test_missing_middle_record_is_rejected(self):
        self.append(); self.append("STOP"); self.append("TP1")
        self.row_path(2).unlink()
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)

    def test_digest_tampering_and_unknown_schema_are_rejected(self):
        self.append()
        original = self.row_path().read_bytes()
        self.row_path().write_bytes(original.replace(b'"ENTRY"', b'"STOP"'))
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)
        self.row_path().write_bytes(original)
        self.edit_row(1, schema="UNKNOWN")
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)

    def test_rehashed_bad_identity_sequence_and_previous_are_rejected(self):
        self.append()
        original = self.row_path().read_bytes()
        for fields in (dict(actor="owner"), dict(role="UNKNOWN"), dict(sequence=True),
                       dict(previous="f" * 64), dict(epoch=0), dict(generation=-1)):
            with self.subTest(fields=fields):
                self.row_path().write_bytes(original)
                self.edit_row(1, **fields)
                self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)

    def test_epoch_or_generation_regression_is_rejected_even_if_rehashed(self):
        self.lab.restart()
        self.append()
        self.lab.restart()
        self.append("STOP")
        original = self.row_path(2).read_bytes()
        for fields in (dict(epoch=1), dict(generation=1)):
            self.row_path(2).write_bytes(original)
            self.edit_row(2, **fields)
            self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)

    def test_valid_post_restart_replay_remains_mock_only(self):
        self.append()
        self.lab.restart()
        self.append("STOP")
        result = self.lab.replay((2, self.lab.tail))
        self.assertEqual(result.status, "PASS_SYNTHETIC_ONLY")
        assert_closed(self, result)

    def test_duplicate_json_keys_cannot_be_hidden_by_last_value(self):
        self.append()
        raw = self.row_path().read_bytes()
        self.row_path().write_bytes(raw.replace(b'"role": "ENTRY"', b'"role": "STOP", "role": "ENTRY"'))
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)

    def test_reordered_record_payloads_are_rejected(self):
        self.append(); self.append("STOP")
        a, b = self.row_path().read_bytes(), self.row_path(2).read_bytes()
        self.row_path().write_bytes(b); self.row_path(2).write_bytes(a)
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)

    def test_duplicate_record_filename_is_not_silently_replayed(self):
        self.append()
        (self.lab.root / "duplicate.json").write_bytes(self.row_path().read_bytes())
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)

    def test_symlink_record_is_rejected_before_following_target(self):
        self.append()
        target = self.lab.root / "target.txt"
        target.write_bytes(self.row_path().read_bytes())
        self.row_path().unlink()
        self.row_path().symlink_to(target)
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)

    def test_replay_extra_field_and_missing_newline_are_rejected(self):
        self.append()
        original = self.row_path().read_bytes()
        self.edit_row(1, unlock=True)
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)
        self.row_path().write_bytes(original.rstrip(b"\n"))
        self.assertIn("REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY", self.lab.replay().reasons)


class SyntheticCliTests(unittest.TestCase):
    def test_all_builtin_scenarios_report_no_authority(self):
        for scenario in g52.SCENARIOS:
            report = g52.run_scenario(scenario)
            self.assertFalse(report["os_process_concurrency_tested"])
            self.assertEqual(report["decision"]["status"], "UNPROVEN")
            assert_closed(self, report)

    def test_late_writer_and_aba_scenarios_contain_actual_model_rejections(self):
        for name in ("late_writer", "aba"):
            report = g52.run_scenario(name)
            self.assertTrue(any(d["status"] == "FAIL" and
                                "STALE_OR_WRONG_ROLE_NO_APPEND" in d["reasons"]
                                for d in report["decisions"]))

    def test_unknown_scenario_fails_without_opening_a_lab(self):
        with patch.object(g52, "SyntheticLab", side_effect=AssertionError("must not create fixture")):
            report = g52.run_scenario("/arbitrary/path")
        self.assertEqual(report["decision"]["status"], "FAIL")
        assert_closed(self, report)

    def test_cli_emits_json_for_synthetic_inputs_only(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(g52.main(["--scenario", "all"]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(len(report["scenarios"]), 5)
        assert_closed(self, report)

    def test_cli_rejects_source_wal_lock_and_resolve_arguments(self):
        with patch.object(g52, "SyntheticLab", side_effect=AssertionError("no fixture for bad args")):
            for args in (["--source", "anything.py"], ["--wal", "anything"],
                         ["--unlock"], ["--resolve"], ["--scenario", "owner"]):
                with self.subTest(args=args), patch("sys.stderr", new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as raised:
                        g52.main(args)
                    self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
