#!/usr/bin/env python3
"""ATLAS FAST: STOP-ambiguity guard and bounded emergency close (CANDIDATE).

Claude, 18.10.2026, ATLAS-FAST-005. Offline review candidate: no network, no
SDK, no API key, no owner-private path. NOT wired into any executor (the GPT
fenced R8 executor is not on GitHub). Design note:
reviews/CLAUDE_STOP_AMBIGUITY_GUARD_20261018.md

Owner design decision (18.10.2026, DESIGN ONLY, relayed via Issue #1):
  * STOP state uncertain        -> block every new ENTRY.
  * unprotected position PROVEN by fresh signed reads -> a bounded automatic
    emergency close may run.
  * cannot prove it (stale data, network down, read error, inconsistent
    answers, unknown order result) -> NO close; quarantine + alarm +
    re-reconcile. Never report a close as successful without proof.

Classification of one evidence snapshot (classify):
  FLAT                  signed position 0 and no own exit orders open
  FLAT_EXITS_OPEN       signed position 0, own STOP/TP still open (cleanup)
  PROTECTED             own closePosition STOP_MARKET open, NEW, on the
                        protective side of a fresh mark
  UNPROTECTED_PROVEN    every condition in _unprotected_reasons() holds
  AMBIGUOUS             anything else (the default)

Bounded close (guard_step):
  1. UNPROTECTED_PROVEN in CONFIRMATIONS consecutive snapshots taken at
     least CONFIRM_GAP_MS apart, identical position size and sign;
  2. under the shared emergency flock: fresh snapshot, still PROVEN, same size;
  3. O_EXCL journal record BEFORE the write (crash -> never replayed);
  4. exactly ONE reduce-only MARKET for exactly |position| on the opposite
     side, deterministic clientOrderId; at most MAX_AUTO_CLOSES per scope;
  5. result is resolved only by signed reads of that clientOrderId and the
     account; FILLED + signed FLAT -> CLOSED_VERIFIED, anything else ->
     QUARANTINE (no second order, owner review).
reduceOnly makes a stale-quantity or duplicate order unable to reverse the
position; the journal + flock + one-attempt cap make a second order unlikely
in the first place.

037 (Issue #4, closes 036 G-1 / G-2):
  * planned-STOP binding: classify(ev, expected) / guard_step(..., expected_stop=)
    accept PROTECTED only for the PLANNED STOP wire: same clientAlgoId, symbol,
    side, triggerPrice (Decimal equal), workingType == CONTRACT_PRICE and
    positionSide == BOTH on both the plan and the exchange row. Any difference
    -> AMBIGUOUS "OWN_STOP_NOT_PLANNED:<field>". expected=None keeps the
    pre-037 behaviour (restart scan; that path never allows an entry while a
    position is open anyway).
  * bounded emergency-lock wait: Journal.lock() waits at most lock_timeout_s
    (EMERGENCY_LOCK_WAIT_S = 30 s CANDIDATE) and then raises
    EMERGENCY_LOCK_BUSY; guard_step turns that into AMBIGUOUS + alarm (no send).
"""
from __future__ import annotations
import fcntl, hashlib, json, os, stat, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

CONFIRMATIONS = 2
CONFIRM_GAP_MS = 3_000
EVIDENCE_MAX_AGE_MS = 5_000      # every read must be at most this old
EVIDENCE_MAX_SPREAD_MS = 3_000   # all reads of one snapshot within this span
MAX_AUTO_CLOSES = 1              # per scope; structural: any E record -> resolve-only path
RESOLVE_POLLS = 5
ACTIVE_ALGO = ("NEW",)
TRANSIENT_ALGO = ("TRIGGERING", "TRIGGERED")
TERMINAL_ALGO = ("CANCELED", "EXPIRED", "REJECTED", "FINISHED")
EMERGENCY_LOCK_WAIT_S = 30      # 037 CANDIDATE: bounded wait for the shared emergency flock
PLANNED_WORKING_TYPE = "CONTRACT_PRICE"   # owner default D3 (not changed here)
BLOCKING_STATES = ("AMBIGUOUS", "UNPROTECTED_PROVEN", "EMERGENCY_SENT",
                   "EMERGENCY_UNKNOWN", "QUARANTINE")


class GuardBlocked(RuntimeError):
    pass


class OrderNotFound(Exception):
    """Venue adapters raise this ONLY for the exchange's explicit
    'order does not exist' answer (-2013). Timeouts/other errors must not."""


def _dec(v):
    if isinstance(v, (bool, float)) or not isinstance(v, (str, int, Decimal)):
        return None
    try:
        x = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return x if x.is_finite() else None


