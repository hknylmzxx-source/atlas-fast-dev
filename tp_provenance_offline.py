"""Yalnız offline TP olay/plan/bağımsız okuma tutarlılığı denetimi.

Enjekte edilen okuma da sentetiktir; kaynak etiketi veya eşleşen veri gerçek
Binance kimlik doğrulaması değildir. Owner'a bağlı değildir, emir göndermez,
kilit açmaz ve hiçbir sonuç işlem ya da karantina kaldırma yetkisi vermez.
"""
from decimal import Decimal, InvalidOperation


class TpEvidenceBlocked(ValueError):
    pass


_WIRE_FIELDS = frozenset((
    "symbol", "side", "positionSide", "type", "timeInForce", "quantity",
    "price", "reduceOnly", "newClientOrderId",
))
_EVENT_FIELDS = {
    "s": "symbol", "c": "newClientOrderId", "S": "side",
    "ps": "positionSide", "o": "type", "f": "timeInForce",
}
_READ_FIELDS = {
    "symbol": "symbol", "clientOrderId": "newClientOrderId", "side": "side",
    "positionSide": "positionSide", "type": "type", "timeInForce": "timeInForce",
}


def _decimal(value):
    if type(value) is not str or not value or value.strip() != value:
        raise TpEvidenceBlocked("TP_AMOUNT_INVALID")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise TpEvidenceBlocked("TP_AMOUNT_INVALID") from None
    if not number.is_finite() or number < 0:
        raise TpEvidenceBlocked("TP_AMOUNT_INVALID")
    return number


def _fill(status, filled, quantity):
    if not ((status == "NEW" and filled == 0)
            or (status == "PARTIALLY_FILLED" and 0 < filled < quantity)
            or (status == "FILLED" and filled == quantity)):
        raise TpEvidenceBlocked("TP_FILL_STATE_INVALID")


def _fresh(timestamp, now_ms, max_age_ms):
    return (type(timestamp) is int and timestamp >= 0
            and 0 <= now_ms - timestamp <= max_age_ms)


def _result(accepted, reason):
    return {
        "decision": "SYNTHETIC_TP_MATCH_ONLY" if accepted else "TP_EVIDENCE_REJECTED",
        "synthetic_tp_consistent": accepted,
        "reasons": [] if accepted else [reason],
        "authenticated_signed_get_verified": False,
        "entry_authorized": False,
        "may_clear_latch": False,
        "may_release_owner_lock": False,
    }


