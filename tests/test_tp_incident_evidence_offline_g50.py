"""G50: gerçek dosya/ağ/SDK kullanmayan sentetik JSON ve negatif sınır testleri."""

import ast
import copy
from dataclasses import FrozenInstanceError
import io
import itertools
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import tp_incident_evidence_offline_g50 as g50


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tp_incident_evidence_offline_g50.py"
C = g50.FindingCode
V = g50.Verdict


def event(ref="r:e1", *, day="2026-11-06", source="SIGNED_RECEIPT", origin="SYNTHETIC",
          event_type="ORDER_STATE", role="TP1", status="NEW", second=0):
    e = json.loads(g50.synthetic_document())["events"][0]
    e.update(event_ref=ref, incident_day=day, occurred_at=f"{day}T09:00:{second:02d}Z",
             observed_at=f"{day}T09:01:00Z", event_type=event_type, status=status,
             executed_qty="0")
    e["provenance"].update(source=source, origin=origin)
    e["identity"]["role"] = role
    if role == "STOP":
        e["identity"].update(kind="ALGO", endpoint="/fapi/v1/algoOrder", client_id=None,
                             order_id=None, client_algo_id="r:stop", algo_id="r:algo")
        e.update(status_field="algoStatus", orig_qty=None, executed_qty=None)
    if event_type not in ("ORDER_STATE", "DELETE_ACK"):
        e.update(status=None, status_field=None, orig_qty=None, executed_qty=None)
    if event_type in ("POSITION", "STREAM_GAP"):
        e["identity"] = None
    if event_type == "POSITION":
        e["position_qty"] = "0"
    if event_type == "QUERY_MISSING":
        e["query_code"] = "HTTP_404"
    return e


def document(*events):
    return {"schema": g50.SCHEMA, "redacted": True, "events": list(events)}


def wire(*events):
    return json.dumps(document(*events)).encode("utf-8")


def report(*events):
    return g50.analyze_document(wire(*events))


def codes(result, index=0):
    return {f.code for f in result.incidents[index].findings}


def cli(raw=b"", *args):
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], input=raw,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, cwd=ROOT)


