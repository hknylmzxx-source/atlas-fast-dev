"""G48 bağımsız OFFLINE profil/kimlik/replay/yetkisizlik regresyonları."""
import ast
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import unittest

import identity_kind_compat_offline_g48 as g48


OWNER = g48.Profile.OWNER_V1
HYPOTHETICAL = g48.Profile.CONDITIONAL_ALGO_V2


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def identity(profile=OWNER, role=g48.Role.ENTRY, client="client-1", server=None, **changes):
    normal = role is g48.Role.ENTRY or (profile is OWNER and role in (g48.Role.TP1, g48.Role.TP2))
    reference = g48.OrderReference(
        g48.ClientNamespace.NORMAL if normal else g48.ClientNamespace.ALGO,
        client if normal else None, None if normal else client,
        server if normal else None, None if normal else server, None,
    )
    entry_side = changes.get("entry_side", g48.Side.BUY)
    data = dict(
        protocol=g48.PROTOCOL, schema_version=1, profile=profile,
        environment=g48.Environment.OFFLINE, venue="BINANCE_USDM", account_id="account-1",
        parent_scope_id="parent-1", execution_epoch=10, slot_id="A", slot_generation=20,
        symbol="BTCUSDT", position_mode=g48.PositionMode.ONE_WAY,
        position_side=g48.PositionSide.BOTH, entry_side=entry_side,
        order_side=entry_side if role is g48.Role.ENTRY else
        (g48.Side.SELL if entry_side is g48.Side.BUY else g48.Side.BUY),
        order_role=role, order_kind=g48.Kind.NORMAL if normal else g48.Kind.ALGO,
        endpoint="/fapi/v1/order" if normal else "/fapi/v1/algoOrder", order_identity=reference,
    )
    data.update(changes)
    return g48.CompatIdentityV1(**data)


def hedge(side=g48.PositionSide.LONG, **changes):
    return identity(profile=HYPOTHETICAL, position_mode=g48.PositionMode.HEDGE,
                    position_side=side,
                    entry_side=g48.Side.BUY if side is g48.PositionSide.LONG else g48.Side.SELL,
                    **changes)


def receipt(n=1, ident=None, **changes):
    data = dict(protocol=g48.PROTOCOL, schema_version=1, event_id=f"event-{n}",
                parent_event_id=None, sequence=n,
                identity=identity() if ident is None else ident, status=g48.MockStatus.ACKED)
    data.update(changes)
    return g48.MockReceiptV1(**data)


def rehash(data):
    payload = {k: v for k, v in data.items() if k != "receipt_hash"}
    data["receipt_hash"] = hashlib.sha256(g48.RECEIPT_DOMAIN + canonical(payload)).hexdigest()
    return canonical(data)


class Assertions(unittest.TestCase):
    def rejected(self, call, code=None):
        with self.assertRaises(g48.CompatError) as caught:
            call()
        if code is not None:
            self.assertEqual(str(caught.exception), code)

    def closed(self, value):
        for name in ("entry_authorized", "owner_authorized", "unlock", "stop_proven"):
            self.assertIs(getattr(value, name), False)
        self.assertEqual(value.decision, "FAIL_CLOSED")
        self.assertEqual(value.retry_policy, "NO_RETRY")
        self.assertEqual(value.provenance, "OFFLINE_MOCK")

    def consume(self, consumer, value):
        result = consumer.consume(value.canonical_bytes())
        self.closed(result)
        return result