# ---- evidence ----------------------------------------------------------------

def gather(venue, *, symbol, stop_id, tp_ids):
    """One snapshot of signed reads. Never raises: failures become fields."""
    ev = {"symbol": symbol, "stop_id": stop_id, "tp_ids": list(tp_ids), "errors": []}
    for key, fn in (("account", lambda: venue.read_account()),
                    ("open_algo", lambda: venue.read_open_algo_orders(symbol)),
                    ("open_orders", lambda: venue.read_open_orders(symbol)),
                    ("mark", lambda: venue.read_mark(symbol))):
        try:
            ev[key] = fn()
        except Exception as e:
            code = str(e) if str(e) in ("SIGNED_GET_HTTP_429","SIGNED_GET_HTTP_418") else type(e).__name__
            ev["errors"].append("%s:%s" % (key,code))
    try:
        ev["own_stop"] = ("FOUND", venue.query_algo(stop_id))
    except OrderNotFound:
        ev["own_stop"] = ("NOT_FOUND", None)
    except Exception as e:
        ev["errors"].append("own_stop:%s" % type(e).__name__)
    try:
        ev["now_ms"] = venue.now_ms()
    except Exception as e:
        ev["errors"].append("clock:%s" % type(e).__name__)
    return ev


def _fresh(ev):
    """All four reads present, each (payload, received_at_ms), fresh and close."""
    times = []
    for key in ("account", "open_algo", "open_orders", "mark"):
        item = ev.get(key)
        if type(item) is not tuple or len(item) != 2 or type(item[1]) is not int:
            return False
        times.append(item[1])
    now = ev.get("now_ms")
    if type(now) is not int:
        return False
    return (all(0 <= now - t <= EVIDENCE_MAX_AGE_MS for t in times)
            and max(times) - min(times) <= EVIDENCE_MAX_SPREAD_MS)


def position_of(ev):
    acct = ev["account"][0]
    if type(acct) is not dict or type(acct.get("positions")) is not list:
        return None
    rows = [r for r in acct["positions"] if type(r) is dict and r.get("symbol") == ev["symbol"]]
    # 01.11.2026 (yurutucu 055 ile ayni): GET /fapi/v3/account yalniz pozisyonu ya da acik emri olan
    # sembolleri listeler; sembol satiri yoksa imzali DUZ (0). Birden fazla satir / hedge -> None (degismedi).
    if not rows:
        return Decimal("0")
    if len(rows) != 1 or rows[0].get("positionSide") not in ("BOTH", None):
        return None
    return _dec(rows[0].get("positionAmt"))


def _protective(stop, size, mark):
    side_ok = stop.get("side") == ("SELL" if size > 0 else "BUY")
    trig = _dec(stop.get("triggerPrice"))
    if not side_ok or trig is None or mark is None:
        return False
    return trig < mark if size > 0 else trig > mark


def plan_mismatch(stop, expected):
    """037 (G-1): None if `stop` (an exchange algo row) is exactly the planned
    STOP wire `expected`; otherwise the first differing field. An invalid plan
    is itself a mismatch (fail closed)."""
    if type(expected) is not dict:
        return "PLAN_INVALID"
    if (expected.get("workingType") != PLANNED_WORKING_TYPE
            or expected.get("positionSide", "BOTH") != "BOTH"
            or type(expected.get("clientAlgoId")) is not str
            or _dec(expected.get("triggerPrice")) is None):
        return "PLAN_INVALID"
    for key in ("clientAlgoId", "symbol", "side"):
        if stop.get(key) != expected.get(key):
            return key
    if stop.get("positionSide") != "BOTH":
        return "positionSide"
    if stop.get("workingType") != PLANNED_WORKING_TYPE:
        return "workingType"
    trig = _dec(stop.get("triggerPrice"))
    if trig is None or trig != _dec(expected.get("triggerPrice")):
        return "triggerPrice"
    return None