class InputAndPrivacyTests(unittest.TestCase):
    def rejected(self, e, expected=None):
        with self.assertRaises(g50.EvidenceError) as raised:
            g50.parse_document(wire(e))
        if expected:
            self.assertEqual(str(raised.exception), expected)
        self.assertNotIn("r:", str(raised.exception))

    def test_empty_input_has_two_unresolved_incidents_and_zero_authority(self):
        r = report()
        self.assertEqual([i.incident_day for i in r.incidents], list(g50.INCIDENT_DAYS))
        self.assertEqual(r.verdict, V.UNRESOLVED)
        self.assertTrue(all(C.MISSING_REAL_EVIDENCE in codes(r, n) for n in (0, 1)))
        self.assertEqual(r.to_dict()["authority"], dict(entry_authorized=False,
                         owner_authorized=False, unlock=False, stop_proven=False))

    def test_demo_is_explicitly_synthetic_on_both_days(self):
        r = g50.analyze_document(g50.synthetic_document())
        for n in (0, 1):
            self.assertIn(C.SYNTHETIC_ISOLATED, codes(r, n))
            self.assertIn(C.MISSING_REAL_EVIDENCE, codes(r, n))
            self.assertEqual(r.incidents[n].timeline[0].origin, g50.Origin.SYNTHETIC)
        self.assertEqual(r.real_incident_cause, V.UNRESOLVED)

    def test_input_must_be_immutable_bytes(self):
        for raw in ("{}", bytearray(wire()), {}, None):
            with self.subTest(raw_type=type(raw).__name__), self.assertRaises(g50.EvidenceError):
                g50.parse_document(raw)

    def test_input_byte_limit_is_enforced_before_parsing(self):
        with self.assertRaisesRegex(g50.EvidenceError, "INPUT_SIZE_OR_TYPE_INVALID"):
            g50.parse_document(b" " * (g50.MAX_BYTES + 1))

    def test_event_count_limit_is_enforced(self):
        d = document(*([{}] * (g50.MAX_EVENTS + 1)))
        with self.assertRaisesRegex(g50.EvidenceError, "EVENT_LIMIT_INVALID"):
            g50.parse_document(json.dumps(d).encode())

    def test_unknown_root_authority_field_is_rejected(self):
        d = document(event())
        d["entry_authorized"] = True
        with self.assertRaises(g50.EvidenceError):
            g50.parse_document(json.dumps(d).encode())

    def test_redaction_attestation_must_be_true_boolean(self):
        for value in (False, 1, "true", None):
            d = document(event())
            d["redacted"] = value
            with self.subTest(value=value), self.assertRaises(g50.EvidenceError):
                g50.parse_document(json.dumps(d).encode())

    def test_missing_event_field_is_rejected(self):
        e = event()
        del e["scope"]
        self.rejected(e, "SCHEMA_FIELDS_INVALID")

    def test_unknown_secret_field_is_rejected_without_echo(self):
        e = event()
        e["apiKey"] = "synthetic-private-value"
        self.rejected(e, "SCHEMA_FIELDS_INVALID")
        p = cli(wire(e))
        self.assertEqual(p.returncode, 2)
        self.assertNotIn(b"synthetic-private-value", p.stderr + p.stdout)
        self.assertNotIn(b"apiKey", p.stderr + p.stdout)

    def test_duplicate_nested_json_key_is_rejected(self):
        raw = wire(event()).replace(b'"role": "TP1"', b'"role":"TP1","role":"STOP"')
        with self.assertRaisesRegex(g50.EvidenceError, "DUPLICATE_JSON_KEY"):
            g50.parse_document(raw)

    def test_nonfinite_json_numbers_are_rejected(self):
        for value in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(value=value), self.assertRaises(g50.EvidenceError):
                g50.parse_document(b'{"schema":' + value + b'}')

    def test_bad_utf8_bom_and_nested_json_fail_without_raw_text(self):
        for raw in (b"\xffsynthetic-private", b"\xef\xbb\xbf" + wire(),
                    b"[" * 1500 + b"]" * 1500):
            with self.subTest(raw_size=len(raw)), self.assertRaises(g50.EvidenceError) as raised:
                g50.parse_document(raw)
            self.assertNotIn("synthetic-private", str(raised.exception))

    def test_real_numeric_and_unredacted_ids_are_rejected(self):
        for value in (123456, "123456", "owner-private-identifier", "r:BAD", "r:" + "x" * 33):
            e = event()
            e["identity"]["order_id"] = value
            self.rejected(e, "REDACTED_ALIAS_REQUIRED")

    def test_all_identifier_domains_are_realiased_in_report(self):
        e = event("r:hidden-event")
        e["scope"].update(account="r:hidden-account", parent="r:hidden-scope", slot="r:hidden-slot")
        e["provenance"]["artifact_ref"] = "r:hidden-artifact"
        e["identity"].update(client_id="r:hidden-client", order_id="r:hidden-server")
        output = report(e).to_json()
        self.assertNotRegex(output, r'"r:[a-z]')
        self.assertNotIn("hidden", output)
        row = report(e).incidents[0].timeline[0]
        self.assertEqual(row.role, "TP1")
        self.assertTrue(row.slot_ref.startswith("slot-"))
        self.assertTrue(row.client_ref.startswith("client-"))

    def test_missing_or_unverified_provenance_is_not_promoted(self):
        e = event()
        e["provenance"]["authenticity"] = "VERIFIED"
        self.rejected(e, "ENUM_INVALID")
        del e["provenance"]["authenticity"]
        self.rejected(e, "SCHEMA_FIELDS_INVALID")

    def test_invalid_origin_cannot_become_real_evidence(self):
        e = event()
        e["provenance"]["origin"] = "REAL_CONFIRMED"
        self.rejected(e, "ENUM_INVALID")

    def test_caller_complete_claim_stays_unverified(self):
        e = event(origin="USER_REDACTED")
        e["provenance"]["coverage"] = "CALLER_CLAIMED_COMPLETE"
        r = report(e)
        self.assertIn(C.FRAGMENTARY_EVIDENCE, codes(r))
        self.assertIn(C.PROVENANCE_UNVERIFIED, codes(r))
        self.assertEqual(r.incidents[0].timeline[0].authenticity, "NOT_VERIFIED_OFFLINE")

    def test_unknown_incident_day_and_symbol_are_rejected(self):
        for key, value in (("incident_day", "2026-11-07"), ("symbol", "BTCUSDT"), ("symbol", "ususdt")):
            e = event()
            e[key] = value
            self.rejected(e, "ENUM_INVALID")

    def test_boolean_generation_is_not_integer_generation(self):
        e = event()
        e["scope"]["generation"] = True
        self.rejected(e, "GENERATION_INVALID")

    def test_sequence_requires_explicit_domain_and_integer(self):
        e = event(source="USER_STREAM")
        e["sequence"] = 1
        self.rejected(e, "SEQUENCE_DOMAIN_INVALID")
        e["sequence_domain"] = "r:stream"
        e["sequence"] = True
        self.rejected(e, "SEQUENCE_INVALID")

    def test_signed_snapshot_does_not_accept_stream_sequence(self):
        e = event()
        e.update(sequence=1, sequence_domain="r:stream")
        self.rejected(e, "SEQUENCE_DOMAIN_INVALID")

    def test_free_text_reason_and_actor_are_rejected(self):
        for key in ("actor", "reason"):
            e = event()
            e[key] = "synthetic-private-free-text"
            self.rejected(e, "ENUM_INVALID")

    def test_malformed_container_types_fail_closed(self):
        for key in ("identity", "scope", "provenance", "event_type", "status", "actor"):
            e = event()
            e[key] = ["synthetic-malformed"]
            self.rejected(e)


class TimestampTests(unittest.TestCase):
    def test_plus_three_timezone_converts_to_utc(self):
        self.assertEqual(g50.parse_utc("2026-11-06T12:00:00+03:00"),
                         g50.parse_utc("2026-11-06T09:00:00Z"))

    def test_negative_offset_crosses_calendar_day_without_relabeling_incident(self):
        e = event(day="2026-11-06")
        e.update(occurred_at="2026-11-06T23:30:00-03:00", observed_at="2026-11-07T02:31:00Z")
        r = report(e)
        self.assertEqual(r.incidents[0].incident_day, "2026-11-06")
        self.assertEqual(r.incidents[0].timeline[0].occurred_at_utc, "2026-11-07T02:30:00.000000Z")
        self.assertFalse(r.incidents[1].timeline)

    def test_naive_epoch_bool_and_unknown_offset_are_rejected(self):
        for value in ("2026-11-06T09:00:00", 1793955600, True, "2026-11-06T09:00:00-00:00"):
            with self.subTest(value=value), self.assertRaises(g50.EvidenceError):
                g50.parse_utc(value)

    def test_invalid_dates_offsets_leap_seconds_and_precision_are_rejected(self):
        for value in ("2026-11-31T00:00:00Z", "2026-11-06T00:00:00+14:01",
                      "2026-11-06T00:00:00+03:60", "2026-11-06T00:00:60Z",
                      "2026-11-06T00:00:00.1234567Z", "2026-11-06 00:00:00Z"):
            with self.subTest(value=value), self.assertRaises(g50.EvidenceError):
                g50.parse_utc(value)

    def test_microseconds_determine_timeline_order(self):
        a, b = event("r:a"), event("r:b")
        a["occurred_at"] = "2026-11-06T09:00:00.999999Z"
        b["occurred_at"] = "2026-11-06T09:00:00.000001Z"
        r = report(a, b)
        self.assertTrue(r.incidents[0].timeline[0].occurred_at_utc.endswith("000001Z"))

    def test_observation_before_event_is_review_required(self):
        e = event()
        e["observed_at"] = "2026-11-06T08:59:59Z"
        r = report(e)
        self.assertEqual(r.verdict, V.REVIEW_REQUIRED)
        self.assertIn(C.CLOCK_CONFLICT, codes(r))


