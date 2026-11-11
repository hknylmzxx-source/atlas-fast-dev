"""G56 (Faz 3b): owner gözetim döngüsüne bağlanan TP'siz-pozisyon denetimi + yaşam döngüsü gözlemi.

- Salt-okuma: yalnız stop_ambiguity_guard.gather ile alınmış imzalı GET kanıtını işler.
  Emir göndermez, iptal etmez, kilit/WAL'a yazmaz.
- İhlalde mevcut owner alarm kanalı (inputs.stop_guard()[1], MANUAL_CLOSE_REQUIRED ile aynı
  kanal) çağrılır + telemetriye OWNER_ALARM kaydı düşülür.
- Tekilleştirme: aynı (sembol, neden) için COOLDOWN_S içinde tek bildirim.
- Denetim hatası botu durdurmaz: maybe_audit ASLA raise etmez; hata kendisi alarm üretir
  (TP_EXPOSURE_AUDIT_FAILED / TP_EXPOSURE_EVIDENCE_INCOMPLETE, aynı soğuma ile).
"""
from __future__ import annotations

import time

from tp_exposure_guard_offline_g55 import unprotected_positions

MIN_INTERVAL_S = 30      # gözetim tick'i 5 sn; imzalı GET yükünü sınırlamak için denetim <=30 sn'de bir
COOLDOWN_S = 300         # aynı ihlal için en fazla 5 dakikada bir bildirim (mevcut _notify ile aynı)


def _payload(ev, key):
    item = ev.get(key) if type(ev) is dict else None
    if type(item) is tuple and len(item) == 2:
        return item[0]
    return None


def evidence_to_inputs(ev, symbol):
    """gather() kanıtı -> (positions{symbol: qty}, orders[list]). Okunamayan -> (None, [])."""
    account = _payload(ev, "account")
    if type(account) is not dict or type(account.get("positions")) is not list:
        return None, []
    qty = "0"
    for row in account["positions"]:
        if type(row) is dict and row.get("symbol") == symbol:
            qty = row.get("positionAmt", "?")
            if qty == "0" or qty == 0:
                continue
            break
    orders = []
    for key in ("open_orders", "open_algo"):
        payload = _payload(ev, key)
        if type(payload) is list:
            orders.extend(o for o in payload if type(o) is dict)
        elif type(payload) is dict and type(payload.get("orders")) is list:
            orders.extend(o for o in payload["orders"] if type(o) is dict)
    return {symbol: qty}, orders


class TpExposureMonitor:
    def __init__(self, *, alarm, symbol, own_ids=None, telemetry=None, clock_s=None,
                 min_interval_s=MIN_INTERVAL_S, cooldown_s=COOLDOWN_S):
        self.alarm = alarm
        self.symbol = symbol
        self.own_ids = dict(own_ids or {})          # client_id -> role
        self.telemetry = telemetry
        self.clock_s = clock_s or time.monotonic
        self.min_interval_s = min_interval_s
        self.cooldown_s = cooldown_s
        self._last_audit = None
        self._last_sent = {}
        self._seen_open = {}                        # client_id -> son durum
        self.audits = 0
        self.alarms_sent = 0
        self.errors = []

    def _raise_alarm(self, key, message, **detail):
        now = self.clock_s()
        last = self._last_sent.get(key)
        if last is not None and 0 <= now - last < self.cooldown_s:
            return False
        self._last_sent[key] = now
        try:
            if self.telemetry is not None:
                self.telemetry.record_alarm(message.split(":", 1)[0], message=message, **detail)
        except Exception:
            pass
        try:
            self.alarm(message)
            self.alarms_sent += 1
            return True
        except Exception as e:
            self.errors.append("ALARM_CHANNEL_FAILED:" + type(e).__name__)
            return False

    def _observe_lifecycle(self, ev, orders):
        if self.telemetry is None:
            return
        try:
            present = {}
            for o in orders:
                cid = o.get("clientAlgoId") or o.get("clientOrderId")
                if cid in self.own_ids:
                    present[cid] = o
                    self.telemetry.record_order_observation(self.own_ids[cid], o, phase="SUPERVISE_OPEN_LIST")
            for cid, last in list(self._seen_open.items()):
                if cid not in present:
                    self.telemetry.record_order_disappeared(self.own_ids.get(cid, "?"), cid, last_state=last)
                    del self._seen_open[cid]
            for cid, o in present.items():
                self._seen_open[cid] = o.get("algoStatus") or o.get("status")
            own_stop = ev.get("own_stop") if type(ev) is dict else None
            if type(own_stop) is tuple and len(own_stop) == 2 and type(own_stop[1]) is dict:
                cid = own_stop[1].get("clientAlgoId")
                self.telemetry.record_order_observation(self.own_ids.get(cid, "STOP"), own_stop[1],
                                                        phase="SUPERVISE_QUERY_ALGO", client_id=cid)
        except Exception as e:
            self.errors.append("LIFECYCLE_OBSERVE_FAILED:" + type(e).__name__)

    def check(self, ev):
        """Tek kanıt üzerinde denetim. Dönüş: ihlal listesi. Asla raise etmez."""
        try:
            self.audits += 1
            errors = ev.get("errors") if type(ev) is dict else ["EVIDENCE_NOT_OBJECT"]
            positions, orders = evidence_to_inputs(ev, self.symbol)
            self._observe_lifecycle(ev, orders)
            missing = [k for k in ("account", "open_orders", "open_algo") if _payload(ev, k) is None]
            if positions is None or missing:
                self._raise_alarm(("EVIDENCE", self.symbol),
                                  "TP_EXPOSURE_EVIDENCE_INCOMPLETE:%s:%s" % (self.symbol, ",".join(missing or ["account"])),
                                  errors=";".join(str(x) for x in (errors or []))[:200])
                return [{"symbol": self.symbol, "position_qty": "?", "reason": "EVIDENCE_INCOMPLETE"}]
            violations = unprotected_positions(positions, orders)
            for v in violations:
                self._raise_alarm((v["symbol"], v["reason"]),
                                  "TP_EXPOSURE_ALARM:%s:%s:%s" % (v["symbol"], v["position_qty"], v["reason"]),
                                  symbol=v["symbol"], position_qty=v["position_qty"], reason=v["reason"])
            return violations
        except Exception as e:
            self.errors.append("CHECK_FAILED:" + type(e).__name__)
            self._raise_alarm(("AUDIT", self.symbol), "TP_EXPOSURE_AUDIT_FAILED:%s:%s" % (self.symbol, type(e).__name__))
            return [{"symbol": self.symbol, "position_qty": "?", "reason": "AUDIT_FAILED"}]

    def maybe_audit(self, read_evidence):
        """Aralık dolduysa kanıtı oku ve denetle. Asla raise etmez."""
        try:
            now = self.clock_s()
            if self._last_audit is not None and 0 <= now - self._last_audit < self.min_interval_s:
                return None
            self._last_audit = now
            try:
                ev = read_evidence()
            except Exception as e:
                self._raise_alarm(("AUDIT", self.symbol), "TP_EXPOSURE_AUDIT_FAILED:%s:%s" % (self.symbol, type(e).__name__))
                return [{"symbol": self.symbol, "position_qty": "?", "reason": "AUDIT_FAILED"}]
            return self.check(ev)
        except Exception as e:
            try:
                self.errors.append("MAYBE_AUDIT_FAILED:" + type(e).__name__)
            except Exception:
                pass
            return None
