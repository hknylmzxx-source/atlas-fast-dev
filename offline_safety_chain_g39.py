"""G39: çalışan, bağımsız ve yalnız offline TP + kilit + STOP simülatörü.

G37 gerçek yerel telemetri günlüğünü, G38 bellek içi mock preflight'ı sağlar.
STOP yalnız frozen bir sentetik gözlemdir; emir, iptal, onarım veya kilit açma
arabirimi yoktur. Uyumlu rapor bile yetki vermez. Makbuz kimlikleri ve SHA256
gerçek owner/borsa kanıtı değildir; güvenilen Python süreci varsayılır.

Çalıştırma: python3 -B offline_safety_chain_g39.py --scenario all
CLI yalnız kendi geçici dizininde çalışır; dış veri/yol/endpoint kabul etmez.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import tp_lifecycle_offline_g37 as g37
import lock_evidence_preflight_offline_g38 as g38


# Standalone kullanımda ve testte aynı worktree'nin gerçek modülleri gerekir.
for _component in (g37, g38):
    if Path(_component.__file__).resolve().parent != Path(__file__).resolve().parent:
        raise ImportError("G39_LOCAL_OFFLINE_COMPONENTS_REQUIRED")


@dataclass(frozen=True)
class MockStopSnapshot:
    """Yalnız sahte koruma gözlemi; hiçbir yürütme davranışı içermez."""

    binding: g38.Binding
    present: bool = True


def _stop_stamp(stop):
    if (type(stop) is not MockStopSnapshot or type(stop.binding) is not g38.Binding
            or type(stop.present) is not bool):
        return None
    return (stop.binding.scope, stop.binding.account, stop.binding.symbol, stop.present)


@dataclass(frozen=True)
class ChainAlarm:
    origin: str
    code: str
    role: str = "CHAIN"
    event_id: str = ""
    storage: str = "MEMORY_ONLY"
    kind: str = "OWNER_REVIEW"


@dataclass(frozen=True)
class TPState:
    role: str
    state: str
    filled_qty: str
    quarantined: bool
    no_retry: bool


@dataclass(frozen=True)
class ChainReport:
    disposition: str
    reason_codes: tuple
    tp: tuple
    stop_state: str
    stop_unchanged: bool
    telemetry_health: str
    preflight_disposition: str
    preflight_reason_codes: tuple
    alarms: tuple
    restarted: bool
    synthetic_post_intents: int | None
    entry_authorized: bool = field(default=False, init=False)
    unlock: bool = field(default=False, init=False)
    manual_owner_review_required: bool = field(default=True, init=False)
    mode: str = field(default="OFFLINE_MOCK_ONLY", init=False)
    entry_policy: str = field(default="NO_NEW_ENTRY", init=False)
    post_policy: str = field(default="NO_RETRY", init=False)
    stop_policy: str = field(default="IMMUTABLE_MOCK_ONLY", init=False)
    real_signature_verified: bool = field(default=False, init=False)
    live_binance_validated: bool = field(default=False, init=False)

    def to_dict(self):
        return asdict(self)


class OfflineSafetyChain:
    """İki gerçek offline bileşeni bağlar; canlı yürütme kabiliyeti yoktur.

    Preflight her raporda güncel mock saatle yeniden değerlendirilir. G37
    olayları diskten replay edilir; G39 alarmları yalnız bellektedir. Günlük
    belirsizliği aynı örnekte kalıcıdır. Her disk replay ayrıca kapalı kalır:
    okunabilir kayıt geçmiş fsync başarısını veya gerçek dolumu kanıtlamaz.
    """

    def __init__(self, directory, *, binding, preflight, stop, clock_ms,
                 tp_source=None):
        self._binding = binding
        self._preflight = preflight
        self._stop = stop
        self._initial_stop = _stop_stamp(stop)
        self._clock = clock_ms
        self._source = g37.MockTrustedSource() if tp_source is None else tp_source
        self._evidence = None
        self._faults = []
        self._memory_alarms = []
        self._journal_unknown = False
        self._tracker = None
        if (type(binding) is not g38.Binding
                or type(preflight) is not g38.OfflinePreflight
                or type(self._source) is not g37.MockTrustedSource
                or not callable(clock_ms)):
            self._latch("INVALID_CHAIN_CONFIGURATION")
            return
        try:
            self._tracker = g37.TPLifecycleOffline(
                g37.EventJournal(directory), self._source, clock_ms=self._clock)
        except (g37.LifecycleError, OSError):
            self._mark_journal_unknown()

    @property
    def stop(self):
        return self._stop

    @property
    def source(self):
        """Makbuz üreten yerel mock kaynak; gerçek provenance sağlamaz."""
        return self._source

    @property
    def events(self):
        return () if self._tracker is None else self._tracker.events

    def tp_view(self, role):
        return None if self._tracker is None else self._tracker.view(role)

    def _latch(self, code):
        if code not in self._faults:
            self._faults.append(code)
        self._alarm(code)

    def _alarm(self, code):
        alarm = ChainAlarm("G39", code)
        if alarm not in self._memory_alarms:
            self._memory_alarms.append(alarm)

    def _mark_journal_unknown(self):
        self._journal_unknown = True
        self._latch("TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY")

    def _call_tp(self, operation, *args, **kwargs):
        if self._journal_unknown or self._tracker is None:
            return None
        try:
            return operation(*args, **kwargs)
        except g37.JournalError:
            self._mark_journal_unknown()
        except g37.LifecycleError:
            # Ham exception/yanıt veya serbest caller metni rapora girmez.
            self._latch("TP_OPERATION_REJECTED_NO_RETRY")
        return None

    def record_synthetic_intent(self, binding):
        if (type(binding) is not g37.Binding or type(self._binding) is not g38.Binding
                or binding.scope != self._binding.scope
                or binding.symbol != self._binding.symbol):
            self._latch("TP_CHAIN_BINDING_MISMATCH")
            return None
        operation = None if self._tracker is None else self._tracker.record_synthetic_post_intent
        return self._call_tp(operation, binding)

    def observe(self, role, receipt):
        operation = None if self._tracker is None else self._tracker.observe
        return self._call_tp(operation, role, receipt)

    def record_unknown(self, role, *, reason="AMBIGUOUS_POST"):
        operation = None if self._tracker is None else self._tracker.record_unknown
        return self._call_tp(operation, role, reason=reason)

    def supply_owner_evidence(self, receipts, journal, snapshot):
        """Hazır Decision kabul edilmez; her rapor gerçek G38 evaluate çağırır."""
        self._evidence = (receipts, journal, snapshot)

    def _owner_decision(self):
        if "INVALID_CHAIN_CONFIGURATION" in self._faults:
            return g38.Decision("FAIL_CLOSED", ("INVALID_CHAIN_CONFIGURATION",))
        if self._preflight.binding != self._binding:
            return g38.Decision("FAIL_CLOSED", ("PREFLIGHT_CHAIN_BINDING_MISMATCH",))
        if self._evidence is None:
            return g38.Decision("FAIL_CLOSED", ("OWNER_EVIDENCE_ABSENT",))
        try:
            return g38.OfflinePreflight.evaluate(
                self._preflight, *self._evidence, now_ms=self._clock())
        except (TypeError, ValueError, AttributeError, OSError):
            return g38.Decision("FAIL_CLOSED", ("OWNER_PREFLIGHT_EVALUATION_UNKNOWN",))

    def report(self):
        owner = self._owner_decision()
        # Önceden raporlanmış yerel alarmlar da otomatik temizlenmez.
        failures = list(dict.fromkeys((*self._faults, *(a.code for a in self._memory_alarms))))
        stamp = _stop_stamp(self._stop)
        unchanged = stamp is not None and stamp == self._initial_stop
        if not unchanged:
            failures.append("STOP_SNAPSHOT_INVALID_OR_CHANGED")
        if stamp is None:
            stop_state = "INVALID_MOCK"
        else:
            stop_state = "PRESENT_MOCK" if self._stop.present else "ABSENT_MOCK"
            if self._stop.binding != self._binding:
                failures.append("STOP_CHAIN_BINDING_MISMATCH")
            if not self._stop.present:
                failures.append("STOP_ABSENT_NO_REPAIR")
        if owner.disposition != "MANUAL_OWNER_REVIEW":
            failures.append("OWNER_PREFLIGHT_FAIL_CLOSED")
            failures.extend(code for code in owner.reason_codes if code not in
                            {"MOCK_PROVENANCE_ONLY", "MANUAL_OWNER_REVIEW_REQUIRED"})

        telemetry = None if self._tracker is None else self._tracker.report()
        unknown = self._journal_unknown or telemetry is None or telemetry["journal_health"] != "OK"
        if unknown:
            failures.append("TELEMETRY_JOURNAL_UNKNOWN_NO_RETRY")
        restarted = telemetry is not None and telemetry["restarted"]
        if restarted:
            failures.append("RESTART_REPLAY_REQUIRES_OWNER_REVIEW")
        states = []
        for role in ("TP1", "TP2"):
            view = self.tp_view(role)
            if view is None:
                failures.append(f"{role}_UNAVAILABLE" if unknown else f"{role}_ABSENT")
            else:
                states.append(TPState(role, view.state, view.filled_qty,
                                      view.quarantined, view.no_retry))
                if view.binding.scope != self._binding.scope or view.binding.symbol != self._binding.symbol:
                    failures.append("TP_CHAIN_BINDING_MISMATCH")
                if view.state != "FILLED":
                    failures.append(f"{role}_{view.state}")
        alarms = tuple(
            ChainAlarm("G37", e.reason, e.binding.role, e.event_id,
                       "JOURNALED_LOCAL_OUTBOX", e.alarm)
            for e in self.events if e.alarm != "NONE"
        )
        if alarms:
            failures.append("TP_ALARM_HISTORY_REQUIRES_OWNER_REVIEW")
        for code in dict.fromkeys(failures):
            self._alarm(code)
        reasons = tuple(dict.fromkeys(("MOCK_PROVENANCE_ONLY", "MANUAL_OWNER_REVIEW_REQUIRED",
                                       *failures)))
        return ChainReport(
            "FAIL_CLOSED" if failures else "MANUAL_OWNER_REVIEW", reasons, tuple(states),
            stop_state, unchanged, "UNKNOWN_NO_RETRY" if unknown else
            ("REPLAY_ONLY_NO_DURABILITY_PROOF" if restarted else "OK"),
            owner.disposition, owner.reason_codes, alarms + tuple(self._memory_alarms),
            restarted, None if unknown else telemetry["synthetic_post_intents"],
        )


SCENARIOS = (
    "normal", "tp1-expired", "tp2-partial", "post-unknown", "stop-absent",
    "owner-flat", "owner-open", "preflight-stale", "preflight-forged",
    "preflight-mismatched", "conditional-open-order", "normal-open-order",
    "duplicate", "reordered", "crash-restart", "journal-fsync-unknown",
    "journal-published-fsync-unknown", "owner-journal-partial", "owner-journal-toctou",
)


def _fsync_fault(stage):
    """Yalnız tek iş parçacıklı CLI fixture'ında, geçici günlük için enjeksiyon."""
    original = g37.os.fsync
    count = 0

    def injected(fd):
        nonlocal count
        count += 1
        if count == stage:
            raise OSError("SYNTHETIC_FSYNC_UNKNOWN")
        return original(fd)

    return patch.object(g37.os, "fsync", side_effect=injected)