class IdentityTests(unittest.TestCase):
    def rejected(self, e, code):
        with self.assertRaisesRegex(g50.EvidenceError, code):
            g50.parse_document(wire(e))

    def test_normal_tp_and_algo_stop_have_separate_server_namespaces(self):
        tp, stop = event(), event("r:stop-event", role="STOP")
        stop["identity"].update(algo_id="r:server", client_algo_id="r:tp")
        r = report(tp, stop)
        self.assertNotIn(C.IDENTITY_SCOPE_CONFLICT, codes(r))
        self.assertEqual({row.kind for row in r.incidents[0].timeline}, {"NORMAL", "ALGO"})
        self.assertEqual(len({row.server_ref for row in r.incidents[0].timeline}), 2)
        self.assertEqual(len({row.client_ref for row in r.incidents[0].timeline}), 2)

    def test_tp_algo_kind_is_rejected(self):
        e = event()
        e["identity"]["kind"] = "ALGO"
        self.rejected(e, "OWNER_ROLE_KIND_CONFLICT")

    def test_stop_normal_kind_is_rejected(self):
        e = event(role="STOP")
        e["identity"]["kind"] = "NORMAL"
        self.rejected(e, "OWNER_ROLE_KIND_CONFLICT")

    def test_wrong_endpoint_is_rejected(self):
        e = event()
        e["identity"]["endpoint"] = "/fapi/v1/algoOrder"
        self.rejected(e, "ENDPOINT_KIND_CONFLICT")

    def test_normal_client_cannot_use_algo_alias_field(self):
        e = event()
        e["identity"]["client_algo_id"] = "r:algo-client"
        self.rejected(e, "IDENTITY_NAMESPACE_CONFLICT")

    def test_algo_client_cannot_use_normal_client_or_order_field(self):
        for field in ("client_id", "order_id"):
            e = event(role="STOP")
            e["identity"][field] = "r:normal"
            self.rejected(e, "IDENTITY_NAMESPACE_CONFLICT")

    def test_client_binding_is_required_even_with_server_id(self):
        e = event()
        e["identity"]["client_id"] = None
        self.rejected(e, "IDENTITY_NAMESPACE_CONFLICT")

    def test_actual_order_id_requires_algo_id(self):
        e = event(role="STOP")
        e["identity"].update(algo_id=None, actual_order_id="r:child")
        self.rejected(e, "IDENTITY_NAMESPACE_CONFLICT")

    def test_actual_child_does_not_bind_to_normal_tp(self):
        tp, stop = event(), event("r:stop-event", role="STOP")
        stop["identity"]["actual_order_id"] = "r:server"
        r = report(tp, stop)
        self.assertNotIn(C.IDENTITY_SCOPE_CONFLICT, codes(r))
        children = [row.actual_order_ref for row in r.incidents[0].timeline if row.actual_order_ref]
        self.assertEqual(len(children), 1)
        self.assertNotIn(children[0], [row.server_ref for row in r.incidents[0].timeline])
        self.assertNotRegex(r.to_json(), r'"r:[a-z]')

    def test_status_and_algo_status_fields_are_distinct(self):
        e = event(role="STOP")
        e["status_field"] = "status"
        self.rejected(e, "ENUM_INVALID")

    def test_server_id_enrichment_with_same_client_is_not_conflict(self):
        a, b = event("r:a"), event("r:b", second=1)
        a["identity"]["order_id"] = None
        self.assertNotIn(C.SERVER_ID_CONFLICT, codes(report(a, b)))

    def test_server_id_change_for_same_client_is_conflict(self):
        a, b = event("r:a"), event("r:b", second=1)
        b["identity"]["order_id"] = "r:different-server"
        self.assertIn(C.SERVER_ID_CONFLICT, codes(report(a, b)))

    def test_same_server_with_two_clients_is_not_merged(self):
        a, b = event("r:a"), event("r:b")
        b["identity"]["client_id"] = "r:other-client"
        self.assertIn(C.IDENTITY_SCOPE_CONFLICT, codes(report(a, b)))

    def test_client_reuse_across_roles_is_review_required(self):
        a, b = event("r:a"), event("r:b", role="TP2")
        self.assertIn(C.IDENTITY_SCOPE_CONFLICT, codes(report(a, b)))

    def test_scope_slot_generation_and_position_side_do_not_cross_bind(self):
        for field, value in (("parent", "r:other"), ("slot", "r:other"),
                             ("generation", 2), ("position_side", "LONG")):
            a, b = event("r:a"), event("r:b")
            b["scope"][field] = value
            with self.subTest(field=field):
                r = report(a, b)
                self.assertIn(C.IDENTITY_SCOPE_CONFLICT, codes(r))
                self.assertEqual(len({row.lifecycle_ref for row in r.incidents[0].timeline}), 2)

    def test_account_namespaces_do_not_conflict(self):
        a, b = event("r:a"), event("r:b")
        b["scope"]["account"] = "r:other-account"
        self.assertNotIn(C.IDENTITY_SCOPE_CONFLICT, codes(report(a, b)))

    def test_ususdt_two_days_do_not_share_ids_or_event_refs(self):
        a, b = event(), event(day="2026-11-09", status="CANCELED")
        b["identity"]["order_id"] = "r:different-server"
        r = report(a, b)
        for n in (0, 1):
            self.assertNotIn(C.SERVER_ID_CONFLICT, codes(r, n))
            self.assertNotIn(C.EVENT_REF_CONFLICT, codes(r, n))
            self.assertNotIn(C.TERMINAL_STATE_CONFLICT, codes(r, n))
            self.assertEqual(len(r.incidents[n].timeline), 1)

    def test_synthetic_and_user_namespaces_never_merge(self):
        a, b = event(), event(origin="USER_REDACTED", status="CANCELED")
        b["identity"]["order_id"] = "r:other-server"
        r = report(a, b)
        self.assertNotIn(C.STATE_CONFLICT, codes(r))
        self.assertNotIn(C.EVENT_REF_CONFLICT, codes(r))
        self.assertNotIn(C.SERVER_ID_CONFLICT, codes(r))
        self.assertEqual(len({row.lifecycle_ref for row in r.incidents[0].timeline}), 2)


