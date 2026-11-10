"""G46: gerçek importer/SDK kullanmadan V1 negatif sözleşme testleri."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import unittest

import identity_decision_contract_offline_g46 as g46


def identity(**changes):
    data = dict(
        schema_version=1, environment=g46.Environment.OFFLINE, venue="MOCK_VENUE",
        account_id="account-1", parent_scope_id="parent-1", execution_epoch=10,
        slot_id="A", slot_generation=20, symbol="BTCUSDT",
        position_mode=g46.PositionMode.HEDGE, position_side=g46.PositionSide.LONG,
        order_role=g46.OrderRole.ENTRY, order_kind=g46.OrderKind.NORMAL,
        order_identity=g46.OrderIdentityV1("client-1", None, None),
    )
    data.update(changes)
    return g46.CanonicalIdentityV1(**data)


def other_order(client="client-2", server=None, **changes):
    return identity(order_identity=g46.OrderIdentityV1(client, None, server), **changes)


def protective(role=g46.OrderRole.STOP, client="client-1", server=None, **changes):
    return identity(order_role=role, order_kind=g46.OrderKind.ALGO,
                    order_identity=g46.OrderIdentityV1(None, client, server), **changes)


def envelope(n=1, **changes):
    ident = changes.pop("identity", None)
    if ident is None:
        ident = identity()
    status = changes.get("status", g46.ModelStatus.RESERVED)
    kind = {
        g46.ModelStatus.ACKED: g46.EvidenceKind.CALLBACK_ACK,
        g46.ModelStatus.DURABLE: g46.EvidenceKind.LOCAL_JOURNAL,
        g46.ModelStatus.NEW: g46.EvidenceKind.MOCK_OBSERVATION,
        g46.ModelStatus.PARTIAL: g46.EvidenceKind.MOCK_OBSERVATION,
        g46.ModelStatus.FILLED: g46.EvidenceKind.MOCK_OBSERVATION,
        g46.ModelStatus.MISSING: g46.EvidenceKind.MOCK_OBSERVATION,
    }.get(status, g46.EvidenceKind.MODEL_RESULT)
    provenance, durability = {
        g46.EvidenceKind.MODEL_RESULT: (g46.Provenance.OFFLINE_MODEL,
                                       g46.DurabilityState.NOT_CONFIRMED),
        g46.EvidenceKind.MOCK_OBSERVATION: (g46.Provenance.OFFLINE_MODEL,
                                           g46.DurabilityState.NOT_CONFIRMED),
        g46.EvidenceKind.CALLBACK_ACK: (g46.Provenance.OFFLINE_CALLBACK,
                                       g46.DurabilityState.CALLBACK_ACK_ONLY),
        g46.EvidenceKind.LOCAL_JOURNAL: (g46.Provenance.OFFLINE_LOCAL_JOURNAL,
                                        g46.DurabilityState.LOCAL_FSYNC_ACK_ONLY),
    }[kind]
    data = dict(
        schema_version=1, event_id=f"event-{n}", parent_event_id=None, identity=ident,
        lease=g46.LeaseV1(30, 40, "mock-owner"),
        risk_reservation_id="risk-" + ident.identity_hash[:12],
        intent_hash="1" * 64, policy_hash="2" * 64,
        recorded_at_monotonic_source="mock-clock-1", recorded_at_monotonic_ns=n * 100,
        recorded_at_utc_observed="2026-11-10T12:00:00Z", evidence_kind=kind,
        status=status, provenance=provenance, wal_sequence=n, durability_state=durability,
    )
    data.update(changes)
    return g46.DecisionEnvelopeV1(**data)


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


class ContractAssertions(unittest.TestCase):
    def assertClosed(self, value):
        self.assertIs(value.entry_authorized, False)
        self.assertIs(value.unlock, False)
        self.assertIs(value.stop_proven, False)
        self.assertIs(value.decision, g46.DecisionCode.FAIL_CLOSED)
        self.assertIs(value.retry_policy, g46.RetryPolicy.NO_RETRY)

    def assertRejected(self, call, code=None):
        with self.assertRaises(g46.ContractError) as caught:
            call()
        if code is not None:
            self.assertEqual(str(caught.exception), code)


class IdentityTests(ContractAssertions):
    def test_valid_identity_round_trip(self):
        original = identity()
        self.assertEqual(g46.CanonicalIdentityV1.from_bytes(original.canonical_bytes()), original)

    def test_composite_key_has_all_thirteen_components_in_contract_order(self):
        value = identity()
        self.assertEqual(value.composite_key, (
            g46.Environment.OFFLINE, "MOCK_VENUE", "account-1", "parent-1", 10, "A", 20,
            "BTCUSDT", g46.PositionMode.HEDGE, g46.PositionSide.LONG,
            g46.OrderRole.ENTRY, g46.OrderKind.NORMAL, value.order_identity))

    def test_each_namespace_component_separates_keys(self):
        original = identity()
        variants = [identity(environment=g46.Environment.TESTNET),
                    identity(environment=g46.Environment.MAINNET), identity(venue="OTHER"),
                    identity(account_id="account-2"), identity(parent_scope_id="parent-2"),
                    identity(execution_epoch=11), identity(slot_id="B"),
                    identity(slot_generation=21), identity(symbol="ETHUSDT"),
                    identity(position_side=g46.PositionSide.SHORT),
                    identity(position_mode=g46.PositionMode.ONE_WAY,
                             position_side=g46.PositionSide.BOTH),
                    protective(), protective(g46.OrderRole.TP1),
                    protective(g46.OrderRole.TP2), other_order(),
                    identity(order_identity=g46.OrderIdentityV1("client-1", None, "99"))]
        keys = {original.composite_key, *(v.composite_key for v in variants)}
        hashes = {original.identity_hash, *(v.identity_hash for v in variants)}
        self.assertEqual(len(keys), 1 + len(variants))
        self.assertEqual(len(hashes), 1 + len(variants))

    def test_all_identity_fields_required(self):
        source = identity().to_dict()
        for field in source:
            with self.subTest(field=field):
                data = dict(source)
                del data[field]
                self.assertRejected(lambda: g46.CanonicalIdentityV1.from_dict(data),
                                    "FIELD_SET_MISMATCH")

    def test_extra_identity_fields_rejected(self):
        data = identity().to_dict()
        data["lease_epoch"] = 1
        self.assertRejected(lambda: g46.CanonicalIdentityV1.from_dict(data), "FIELD_SET_MISMATCH")

    def test_version_is_exact_integer_one(self):
        for value in (0, 2, True, 1.0, "1", None):
            with self.subTest(value=value):
                self.assertRejected(lambda: identity(schema_version=value), "UNSUPPORTED_VERSION")

    def test_epochs_and_generations_reject_coercion_and_overflow(self):
        for field in ("execution_epoch", "slot_generation"):
            for value in (True, 1.0, "1", 0, -1, 2**63, None):
                with self.subTest(field=field, value=value):
                    self.assertRejected(lambda: identity(**{field: value}), "INVALID_INTEGER")

    def test_generation_upper_bound_is_accepted(self):
        self.assertEqual(identity(slot_generation=2**63 - 1).slot_generation, 2**63 - 1)

    def test_unknown_and_alias_enums_rejected(self):
        for field, value in (("environment", "offline"), ("environment", "PROD"),
                             ("position_mode", "DUAL"), ("position_side", "BUY"),
                             ("order_role", "TP"), ("order_kind", "CONDITIONAL")):
            with self.subTest(field=field):
                data = identity().to_dict()
                data[field] = value
                self.assertRejected(lambda: g46.CanonicalIdentityV1.from_dict(data), "UNKNOWN_ENUM")

    def test_direct_constructor_requires_typed_enums(self):
        self.assertRejected(lambda: identity(environment="OFFLINE"), "INVALID_ENUM_TYPE")

    def test_names_are_not_normalized(self):
        for value in ("", " account", "account ", "a/b", "a:b", "é", "a\n", "x" * 65):
            with self.subTest(value=value):
                self.assertRejected(lambda: identity(account_id=value), "INVALID_NAME")
        self.assertNotEqual(identity(account_id="Account"), identity(account_id="account"))

    def test_symbols_venues_and_slots_are_strict(self):
        for field, values in (("symbol", ("btcusdt", "BTC/USDT", "", "A")),
                              ("venue", ("mock", " MOCK", "")),
                              ("slot_id", ("a", "C", "", 1))):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertRejected(lambda: identity(**{field: value}))

    def test_one_way_rejects_long_and_short(self):
        for side in (g46.PositionSide.LONG, g46.PositionSide.SHORT):
            self.assertRejected(lambda: identity(position_mode=g46.PositionMode.ONE_WAY,
                                                 position_side=side), "POSITION_MODE_SIDE_MISMATCH")

    def test_hedge_rejects_both(self):
        self.assertRejected(lambda: identity(position_side=g46.PositionSide.BOTH),
                            "POSITION_MODE_SIDE_MISMATCH")

    def test_role_kind_mismatches_rejected(self):
        self.assertRejected(lambda: identity(order_kind=g46.OrderKind.ALGO),
                            "ORDER_ROLE_KIND_MISMATCH")
        for role in (g46.OrderRole.STOP, g46.OrderRole.TP1, g46.OrderRole.TP2):
            self.assertRejected(lambda: identity(order_role=role), "ORDER_ROLE_KIND_MISMATCH")

    def test_normal_and_algo_require_the_correct_client_field(self):
        self.assertRejected(lambda: identity(order_identity=g46.OrderIdentityV1(None, "c", None)),
                            "ORDER_CLIENT_NAMESPACE_MISMATCH")
        self.assertRejected(lambda: identity(order_role=g46.OrderRole.STOP,
            order_kind=g46.OrderKind.ALGO, order_identity=g46.OrderIdentityV1("c", None, None)),
            "ORDER_CLIENT_NAMESPACE_MISMATCH")

    def test_client_namespace_is_exclusive_and_nonempty(self):
        for normal, algo in ((None, None), ("c", "c"), ("", None), (True, None)):
            self.assertRejected(lambda: g46.OrderIdentityV1(normal, algo, None))

    def test_server_identity_is_canonical_decimal_text_or_null(self):
        for value in (0, 1, True, "0", "01", "-1", "+1", "1.0", " 1", "1" * 21):
            with self.subTest(value=value):
                self.assertRejected(lambda: g46.OrderIdentityV1("c", None, value))
        self.assertEqual(g46.OrderIdentityV1("c", None, "123").server_order_id, "123")

    def test_nested_order_identity_schema_is_exact(self):
        for mutation in (lambda d: d.pop("server_order_id"),
                         lambda d: d.update(order_id="alias")):
            data = identity().to_dict()
            mutation(data["order_identity"])
            self.assertRejected(lambda: g46.CanonicalIdentityV1.from_dict(data), "FIELD_SET_MISMATCH")

    def test_identity_and_nested_order_are_frozen(self):
        value = identity()
        with self.assertRaises(FrozenInstanceError):
            value.slot_generation = 21
        with self.assertRaises(FrozenInstanceError):
            value.order_identity.client_id = "changed"

    def test_input_dictionary_mutation_cannot_change_identity(self):
        data = identity().to_dict()
        result = g46.CanonicalIdentityV1.from_dict(data)
        before = result.canonical_bytes()
        data["account_id"] = "changed"
        data["order_identity"]["client_id"] = "changed"
        self.assertEqual(result.canonical_bytes(), before)

    def test_output_dictionary_mutation_cannot_change_identity(self):
        value = identity()
        exported = value.to_dict()
        exported["order_identity"]["server_order_id"] = "99"
        self.assertIsNone(value.order_identity.server_order_id)

    def test_dict_subclasses_and_nonstring_keys_rejected(self):
        class MutableAlias(dict):
            pass
        self.assertRejected(lambda: g46.CanonicalIdentityV1.from_dict(
            MutableAlias(identity().to_dict())), "OBJECT_REQUIRED")
        data = identity().to_dict()
        data[1] = "bad"
        self.assertRejected(lambda: g46.CanonicalIdentityV1.from_dict(data), "INVALID_FIELD_NAME")


class SerializationTests(ContractAssertions):
    def test_identity_canonical_golden_bytes(self):
        expected = (
            b'{"account_id":"account-1","environment":"OFFLINE","execution_epoch":10,'
            b'"order_identity":{"client_algo_id":null,"client_id":"client-1",'
            b'"server_order_id":null},"order_kind":"NORMAL","order_role":"ENTRY",'
            b'"parent_scope_id":"parent-1","position_mode":"HEDGE","position_side":"LONG",'
            b'"schema_version":1,"slot_generation":20,"slot_id":"A","symbol":"BTCUSDT",'
            b'"venue":"MOCK_VENUE"}')
        self.assertEqual(identity().canonical_bytes(), expected)
        self.assertEqual(identity().identity_hash,
                         hashlib.sha256(b"ATLAS/G46/IDENTITY/V1\x00" + expected).hexdigest())

    def test_dict_insertion_order_does_not_change_bytes_or_receipt(self):
        original = envelope()
        reversed_data = dict(reversed(list(original.to_dict().items())))
        copied = g46.DecisionEnvelopeV1.from_dict(reversed_data)
        self.assertEqual(copied.canonical_bytes(), original.canonical_bytes())
        self.assertEqual(copied.receipt_hash, original.receipt_hash)

    def test_receipt_hash_is_domain_separated_payload_hash(self):
        value = envelope()
        payload = value.to_dict()
        del payload["receipt_hash"]
        self.assertEqual(value.receipt_hash,
                         hashlib.sha256(b"ATLAS/G46/DECISION/V1\x00" + canonical(payload)).hexdigest())
        self.assertNotEqual(value.receipt_hash, hashlib.sha256(canonical(payload)).hexdigest())
        self.assertNotEqual(value.receipt_hash, value.identity.identity_hash)

    def test_noncanonical_whitespace_and_trailing_newline_rejected(self):
        data = identity().canonical_bytes()
        for raw in (b" " + data, data + b"\n", data.replace(b'":', b'": ')):
            self.assertRejected(lambda: g46.CanonicalIdentityV1.from_bytes(raw),
                                "NONCANONICAL_SERIALIZATION")

    def test_noncanonical_key_order_rejected(self):
        raw = json.dumps(dict(reversed(list(identity().to_dict().items()))),
                         separators=(",", ":")).encode()
        self.assertRejected(lambda: g46.CanonicalIdentityV1.from_bytes(raw),
                            "NONCANONICAL_SERIALIZATION")

    def test_duplicate_top_level_field_rejected_even_if_values_equal(self):
        raw = identity().canonical_bytes().replace(b'{"account_id":',
                                                   b'{"account_id":"account-1","account_id":', 1)
        self.assertRejected(lambda: g46.CanonicalIdentityV1.from_bytes(raw), "DUPLICATE_FIELD")

    def test_duplicate_nested_field_rejected(self):
        raw = envelope().canonical_bytes().replace(b'"client_id":"client-1"',
            b'"client_id":"client-1","client_id":"different"')
        self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_bytes(raw), "DUPLICATE_FIELD")

    def test_noncanonical_string_escape_rejected(self):
        raw = identity().canonical_bytes().replace(b'"OFFLINE"', b'"\\u004fFFLINE"')
        self.assertRejected(lambda: g46.CanonicalIdentityV1.from_bytes(raw),
                            "NONCANONICAL_SERIALIZATION")

    def test_noncanonical_numeric_forms_rejected(self):
        raw = identity().canonical_bytes()
        for number in (b"1e1", b"10.0", b"010", b"+10"):
            self.assertRejected(lambda: g46.CanonicalIdentityV1.from_bytes(
                raw.replace(b'"execution_epoch":10', b'"execution_epoch":' + number)))

    def test_nan_infinity_and_negative_zero_rejected(self):
        raw = identity().canonical_bytes()
        for number in (b"NaN", b"Infinity", b"-Infinity", b"-0"):
            self.assertRejected(lambda: g46.CanonicalIdentityV1.from_bytes(
                raw.replace(b'"execution_epoch":10', b'"execution_epoch":' + number)))

    def test_non_utf8_bom_and_malformed_json_rejected(self):
        for data in (b"\xff", b"\xef\xbb\xbf" + identity().canonical_bytes(), b"{", b"null", b"[]"):
            self.assertRejected(lambda: g46.CanonicalIdentityV1.from_bytes(data))

    def test_wire_requires_immutable_bytes_with_size_bound(self):
        for data in (identity().canonical_bytes().decode(), bytearray(b"{}"),
                     memoryview(b"{}"), b"", b"x" * (g46.MAX_WIRE_BYTES + 1)):
            self.assertRejected(lambda: g46.CanonicalIdentityV1.from_bytes(data), "INVALID_WIRE_BYTES")

    def test_deep_wire_rejected_without_acceptance(self):
        data = b'{"deep":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"
        self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_bytes(data))

    def test_changed_receipt_payload_without_rehash_rejected(self):
        data = envelope().to_dict()
        data["event_id"] = "changed"
        self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_bytes(canonical(data)),
                            "RECEIPT_HASH_MISMATCH")

    def test_malformed_receipt_hash_rejected(self):
        for digest in ("", "x" * 64, "A" * 64, 0, None, "1" * 63):
            data = envelope().to_dict()
            data["receipt_hash"] = digest
            self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_dict(data), "INVALID_HASH")

    def test_wellformed_but_incorrect_receipt_hash_rejected(self):
        data = envelope().to_dict()
        data["receipt_hash"] = "0" * 64
        self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_dict(data), "RECEIPT_HASH_MISMATCH")


class EnvelopeTests(ContractAssertions):
    def test_valid_envelope_round_trip_is_closed(self):
        value = envelope()
        copied = g46.DecisionEnvelopeV1.from_bytes(value.canonical_bytes())
        self.assertEqual(copied, value)
        self.assertClosed(copied)

    def test_every_wire_field_required_including_false_null_and_receipt(self):
        source = envelope().to_dict()
        for field in source:
            with self.subTest(field=field):
                data = dict(source)
                del data[field]
                self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_dict(data), "FIELD_SET_MISMATCH")

    def test_extra_authority_and_stop_boolean_fields_rejected(self):
        for field in ("authorization", "owner_approved", "signed", "stop_present", "stop_active"):
            data = envelope().to_dict()
            data[field] = True
            self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_dict(data), "FIELD_SET_MISMATCH")

    def test_positive_authority_rejected_for_every_model_status(self):
        for status in g46.ModelStatus:
            for field in ("entry_authorized", "unlock", "stop_proven"):
                with self.subTest(status=status, field=field):
                    self.assertRejected(lambda: envelope(status=status, **{field: True}),
                                        "UNSAFE_AUTHORITY")

    def test_rehashed_positive_authority_rejected_at_wire_boundary(self):
        for field in ("entry_authorized", "unlock", "stop_proven"):
            data = envelope(status=g46.ModelStatus.ACKED).to_dict()
            del data["receipt_hash"]
            data[field] = True
            data["receipt_hash"] = hashlib.sha256(
                b"ATLAS/G46/DECISION/V1\x00" + canonical(data)).hexdigest()
            self.assertRejected(lambda: g46.OfflineDecisionConsumer().consume(canonical(data)),
                                "UNSAFE_AUTHORITY")

    def test_truthy_and_false_like_authority_coercions_rejected(self):
        for field in ("entry_authorized", "unlock", "stop_proven"):
            for value in ("NO_UNLOCK", "false", "False", 0, 1, None, [], {}):
                with self.subTest(field=field, value=value):
                    self.assertRejected(lambda: envelope(**{field: value}), "UNSAFE_AUTHORITY")

    def test_every_supported_status_stays_closed_after_consumption(self):
        for status in g46.ModelStatus:
            with self.subTest(status=status):
                value = g46.OfflineDecisionConsumer().consume(envelope(status=status).canonical_bytes())
                self.assertClosed(value)

    def test_envelope_cannot_be_used_as_boolean_success(self):
        with self.assertRaisesRegex(TypeError, "DECISION_HAS_NO_TRUTHINESS"):
            bool(envelope(status=g46.ModelStatus.ACKED))

    def test_stop_filled_observation_is_never_real_stop_proof(self):
        value = g46.OfflineDecisionConsumer().consume(envelope(
            identity=protective(), status=g46.ModelStatus.FILLED).canonical_bytes())
        self.assertClosed(value)

    def test_ack_cannot_claim_signed_provenance_or_real_durability(self):
        for field, value in (("provenance", "SIGNED_EXCHANGE"),
                             ("durability_state", "DURABLE"), ("decision", "ALLOW"),
                             ("retry_policy", "RETRY"), ("evidence_kind", "STOP_PROOF")):
            data = envelope(status=g46.ModelStatus.ACKED).to_dict()
            data[field] = value
            self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_dict(data), "UNKNOWN_ENUM")

    def test_known_but_conflicting_provenance_is_rejected(self):
        self.assertRejected(lambda: envelope(status=g46.ModelStatus.ACKED,
            provenance=g46.Provenance.OFFLINE_MODEL), "EVIDENCE_PROVENANCE_MISMATCH")
        self.assertRejected(lambda: envelope(durability_state=g46.DurabilityState.LOCAL_FSYNC_ACK_ONLY),
                            "EVIDENCE_PROVENANCE_MISMATCH")

    def test_mock_acked_cannot_masquerade_as_model_result(self):
        self.assertRejected(lambda: envelope(status=g46.ModelStatus.ACKED,
            evidence_kind=g46.EvidenceKind.MODEL_RESULT, provenance=g46.Provenance.OFFLINE_MODEL,
            durability_state=g46.DurabilityState.NOT_CONFIRMED), "STATUS_EVIDENCE_MISMATCH")

    def test_durable_cannot_masquerade_as_callback_ack(self):
        self.assertRejected(lambda: envelope(status=g46.ModelStatus.DURABLE,
            evidence_kind=g46.EvidenceKind.CALLBACK_ACK, provenance=g46.Provenance.OFFLINE_CALLBACK,
            durability_state=g46.DurabilityState.CALLBACK_ACK_ONLY), "STATUS_EVIDENCE_MISMATCH")

    def test_unknown_status_and_wrong_enum_types_rejected(self):
        data = envelope().to_dict()
        data["status"] = "SUCCESS"
        self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_dict(data), "UNKNOWN_ENUM")
        self.assertRejected(lambda: envelope(status="RESERVED"), "INVALID_ENUM_TYPE")

    def test_envelope_version_is_strict(self):
        for value in (2, True, "1", 1.0):
            self.assertRejected(lambda: envelope(schema_version=value), "UNSUPPORTED_VERSION")

    def test_lease_epoch_generation_and_owner_are_required(self):
        for field in ("lease_epoch", "lease_generation", "lease_owner"):
            data = envelope().to_dict()
            del data["lease"][field]
            self.assertRejected(lambda: g46.DecisionEnvelopeV1.from_dict(data), "FIELD_SET_MISMATCH")

    def test_lease_rejects_coercion_and_extra_fields(self):
        for value in (True, 0, -1, 1.0, "1", 2**63):
            self.assertRejected(lambda: g46.LeaseV1(value, 1, "owner"), "INVALID_INTEGER")
            self.assertRejected(lambda: g46.LeaseV1(1, value, "owner"), "INVALID_INTEGER")
        self.assertRejected(lambda: g46.LeaseV1.from_dict(dict(
            lease_epoch=1, lease_generation=1, lease_owner="owner", expires=True)), "FIELD_SET_MISMATCH")

    def test_nested_objects_require_typed_instances(self):
        self.assertRejected(lambda: replace(envelope(), identity=identity().to_dict()),
                            "INVALID_IDENTITY_TYPE")
        self.assertRejected(lambda: envelope(lease=dict(lease_epoch=1)), "INVALID_LEASE_TYPE")

    def test_event_names_risk_id_and_hashes_are_strict(self):
        for field, value in (("event_id", ""), ("parent_event_id", ""),
                             ("risk_reservation_id", ""), ("intent_hash", "x" * 64),
                             ("policy_hash", "F" * 64)):
            self.assertRejected(lambda: envelope(**{field: value}))
        self.assertRejected(lambda: envelope(parent_event_id="event-1"), "SELF_PARENT_EVENT")

    def test_utc_timestamp_has_one_format_and_calendar_validation(self):
        for value in (None, "2026-11-10T12:00:00+00:00", "2026-11-10T12:00:00.0Z",
                      "2026-2-1T12:00:00Z", "2026-02-30T12:00:00Z", "2026-11-10T12:00:60Z"):
            with self.subTest(value=value):
                self.assertRejected(lambda: envelope(recorded_at_utc_observed=value), "INVALID_UTC")

    def test_sequence_and_monotonic_time_are_strict_integers(self):
        for field in ("wal_sequence", "recorded_at_monotonic_ns"):
            for value in (True, "1", 1.0, -1, 2**63):
                self.assertRejected(lambda: envelope(**{field: value}), "INVALID_INTEGER")
        self.assertRejected(lambda: envelope(wal_sequence=0), "INVALID_INTEGER")
        self.assertEqual(envelope(recorded_at_monotonic_ns=0).recorded_at_monotonic_ns, 0)

    def test_envelope_and_lease_are_frozen(self):
        value = envelope()
        with self.assertRaises(FrozenInstanceError):
            value.unlock = True
        with self.assertRaises(FrozenInstanceError):
            value.lease.lease_generation = 999

    def test_input_and_export_mutation_do_not_change_receipt(self):
        source = envelope().to_dict()
        value = g46.DecisionEnvelopeV1.from_dict(source)
        original = value.canonical_bytes()
        source["identity"]["order_identity"]["client_id"] = "mutated"
        source["lease"]["lease_generation"] = 1000
        source["unlock"] = True
        exported = value.to_dict()
        exported["identity"]["account_id"] = "other"
        self.assertEqual(value.canonical_bytes(), original)
        self.assertClosed(value)

    def test_lease_and_risk_are_separate_from_canonical_identity(self):
        first = envelope()
        second = replace(first, lease=g46.LeaseV1(31, 1, "next-owner"),
                         risk_reservation_id="other-risk")
        self.assertEqual(first.identity.composite_key, second.identity.composite_key)
        self.assertEqual(first.identity.identity_hash, second.identity.identity_hash)
        self.assertNotEqual(first.receipt_hash, second.receipt_hash)


class ConsumerTests(ContractAssertions):
    def setUp(self):
        self.consumer = g46.OfflineDecisionConsumer()

    def consume(self, value):
        result = self.consumer.consume(value.canonical_bytes())
        self.assertClosed(result)
        return result

    def reject(self, value, code):
        self.assertRejected(lambda: self.consumer.consume(value.canonical_bytes()), code)

    def test_only_canonical_wire_is_accepted(self):
        self.assertRejected(lambda: self.consumer.consume(envelope()), "INVALID_WIRE_BYTES")
        self.assertRejected(lambda: self.consumer.consume(envelope().to_dict()), "INVALID_WIRE_BYTES")

    def test_two_accounts_with_same_order_text_are_isolated(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=identity(account_id="account-2")))

    def test_same_risk_reference_text_is_isolated_between_accounts(self):
        self.consume(envelope(risk_reservation_id="same-reference"))
        self.consume(envelope(2, identity=identity(account_id="account-2"),
                              risk_reservation_id="same-reference"))

    def test_venue_and_environment_namespaces_are_isolated(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=identity(venue="OTHER")))
        self.consume(envelope(3, identity=identity(environment=g46.Environment.TESTNET)))
        self.consume(envelope(4, identity=identity(environment=g46.Environment.MAINNET)))

    def test_same_symbol_long_short_in_hedge_mode_are_distinct(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=other_order(slot_id="B", position_side=g46.PositionSide.SHORT)))

    def test_same_symbol_conflicting_position_mode_is_rejected(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(slot_id="B",
            position_mode=g46.PositionMode.ONE_WAY, position_side=g46.PositionSide.BOTH)),
            "ACCOUNT_MODE_CONFLICT")

    def test_slot_binding_cannot_change_side_in_same_generation(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(position_side=g46.PositionSide.SHORT)),
                    "SLOT_IDENTITY_COLLISION")

    def test_slot_binding_cannot_change_symbol_in_same_generation(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(symbol="ETHUSDT")), "SLOT_IDENTITY_COLLISION")

    def test_two_slots_can_each_have_entry_stop_tp1_tp2(self):
        n = 0
        for slot, side in (("A", g46.PositionSide.LONG), ("B", g46.PositionSide.SHORT)):
            for role in g46.OrderRole:
                n += 1
                args = dict(slot_id=slot, position_side=side)
                ident = (other_order(f"{slot}-entry", **args) if role is g46.OrderRole.ENTRY
                         else protective(role, f"{slot}-{role.value}", **args))
                self.consume(envelope(n, identity=ident))

    def test_normal_algo_can_share_client_and_server_text_without_authority(self):
        self.consume(envelope(identity=other_order("shared", "123")))
        self.consume(envelope(2, identity=protective(client="shared", server="123")))

    def test_client_collision_between_slots_rejected(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=identity(slot_id="B")), "ORDER_IDENTITY_COLLISION")

    def test_client_collision_between_roles_rejected(self):
        self.consume(envelope(identity=protective()))
        self.reject(envelope(2, identity=protective(g46.OrderRole.TP1)), "ORDER_IDENTITY_COLLISION")

    def test_server_collision_with_different_client_rejected(self):
        self.consume(envelope(identity=other_order("client-a", "123")))
        self.reject(envelope(2, identity=other_order("client-b", "123", slot_id="B")),
                    "ORDER_IDENTITY_COLLISION")

    def test_identity_cannot_be_enriched_with_server_id(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=identity(order_identity=
                    g46.OrderIdentityV1("client-1", None, "99"))), "ORDER_IDENTITY_COLLISION")

    def test_same_role_same_lifecycle_cannot_be_rebound(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order()), "ROLE_IDENTITY_COLLISION")

    def test_new_generation_accepts_new_identity_and_preserves_old_tombstone(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=other_order(slot_generation=21)))
        self.reject(envelope(3, identity=identity(slot_generation=22)), "ORDER_IDENTITY_COLLISION")

    def test_stale_generation_rejected_even_with_new_client(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(slot_generation=19)), "STALE_SLOT_GENERATION")

    def test_generation_aba_20_21_20_rejected(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=other_order(slot_generation=21)))
        self.reject(envelope(3, identity=other_order("client-3", slot_generation=20)),
                    "STALE_SLOT_GENERATION")

    def test_restart_epoch_cannot_reuse_slot_generation(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(execution_epoch=11)), "SLOT_ABA")

    def test_higher_execution_epoch_and_generation_accepted(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=other_order(execution_epoch=11, slot_generation=21)))

    def test_execution_epoch_aba_rejected_across_slots(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=other_order(slot_id="B", execution_epoch=11)))
        self.reject(envelope(3, identity=other_order("client-3", slot_generation=21)),
                    "STALE_EXECUTION_EPOCH")

    def test_lease_generation_regression_rejected(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(slot_id="B"),
                    lease=g46.LeaseV1(30, 39, "mock-owner")), "STALE_LEASE")

    def test_older_lease_epoch_with_higher_generation_rejected(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(slot_id="B"),
                    lease=g46.LeaseV1(29, 999, "mock-owner")), "STALE_LEASE")

    def test_lease_aba_after_higher_token_rejected(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=other_order(slot_id="B"),
                              lease=g46.LeaseV1(31, 1, "next-owner")))
        self.reject(envelope(3, identity=protective()), "STALE_LEASE")

    def test_same_lease_token_cannot_change_owner(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(slot_id="B"),
                    lease=g46.LeaseV1(30, 40, "other-owner")), "LEASE_OWNER_COLLISION")

    def test_lease_epoch_is_independent_of_execution_and_slot_generation(self):
        self.consume(envelope(lease=g46.LeaseV1(1, 1, "owner")))
        self.consume(envelope(2, identity=other_order(slot_generation=21),
                              lease=g46.LeaseV1(1, 2, "owner")))

    def test_duplicate_receipt_is_rejected(self):
        first = envelope()
        self.consume(first)
        self.reject(first, "DUPLICATE_EVENT")

    def test_same_event_id_with_changed_content_is_collision(self):
        self.consume(envelope())
        self.reject(envelope(status=g46.ModelStatus.UNKNOWN), "EVENT_ID_COLLISION")

    def test_replay_with_new_event_id_and_old_sequence_is_rejected(self):
        self.consume(envelope())
        self.reject(envelope(2, parent_event_id="event-1", wal_sequence=1), "STALE_WAL_SEQUENCE")

    def test_wal_sequence_is_shared_across_parent_slots(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=other_order(slot_id="B"), wal_sequence=1),
                    "STALE_WAL_SEQUENCE")

    def test_valid_causal_followup_with_fresh_lease_remains_closed(self):
        self.consume(envelope())
        self.consume(envelope(2, parent_event_id="event-1", status=g46.ModelStatus.ACKED,
                              lease=g46.LeaseV1(30, 41, "mock-owner")))

    def test_missing_parent_on_followup_rejected(self):
        self.consume(envelope())
        self.reject(envelope(2), "CAUSAL_CHAIN_MISMATCH")

    def test_unknown_parent_on_first_event_rejected(self):
        self.reject(envelope(parent_event_id="unknown"), "UNKNOWN_PARENT_EVENT")

    def test_followup_cannot_link_to_another_identity_event(self):
        self.consume(envelope())
        self.consume(envelope(2, identity=other_order(slot_id="B")))
        self.reject(envelope(3, parent_event_id="event-2"), "CAUSAL_CHAIN_MISMATCH")

    def test_stale_observation_and_clock_source_change_rejected(self):
        self.consume(envelope())
        for ticks in (99, 100):
            self.reject(envelope(2, parent_event_id="event-1", recorded_at_monotonic_ns=ticks),
                        "STALE_OBSERVATION")
        self.reject(envelope(2, parent_event_id="event-1", recorded_at_monotonic_source="other"),
                    "CLOCK_SOURCE_MISMATCH")

    def test_risk_id_cannot_be_reused_for_another_identity(self):
        first = envelope()
        self.consume(first)
        self.reject(envelope(2, identity=other_order(slot_id="B"),
                    risk_reservation_id=first.risk_reservation_id), "RISK_RESERVATION_COLLISION")

    def test_same_risk_id_cannot_rebind_intent_or_policy(self):
        self.consume(envelope())
        for field in ("intent_hash", "policy_hash"):
            self.reject(envelope(2, parent_event_id="event-1", **{field: "3" * 64}),
                        "RISK_RESERVATION_COLLISION")

    def test_identity_cannot_swap_risk_id(self):
        self.consume(envelope())
        self.reject(envelope(2, parent_event_id="event-1", risk_reservation_id="new-risk"),
                    "IMMUTABLE_INTENT_COLLISION")

    def test_retirement_model_status_does_not_release_identity_or_risk(self):
        first = envelope(status=g46.ModelStatus.MODEL_RETIRED)
        self.consume(first)
        self.reject(envelope(2, identity=identity(slot_generation=21)), "ORDER_IDENTITY_COLLISION")
        self.reject(envelope(2, identity=other_order(slot_generation=21),
                    risk_reservation_id=first.risk_reservation_id), "RISK_RESERVATION_COLLISION")

    def test_parent_scope_isolation_does_not_allow_account_order_reuse(self):
        self.consume(envelope())
        self.reject(envelope(2, identity=identity(parent_scope_id="parent-2")),
                    "ORDER_IDENTITY_COLLISION")

    def test_rejection_does_not_advance_epoch_or_poison_event_id(self):
        self.consume(envelope())
        rejected = envelope(2, identity=other_order(slot_id="B", execution_epoch=11),
                            lease=g46.LeaseV1(29, 1, "old-owner"))
        self.reject(rejected, "STALE_LEASE")
        self.consume(envelope(2, identity=other_order(slot_id="B")))

    def test_input_mutation_after_consumption_does_not_change_stored_binding(self):
        data = envelope().to_dict()
        self.consumer.consume(canonical(data))
        data["identity"]["slot_generation"] = 999
        data["lease"]["lease_generation"] = 999
        self.consume(envelope(2, parent_event_id="event-1"))

    def test_new_consumer_has_no_durable_replay_knowledge_explicit_limit(self):
        first = envelope()
        self.consume(first)
        # Yeni instance'ın aynı kaydı kabulü bilerek sınır kanıtıdır; recovery değildir.
        result = g46.OfflineDecisionConsumer().consume(first.canonical_bytes())
        self.assertClosed(result)


class OfflineBoundaryTests(ContractAssertions):
    def test_module_imports_only_stdlib_and_has_no_other_project_imports(self):
        import ast
        source = Path(g46.__file__).read_text()
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(n.name.split(".")[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module.split(".")[0])
        self.assertLessEqual(imports, {"__future__", "dataclasses", "datetime", "enum",
                                       "hashlib", "json", "re", "typing"})

    def test_recomputed_hash_is_not_authenticity_and_still_cannot_authorize(self):
        # Bağımsız bir imza yoktur: geçerli biçimde yeniden hash'leme kabul edilebilir.
        forged = replace(envelope(), event_id="rehashed-synthetic", intent_hash="f" * 64)
        result = g46.OfflineDecisionConsumer().consume(forged.canonical_bytes())
        self.assertClosed(result)

    def test_contract_errors_do_not_echo_rejected_input(self):
        with self.assertRaises(g46.ContractError) as caught:
            identity(account_id="DO_NOT_ECHO_THIS_INPUT!")
        self.assertEqual(str(caught.exception), "INVALID_NAME")


if __name__ == "__main__":
    unittest.main()