class ProfileTests(Assertions):
    def test_owner_entry_and_both_take_profits_are_normal(self):
        for role in (g48.Role.ENTRY, g48.Role.TP1, g48.Role.TP2):
            with self.subTest(role=role):
                value = identity(role=role)
                self.assertIs(value.order_kind, g48.Kind.NORMAL)
                self.assertEqual(value.endpoint, "/fapi/v1/order")
                self.assertIs(value.order_identity.client_namespace, g48.ClientNamespace.NORMAL)

    def test_owner_stop_is_algo(self):
        value = identity(role=g48.Role.STOP)
        self.assertIs(value.order_kind, g48.Kind.ALGO)
        self.assertEqual(value.endpoint, "/fapi/v1/algoOrder")
        self.assertIs(value.order_identity.client_namespace, g48.ClientNamespace.ALGO)

    def test_hypothetical_profile_has_normal_entry_and_algo_protection(self):
        for role in g48.Role:
            with self.subTest(role=role):
                value = identity(profile=HYPOTHETICAL, role=role)
                self.assertIs(value.order_kind, g48.Kind.NORMAL if role is g48.Role.ENTRY
                              else g48.Kind.ALGO)

    def test_owner_rejects_g46_algo_take_profit_mapping(self):
        for role in (g48.Role.TP1, g48.Role.TP2):
            candidate = identity(profile=HYPOTHETICAL, role=role)
            self.rejected(lambda: replace(candidate, profile=OWNER), "ROLE_KIND_PROFILE_MISMATCH")

    def test_hypothetical_rejects_normal_take_profit_mapping(self):
        for role in (g48.Role.TP1, g48.Role.TP2):
            candidate = identity(role=role)
            self.rejected(lambda: replace(candidate, profile=HYPOTHETICAL),
                          "ROLE_KIND_PROFILE_MISMATCH")

    def test_entry_algo_and_stop_normal_are_invalid_in_both_profiles(self):
        for profile in g48.Profile:
            for role, wrong in ((g48.Role.ENTRY, g48.Kind.ALGO), (g48.Role.STOP, g48.Kind.NORMAL)):
                self.rejected(lambda: replace(identity(profile, role), order_kind=wrong),
                              "ROLE_KIND_PROFILE_MISMATCH")

    def test_endpoint_cannot_cross_normal_and_algo_namespaces(self):
        self.rejected(lambda: replace(identity(), endpoint="/fapi/v1/algoOrder"),
                      "ENDPOINT_KIND_MISMATCH")
        self.rejected(lambda: replace(identity(role=g48.Role.STOP), endpoint="/fapi/v1/order"),
                      "ENDPOINT_KIND_MISMATCH")

    def test_endpoint_requires_exact_path_without_alias_url_or_query(self):
        for endpoint in ("/dapi/v1/order", "/papi/v1/order", "/fapi/v1/order/",
                         "/fapi/v1/order?symbol=BTCUSDT", "https://invalid/fapi/v1/order", None):
            with self.subTest(endpoint=endpoint):
                self.rejected(lambda: replace(identity(), endpoint=endpoint), "ENDPOINT_KIND_MISMATCH")

    def test_expected_profile_conflict_even_when_entry_mapping_is_identical(self):
        value = identity(profile=HYPOTHETICAL)
        self.rejected(lambda: g48.CompatIdentityV1.from_bytes(value.canonical_bytes(),
                      expected_profile=OWNER), "EXPECTED_PROFILE_CONFLICT")

    def test_expected_profile_conflict_for_take_profit_in_both_directions(self):
        for profile, expected in ((OWNER, HYPOTHETICAL), (HYPOTHETICAL, OWNER)):
            value = receipt(ident=identity(profile, g48.Role.TP1))
            self.rejected(lambda: g48.MockReceiptV1.from_bytes(value.canonical_bytes(),
                          expected_profile=expected), "EXPECTED_PROFILE_CONFLICT")

    def test_profile_is_required_and_never_inferred_from_endpoint(self):
        data = identity().to_dict()
        del data["profile"]
        self.rejected(lambda: g48.CompatIdentityV1.from_dict(data, expected_profile=OWNER),
                      "FIELD_SET_MISMATCH")

    def test_unknown_profile_and_alias_are_not_downgraded(self):
        for profile in ("owner", "OWNER-V1", "conditional-algo-v3", "", None, 1):
            data = identity().to_dict()
            data["profile"] = profile
            self.rejected(lambda: g48.CompatIdentityV1.from_dict(data, expected_profile=OWNER))

    def test_expected_profile_has_no_default_or_string_coercion(self):
        with self.assertRaises(TypeError):
            g48.OfflineCompatConsumer()
        with self.assertRaises(TypeError):
            g48.CompatIdentityV1.from_bytes(identity().canonical_bytes())
        with self.assertRaises(TypeError):
            g48.MockReceiptV1.from_bytes(receipt().canonical_bytes())
        for expected in (None, "owner-v1", 1, True):
            self.rejected(lambda: g48.OfflineCompatConsumer(expected_profile=expected),
                          "INVALID_ENUM_TYPE")

    def test_profile_is_part_of_full_key_and_domain_hash(self):
        owner = identity()
        hypothetical = identity(profile=HYPOTHETICAL)
        self.assertNotEqual(owner.composite_key, hypothetical.composite_key)
        self.assertNotEqual(owner.identity_hash, hypothetical.identity_hash)

    def test_owner_profile_does_not_claim_hedge_support(self):
        self.rejected(lambda: replace(hedge(), profile=OWNER), "OWNER_V1_REQUIRES_ONE_WAY")