class EvidenceSemanticsTests(unittest.TestCase):
    def test_404_is_not_canceled_or_filled(self):
        r = report(event(event_type="QUERY_MISSING"))
        self.assertIn(C.QUERY_MISSING_NOT_CANCELED, codes(r))
        self.assertNotIn(C.CANCELED_REPORTED, codes(r))
        self.assertNotIn(C.FILL_REPORTED, codes(r))
        self.assertIn(C.SIGNED_ORDER_EVIDENCE_MISSING, codes(r))

    def test_order_not_found_is_not_a_terminal_state(self):
        e = event(event_type="QUERY_MISSING")
        e["query_code"] = "ORDER_NOT_FOUND"
        r = report(e)
        self.assertIn(C.QUERY_MISSING_NOT_CANCELED, codes(r))
        self.assertIsNone(r.incidents[0].timeline[0].status_claim)

    def test_open_list_absence_is_not_fill_or_cancel(self):
        r = report(event(event_type="OPEN_LIST_ABSENT"))
        self.assertIn(C.OPEN_ABSENCE_NOT_TERMINAL, codes(r))
        self.assertNotIn(C.CANCELED_REPORTED, codes(r))
        self.assertNotIn(C.FILL_REPORTED, codes(r))

    def test_expired_does_not_determine_exchange_cause(self):
        r = report(event(status="EXPIRED", origin="USER_REDACTED"))
        self.assertIn(C.EXPIRY_CAUSE_UNRESOLVED, codes(r))
        self.assertIn(C.ACTOR_UNKNOWN, codes(r))
        self.assertNotIn(C.CANCELED_REPORTED, codes(r))
        self.assertEqual(r.real_incident_cause, V.UNRESOLVED)

    def test_expired_in_match_does_not_prove_stp(self):
        r = report(event(status="EXPIRED_IN_MATCH"))
        self.assertIn(C.EXPIRY_CAUSE_UNRESOLVED, codes(r))
        self.assertIsNone(r.incidents[0].timeline[0].reason_claim)
        self.assertEqual(r.real_incident_cause, V.UNRESOLVED)

    def test_signed_flat_does_not_cancel_all_tps(self):
        r = report(event(event_type="POSITION", origin="USER_REDACTED"))
        self.assertIn(C.FLAT_NOT_TP_CANCEL_PROOF, codes(r))
        self.assertIn(C.TP_EVIDENCE_MISSING, codes(r))
        self.assertNotIn(C.CANCELED_REPORTED, codes(r))

    def test_nonzero_position_does_not_imply_flat(self):
        e = event(event_type="POSITION")
        e["position_qty"] = "-2.5"
        self.assertNotIn(C.FLAT_NOT_TP_CANCEL_PROOF, codes(report(e)))

    def test_cancel_wal_is_intent_not_confirmed_delete(self):
        r = report(event(event_type="CANCEL_INTENT", source="WAL"))
        self.assertIn(C.CANCEL_NOT_CONFIRMED, codes(r))
        self.assertNotIn(C.CANCELED_REPORTED, codes(r))
        self.assertNotIn(C.DELETE_ACK_REPORTED, codes(r))

    def test_wal_then_canceled_without_delete_ack_stays_unconfirmed(self):
        a = event("r:wal", event_type="CANCEL_INTENT", source="WAL")
        b = event("r:state", status="CANCELED", second=1)
        r = report(a, b)
        self.assertIn(C.CANCEL_NOT_CONFIRMED, codes(r))
        self.assertIn(C.TEMPORAL_ASSOCIATION_ONLY, codes(r))
        self.assertIn(C.ACTOR_UNKNOWN, codes(r))

    def test_delete_ack_is_only_a_provenance_claim(self):
        a = event("r:wal", event_type="CANCEL_INTENT", source="WAL")
        b = event("r:ack", event_type="DELETE_ACK", status="CANCELED", second=1)
        r = report(a, b)
        self.assertIn(C.DELETE_ACK_REPORTED, codes(r))
        self.assertIn(C.TEMPORAL_ASSOCIATION_ONLY, codes(r))
        self.assertNotIn(C.CANCEL_NOT_CONFIRMED, codes(r))
        self.assertIn(C.PROVENANCE_UNVERIFIED, codes(r))
        self.assertEqual(r.real_incident_cause, V.UNRESOLVED)

    def test_delete_ack_before_intent_does_not_confirm_it(self):
        a = event("r:wal", event_type="CANCEL_INTENT", source="WAL", second=2)
        b = event("r:ack", event_type="DELETE_ACK", status="CANCELED", second=1)
        r = report(a, b)
        self.assertIn(C.CANCEL_NOT_CONFIRMED, codes(r))
        self.assertNotIn(C.TEMPORAL_ASSOCIATION_ONLY, codes(r))

    def test_delete_ack_from_other_slot_is_not_linked(self):
        a = event("r:wal", event_type="CANCEL_INTENT", source="WAL")
        b = event("r:ack", event_type="DELETE_ACK", status="CANCELED", second=1)
        b["scope"]["slot"] = "r:other-slot"
        self.assertIn(C.CANCEL_NOT_CONFIRMED, codes(report(a, b)))
        self.assertNotIn(C.TEMPORAL_ASSOCIATION_ONLY, codes(report(a, b)))

    def test_conflicting_server_id_prevents_intent_ack_link(self):
        a = event("r:wal", event_type="CANCEL_INTENT", source="WAL")
        b = event("r:ack", event_type="DELETE_ACK", status="CANCELED", second=1)
        b["identity"]["order_id"] = "r:other-server"
        r = report(a, b)
        self.assertIn(C.SERVER_ID_CONFLICT, codes(r))
        self.assertIn(C.CANCEL_NOT_CONFIRMED, codes(r))
        self.assertNotIn(C.TEMPORAL_ASSOCIATION_ONLY, codes(r))

    def test_synthetic_ack_cannot_confirm_user_intent(self):
        a = event("r:wal", event_type="CANCEL_INTENT", source="WAL", origin="USER_REDACTED")
        b = event("r:ack", event_type="DELETE_ACK", status="CANCELED", second=1)
        r = report(a, b)
        self.assertIn(C.CANCEL_NOT_CONFIRMED, codes(r))
        self.assertNotIn(C.TEMPORAL_ASSOCIATION_ONLY, codes(r))

    def test_november_nine_ack_cannot_confirm_november_six_intent(self):
        a = event("r:wal", event_type="CANCEL_INTENT", source="WAL")
        b = event("r:ack", day="2026-11-09", event_type="DELETE_ACK", status="CANCELED")
        r = report(a, b)
        self.assertIn(C.CANCEL_NOT_CONFIRMED, codes(r, 0))
        self.assertNotIn(C.TEMPORAL_ASSOCIATION_ONLY, codes(r, 0))
        self.assertNotIn(C.TEMPORAL_ASSOCIATION_ONLY, codes(r, 1))

    def test_algo_new_or_working_is_never_stop_proof(self):
        for status in ("NEW", "WORKING"):
            r = report(event(role="STOP", status=status, origin="USER_REDACTED"))
            with self.subTest(status=status):
                self.assertIs(r.authority.stop_proven, False)
                self.assertIs(r.authority.entry_authorized, False)
                self.assertEqual(r.real_incident_cause, V.UNRESOLVED)

    def test_all_sources_flat_and_both_tp_states_still_do_not_resolve_cause(self):
        a = event("r:wal", event_type="CANCEL_INTENT", source="WAL", second=1)
        b = event("r:ack", event_type="DELETE_ACK", status="CANCELED", second=2)
        c = event("r:stream", status="NEW", source="USER_STREAM")
        d = event("r:tp2", role="TP2", status="FILLED", second=3)
        d["executed_qty"] = "10"
        d["identity"].update(client_id="r:tp2-client", order_id="r:tp2-server")
        e = event("r:flat", event_type="POSITION", second=4)
        for item in (a, b, c, d, e):
            item["provenance"].update(origin="USER_REDACTED", coverage="CALLER_CLAIMED_COMPLETE")
        r = report(a, b, c, d, e)
        self.assertNotIn(C.MISSING_SOURCE, codes(r))
        self.assertNotIn(C.TP_EVIDENCE_MISSING, codes(r))
        self.assertNotIn(C.MISSING_REAL_EVIDENCE, codes(r))
        self.assertIn(C.FLAT_NOT_TP_CANCEL_PROOF, codes(r))
        self.assertEqual(r.real_incident_cause, V.UNRESOLVED)
        self.assertEqual(r.incidents[0].verdict, V.UNRESOLVED)
        self.assertTrue(all(v is False for v in r.to_dict()["authority"].values()))

    def test_actor_and_reason_claims_do_not_resolve_real_cause(self):
        e = event(status="CANCELED", origin="USER_REDACTED")
        e.update(actor="OWNER_REPORTED", reason="REDUCE_ONLY_REPORTED")
        r = report(e)
        self.assertIn(C.CLAIM_UNVERIFIED, codes(r))
        self.assertNotIn(C.ACTOR_UNKNOWN, codes(r))
        self.assertEqual(r.real_incident_cause, V.UNRESOLVED)

    def test_missing_real_sources_are_explicit(self):
        r = report(event(origin="USER_REDACTED"))
        missing = [f.detail for f in r.incidents[0].findings if f.code == C.MISSING_SOURCE]
        self.assertEqual(len(missing), 2)
        self.assertTrue(any("WAL" in d for d in missing))
        self.assertTrue(any("USER_STREAM" in d for d in missing))

    def test_synthetic_tp2_does_not_close_missing_real_tp2(self):
        a, b = event(origin="USER_REDACTED"), event("r:tp2", role="TP2")
        b["identity"].update(client_id="r:tp2-client", order_id="r:tp2-server")
        missing = [f.detail for f in report(a, b).incidents[0].findings if f.code == C.TP_EVIDENCE_MISSING]
        self.assertEqual(len(missing), 1)
        self.assertIn("TP2", missing[0])

    def test_stream_state_does_not_substitute_signed_state(self):
        r = report(event(source="USER_STREAM", status="CANCELED"))
        self.assertIn(C.SIGNED_ORDER_EVIDENCE_MISSING, codes(r))

    def test_wal_cannot_claim_exchange_order_state(self):
        with self.assertRaisesRegex(g50.EvidenceError, "WAL_IS_NOT_EXCHANGE_STATE"):
            g50.parse_document(wire(event(source="WAL", status="CANCELED")))

    def test_delete_ack_stream_source_is_rejected(self):
        with self.assertRaisesRegex(g50.EvidenceError, "DELETE_ACK_SOURCE_INVALID"):
            g50.parse_document(wire(event(source="USER_STREAM", event_type="DELETE_ACK", status="CANCELED")))

    def test_cancel_intent_receipt_source_is_rejected(self):
        with self.assertRaisesRegex(g50.EvidenceError, "CANCEL_INTENT_SOURCE_INVALID"):
            g50.parse_document(wire(event(event_type="CANCEL_INTENT")))

    def test_query_absence_cannot_carry_canceled_status(self):
        e = event(event_type="QUERY_MISSING")
        e.update(status="CANCELED", status_field="status")
        with self.assertRaisesRegex(g50.EvidenceError, "STATUS_FORBIDDEN"):
            g50.parse_document(wire(e))

    def test_position_must_have_quantity_and_no_order_identity(self):
        e = event(event_type="POSITION")
        e["position_qty"] = None
        with self.assertRaisesRegex(g50.EvidenceError, "POSITION_QUANTITY_REQUIRED_OR_FORBIDDEN"):
            g50.parse_document(wire(e))
        e["position_qty"] = "0"
        e["identity"] = event()["identity"]
        with self.assertRaisesRegex(g50.EvidenceError, "EVENT_IDENTITY_REQUIRED_OR_FORBIDDEN"):
            g50.parse_document(wire(e))