def run_scenario(name="normal"):
    """İki TP'yi yürüyen olaylarla simüle eder; yalnız sentetik girdiler üretir."""
    if name not in SCENARIOS:
        raise ValueError("Bilinmeyen offline G39 senaryosu")
    owner_name = {
        "owner-open": "position-open", "preflight-stale": "stale",
        "preflight-mismatched": "account-mismatch", "conditional-open-order": "algo-order-open",
        "normal-open-order": "normal-order-open", "owner-journal-partial": "crash-partial",
        "owner-journal-toctou": "toctou",
    }.get(name, "coherent")
    owner = g38.build_scenario(owner_name)
    if name == "preflight-forged":
        owner = replace(owner, receipts=(replace(owner.receipts[0]),) + owner.receipts[1:])
    binding = owner.preflight.binding
    stop = MockStopSnapshot(binding, name != "stop-absent")
    now = [owner.now_ms]
    tp_bindings = tuple(g37.Binding(role, binding.scope, binding.symbol, f"g39-{role}", qty)
                        for role, qty in (("TP1", "2"), ("TP2", "3")))

    with TemporaryDirectory(prefix="atlas-g39-") as temporary:
        directory = Path(temporary) / "telemetry"

        def new_chain():
            result = OfflineSafetyChain(directory, binding=binding, preflight=owner.preflight,
                                        stop=stop, clock_ms=lambda: now[0])
            result.supply_owner_evidence(owner.receipts, owner.journal, owner.snapshot)
            return result

        chain = new_chain()
        for tp in tp_bindings:
            chain.record_synthetic_intent(tp)

        def read(tp, status, qty="0", sequence=None):
            now[0] += 10
            view = chain.tp_view(tp.role)
            receipt = chain.source.issue_mock_read(
                tp, status, qty, now[0], view.read_sequence + 1 if sequence is None else sequence)
            chain.observe(tp.role, receipt)
            return receipt

        if name == "post-unknown":
            chain.record_unknown("TP1")
        else:
            first = read(tp_bindings[0], "NEW")
        read(tp_bindings[1], "NEW")

        before_restart = None
        if name == "tp1-expired":
            read(tp_bindings[0], "EXPIRED")
        elif name == "duplicate":
            chain.observe("TP1", first)
        elif name == "reordered":
            read(tp_bindings[0], "PARTIAL", "1", sequence=1)
        elif name == "crash-restart":
            chain.record_unknown("TP1", reason="READ_TIMEOUT")
        elif name in ("journal-fsync-unknown", "journal-published-fsync-unknown"):
            # 1: dosya fsync; 3: yayın/pending kaldırma sonrası dizin fsync.
            with _fsync_fault(1 if name == "journal-fsync-unknown" else 3):
                read(tp_bindings[0], "PARTIAL", "1")
        elif name != "post-unknown":
            read(tp_bindings[0], "FILLED", "2")

        if name not in ("journal-fsync-unknown", "journal-published-fsync-unknown"):
            read(tp_bindings[1], "PARTIAL" if name == "tp2-partial" else "FILLED",
                 "1" if name == "tp2-partial" else "3")
        if name in ("crash-restart", "journal-fsync-unknown", "journal-published-fsync-unknown"):
            before_restart = chain.report().to_dict()
            chain = new_chain()  # Önceki nesnenin belleği aktarılmaz; gerçek G37 disk replay.
            chain.record_synthetic_intent(tp_bindings[0])  # Tekrar niyet yetkisi verilmez.
        return {"scenario": name, "before_restart": before_restart,
                "result": chain.report().to_dict()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="G39: yalnız offline TP/kilit/STOP simülatörü")
    parser.add_argument("--scenario", choices=SCENARIOS + ("all",), default="normal")
    args = parser.parse_args(argv)
    names = SCENARIOS if args.scenario == "all" else (args.scenario,)
    results = [run_scenario(name) for name in names]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 2 if any(r["result"]["disposition"] == "FAIL_CLOSED" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