def classify(ev, expected=None):
    """-> (state, reason). AMBIGUOUS whenever anything is missing or odd.
    expected: the planned STOP wire (037 binding) or None (pre-037 behaviour)."""
    if ev.get("errors"):
        return "AMBIGUOUS", "READ_FAILED:" + ",".join(ev["errors"])
    if not _fresh(ev):
        return "AMBIGUOUS", "EVIDENCE_STALE_OR_INCOMPLETE"
    size = position_of(ev)
    algo, orders = ev["open_algo"][0], ev["open_orders"][0]
    mark_doc = ev["mark"][0]
    if size is None or type(algo) is not list or type(orders) is not list:
        return "AMBIGUOUS", "EVIDENCE_SHAPE_INVALID"
    mark = _dec(mark_doc.get("markPrice")) if type(mark_doc) is dict else None
    sym = ev["symbol"]
    sym_algo = [a for a in algo if type(a) is dict and a.get("symbol") == sym]
    own_open = [a for a in sym_algo if a.get("clientAlgoId") == ev["stop_id"]]
    foreign_algo = [a for a in sym_algo if a.get("clientAlgoId") != ev["stop_id"]]
    sym_orders = [o for o in orders if type(o) is dict and o.get("symbol") == sym]
    foreign_orders = [o for o in sym_orders if o.get("clientOrderId") not in ev["tp_ids"]]
    found, own = ev["own_stop"]
    if foreign_algo or foreign_orders:
        return "AMBIGUOUS", "FOREIGN_ORDERS_ON_SYMBOL"
    if len(own_open) > 1:
        return "AMBIGUOUS", "DUPLICATE_OWN_STOP"
    if size == 0:
        if own_open or sym_orders:
            return "FLAT_EXITS_OPEN", "SIGNED_FLAT_OWN_EXITS_NEED_CLEANUP"
        return "FLAT", "SIGNED_FLAT"
    if own_open:
        st = own_open[0]
        if (found != "FOUND" or type(own) is not dict
                or own.get("algoStatus") != st.get("algoStatus")):
            return "AMBIGUOUS", "OWN_STOP_READS_DISAGREE"
        if st.get("algoStatus") in TRANSIENT_ALGO:
            return "AMBIGUOUS", "OWN_STOP_TRIGGERING"
        if (st.get("algoStatus") in ACTIVE_ALGO and str(st.get("closePosition")).lower() == "true"
                and st.get("orderType", st.get("type")) == "STOP_MARKET"
                and _protective(st, size, mark)):
            if expected is not None:
                why = plan_mismatch(st, expected) or plan_mismatch(own, expected)
                if why:
                    return "AMBIGUOUS", "OWN_STOP_NOT_PLANNED:" + why
            return "PROTECTED", "OWN_STOP_ACTIVE_PROTECTIVE"
        return "AMBIGUOUS", "OWN_STOP_PRESENT_BUT_NOT_PROTECTIVE_OR_UNKNOWN"
    reasons = _unprotected_reasons(found, own)
    if reasons:
        return "AMBIGUOUS", reasons
    return "UNPROTECTED_PROVEN", "POSITION_OPEN_NO_STOP_DEFINITIVE"


def _unprotected_reasons(found, own):
    """Own STOP absent from the open list AND its exact query is definitive."""
    if found == "NOT_FOUND":
        return ""
    if found == "FOUND" and type(own) is dict:
        status = own.get("algoStatus")
        if status in TERMINAL_ALGO:
            return ""
        if status in TRANSIENT_ALGO:
            return "OWN_STOP_TRIGGERING"
        return "OWN_STOP_STATUS_%s_BUT_NOT_IN_OPEN_LIST" % str(status)[:20]
    return "OWN_STOP_QUERY_INCONCLUSIVE"


# ---- durable state ---------------------------------------------------------

class Journal:
    """O_EXCL one-file-per-record journal + shared emergency flock."""

    def __init__(self, root, uid=None, lock_path=None, lock_timeout_s=EMERGENCY_LOCK_WAIT_S):
        """lock_path: the executor's EXISTING emergency lock (fenced R8 uses
        PRIVATE/owner_emergency_intervention.lock) so that the guard and the
        executor's own emergency path are serialised by ONE flock. Default
        (None) = <root>/emergency.lock, only for isolated tests."""
        self.root = Path(root)
        self.uid = os.geteuid() if uid is None else uid
        self.lock_path = Path(lock_path) if lock_path is not None else None
        self.lock_timeout_s = lock_timeout_s

    def _check(self):
        st = os.lstat(self.root)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != self.uid or st.st_mode & 0o077:
            raise GuardBlocked("GUARD_JOURNAL_UNTRUSTED")

    def lock(self):
        self._check()
        path = self.root / "emergency.lock"
        if self.lock_path is not None:
            if not self.lock_path.is_absolute():
                raise GuardBlocked("GUARD_LOCK_PATH_NOT_ABSOLUTE")
            pst = os.lstat(self.lock_path.parent)
            if not stat.S_ISDIR(pst.st_mode) or pst.st_uid != self.uid or pst.st_mode & 0o077:
                raise GuardBlocked("GUARD_LOCK_DIR_UNTRUSTED")
            path = self.lock_path
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != self.uid or st.st_mode & 0o077:
            os.close(fd)
            raise GuardBlocked("GUARD_LOCK_FILE_UNTRUSTED")
        # 037 (G-2): bounded wait; a hung holder must not silence the guard.
        deadline = time.monotonic() + self.lock_timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise GuardBlocked("EMERGENCY_LOCK_BUSY") from None
                time.sleep(0.05)

    @staticmethod
    def unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def write_once(self, name, body):
        self._check()
        if not name.replace("_", "").isalnum():
            raise GuardBlocked("GUARD_RECORD_NAME_INVALID")
        path = self.root / (name + ".json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise GuardBlocked("GUARD_RECORD_ALREADY_EXISTS:" + name) from None
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(body, sort_keys=True).encode()); f.flush(); os.fsync(f.fileno())
        d = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)

    def records(self, prefix):
        self._check()
        out = {}
        for p in sorted(self.root.iterdir()):
            if p.name.startswith(prefix) and p.suffix == ".json":
                try:
                    out[p.stem] = json.loads(p.read_bytes())
                except Exception:
                    raise GuardBlocked("GUARD_RECORD_UNREADABLE:" + p.name) from None
        return out