class TpProvenanceReplay:
    """Tek TP planı için sıralı, bellekte tutulan offline olay denetleyicisi.

    read_order yalnız mock/replay çağıranı içindir ve (emir, alınma_zamanı_ms)
    döndürür. Her kabul için planın tam kimliğiyle ayrı okuma zorunludur.
    Durum süreçler arasında kalıcı değildir; paylaşılan/kritik kilit değildir.
    Eşzamanlı kullanım ve canlı akışa bağlama bu adayın kapsamı dışındadır.
    """

    def __init__(self, *, scope, role, wire):
        if type(scope) is not str or not scope or role not in ("TP1", "TP2"):
            raise TpEvidenceBlocked("TP_PLAN_CONTEXT_INVALID")
        if type(wire) is not dict or set(wire) != _WIRE_FIELDS:
            raise TpEvidenceBlocked("TP_PLAN_SHAPE_INVALID")
        if (any(type(wire[k]) is not str or not wire[k]
                for k in ("symbol", "newClientOrderId"))
                or wire["side"] not in ("BUY", "SELL")
                or wire["positionSide"] != "BOTH" or wire["type"] != "LIMIT"
                or wire["timeInForce"] != "GTC"
                or type(wire["reduceOnly"]) is not str or wire["reduceOnly"] != "true"):
            raise TpEvidenceBlocked("TP_PLAN_CONTRACT_INVALID")
        quantity, price = _decimal(wire["quantity"]), _decimal(wire["price"])
        if quantity <= 0 or price <= 0:
            raise TpEvidenceBlocked("TP_PLAN_AMOUNT_INVALID")
        self.scope, self.role = scope, role
        self._wire = dict(wire)
        self._quantity, self._price = quantity, price
        self._previous = None

    def inspect(self, event, *, read_order, now_ms, max_age_ms=5000):
        """Reddedilen olay ilerleme durumunu değiştirmez; makbuz yerine geçmez."""
        if (type(now_ms) is not int or now_ms < 0 or type(max_age_ms) is not int
                or not 0 < max_age_ms <= 10000):
            return _result(False, "TP_REVIEW_CLOCK_INVALID")
        try:
            current = self._event(event, now_ms, max_age_ms)
            order_id, timestamp, status, filled, _ = current
            if self._previous is not None:
                old_id, old_time, old_status, old_fill, _ = self._previous
                if order_id != old_id:
                    raise TpEvidenceBlocked("TP_ORDER_ID_CHANGED")
                if timestamp <= old_time:
                    raise TpEvidenceBlocked("TP_EVENT_REPLAY_OR_OUT_OF_ORDER")
                if old_status == "FILLED" or filled <= old_fill:
                    raise TpEvidenceBlocked("TP_FILL_REGRESSION_OR_NO_PROGRESS")
        except TpEvidenceBlocked as exc:
            return _result(False, str(exc))

        try:
            observation = read_order(
                symbol=self._wire["symbol"],
                orig_client_order_id=self._wire["newClientOrderId"], recv_window=5000)
        except Exception:
            return _result(False, "INDEPENDENT_ORDER_READ_FAILED")
        try:
            self._receipt(observation, current, now_ms, max_age_ms)
        except TpEvidenceBlocked as exc:
            return _result(False, str(exc))
        self._previous = current
        return _result(True, None)

    def _event(self, event, now_ms, max_age_ms):
        if type(event) is not dict or event.get("e") != "ORDER_TRADE_UPDATE":
            raise TpEvidenceBlocked("TP_EVENT_TYPE_INVALID")
        order = event.get("o")
        if type(order) is not dict:
            raise TpEvidenceBlocked("TP_EVENT_ORDER_MISSING")
        if any(order.get(k) != self._wire[v] for k, v in _EVENT_FIELDS.items()):
            raise TpEvidenceBlocked("TP_EVENT_PLAN_MISMATCH")
        timestamp = order.get("T")
        if (not _fresh(event.get("E"), now_ms, max_age_ms)
                or not _fresh(timestamp, now_ms, max_age_ms)
                or type(event.get("T")) is not int or event["T"] != timestamp
                or event["E"] < timestamp):
            raise TpEvidenceBlocked("TP_EVENT_STALE_OR_INVALID_CLOCK")
        order_id = order.get("i")
        if type(order_id) is not int or order_id <= 0 or order.get("R") is not True:
            raise TpEvidenceBlocked("TP_EVENT_ID_OR_REDUCE_ONLY_INVALID")
        if (_decimal(order.get("q")) != self._quantity
                or _decimal(order.get("p")) != self._price):
            raise TpEvidenceBlocked("TP_EVENT_PRICE_QUANTITY_MISMATCH")
        status, filled = order.get("X"), _decimal(order.get("z"))
        _fill(status, filled, self._quantity)
        if order.get("x") != ("NEW" if status == "NEW" else "TRADE"):
            raise TpEvidenceBlocked("TP_EXECUTION_TYPE_INVALID")
        return order_id, timestamp, status, filled, event["E"]

    def _receipt(self, observation, current, now_ms, max_age_ms):
        if type(observation) is not tuple or len(observation) != 2:
            raise TpEvidenceBlocked("INDEPENDENT_ORDER_READ_SHAPE_INVALID")
        receipt, received_at_ms = observation
        if type(receipt) is not dict or not _fresh(received_at_ms, now_ms, max_age_ms):
            raise TpEvidenceBlocked("INDEPENDENT_ORDER_READ_STALE_OR_INVALID")
        if any(receipt.get(k) != self._wire[v] for k, v in _READ_FIELDS.items()):
            raise TpEvidenceBlocked("TP_READ_PLAN_MISMATCH")
        if (type(receipt.get("orderId")) is not int or receipt["orderId"] != current[0]
                or type(receipt.get("updateTime")) is not int
                or receipt["updateTime"] != current[1] or received_at_ms < current[4]
                or receipt.get("status") != current[2]):
            raise TpEvidenceBlocked("TP_READ_EVENT_MISMATCH")
        if (receipt.get("reduceOnly") not in (True, "true", "TRUE")
                or type(receipt.get("reduceOnly")) not in (bool, str)
                or _decimal(receipt.get("origQty")) != self._quantity
                or _decimal(receipt.get("price")) != self._price
                or _decimal(receipt.get("executedQty")) != current[3]):
            raise TpEvidenceBlocked("TP_READ_AMOUNT_OR_REDUCE_ONLY_MISMATCH")