class ReferenceAndPositionTests(Assertions):
    def test_ambiguous_client_and_client_algo_id_are_rejected(self):
        for namespace in g48.ClientNamespace:
            self.rejected(lambda: g48.OrderReference(namespace, "same", "same", None, None, None),
                          "AMBIGUOUS_CLIENT_NAMESPACE")

    def test_missing_both_client_fields_is_rejected(self):
        for namespace in g48.ClientNamespace:
            self.rejected(lambda: g48.OrderReference(namespace, None, None, None, None, None),
                          "AMBIGUOUS_CLIENT_NAMESPACE")

    def test_client_algo_id_cannot_be_used_by_normal_order(self):
        algo = identity(role=g48.Role.STOP).order_identity
        self.rejected(lambda: replace(identity(), order_identity=algo), "CLIENT_KIND_MISMATCH")

    def test_normal_client_field_cannot_be_used_by_algo_order(self):
        self.rejected(lambda: replace(identity(role=g48.Role.STOP),
                      order_identity=identity().order_identity), "CLIENT_KIND_MISMATCH")

    def test_readback_client_order_id_alias_is_not_request_namespace(self):
        data = identity().to_dict()
        data["order_identity"]["client_namespace"] = "clientOrderId"
        self.rejected(lambda: g48.CompatIdentityV1.from_dict(data, expected_profile=OWNER), "UNKNOWN_ENUM")

    def test_client_ids_are_bounded_exact_ascii_without_normalization(self):
        for client in ("", " padded", "padded ", "ç", "x" * 37, 12, False):
            self.rejected(lambda: identity(client=client), "INVALID_CLIENT_ID")
        self.assertNotEqual(identity(client="Case").identity_hash, identity(client="case").identity_hash)

    def test_normal_order_rejects_algo_id_and_actual_order_id(self):
        reference = identity().order_identity
        self.rejected(lambda: replace(reference, algo_id="123"), "ORDER_ID_NAMESPACE_MISMATCH")
        self.rejected(lambda: replace(reference, actual_order_id="123"), "ORDER_ID_NAMESPACE_MISMATCH")

    def test_algo_order_rejects_normal_order_id_even_if_digits_match(self):
        reference = identity(role=g48.Role.STOP, server="123").order_identity
        self.rejected(lambda: replace(reference, order_id="123"), "ORDER_ID_NAMESPACE_MISMATCH")

    def test_actual_order_id_requires_parent_algo_id(self):
        reference = identity(role=g48.Role.STOP).order_identity
        self.rejected(lambda: replace(reference, actual_order_id="123"), "UNBOUND_ACTUAL_ORDER_ID")

    def test_algo_and_triggered_child_id_stay_separate_in_roundtrip(self):
        value = identity(role=g48.Role.STOP, server="123")
        value = replace(value, order_identity=replace(value.order_identity, actual_order_id="456"))
        parsed = g48.CompatIdentityV1.from_bytes(value.canonical_bytes(), expected_profile=OWNER)
        self.assertEqual(parsed.order_identity.algo_id, "123")
        self.assertEqual(parsed.order_identity.actual_order_id, "456")
        self.assertIsNone(parsed.order_identity.order_id)
        self.assertNotEqual(parsed.identity_hash, identity(role=g48.Role.STOP, server="456").identity_hash)

    def test_server_ids_reject_numeric_coercion_leading_zero_and_sign(self):
        for server in (0, 1, True, 1.0, "0", "01", "-1", "+1", "1.0", " 1", "1" * 21, "١"):
            for role in (g48.Role.ENTRY, g48.Role.STOP):
                self.rejected(lambda: identity(role=role, server=server), "INVALID_SERVER_ID")
        self.assertEqual(identity(server="9" * 20).order_identity.order_id, "9" * 20)

    def test_one_way_requires_both(self):
        for side in (g48.PositionSide.LONG, g48.PositionSide.SHORT):
            self.rejected(lambda: identity(position_side=side), "POSITION_MODE_SIDE_MISMATCH")

    def test_hedge_requires_long_or_short(self):
        self.rejected(lambda: identity(profile=HYPOTHETICAL, position_mode=g48.PositionMode.HEDGE),
                      "POSITION_MODE_SIDE_MISMATCH")

    def test_hedge_entry_side_is_bound_to_position_side(self):
        for value in (hedge(), hedge(g48.PositionSide.SHORT)):
            wrong = g48.Side.SELL if value.entry_side is g48.Side.BUY else g48.Side.BUY
            self.rejected(lambda: replace(value, entry_side=wrong, order_side=wrong),
                          "HEDGE_ENTRY_SIDE_MISMATCH")

    def test_protective_side_must_close_entry_direction_for_all_roles(self):
        for profile in g48.Profile:
            for role in (g48.Role.STOP, g48.Role.TP1, g48.Role.TP2):
                for side in g48.Side:
                    value = identity(profile, role, entry_side=side)
                    self.rejected(lambda: replace(value, order_side=side), "ORDER_SIDE_MISMATCH")

    def test_entry_side_and_order_side_must_match(self):
        self.rejected(lambda: replace(identity(), order_side=g48.Side.SELL), "ORDER_SIDE_MISMATCH")

    def test_strict_names_symbols_venue_slots_and_typed_enums(self):
        for changes in ({"symbol": "btcusdt"}, {"symbol": "BTC/USDT"}, {"venue": "BINANCE"},
                        {"account_id": " padded"}, {"slot_id": "a"}, {"slot_id": 1},
                        {"order_role": "ENTRY"}, {"position_mode": "ONE_WAY"},
                        {"order_identity": identity().order_identity.to_dict()}):
            self.rejected(lambda: replace(identity(), **changes))

    def test_epoch_generation_and_sequence_reject_bool_float_string_or_overflow(self):
        for value in (True, 1.0, "1", 0, -1, 2**63):
            for name in ("execution_epoch", "slot_generation"):
                self.rejected(lambda: replace(identity(), **{name: value}), "INVALID_INTEGER")
            self.rejected(lambda: receipt(sequence=value), "INVALID_INTEGER")