def _rec(kind, scope, n=""):
    return "%s_%s%s" % (kind, hashlib.sha256(scope.encode()).hexdigest()[:24], n)


def close_client_id(scope, attempt):
    return "AGC" + hashlib.sha256((scope + ":" + str(attempt)).encode()).hexdigest()[:24].upper()


def entry_allowed(journal):
    """New ENTRY only if no scope has an uncleared guard record: suspicious
    (S), emergency attempt (E, even if verified) or quarantine (Q). Only an
    explicit owner_clear (C) after signed FLAT releases a scope."""
    recs = journal.records("")
    flagged = {k[2:26] for k in recs if k[:2] in ("S_", "E_", "Q_")}
    cleared = {k[2:26] for k in recs if k[:2] == "C_"}
    pending = sorted(flagged - cleared)
    return (not pending), pending


def owner_clear(journal, scope, *, reason, flat_snapshot):
    """Explicit owner act after review: requires a signed FLAT snapshot."""
    if type(reason) is not str or not 8 <= len(reason) <= 200:
        raise GuardBlocked("OWNER_CLEAR_REASON_REQUIRED")
    state, why = classify(flat_snapshot)
    if state != "FLAT":
        raise GuardBlocked("OWNER_CLEAR_REQUIRES_SIGNED_FLAT:" + why)
    journal.write_once(_rec("C", scope), {"scope": scope, "reason": reason,
                                          "at_ms": flat_snapshot["now_ms"]})


# ---- one guard decision step ---------------------------------------------

