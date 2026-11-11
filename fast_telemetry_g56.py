"""G56 (Faz 3b): FAST işlem telemetrisi — sinyal/ön-giriş referans fiyatı ve emir yaşam döngüsü.

Eklemeli (append-only) JSONL, WAL'dan AYRI dizinde (fast_trade_telemetry/).
WAL kayıt biçimine, kapsam (scope) dosya kümesine ve resolve mantığına dokunmaz.
Hiçbir fonksiyon istisna fırlatmaz: telemetri hatası işlem akışını ASLA bloklamaz
(yazılamazsa False döner ve sayaç artar). Ağ, SDK, emir yürütme yoktur.

Slippage sonradan: dolum ort. fiyatı (ENTRY alındısı avgPrice) - referans fiyat
(SIGNAL.signal_price varsa o, yoksa PRE_ENTRY_REFERENCE.mark).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import time

DIRNAME = "fast_trade_telemetry"
SCHEMA = 1
MAX_FIELD = 200
# Emir alındısından kaydedilen alanlar (normal + ALGO/koşullu)
RECEIPT_FIELDS = (
    "symbol", "side", "positionSide", "type", "orderType", "algoType",
    "status", "algoStatus", "orderId", "algoId", "clientOrderId", "clientAlgoId",
    "reduceOnly", "closePosition", "timeInForce", "workingType",
    "origQty", "quantity", "executedQty", "avgPrice", "price", "triggerPrice",
    "actualOrderId", "actualPrice", "updateTime", "createTime", "time",
    "code", "msg",
)


def _clip(value):
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return repr(value)
    return str(value)[:MAX_FIELD]


def _dec(value):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def slippage(reference_price, avg_fill, side):
    """İşaretli slippage (fiyat birimi ve bps). Pozitif = aleyhte. Hatalıysa None."""
    ref, fill = _dec(reference_price), _dec(avg_fill)
    if ref is None or fill is None or ref <= 0 or side not in ("BUY", "SELL"):
        return None
    diff = (fill - ref) if side == "BUY" else (ref - fill)
    return {"price": str(diff), "bps": str((diff / ref * Decimal(10000)).quantize(Decimal("0.01")))}


class Telemetry:
    def __init__(self, directory, *, clock_ms=None, expected_uid=None):
        self.directory = Path(directory)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.expected_uid = expected_uid
        self.failures = 0
        self.written = 0
        self._last_status = {}

    # --- düşük seviye: asla raise etmez
    def append(self, kind, **fields):
        try:
            now = int(self.clock_ms())
            row = {"schema": SCHEMA, "kind": str(kind)[:64], "at_ms": now}
            for key, value in fields.items():
                if type(value) is dict:
                    row[key] = {str(k)[:64]: _clip(v) for k, v in list(value.items())[:64]}
                else:
                    row[key] = _clip(value)
            if self.directory.is_symlink():
                raise OSError("TELEMETRY_DIR_SYMLINK")
            self.directory.mkdir(mode=0o700, parents=False, exist_ok=True)
            st = self.directory.stat()
            if self.expected_uid is not None and st.st_uid != self.expected_uid:
                raise OSError("TELEMETRY_DIR_UID")
            if st.st_mode & 0o077:
                raise OSError("TELEMETRY_DIR_MODE")
            day = time.strftime("%Y%m%d", time.gmtime(now / 1000))
            path = self.directory / ("telemetry_%s.jsonl" % day)
            raw = (json.dumps(row, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False) + "\n").encode()
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            self.written += 1
            return True
        except Exception:
            self.failures += 1
            return False

    # --- olay türleri
    def record_signal(self, *, symbol, side, signal_price, signal_ts_ms, source, bundle_fp=None):
        """Sinyal üreticisinin gördüğü fiyat/zaman (emir öncesi)."""
        return self.append("SIGNAL", symbol=symbol, side=side, signal_price=signal_price,
                           signal_ts_ms=signal_ts_ms, source=source, bundle_fp=bundle_fp)

    def record_pre_entry_reference(self, *, bundle_fp, symbol, side, market):
        """Owner'ın giriş öncesi imzalı piyasa görüntüsü (mark/last) — sinyal fiyatı yoksa slippage referansı."""
        try:
            m = market if type(market) is dict else {}
            return self.append("PRE_ENTRY_REFERENCE", bundle_fp=bundle_fp, symbol=symbol, side=side,
                               mark=m.get("mark"), last_price=m.get("last_price"),
                               mark_time_ms=m.get("mark_time_ms"))
        except Exception:
            self.failures += 1
            return False

    def record_order_observation(self, role, receipt, *, phase, client_id=None):
        """İmzalı emir alındısı -> yaşam döngüsü kaydı. Aynı (kimlik, durum, dolum) tekrar yazılmaz."""
        try:
            r = receipt if type(receipt) is dict else {}
            fields = {k: r.get(k) for k in RECEIPT_FIELDS if k in r}
            cid = client_id or r.get("clientAlgoId") or r.get("clientOrderId") or "?"
            status = r.get("algoStatus") or r.get("status") or "UNKNOWN"
            key = (str(cid), str(status), str(r.get("executedQty")))
            if self._last_status.get(str(cid)) == key:
                return False
            self._last_status[str(cid)] = key
            return self.append("ORDER_STATE", role=role, phase=phase, client_id=cid,
                               state=status, receipt=fields)
        except Exception:
            self.failures += 1
            return False

    def record_order_disappeared(self, role, client_id, *, last_state):
        """Açık emir listesinden çıkan kendi emrimiz (terminal: FILLED/EXPIRED/CANCELED olabilir)."""
        try:
            prev = self._last_status.get(str(client_id))
            if prev is not None and prev[1] == "LEFT_OPEN_LIST":
                return False
            self._last_status[str(client_id)] = (str(client_id), "LEFT_OPEN_LIST", "")
            return self.append("ORDER_LEFT_OPEN_LIST", role=role, client_id=client_id,
                               last_state=last_state)
        except Exception:
            self.failures += 1
            return False

    def record_order_error(self, role, client_id, *, code, msg):
        return self.append("ORDER_ERROR", role=role, client_id=client_id, code=code, msg=msg)

    def record_alarm(self, alarm_code, **detail):
        return self.append("OWNER_ALARM", alarm=alarm_code, detail=detail)


_DEFAULT = {}


def default(base_dir):
    """Süreç başına tek Telemetry (owner PRIVATE altında). Hata -> None."""
    try:
        key = str(base_dir)
        if key not in _DEFAULT:
            _DEFAULT[key] = Telemetry(Path(base_dir) / DIRNAME, expected_uid=501)
        return _DEFAULT[key]
    except Exception:
        return None