class CanonicalTests(Assertions):
    def test_complete_identity_and_receipt_roundtrip_for_both_profiles_and_roles(self):
        for profile in g48.Profile:
            for role in g48.Role:
                value = identity(profile, role, server="123")
                wire = value.canonical_bytes()
                result = g48.CompatIdentityV1.from_bytes(wire, expected_profile=profile)
                self.assertEqual(result, value)
                self.assertEqual(result.composite_key, value.composite_key)
                self.assertEqual(result.canonical_bytes(), wire)
                event = receipt(ident=value)
                parsed = g48.MockReceiptV1.from_bytes(event.canonical_bytes(), expected_profile=profile)
                self.assertEqual(parsed, event)
                self.assertEqual(parsed.canonical_bytes(), event.canonical_bytes())
                self.closed(parsed)

    def test_identity_golden_bytes_and_full_key_order(self):
        value = identity()
        expected = (b'{"account_id":"account-1","endpoint":"/fapi/v1/order","entry_side":"BUY",'
                    b'"environment":"OFFLINE","execution_epoch":10,"order_identity":{'
                    b'"actual_order_id":null,"algo_id":null,"client_algo_id":null,'
                    b'"client_id":"client-1","client_namespace":"newClientOrderId","order_id":null},'
                    b'"order_kind":"NORMAL","order_role":"ENTRY","order_side":"BUY",'
                    b'"parent_scope_id":"parent-1","position_mode":"ONE_WAY","position_side":"BOTH",'
                    b'"profile":"owner-v1","protocol":"ATLAS_G48_IDENTITY_KIND_COMPAT",'
                    b'"schema_version":1,"slot_generation":20,"slot_id":"A","symbol":"BTCUSDT",'
                    b'"venue":"BINANCE_USDM"}')
        self.assertEqual(value.canonical_bytes(), expected)
        self.assertEqual(value.composite_key, (
            g48.PROTOCOL, 1, OWNER, g48.Environment.OFFLINE, "BINANCE_USDM", "account-1",
            "parent-1", 10, "A", 20, "BTCUSDT", g48.PositionMode.ONE_WAY,
            g48.PositionSide.BOTH, g48.Side.BUY, g48.Side.BUY, g48.Role.ENTRY, g48.Kind.NORMAL,
            "/fapi/v1/order", value.order_identity))
        self.assertEqual(value.identity_hash, hashlib.sha256(
            b"ATLAS/G48/KIND_COMPAT/IDENTITY/V1\x00" + expected).hexdigest())

    def test_receipt_hash_covers_all_payload_fields_and_separates_domain(self):
        event = receipt()
        data = event.to_dict()
        supplied = data.pop("receipt_hash")
        self.assertEqual(supplied, hashlib.sha256(
            b"ATLAS/G48/KIND_COMPAT/MOCK_RECEIPT/V1\x00" + canonical(data)).hexdigest())
        self.assertNotEqual(supplied, hashlib.sha256(g48.IDENTITY_DOMAIN + canonical(data)).hexdigest())
        self.assertNotEqual(supplied, replace(event, status=g48.MockStatus.FILLED).receipt_hash)

    def test_input_export_and_dict_order_do_not_mutate_frozen_identity(self):
        value = identity()
        data = value.to_dict()
        result = g48.CompatIdentityV1.from_dict(dict(reversed(list(data.items()))), expected_profile=OWNER)
        data["order_identity"]["client_id"] = "changed"
        exported = result.to_dict()
        exported["profile"] = "changed"
        self.assertEqual(result.canonical_bytes(), value.canonical_bytes())
        with self.assertRaises(FrozenInstanceError):
            result.profile = HYPOTHETICAL
        with self.assertRaises(FrozenInstanceError):
            result.order_identity.order_id = "123"

    def test_missing_and_extra_fields_at_every_level_are_rejected(self):
        event = receipt()
        for level in ("receipt", "identity", "reference"):
            original = event.to_dict()
            target = (original if level == "receipt" else original["identity"] if level == "identity"
                      else original["identity"]["order_identity"])
            for name in list(target):
                data = json.loads(event.canonical_bytes())
                target_copy = (data if level == "receipt" else data["identity"] if level == "identity"
                               else data["identity"]["order_identity"])
                del target_copy[name]
                self.rejected(lambda: g48.MockReceiptV1.from_dict(data, expected_profile=OWNER))
            target["unexpected"] = True
            self.rejected(lambda: g48.MockReceiptV1.from_dict(original, expected_profile=OWNER),
                          "FIELD_SET_MISMATCH")

    def test_duplicate_json_fields_at_receipt_and_nested_identity_are_rejected(self):
        wire = receipt().canonical_bytes()
        for old, new in ((b'"event_id":"event-1"', b'"event_id":"event-1","event_id":"event-1"'),
                         (b'"profile":"owner-v1"', b'"profile":"owner-v1","profile":"owner-v1"'),
                         (b'"algo_id":null', b'"algo_id":null,"algo_id":null')):
            self.rejected(lambda: g48.MockReceiptV1.from_bytes(wire.replace(old, new),
                          expected_profile=OWNER), "DUPLICATE_FIELD")

    def test_noncanonical_whitespace_key_order_escape_and_newline_are_rejected(self):
        wire = identity().canonical_bytes()
        variants = (b" " + wire, wire + b"\n", wire.replace(b":", b": ", 1),
                    canonical(identity().to_dict()).replace(b"BTCUSDT", b"BTCUS\\u0044T"),
                    json.dumps(identity().to_dict()).encode())
        for data in variants:
            self.rejected(lambda: g48.CompatIdentityV1.from_bytes(data, expected_profile=OWNER),
                          "NONCANONICAL_SERIALIZATION")

    def test_wire_requires_bounded_immutable_utf8_bytes_and_valid_json(self):
        for data in ("{}", bytearray(b"{}"), b"", b"x" * (g48.MAX_WIRE_BYTES + 1),
                     b"\xff", b"\xef\xbb\xbf{}", b"[]", b"null", b"{", b'{"x":NaN}',
                     b'{"x":Infinity}', b"[" * 1200 + b"]" * 1200):
            self.rejected(lambda: g48.CompatIdentityV1.from_bytes(data, expected_profile=OWNER))

    def test_schema_and_protocol_never_silently_downgrade(self):
        for version in (True, 1.0, "1", 0, 2, 46, None):
            self.rejected(lambda: replace(identity(), schema_version=version), "UNSUPPORTED_VERSION")
            self.rejected(lambda: replace(receipt(), schema_version=version), "UNSUPPORTED_VERSION")
        for protocol in ("G46", "", None, 1):
            self.rejected(lambda: replace(identity(), protocol=protocol), "PROTOCOL_MISMATCH")

    def test_g46_shaped_identity_is_not_implicitly_upgraded(self):
        data = identity().to_dict()
        for name in ("protocol", "profile", "entry_side", "order_side", "endpoint"):
            del data[name]
        data["order_identity"] = {"client_id": "client-1", "client_algo_id": None, "server_order_id": None}
        self.rejected(lambda: g48.CompatIdentityV1.from_dict(data, expected_profile=OWNER),
                      "FIELD_SET_MISMATCH")

    def test_tampered_or_malformed_receipt_hash_is_rejected(self):
        data = receipt().to_dict()
        data["sequence"] = 2
        self.rejected(lambda: g48.MockReceiptV1.from_bytes(canonical(data), expected_profile=OWNER),
                      "RECEIPT_HASH_MISMATCH")
        for digest in ("0" * 64, "A" * 64, "", None, 123):
            data = receipt().to_dict()
            data["receipt_hash"] = digest
            self.rejected(lambda: g48.MockReceiptV1.from_dict(data, expected_profile=OWNER))

    def test_dict_subclasses_and_nonstring_fields_are_rejected(self):
        class OtherDict(dict):
            pass
        self.rejected(lambda: g48.CompatIdentityV1.from_dict(OtherDict(identity().to_dict()),
                      expected_profile=OWNER), "OBJECT_REQUIRED")
        data = identity().to_dict()
        data[1] = "untrusted"
        self.rejected(lambda: g48.CompatIdentityV1.from_dict(data, expected_profile=OWNER),
                      "INVALID_FIELD_NAME")