def guard_step(venue, journal, *, scope, symbol, stop_id, tp_ids, sleep, alarm, expected_stop=None):
    """Returns one of FLAT / PROTECTED / AMBIGUOUS / CLOSED_VERIFIED /
    QUARANTINE / ALREADY_HANDLED. Sends at most one order per scope."""
    def snap():
        return gather(venue, symbol=symbol, stop_id=stop_id, tp_ids=tp_ids)

    def suspicious(reason):
        try:
            journal.write_once(_rec("S", scope), {"scope": scope, "reason": reason})
        except GuardBlocked:
            pass                                   # already marked
        alarm("STOP_AMBIGUOUS:" + reason)
        return "AMBIGUOUS"

    def lock_or_none():
        try:
            return journal.lock()
        except GuardBlocked as e:
            if str(e) == "EMERGENCY_LOCK_BUSY":
                return None
            raise

    if journal.records(_rec("E", scope)):
        # Resolve UNDER the lock: another guard may still be inside its close
        # (E written, POST/resolution in progress). Resolving without the lock
        # raced it and produced a false QUARANTINE (found 19.10, now tested).
        fd = lock_or_none()
        if fd is None:
            return suspicious("EMERGENCY_LOCK_BUSY")
        try:
            return _resolve_existing(venue, journal, scope=scope, symbol=symbol, stop_id=stop_id,
                                     tp_ids=tp_ids, sleep=sleep, alarm=alarm)
        finally:
            journal.unlock(fd)
    sizes = []
    for i in range(CONFIRMATIONS):
        if i:
            sleep(CONFIRM_GAP_MS / 1000)
        ev = snap()
        state, why = classify(ev, expected_stop)
        if state in ("FLAT", "FLAT_EXITS_OPEN", "PROTECTED"):
            return state
        if state != "UNPROTECTED_PROVEN":
            return suspicious(why)
        sizes.append(position_of(ev))
    if len(set(sizes)) != 1:
        return suspicious("POSITION_CHANGED_BETWEEN_CONFIRMATIONS")
    fd = lock_or_none()
    if fd is None:
        return suspicious("EMERGENCY_LOCK_BUSY")
    try:
        if journal.records(_rec("E", scope)):
            return "ALREADY_HANDLED"               # the other guard acted first
        ev = snap()
        state, why = classify(ev, expected_stop)
        if state in ("FLAT", "FLAT_EXITS_OPEN", "PROTECTED"):
            return state
        if state != "UNPROTECTED_PROVEN":
            return suspicious(why)
        size = position_of(ev)
        if size != sizes[0]:
            return suspicious("POSITION_CHANGED_BEFORE_CLOSE")
        if getattr(venue, "emergency_post_enabled", True) is False:
            # V3COMPAT-010: the emergency channel (R2) is closed. Do not journal or
            # announce a send that cannot happen; tell the owner to close by hand.
            side = "SELL" if size > 0 else "BUY"
            alarm("STOP_MISSING_MANUAL_CLOSE_REQUIRED:%s:%s:%s" % (symbol, side, format(abs(size), "f")))
            return _quarantine(journal, scope, "UNPROTECTED_R2_DISABLED_MANUAL_CLOSE_REQUIRED", alarm)
        attempts = len(journal.records(_rec("E", scope)))
        if attempts >= MAX_AUTO_CLOSES:
            return _quarantine(journal, scope, "AUTO_CLOSE_LIMIT_REACHED", alarm)
        cid = close_client_id(scope, attempts + 1)
        side = "SELL" if size > 0 else "BUY"
        qty = format(abs(size), "f")
        journal.write_once(_rec("E", scope, "_%d" % (attempts + 1)),
                           {"scope": scope, "symbol": symbol, "client_id": cid, "side": side,
                            "quantity": qty, "position_before": str(size), "at_ms": ev["now_ms"]})
        alarm("EMERGENCY_CLOSE_SENDING:" + cid)
        try:
            venue.post_reduce_only_market(symbol=symbol, side=side, quantity=qty,
                                          client_order_id=cid)
        except Exception:
            pass                                   # result decided by signed reads only
        return _resolve(venue, journal, scope=scope, symbol=symbol, cid=cid,
                        before=size, sleep=sleep, alarm=alarm)
    finally:
        journal.unlock(fd)


def _resolve(venue, journal, *, scope, symbol, cid, before, sleep, alarm):
    order = None
    for i in range(RESOLVE_POLLS):
        if i:
            sleep(1)
        try:
            order = venue.query_order(symbol=symbol, client_order_id=cid)
        except OrderNotFound:
            order = None
            continue
        except Exception:
            order = None
            continue
        if type(order) is dict and order.get("status") in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
            break
    if type(order) is not dict or order.get("clientOrderId") != cid or order.get("status") != "FILLED":
        return _quarantine(journal, scope, "EMERGENCY_RESULT_NOT_VERIFIED", alarm)
    try:
        acct, _ = venue.read_account()
    except Exception:
        return _quarantine(journal, scope, "EMERGENCY_FILLED_BUT_FLAT_NOT_VERIFIED", alarm)
    after = position_of({"account": (acct, 0), "symbol": symbol})
    if after is None:
        return _quarantine(journal, scope, "EMERGENCY_FILLED_BUT_FLAT_NOT_VERIFIED", alarm)
    if after != 0:
        flipped = (after > 0) != (before > 0)
        return _quarantine(journal, scope, "POSITION_FLIPPED" if flipped
                           else "EMERGENCY_PARTIAL_POSITION_REMAINS", alarm)
    journal.write_once(_rec("V", scope), {"scope": scope, "client_id": cid})
    alarm("EMERGENCY_CLOSE_VERIFIED:" + cid)
    return "CLOSED_VERIFIED"


def _resolve_existing(venue, journal, *, scope, symbol, stop_id, tp_ids, sleep, alarm):
    """Restart / second guard: never send again, only resolve by reads."""
    if journal.records(_rec("V", scope)):
        return "ALREADY_HANDLED"
    if journal.records(_rec("Q", scope)):
        return "QUARANTINE"
    recs = journal.records(_rec("E", scope))
    last = recs[sorted(recs)[-1]]
    return _resolve(venue, journal, scope=scope, symbol=symbol, cid=last["client_id"],
                    before=Decimal(last["position_before"]), sleep=sleep, alarm=alarm)


def _quarantine(journal, scope, reason, alarm):
    try:
        journal.write_once(_rec("Q", scope), {"scope": scope, "reason": reason})
    except GuardBlocked:
        pass
    alarm("QUARANTINE:" + reason)
    return "QUARANTINE"
