"""G41: yalnız bellek üzerinde çalışan, saf offline deadline/fence modeli.

Her çağrı eski durumu değiştirmeden Transition döndürür. Saat, contention ve
fsync sonuçları açık sentetik girdilerdir; sleep, dosya, ağ, SDK veya OS kilidi
yoktur. Token kimliği yalnız mock otoriteyi temsil eder; imza/güvenlik değildir.

SÖZLEŞME: pozitif timeout için now < deadline; timeout_s=0 için yalnız başlangıç
anında tek nonblocking fence denemesi ve aynı model anında mutation mümkündür.
Busy sıfır bütçede terminaldir. Readiness bildirimi lease yerine kullanılamaz.
Lease ve deadline hem fence öncesinde hem fence altında mutation öncesinde
kontrol edilir. Son kontrol + mock WAL ekleme tek SOYUT atomik geçiştir.
Gerçek Python talimatları arasındaki preemption/TOCTOU bu varsayımla çözülmüş
sayılmaz; tüm yazarları kapsayan gerçek otorite ve atomiklik ayrıca gerekir.

Mutation başladıktan sonra gecikme, fsync hatası veya crash UNKNOWN/NO_RETRY
üretir. WAL yokluğu çıkarılmaz. Crash sonrası durum yalnız replay içindir.
Mock WAL kaydı STOP kanıtı değildir; STOP gözlemi hiçbir geçişte değiştirilmez.
Her karar NO_NEW_ENTRY ve NO_UNLOCK taşır. Canlı entegrasyon onayı değildir.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math


class Phase(str, Enum):
    WAITING = "WAITING"
    FENCED = "FENCED"
    MUTATED = "MUTATED"
    DURABLE = "DURABLE"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class FsyncOutcome(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MockStopSnapshot:
    """Dışarıdan verilen değişmez mock gözlem; WAL ile üretilemez/onarılmaz."""

    present: bool | None = None


@dataclass(frozen=True, eq=False)
class MockLeaseToken:
    """Yalnız modelin güncel nesnesi geçerli; alanları aynı kopya geçersizdir."""

    scope: str
    owner: str
    epoch: int
    generation: int
    expires_at: float


@dataclass(frozen=True)
class Attempt:
    request_id: str
    role: str
    token: MockLeaseToken
    started_at: float
    deadline: float
    poll_s: float
    zero_budget: bool
    phase: Phase = Phase.WAITING
    fence_attempts: int = 0
    reason: str = "PENDING"


@dataclass(frozen=True)
class MockWalFact:
    """Gözlemci geçmişi: mutation denendi; gerçek disk/replay kanıtı değildir."""

    request_id: str
    role: str
    epoch: int
    generation: int
    mutated_at: float
    durable: bool | None = None


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    wait_s: float | None = None
    entry_policy: str = field(default="NO_NEW_ENTRY", init=False)
    unlock_policy: str = field(default="NO_UNLOCK", init=False)
    entry_authorized: bool = field(default=False, init=False)
    unlock: bool = field(default=False, init=False)
    stop_proven: bool = field(default=False, init=False)

    @property
    def retry_policy(self) -> str:
        # Yeniden deneme yalnız mutation ÖNCESİ meşgul fence içindir.
        return "WAIT_PRE_MUTATION_ONLY" if self.status == "WAIT" else "NO_RETRY"


@dataclass(frozen=True)
class Transition:
    model: DeadlineFenceModel
    decision: Decision


def _seconds(value) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _identifier(value) -> bool:
    return (type(value) is str and 0 < len(value) <= 80
            and all(c.isascii() and (c.isalnum() or c in "_-") for c in value))


@dataclass(frozen=True)
class DeadlineFenceModel:
    """Tek scope, tek sıralı mock otorite. Eski snapshot üzerinde fork yasaktır.

    Frozen alanlar düşmanca Python kodunu engellemez. Model oluşturucusu ve
    geçiş sırası güvenilir harness'e aittir; yalnız token alanlarını sunmak
    yetmez. Gerçek süreçler arası eşzamanlama veya dayanıklı depolama yoktur.
    """

    scope: str = "G41_OFFLINE"
    stop: MockStopSnapshot = field(default_factory=MockStopSnapshot)
    epoch: int = 1
    generation: int = 0
    lease: MockLeaseToken | None = None
    last_now: float = 0.0
    external_busy: bool = False
    fence_owner: str | None = None
    attempts: tuple[Attempt, ...] = ()
    wal: tuple[MockWalFact, ...] = ()
    quarantined: bool = False
    replay_only: bool = False

    def _reply(self, status, reason, wait_s=None):
        return Transition(self, Decision(status, reason, wait_s))

    def _clock_error(self, now_s):
        if not _seconds(now_s):
            return "CLOCK_INVALID"
        if now_s < self.last_now:
            return "CLOCK_REGRESSION"
        return None

    def _find(self, request_id):
        return next((a for a in self.attempts if a.request_id == request_id), None)

    def _update(self, attempt):
        return replace(self, attempts=tuple(
            attempt if a.request_id == attempt.request_id else a
            for a in self.attempts))

    def _lease_error(self, token, now_s):
        if (type(token) is not MockLeaseToken or token is not self.lease
                or token.scope != self.scope or token.epoch != self.epoch
                or token.generation != self.generation):
            return "LEASE_UNTRUSTED_OR_STALE"
        if now_s >= token.expires_at:
            return "LEASE_EXPIRED"
        return None

    def _admission_error(self, attempt, now_s):
        if self.replay_only:
            return "RESTART_REPLAY_ONLY"
        if self.quarantined:
            return "SCOPE_UNKNOWN_NO_RETRY"
        lease_error = self._lease_error(attempt.token, now_s)
        if lease_error:
            return lease_error
        if (now_s != attempt.started_at if attempt.zero_budget
                else now_s >= attempt.deadline):
            return "DEADLINE_BEFORE_MUTATION"
        return None

    def _finish(self, attempt, phase, reason):
        model = self._update(replace(attempt, phase=phase, reason=reason))
        if model.fence_owner == attempt.request_id:
            # Yalnız modelin bookkeeping alanı; gerçek kilit açılmaz.
            model = replace(model, fence_owner=None)
        if phase == Phase.UNKNOWN:
            model = replace(model, quarantined=True)
        return model._reply(phase.value, reason)

    def _closed_or_missing(self, attempt):
        if attempt is None:
            return self._reply("REJECTED", "REQUEST_NOT_FOUND")
        if attempt.phase in (Phase.DURABLE, Phase.REJECTED, Phase.UNKNOWN):
            return self._reply(attempt.phase.value, attempt.reason)
        return None

    def mock_issue_lease(self, *, owner, now_s, ttl_s):
        """Güvenilir mock otorite yeni nesne/nesil üretir; eski nesil çitlenir."""
        error = self._clock_error(now_s)
        if error:
            return self._reply("REJECTED", error)
        if self.replay_only or self.quarantined:
            return self._reply("REJECTED", "SCOPE_CLOSED_NO_RETRY")
        if (not _identifier(self.scope) or not _identifier(owner)
                or not _seconds(ttl_s) or ttl_s <= 0
                or not _seconds(now_s + ttl_s) or now_s + ttl_s <= now_s):
            return self._reply("REJECTED", "MOCK_LEASE_INPUT_INVALID")
        token = MockLeaseToken(self.scope, owner, self.epoch,
                               self.generation + 1, now_s + ttl_s)
        return replace(self, lease=token, generation=token.generation,
                       last_now=now_s)._reply("MOCK_LEASE", "MOCK_ONLY")

    def mock_contention(self, *, busy, now_s):
        """Harici mock fence'in gözlemi; alınmış model fence'ini bozamaz."""
        error = self._clock_error(now_s)
        if error:
            return self._reply("REJECTED", error)
        if type(busy) is not bool:
            return self._reply("REJECTED", "MOCK_BUSY_INVALID")
        if busy and self.fence_owner is not None:
            return self._reply("REJECTED", "MODEL_FENCE_ALREADY_HELD")
        return replace(self, external_busy=busy, last_now=now_s)._reply(
            "MOCK_CONTENTION", "MOCK_ONLY")

    def begin(self, *, request_id, role, token, timeout_s, poll_s, now_s):
        """Çağrı anında deadline oluşturur; lease zamanı/readiness'e bağlamaz."""
        error = self._clock_error(now_s)
        if error:
            return self._reply("REJECTED", error)
        model = replace(self, last_now=now_s)
        if not _identifier(request_id) or type(role) is not str or role not in (
                "STOP", "TP1", "TP2"):
            return model._reply("REJECTED", "REQUEST_INPUT_INVALID")
        if model._find(request_id) is not None:
            return model._reply("REJECTED", "REQUEST_ALREADY_EXISTS_NO_RETRY")
        if (not _seconds(timeout_s) or timeout_s > 0.5
                or not _seconds(poll_s) or not 0 < poll_s <= 0.05
                or not _seconds(now_s + timeout_s)
                or (timeout_s > 0 and now_s + timeout_s <= now_s)):
            return model._reply("REJECTED", "WAIT_BOUND_INVALID")
        attempt = Attempt(request_id, role, token, now_s, now_s + timeout_s,
                          poll_s, timeout_s == 0)
        model = replace(model, attempts=model.attempts + (attempt,))
        error = model._admission_error(attempt, now_s)
        if error:
            return model._finish(attempt, Phase.REJECTED, error)
        return model._reply("PENDING", "TRY_NONBLOCKING_MOCK_FENCE")

    def try_fence(self, request_id, *, now_s):
        attempt = self._find(request_id)
        closed = self._closed_or_missing(attempt)
        if closed:
            return closed
        if attempt.phase == Phase.MUTATED:
            return self._finish(attempt, Phase.UNKNOWN, "MUTATION_ALREADY_STARTED_NO_RETRY")
        if attempt.phase != Phase.WAITING:
            return self._reply("REJECTED", "FENCE_ATTEMPT_OUT_OF_ORDER")
        error = self._clock_error(now_s)
        model = self if error else replace(self, last_now=now_s)
        error = error or model._admission_error(attempt, now_s)
        if error:
            return model._finish(attempt, Phase.REJECTED, error)
        attempt = replace(attempt, fence_attempts=attempt.fence_attempts + 1)
        model = model._update(attempt)
        if model.external_busy or model.fence_owner is not None:
            if attempt.zero_budget:
                return model._finish(attempt, Phase.REJECTED, "ZERO_BUDGET_BUSY")
            return model._reply("WAIT", "FENCE_BUSY_PRE_MUTATION",
                                min(attempt.poll_s, attempt.deadline - now_s))
        model = model._update(replace(attempt, phase=Phase.FENCED))
        return replace(model, fence_owner=request_id)._reply(
            "FENCED", "MUTATION_REQUIRES_FRESH_VALIDATION")

    def atomic_mutate(self, request_id, *, now_s):
        """Aynı soyut fence altında son kontrol + tek mock WAL mutation.

        Kontrol ile ekleme arasında callback/yield yoktur. Bu, OS seviyesinde
        atomiklik veya fsync'in deadline içinde biteceği garantisi değildir.
        """
        attempt = self._find(request_id)
        closed = self._closed_or_missing(attempt)
        if closed:
            return closed
        if attempt.phase == Phase.MUTATED:
            return self._finish(attempt, Phase.UNKNOWN, "MUTATION_ALREADY_STARTED_NO_RETRY")
        if attempt.phase != Phase.FENCED or self.fence_owner != request_id:
            return self._finish(attempt, Phase.REJECTED, "MODEL_FENCE_NOT_OWNED")
        error = self._clock_error(now_s)
        model = self if error else replace(self, last_now=now_s)
        error = error or model._admission_error(attempt, now_s)
        if error:
            return model._finish(attempt, Phase.REJECTED, error)
        if model.external_busy:
            return model._finish(attempt, Phase.REJECTED, "MODEL_FENCE_CONFLICT")
        # Her role/scope için tek mutation; kontrol de fence altındadır.
        if any(row.role == attempt.role for row in model.wal):
            return model._finish(attempt, Phase.REJECTED, "DUPLICATE_WAL_NO_RETRY")
        fact = MockWalFact(request_id, attempt.role, model.epoch,
                           model.generation, now_s)
        model = model._update(replace(attempt, phase=Phase.MUTATED))
        return replace(model, wal=model.wal + (fact,))._reply(
            "MUTATED", "MOCK_FSYNC_PENDING_NO_RETRY")

    def finish_fsync(self, request_id, *, file_outcome, directory_outcome, now_s):
        """Yazım sonrası hata/eksik/geç ACK asla sıfır mutation sayılmaz."""
        attempt = self._find(request_id)
        closed = self._closed_or_missing(attempt)
        if closed:
            return closed
        if attempt.phase != Phase.MUTATED:
            return self._finish(attempt, Phase.REJECTED, "FSYNC_WITHOUT_MUTATION")
        error = self._clock_error(now_s)
        model = self if error else replace(self, last_now=now_s)
        outcomes_known = (type(file_outcome) is FsyncOutcome
                          and type(directory_outcome) is FsyncOutcome)
        synced = (outcomes_known and file_outcome == FsyncOutcome.OK
                  and directory_outcome == FsyncOutcome.OK)
        model = replace(model, wal=tuple(
            replace(row, durable=True if synced else None)
            if row.request_id == request_id else row for row in model.wal))
        error = error or model._admission_error(attempt, now_s)
        if not synced:
            error = error or "FSYNC_OUTCOME_UNKNOWN"
        if error:
            return model._finish(attempt, Phase.UNKNOWN, error)
        return model._finish(attempt, Phase.DURABLE, "MOCK_WAL_DURABLE_ONLY")

    def crash_restart(self, *, now_s):
        """Soyut çökme: eski lease geçersiz, hiçbir isteğe otomatik devam yok.

        wal bir gözlemci geçmişidir; güç kesintisinde görünür disk içeriği
        modellenmez. Kaydın görünmemesi bile retry yetkisi oluşturmaz.
        """
        error = self._clock_error(now_s)
        attempts = tuple(
            replace(a, phase=Phase.UNKNOWN, reason="CRASH_AFTER_MUTATION_NO_RETRY")
            if a.phase == Phase.MUTATED else
            replace(a, phase=Phase.REJECTED, reason="CRASH_BEFORE_MUTATION")
            if a.phase in (Phase.WAITING, Phase.FENCED) else a
            for a in self.attempts)
        model = replace(self, attempts=attempts, epoch=self.epoch + 1,
                        lease=None, fence_owner=None, external_busy=False,
                        last_now=self.last_now if error else now_s,
                        quarantined=True, replay_only=True)
        return model._reply("RESTARTED", error or "REPLAY_ONLY_NO_RETRY")
