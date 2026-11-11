"""G55 (Faz 3): TP alarmını pozisyon bağlamıyla sınıflandırma + TP'siz açık pozisyon denetimi.

Saf fonksiyonlar: I/O, SDK, ağ, emir yürütme, kilit veya owner bağlantısı yoktur.
Belirsiz/bozuk girdi daima güvenli tarafa (alarm/ihlal) düşer.

Kural (11.11.2026 canlı analizi): STOP dolup pozisyon 0 olduğunda reduce-only TP'lerin
aynı anda EXPIRED olması NORMALDİR ve alarm değildir. Alarm yalnızca bir TP
EXPIRED/CANCELED/REJECTED olduğunda pozisyon hâlâ açıksa VE o pozisyonu koruyan başka
canlı TP yoksa üretilir.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

TERMINAL_NON_FILL = frozenset(("EXPIRED", "CANCELED", "REJECTED"))
LIVE_ORDER_STATUSES = frozenset(("NEW", "PARTIALLY_FILLED", "PARTIAL"))
TP_ORDER_TYPES = frozenset(("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "LIMIT"))
_SYMBOL = re.compile(r"^[A-Z0-9]{2,24}$")

# Sınıflandırma sonuçları
CTX_FLAT_EXPIRY = "FLAT_TERMINAL_INFO"          # pozisyon 0 -> bilgi kaydı, alarm yok
CTX_EXPOSED_PROTECTED = "EXPOSED_OTHER_TP_LIVE"  # pozisyon açık ama başka canlı TP koruyor
CTX_EXPOSED_UNPROTECTED = "EXPOSED_NO_LIVE_TP"   # pozisyon açık, koruyan TP yok -> OWNER_ALARM


def _decimal(value):
    if type(value) not in (str, int, Decimal) or type(value) is bool:
        raise ValueError("INVALID_QTY")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("INVALID_QTY") from None
    if not number.is_finite():
        raise ValueError("INVALID_QTY")
    return number


@dataclass(frozen=True)
class ExposureContext:
    """TP terminal gözlemi anındaki imzalı pozisyon görüntüsü (çağıran sağlar).

    position_qty: işaretli pozisyon miktarı (long>0, short<0, düz=0) — string.
    other_live_tp: aynı sembolde, pozisyonu kapatan yönde, canlı reduce-only TP var mı.
    """
    position_qty: str
    other_live_tp: bool

    def __post_init__(self):
        _decimal(self.position_qty)
        if type(self.other_live_tp) is not bool:
            raise ValueError("INVALID_EXPOSURE_CONTEXT")


def classify_tp_terminal(status, context):
    """TP terminal (non-fill) durumunu pozisyon bağlamıyla sınıflandır.

    context None/bozuksa -> CTX_EXPOSED_UNPROTECTED (fail-closed: alarm).
    """
    if status not in TERMINAL_NON_FILL:
        raise ValueError("NOT_TERMINAL_NON_FILL")
    if type(context) is not ExposureContext:
        return CTX_EXPOSED_UNPROTECTED
    try:
        qty = _decimal(context.position_qty)
    except ValueError:
        return CTX_EXPOSED_UNPROTECTED
    if qty == 0:
        return CTX_FLAT_EXPIRY
    if context.other_live_tp is True:
        return CTX_EXPOSED_PROTECTED
    return CTX_EXPOSED_UNPROTECTED


def _is_true(value):
    return value is True or value == "true" or value == "True"


def _order_protects(order, symbol, qty):
    """Bu emir verilen pozisyonu kapatan yönde canlı bir TP mi?"""
    if type(order) is not dict or order.get("symbol") != symbol:
        return False
    status = order.get("status", order.get("algoStatus"))
    if status not in LIVE_ORDER_STATUSES:
        return False
    otype = order.get("type", order.get("orderType"))
    if otype not in TP_ORDER_TYPES:
        return False
    if not (_is_true(order.get("reduceOnly")) or _is_true(order.get("closePosition"))):
        return False
    side = order.get("side")
    if qty > 0 and side != "SELL":
        return False
    if qty < 0 and side != "BUY":
        return False
    position_side = order.get("positionSide", "BOTH")
    if position_side not in ("BOTH", "LONG", "SHORT"):
        return False
    if position_side == "LONG" and qty < 0 or position_side == "SHORT" and qty > 0:
        return False
    return True


def unprotected_positions(positions, open_orders):
    """Periyodik denetim: (pozisyonlar, açık emirler) -> ihlal listesi.

    positions: {symbol: işaretli miktar (str/int/Decimal)}.
    open_orders: dict listesi (Binance openOrders / openAlgoOrders biçimi:
      symbol, type|orderType, side, status|algoStatus, reduceOnly/closePosition, positionSide).
    Bozuk pozisyon girdisi -> ihlal (UNREADABLE_POSITION). Bozuk emir koruma sayılmaz.
    Dönüş: [{"symbol", "position_qty", "reason"}] sembole göre sıralı.
    """
    violations = []
    if type(positions) is not dict:
        return [{"symbol": "*", "position_qty": "?", "reason": "UNREADABLE_POSITIONS"}]
    try:
        orders = list(open_orders)
    except TypeError:
        orders = []
    for symbol in sorted(positions, key=str):
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
            violations.append({"symbol": str(symbol)[:24], "position_qty": "?",
                               "reason": "UNREADABLE_POSITION"})
            continue
        try:
            qty = _decimal(positions[symbol])
        except ValueError:
            violations.append({"symbol": symbol, "position_qty": "?",
                               "reason": "UNREADABLE_POSITION"})
            continue
        if qty == 0:
            continue
        if not any(_order_protects(order, symbol, qty) for order in orders):
            violations.append({"symbol": symbol, "position_qty": str(qty),
                               "reason": "OPEN_POSITION_WITHOUT_LIVE_TP"})
    return violations