class ConsumerTests(Assertions):
    def setUp(self):
        self.consumer = g48.OfflineCompatConsumer(expected_profile=OWNER)

    def reject_event(self, event, code):
        self.rejected(lambda: self.consumer.consume(event.canonical_bytes()), code)

    def test_consumer_pins_expected_profile_and_rejects_valid_other_profile(self):
        self.reject_event(receipt(ident=identity(profile=HYPOTHETICAL)), "EXPECTED_PROFILE_CONFLICT")
        self.consume(self.consumer, receipt())
        self.assertIs(self.consumer.expected_profile, OWNER)
        with self.assertRaises(AttributeError):
            self.consumer.expected_profile = HYPOTHETICAL

    def test_owner_two_slots_each_accept_entry_stop_and_normal_take_profits_without_authority(self):
        n = 0
        for slot in ("A", "B"):
            for role in g48.Role:
                n += 1
                value = identity(role=role, slot_id=slot, client=f"client-{n}", server=str(n))
                self.consume(self.consumer, receipt(n, value))

    def test_same_symbol_hedge_long_and_short_are_separate(self):
        consumer = g48.OfflineCompatConsumer(expected_profile=HYPOTHETICAL)
        long = hedge(client="long", server="11")
        short = hedge(g48.PositionSide.SHORT, slot_id="B", client="short", server="12")
        self.assertNotEqual(long.composite_key, short.composite_key)
        self.consume(consumer, receipt(1, long))
        self.consume(consumer, receipt(2, short))

    def test_same_symbol_cannot_mix_hedge_and_one_way_in_same_account(self):
        self.consumer = g48.OfflineCompatConsumer(expected_profile=HYPOTHETICAL)
        self.consume(self.consumer, receipt(1, hedge()))
        other = identity(profile=HYPOTHETICAL, slot_id="B", client="other")
        self.reject_event(receipt(2, other), "ACCOUNT_POSITION_MODE_CONFLICT")

    def test_reverse_mode_change_across_parent_and_symbol_is_also_rejected(self):
        self.consumer = g48.OfflineCompatConsumer(expected_profile=HYPOTHETICAL)
        self.consume(self.consumer, receipt(1, identity(profile=HYPOTHETICAL)))
        other = hedge(client="other", parent_scope_id="parent-2", symbol="ETHUSDT")
        self.reject_event(receipt(2, other), "ACCOUNT_POSITION_MODE_CONFLICT")

    def test_same_client_and_server_digits_are_isolated_between_normal_and_algo(self):
        self.consume(self.consumer, receipt(1, identity(client="same", server="123")))
        stop = identity(role=g48.Role.STOP, client="same", server="123")
        stop = replace(stop, order_identity=replace(stop.order_identity, actual_order_id="123"))
        self.consume(self.consumer, receipt(2, stop))

    def test_client_reuse_in_different_slot_is_rejected(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, identity(slot_id="B")), "CLIENT_ID_COLLISION")

    def test_client_reuse_between_entry_and_normal_tp_is_rejected(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, identity(role=g48.Role.TP1)), "CLIENT_ID_COLLISION")

    def test_client_reuse_across_parent_scope_is_rejected(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, identity(parent_scope_id="other")), "CLIENT_ID_COLLISION")

    def test_server_id_reuse_with_different_client_is_rejected(self):
        self.consume(self.consumer, receipt(1, identity(server="123")))
        self.reject_event(receipt(2, identity(slot_id="B", client="other", server="123")),
                          "SERVER_ID_COLLISION")

    def test_same_role_lifecycle_cannot_rebind_identity(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, identity(client="other")), "ROLE_IDENTITY_COLLISION")

    def test_server_id_cannot_be_silently_added_to_registered_identity(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, identity(server="123")), "ROLE_IDENTITY_COLLISION")

    def test_account_and_environment_namespaces_are_separate(self):
        self.consume(self.consumer, receipt())
        self.consume(self.consumer, receipt(2, identity(account_id="account-2")))
        self.consume(self.consumer, receipt(3, identity(environment=g48.Environment.TESTNET)))
        self.consume(self.consumer, receipt(4, identity(environment=g48.Environment.MAINNET)))

    def test_new_generation_accepts_fresh_ids_and_preserves_tombstone(self):
        self.consume(self.consumer, receipt())
        self.consume(self.consumer, receipt(2, identity(client="new", slot_generation=21)))
        self.reject_event(receipt(3, identity(slot_id="B")), "CLIENT_ID_COLLISION")

    def test_stale_generation_is_rejected_even_with_fresh_ids(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, identity(slot_generation=19, client="new")), "STALE_SLOT_GENERATION")

    def test_generation_aba_twenty_twentyone_twenty_is_rejected(self):
        self.consume(self.consumer, receipt())
        self.consume(self.consumer, receipt(2, identity(slot_generation=21, client="new")))
        self.reject_event(receipt(3, identity(client="fresh")), "STALE_SLOT_GENERATION")

    def test_restart_epoch_requires_strictly_new_slot_generation(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, identity(execution_epoch=11, client="new")),
                          "REUSED_RESTART_GENERATION")

    def test_higher_epoch_and_generation_are_accepted_but_old_epoch_is_fenced(self):
        self.consume(self.consumer, receipt())
        self.consume(self.consumer, receipt(2, identity(execution_epoch=11, slot_generation=21, client="new")))
        self.reject_event(receipt(3, identity(slot_id="B", client="fresh")), "STALE_EXECUTION_EPOCH")

    def test_symbol_and_direction_cannot_change_in_same_slot_generation(self):
        self.consume(self.consumer, receipt())
        for candidate in (identity(symbol="ETHUSDT", role=g48.Role.TP1, client="new"),
                          identity(entry_side=g48.Side.SELL, role=g48.Role.TP1, client="new")):
            self.reject_event(receipt(2, candidate), "SLOT_BINDING_CONFLICT")

    def test_hedge_side_cannot_change_in_same_slot_generation(self):
        self.consumer = g48.OfflineCompatConsumer(expected_profile=HYPOTHETICAL)
        self.consume(self.consumer, receipt(1, hedge()))
        self.reject_event(receipt(2, hedge(g48.PositionSide.SHORT, client="new")), "SLOT_BINDING_CONFLICT")

    def test_exact_duplicate_event_is_rejected(self):
        value = receipt()
        self.consume(self.consumer, value)
        self.reject_event(value, "DUPLICATE_EVENT")

    def test_changed_content_with_same_event_id_is_collision(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(status=g48.MockStatus.FILLED), "EVENT_ID_COLLISION")

    def test_replay_with_new_event_id_and_old_sequence_is_rejected(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, sequence=1, parent_event_id="event-1"), "REPLAYED_SEQUENCE")

    def test_sequence_is_shared_across_slots_in_same_parent(self):
        self.consume(self.consumer, receipt(5))
        self.reject_event(receipt(6, identity(slot_id="B", client="new"), sequence=4), "REPLAYED_SEQUENCE")

    def test_followup_requires_exact_causal_parent(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2), "CAUSAL_PARENT_MISMATCH")
        self.reject_event(receipt(2, parent_event_id="unknown"), "CAUSAL_PARENT_MISMATCH")
        self.consume(self.consumer, receipt(2, parent_event_id="event-1", status=g48.MockStatus.FILLED))

    def test_first_event_cannot_attach_to_other_identity(self):
        self.consume(self.consumer, receipt())
        self.reject_event(receipt(2, identity(role=g48.Role.TP1, client="new"),
                          parent_event_id="event-1"), "CAUSAL_PARENT_MISMATCH")

    def test_failed_high_epoch_does_not_advance_watermarks_or_poison_event_id(self):
        self.consume(self.consumer, receipt())
        rejected = identity(execution_epoch=99, slot_generation=99)  # Eski client'a yeniden bağlanma.
        self.reject_event(receipt(2, rejected), "CLIENT_ID_COLLISION")
        self.consume(self.consumer, receipt(2, identity(client="new", slot_generation=21)))
        self.consume(self.consumer, receipt(3, identity(slot_id="B", client="other")))

    def test_model_retirement_does_not_release_client_or_server_tombstone(self):
        value = identity(server="123")
        self.consume(self.consumer, receipt(1, value))
        self.consume(self.consumer, receipt(2, value, parent_event_id="event-1", status=g48.MockStatus.MODEL_RETIRED))
        self.reject_event(receipt(3, identity(slot_generation=21, server="123")), "CLIENT_ID_COLLISION")
        self.reject_event(receipt(3, identity(slot_generation=21, client="new", server="123")),
                          "SERVER_ID_COLLISION")

    def test_new_consumer_has_no_persistent_replay_protection_and_no_authority(self):
        event = receipt()
        self.consume(self.consumer, event)
        fresh = g48.OfflineCompatConsumer(expected_profile=OWNER)
        self.consume(fresh, event)