class FillAndConflictTests(unittest.TestCase):
    def test_partial_fill_then_canceled_retains_partial_evidence(self):
        a, b = event("r:partial", status="PARTIALLY_FILLED"), event("r:canceled", status="CANCELED", second=1)
        a["executed_qty"] = b["executed_qty"] = "3"
        r = report(a, b)
        self.assertIn(C.PARTIAL_FILL_REPORTED, codes(r))
        self.assertIn(C.CANCELED_REPORTED, codes(r))
        self.assertNotIn(C.FILL_REPORTED, codes(r))
        self.assertNotIn(C.FILL_REGRESSION, codes(r))

    def test_partial_tp1_does_not_infer_tp2_exchange_expiry(self):
        a, b = event("r:partial", status="PARTIALLY_FILLED"), event("r:tp2", role="TP2")
        a["executed_qty"] = "3"
        b["identity"].update(client_id="r:tp2-client", order_id="r:tp2-server")
        r = report(a, b)
        self.assertNotIn(C.EXPIRY_CAUSE_UNRESOLVED, codes(r))
        self.assertNotIn(C.CANCELED_REPORTED, codes(r))
        self.assertEqual(r.real_incident_cause, V.UNRESOLVED)

    def test_filled_is_reported_only_for_matching_full_quantity(self):
        e = event(status="FILLED")
        e["executed_qty"] = "10"
        self.assertIn(C.FILL_REPORTED, codes(report(e)))
        e["executed_qty"] = "3"
        self.assertNotIn(C.FILL_REPORTED, codes(report(e)))
        self.assertIn(C.QUANTITY_CONFLICT, codes(report(e)))

    def test_partial_status_with_zero_or_full_fill_is_conflict(self):
        for qty in ("0", "10"):
            e = event(status="PARTIALLY_FILLED")
            e["executed_qty"] = qty
            with self.subTest(qty=qty):
                self.assertIn(C.QUANTITY_CONFLICT, codes(report(e)))

    def test_executed_greater_than_original_is_review_required(self):
        e = event(status="CANCELED")
        e["executed_qty"] = "11"
        r = report(e)
        self.assertEqual(r.verdict, V.REVIEW_REQUIRED)
        self.assertIn(C.QUANTITY_CONFLICT, codes(r))

    def test_new_with_positive_executed_quantity_is_conflict(self):
        e = event(status="NEW")
        e["executed_qty"] = "1"
        self.assertIn(C.QUANTITY_CONFLICT, codes(report(e)))

    def test_missing_quantities_cannot_prove_full_or_partial_fill(self):
        e = event(status="FILLED")
        e.update(orig_qty=None, executed_qty=None)
        r = report(e)
        self.assertIn(C.QUANTITY_EVIDENCE_MISSING, codes(r))
        self.assertNotIn(C.FILL_REPORTED, codes(r))
        self.assertNotIn(C.PARTIAL_FILL_REPORTED, codes(r))

    def test_noncanonical_negative_float_and_exponent_quantities_are_rejected(self):
        for qty in ("-1", 1.25, "NaN", "1e2", "+1", "01", "1." + "1" * 19):
            e = event()
            e["executed_qty"] = qty
            with self.subTest(qty=qty), self.assertRaisesRegex(g50.EvidenceError, "QUANTITY_INVALID"):
                g50.parse_document(wire(e))

    def test_changed_original_quantity_is_conflict(self):
        a, b = event("r:a"), event("r:b", second=1)
        b["orig_qty"] = "11"
        self.assertIn(C.QUANTITY_CONFLICT, codes(report(a, b)))

    def test_fill_regression_is_not_hidden_by_later_status(self):
        a, b = event("r:a", status="PARTIALLY_FILLED"), event("r:b", status="CANCELED", second=1)
        a["executed_qty"], b["executed_qty"] = "4", "2"
        self.assertIn(C.FILL_REGRESSION, codes(report(a, b)))

    def test_same_timestamp_conflicting_fill_quantities_are_review_required(self):
        a, b = event("r:a", status="PARTIALLY_FILLED"), event("r:b", status="PARTIALLY_FILLED")
        a["executed_qty"], b["executed_qty"] = "2", "4"
        self.assertIn(C.QUANTITY_CONFLICT, codes(report(a, b)))

    def test_same_timestamp_status_conflict_does_not_choose_signed_over_stream(self):
        a, b = event("r:a", status="NEW"), event("r:b", source="USER_STREAM", status="CANCELED")
        r = report(a, b)
        self.assertIn(C.STATE_CONFLICT, codes(r))
        self.assertEqual(len(r.incidents[0].timeline), 2)
        self.assertEqual(r.verdict, V.REVIEW_REQUIRED)

    def test_terminal_then_new_is_conflict(self):
        a, b = event("r:a", status="CANCELED"), event("r:b", second=1)
        self.assertIn(C.TERMINAL_STATE_CONFLICT, codes(report(a, b)))

    def test_out_of_order_input_is_sorted_without_inventing_conflict(self):
        a, b = event("r:a"), event("r:b", second=1, status="CANCELED")
        r = report(b, a)
        self.assertNotIn(C.TERMINAL_STATE_CONFLICT, codes(r))
        self.assertEqual([row.status_claim for row in r.incidents[0].timeline], ["NEW", "CANCELED"])