class AuthorityBoundaryTests(Assertions):
    def test_every_mock_status_remains_closed_after_roundtrip_and_consumption(self):
        for status in g48.MockStatus:
            consumer = g48.OfflineCompatConsumer(expected_profile=OWNER)
            self.consume(consumer, receipt(status=status))

    def test_mock_stop_acked_and_filled_are_never_stop_proof(self):
        for status in (g48.MockStatus.ACKED, g48.MockStatus.FILLED):
            consumer = g48.OfflineCompatConsumer(expected_profile=OWNER)
            self.consume(consumer, receipt(ident=identity(role=g48.Role.STOP), status=status))

    def test_all_authority_fields_reject_true_and_false_like_coercions(self):
        for name in ("entry_authorized", "owner_authorized", "unlock", "stop_proven"):
            for value in (True, 0, 1, "false", None):
                self.rejected(lambda: receipt(**{name: value}), "UNSAFE_AUTHORITY")

    def test_rehashed_positive_authority_still_fails_at_wire_boundary(self):
        for name in ("entry_authorized", "owner_authorized", "unlock", "stop_proven"):
            data = receipt().to_dict()
            data[name] = True
            self.rejected(lambda: g48.MockReceiptV1.from_bytes(rehash(data), expected_profile=OWNER),
                          "UNSAFE_AUTHORITY")

    def test_signed_provenance_retry_and_unlocked_decision_are_rejected(self):
        for changes in ({"provenance": "SIGNED_EXCHANGE"}, {"provenance": "OWNER_APPROVED"},
                        {"decision": "ALLOW"}, {"retry_policy": "RETRY"}):
            self.rejected(lambda: receipt(**changes), "UNSAFE_DECISION_OR_SOURCE")

    def test_receipt_is_not_boolean_success_and_is_frozen(self):
        event = receipt()
        with self.assertRaises(TypeError):
            bool(event)
        with self.assertRaises(FrozenInstanceError):
            event.unlock = True

    def test_rehashing_fabricated_mock_input_does_not_create_authenticity(self):
        data = receipt().to_dict()
        data["identity"]["account_id"] = "fabricated-account"
        consumer = g48.OfflineCompatConsumer(expected_profile=OWNER)
        result = consumer.consume(rehash(data))
        self.closed(result)
        self.assertEqual(result.identity.account_id, "fabricated-account")

    def test_missing_observation_never_changes_to_canceled_or_filled(self):
        event = receipt(status=g48.MockStatus.MISSING)
        consumer = g48.OfflineCompatConsumer(expected_profile=OWNER)
        result = self.consume(consumer, event)
        self.assertIs(result.status, g48.MockStatus.MISSING)

    def test_unknown_status_or_extra_owner_grant_fields_are_rejected(self):
        for name, value in (("status", "SIGNED_ACTIVE"), ("stop_present", True),
                            ("owner_grant", "approved"), ("authorization", True)):
            data = receipt().to_dict()
            data[name] = value
            self.rejected(lambda: g48.MockReceiptV1.from_bytes(rehash(data), expected_profile=OWNER))

    def test_module_has_only_stdlib_imports_without_project_sdk_or_io_imports(self):
        tree = ast.parse(Path(g48.__file__).read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {"__future__", "dataclasses", "enum", "hashlib", "json", "re", "typing"})
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id in ("open", "exec", "eval", "__import__")
                             for node in ast.walk(tree)))

    def test_errors_never_echo_untrusted_input(self):
        data = identity().to_dict()
        data["profile"] = "untrusted-private-input"
        with self.assertRaises(g48.CompatError) as caught:
            g48.CompatIdentityV1.from_dict(data, expected_profile=OWNER)
        self.assertEqual(str(caught.exception), "UNKNOWN_ENUM")


if __name__ == "__main__":
    unittest.main()