class ReplayAndCliTests(unittest.TestCase):
    def test_timeline_findings_and_aliases_are_deterministic_for_all_permutations(self):
        events = [event("r:a"), event("r:b", status="CANCELED", source="USER_STREAM"),
                  event("r:c", event_type="CANCEL_INTENT", source="WAL"),
                  event("r:d", day="2026-11-09")]
        expected = report(*events).to_json()
        for permutation in itertools.permutations(events):
            self.assertEqual(report(*permutation).to_json(), expected)

    def test_exact_duplicate_is_deduplicated_not_extra_evidence(self):
        e = event()
        r = report(e, copy.deepcopy(e))
        self.assertIn(C.DUPLICATE_EVENT, codes(r))
        self.assertEqual(len(r.incidents[0].timeline), 1)

    def test_timezone_equivalent_duplicate_is_deduplicated(self):
        a, b = event(), event()
        b["occurred_at"] = "2026-11-06T12:00:00+03:00"
        r = report(a, b)
        self.assertIn(C.DUPLICATE_EVENT, codes(r))
        self.assertEqual(len(r.incidents[0].timeline), 1)

    def test_event_ref_collision_retains_both_claims(self):
        a, b = event(), event(status="CANCELED")
        r = report(a, b)
        self.assertIn(C.EVENT_REF_CONFLICT, codes(r))
        self.assertEqual(len(r.incidents[0].timeline), 2)

    def test_stream_sequence_gap_only_describes_supplied_fragment(self):
        a, b = event("r:a", source="USER_STREAM"), event("r:b", source="USER_STREAM", second=1)
        a.update(sequence=5, sequence_domain="r:stream")
        b.update(sequence=7, sequence_domain="r:stream")
        r = report(a, b)
        self.assertIn(C.SEQUENCE_DISCONTINUITY, codes(r))
        f = next(f for f in r.incidents[0].findings if f.code == C.SEQUENCE_DISCONTINUITY)
        self.assertIn("çıkarılmaz", f.detail)

    def test_sequence_reuse_is_a_conflict(self):
        a, b = event("r:a", source="WAL", event_type="CANCEL_INTENT"), event("r:b", source="WAL", event_type="CANCEL_INTENT")
        a.update(sequence=5, sequence_domain="r:wal")
        b.update(sequence=5, sequence_domain="r:wal")
        self.assertIn(C.SEQUENCE_CONFLICT, codes(report(a, b)))

    def test_sequence_domains_and_days_are_separate(self):
        a, b, c = event("r:a", source="USER_STREAM"), event("r:b", source="USER_STREAM"), event("r:c", source="USER_STREAM", day="2026-11-09")
        a.update(sequence=1, sequence_domain="r:stream-a")
        b.update(sequence=99, sequence_domain="r:stream-b")
        c.update(sequence=900, sequence_domain="r:stream-a")
        r = report(a, b, c)
        for n in (0, 1):
            self.assertNotIn(C.SEQUENCE_DISCONTINUITY, codes(r, n))
            self.assertNotIn(C.SEQUENCE_CONFLICT, codes(r, n))

    def test_explicit_stream_gap_is_review_required(self):
        r = report(event(event_type="STREAM_GAP", source="USER_STREAM"))
        self.assertIn(C.STREAM_GAP_REPORTED, codes(r))
        self.assertEqual(r.verdict, V.REVIEW_REQUIRED)

    def test_typed_frozen_findings_and_report_cannot_be_modified(self):
        r = report(event())
        f = r.incidents[0].findings[0]
        self.assertIsInstance(f.code, g50.FindingCode)
        self.assertIsInstance(f.verdict, g50.Verdict)
        with self.assertRaises(FrozenInstanceError):
            r.authority.unlock = True
        with self.assertRaises(FrozenInstanceError):
            r.real_incident_cause = "RESOLVED"

    def test_cli_demo_emits_safe_json_without_stderr(self):
        p = cli(b"synthetic-input-must-not-be-read", "--demo")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stderr, b"")
        d = json.loads(p.stdout)
        self.assertEqual(d["real_incident_cause"], "UNRESOLVED")
        self.assertEqual(len(d["incidents"]), 2)
        self.assertNotRegex(p.stdout, b'"r:[a-z]')

    def test_cli_stdin_consumes_only_normalized_redacted_json(self):
        p = cli(wire(event(origin="USER_REDACTED")))
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertEqual(d["incidents"][0]["timeline"][0]["origin"], "USER_REDACTED")
        self.assertTrue(all(value is False for value in d["authority"].values()))

    def test_cli_argument_errors_do_not_echo_identifiers_or_paths(self):
        p = cli(b"", "--input", "/synthetic/private/identifier")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stdout, b"")
        self.assertNotIn(b"/synthetic/private", p.stderr)
        self.assertEqual(json.loads(p.stderr)["error"], "CLI_ARGUMENTS_INVALID")

    def test_cli_parse_errors_never_echo_raw_input_or_traceback(self):
        p = cli(b"synthetic-private-unredacted-body")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn(b"synthetic-private", p.stdout + p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)
        d = json.loads(p.stderr)
        self.assertEqual(d["verdict"], "REVIEW_REQUIRED")
        self.assertEqual(d["real_incident_cause"], "UNRESOLVED")

    def test_cli_enforces_stdin_limit_without_echo(self):
        p = cli(b"x" * (g50.MAX_BYTES + 1))
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stderr)["error"], "INPUT_SIZE_OR_TYPE_INVALID")

    def test_cli_unexpected_exception_is_sanitized(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(g50, "analyze_document", side_effect=RuntimeError("synthetic-private-path")), \
             patch("sys.stdout", output), patch("sys.stderr", error):
            self.assertEqual(g50.main(["--demo"]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("synthetic-private-path", error.getvalue())
        self.assertEqual(json.loads(error.getvalue())["error"], "OFFLINE_ANALYSIS_FAILED")

    def test_cli_help_needs_no_input_or_external_files(self):
        p = cli(b"synthetic-private", "--help")
        self.assertEqual(p.returncode, 0)
        self.assertIn("stdin", p.stdout.decode())
        self.assertEqual(p.stderr, b"")

    def test_analysis_does_not_mutate_caller_document_or_keep_state(self):
        e = event()
        before = copy.deepcopy(e)
        first = report(e).to_json()
        report(event(status="CANCELED"))
        self.assertEqual(e, before)
        self.assertEqual(first, report(e).to_json())

    def test_serialized_report_mutation_cannot_change_source_reference(self):
        expected = report(event()).to_json()
        d = report(event()).to_dict()
        d["source_reference"]["callgraph"].append("synthetic-caller-mutation")
        self.assertEqual(report(event()).to_json(), expected)

    def test_cli_output_is_identical_across_fresh_processes(self):
        raw = wire(event("r:a"), event("r:b", source="USER_STREAM", status="CANCELED"))
        first, second = cli(raw), cli(raw)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)

    def test_conflicting_batch_does_not_generate_pairwise_findings(self):
        events = [event(f"r:e{n}", status="CANCELED" if n % 2 else "NEW") for n in range(256)]
        r = report(*events)
        self.assertIn(C.STATE_CONFLICT, codes(r))
        self.assertEqual(len(r.incidents[0].timeline), 256)
        self.assertLess(len(r.incidents[0].findings), 256 * 5)


class SourceReferenceTests(unittest.TestCase):
    def test_reference_function_lines_match_read_only_development_source(self):
        tree = ast.parse((ROOT / "owner_single_execution.py").read_text())
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("actual_order", "checked", "cleanup_owned_exits"):
            self.assertEqual(functions[name].lineno, g50.SOURCE_REFERENCE[name])

    def test_reference_callgraph_matches_ast_without_importing_owner(self):
        tree = ast.parse((ROOT / "owner_single_execution.py").read_text())
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        def calls(name):
            return {n.func.id for n in ast.walk(functions[name])
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertIn("actual_order", calls("checked"))
        self.assertIn("checked", calls("one_write"))
        self.assertIn("cleanup_owned_exits", calls("run_once"))
        self.assertIn("one_write", calls("run_once"))
        cleanup = functions["cleanup_owned_exits"]
        journals = [n.lineno for n in ast.walk(cleanup) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == "journal"]
        deletes = [n.lineno for n in ast.walk(cleanup) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == "delete_once"]
        self.assertEqual(len(journals), 2)
        self.assertEqual(len(deletes), 2)
        self.assertTrue(all(a < b for a, b in zip(sorted(journals), sorted(deletes))))

    def test_analyzer_imports_only_stdlib_and_has_no_file_or_network_operations(self):
        tree = ast.parse(SCRIPT.read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(n.name.split(".")[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "eval", "exec", "__import__"})
        self.assertLessEqual(imports, sys.stdlib_module_names)
        self.assertTrue(imports.isdisjoint({"socket", "urllib", "http", "subprocess", "os", "pathlib"}))


if __name__ == "__main__":
    unittest.main()
