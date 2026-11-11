#!/usr/bin/env python3
"""ATLAS FAST — restart protection scan, restart orchestration and STOP
coverage monitor (B6, OFFLINE CANDIDATE, NOT WIRED). Claude, 19.10.2026.

Gap closed: after a crash, startup_reconcile only blocks new entries and
restart_reconcile only settles FLAT scopes. Nothing checked whether an OPEN
position left behind is protected by its STOP. Here the protection check runs
FIRST, even when incidents exist, and every outcome is fail-closed:

restart_protection_scan(records, venue, guard_step, journal, alarm, ...)
  per unresolved WAL scope (detail.scope/symbol/client_id; ROLE from the WAL):
  * WAL symbols for one scope disagree           -> AMBIGUOUS_WAL (alarm)
  * no STOP record (STOP never sent):
      signed position 0                          -> FLAT_NO_STOP_SENT
      position open                              -> UNPROTECTED_NO_STOP_SENT
                                                    (alarm MANUAL_CLOSE_REQUIRED, side, qty)
      read failed                                -> READ_FAILED (alarm)
  * STOP record present -> the real STOP guard (guard_step) on the given venue
    (production: SignedGuardVenue + R2 channel DISABLED -> never sends).
protection_gate(...) (019): the run_once entry gate, BEFORE the startup gate:
  WAL readable + every scope FLAT/FLAT_NO_STOP_SENT + no alarm-emit failure.
restart_launch(...): load WAL -> scan -> startup gate -> policy restart; entries
  allowed only if WAL readable, every scope FLAT*, startup gate passes and the
  policy result is NO_PENDING_SCOPES/RECONCILED. Never sends, cancels or deletes.
CoverageMonitor: per tick runs the guard (R2 disabled) and reads the observer
  heartbeat; "continuous_stop_coverage_verified" is True only for a window in
  which EVERY tick was PROTECTED, no gap between ticks exceeded max_gap_ms and
  the observer heartbeat was always fresh. Anything else -> False + alarm. It
  is an evidence window, not a guarantee between reads.

037 (Issue #4): the monitor becomes the executor's supervision loop.
  * expected_stop (the planned STOP wire) is passed to the guard: only the
    planned STOP counts as PROTECTED (036 G-1);
  * minimum window while OPEN (036 G-3): verified needs >= min_ticks ticks and
    >= min_span ms (CANDIDATE 3 / 10 s); a window that ends signed-FLAT with no
    break is verified without the minimum (the open period was fully covered);
  * make_observer_status(): heartbeat adapter for the independent observer
    status file -> fresh only while it is MATCHED (sees the owner STOP) and its
    heartbeat_seq keeps ADVANCING with the same pid. Observer FLAT while our
    position is open is NOT a handover;
  * supervise(): ticks until the guard reports signed FLAT. It NEVER returns
    while a position is open (STOP lost, reads failing, quarantine: alarm and
    keep supervising; R2 stays disabled). max_ticks exists for tests only.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

# 019: only a signed-flat scope with no own exits left lets a NEW entry through.
# FLAT_EXITS_OPEN (flat, own STOP/TP still open) needs cleanup first -> blocks.
FLAT_STATES = ("FLAT", "FLAT_NO_STOP_SENT")
# 019: CONSERVATIVE CANDIDATE coverage thresholds for offline tests. NOT owner-
# approved production values (owner decision D: heartbeat source, alarm channel,
# gap and K). Smaller gap / fewer failures = earlier alarm, never more sending.
CANDIDATE_MAX_GAP_MS = 15_000
CANDIDATE_OBSERVER_MAX_AGE_MS = 60_000   # owner decision 23.10.2026 (15000/60000/3)
CANDIDATE_MAX_READ_FAILURES = 3
CANDIDATE_MIN_TICKS = 3              # 037 G-3 (CANDIDATE, not owner-approved)
CANDIDATE_MIN_SPAN_MS = 10_000       # 037 G-3 (CANDIDATE, not owner-approved)
SUPERVISE_INTERVAL_S = 5             # 037: < CANDIDATE_MAX_GAP_MS
POLICY_OK = ("NO_PENDING_SCOPES", "RECONCILED")
TRADE_ROLES = ("ENTRY", "STOP", "TP1", "TP2", "POLICY_RESERVE", "EMERGENCY",
               "CANCEL_STOP", "CANCEL_TP1", "CANCEL_TP2")


class RestartBlocked(RuntimeError):
    pass


class AlarmTap:
    """019: wraps the alarm callable. A raise or a "NOT_PERSISTED" result is an
    alarm-emit FAILURE; it is recorded (never re-raised into STOP logic) and
    voids entries / coverage evidence."""

    def __init__(self, alarm):
        self.alarm = alarm
        self.failures = []

    def __call__(self, code):
        try:
            r = self.alarm(code)
        except Exception as e:
            self.failures.append("ALARM_RAISED:%s" % type(e).__name__)
            return
        if r == "NOT_PERSISTED":
            self.failures.append("ALARM_NOT_PERSISTED")


def _healthy(alarm_healthy):
    if alarm_healthy is None:
        return True
    try:
        return alarm_healthy() is True
    except Exception:
        return False


def _position(venue, symbol):
    acct, _ = venue.read_account()
    rows = [r for r in (acct.get("positions") or []) if type(r) is dict and r.get("symbol") == symbol
            and r.get("positionSide") in ("BOTH", None)]
    if len(rows) != 1:
        raise RestartBlocked("POSITION_SHAPE")
    amt = Decimal(str(rows[0].get("positionAmt")))
    if not amt.is_finite():
        raise RestartBlocked("POSITION_SHAPE")
    return amt


def scopes_from_wal(records):
    """{scope: {"symbols": set, "stop_id": str|None, "tp_ids": [..], "roles": set}}
    for scopes without a resolved_<scope>.json. Unknown/odd rows raise."""
    resolved = {n[len("resolved_"):-len(".json")] for n in records if n.startswith("resolved_")}
    out = {}
    for name, row in records.items():
        if name.startswith(("resolved_", "incident_")) or not name.endswith(".json"):
            continue
        detail = row.get("detail") if type(row) is dict else None
        role = row.get("role") if type(row) is dict else None
        if type(detail) is not dict or type(detail.get("scope")) is not str:
            raise RestartBlocked("WAL_ROW_WITHOUT_SCOPE")
        scope = detail["scope"]
        if scope in resolved:
            continue
        s = out.setdefault(scope, {"symbols": set(), "stop_id": None, "tp_ids": [], "roles": set()})
        if type(role) is str:
            s["roles"].add(role)
        if type(detail.get("symbol")) is str:
            s["symbols"].add(detail["symbol"])
        if role == "STOP" and type(detail.get("client_id")) is str:
            s["stop_id"] = detail["client_id"]
        if role in ("TP1", "TP2") and type(detail.get("client_id")) is str:
            s["tp_ids"].append(detail["client_id"])
    return out


def restart_protection_scan(records, *, venue, guard_step, journal, alarm, sleep):
    out = {}
    for scope, s in sorted(scopes_from_wal(records).items()):
        if len(s["symbols"]) != 1:
            alarm("RESTART_WAL_SYMBOL_AMBIGUOUS:%s" % scope[:24])
            out[scope] = "AMBIGUOUS_WAL"
            continue
        symbol = next(iter(s["symbols"]))
        if s["stop_id"] is None:
            try:
                amt = _position(venue, symbol)
            except Exception:
                alarm("RESTART_POSITION_READ_FAILED:%s" % symbol)
                out[scope] = "READ_FAILED"
                continue
            if amt == 0:
                out[scope] = "FLAT_NO_STOP_SENT"
            else:
                side = "SELL" if amt > 0 else "BUY"
                alarm("STOP_NEVER_SENT_POSITION_OPEN_MANUAL_CLOSE_REQUIRED:%s:%s:%s"
                      % (symbol, side, format(abs(amt), "f")))
                out[scope] = "UNPROTECTED_NO_STOP_SENT"
            continue
        try:
            out[scope] = guard_step(venue, journal, scope=scope, symbol=symbol, stop_id=s["stop_id"],
                                    tp_ids=sorted(s["tp_ids"]), sleep=sleep, alarm=alarm)
        except Exception as e:
            alarm("RESTART_GUARD_FAILED:%s:%s" % (scope[:24], type(e).__name__))
            out[scope] = "GUARD_FAILED"
    return out


def protection_gate(*, load_wal, venue, guard_step, journal, alarm, sleep, alarm_healthy=None):
    """019: the entry-path gate (executor run_once, BEFORE the startup gate).
    entries_allowed only if: WAL readable, scan completed, every unresolved
    scope in FLAT_STATES, no alarm-emit failure and the alarm log healthy."""
    tap = AlarmTap(alarm)
    report = {"wal": None, "protection": {}, "alarm_failures": [], "entries_allowed": False}
    try:
        records = load_wal()
        report["wal"] = "READ"
    except Exception as e:
        tap("RESTART_WAL_UNREADABLE_OWNER_REVIEW:%s" % str(e)[:60])
        report["wal"] = "UNREADABLE"
    if report["wal"] == "READ":
        try:
            report["protection"] = restart_protection_scan(records, venue=venue, guard_step=guard_step,
                                                           journal=journal, alarm=tap, sleep=sleep)
        except Exception as e:
            tap("RESTART_PROTECTION_SCAN_FAILED:%s" % str(e)[:60])
            report["protection"] = {"_scan": "FAILED"}
    if not _healthy(alarm_healthy):
        tap.failures.append("ALARM_LOG_UNHEALTHY")
    report["alarm_failures"] = list(tap.failures)
    report["entries_allowed"] = (report["wal"] == "READ" and not tap.failures
                                 and all(v in FLAT_STATES for v in report["protection"].values()))
    return report


def restart_launch(*, load_wal, venue, guard_step, journal, alarm, sleep, startup_reconcile,
                   restart_reconcile, alarm_healthy=None):
    """Owner-run restart orchestration (offline candidate). Order is fixed:
    WAL -> protection scan -> startup gate -> policy restart."""
    report = {"wal": None, "protection": {}, "alarm_failures": [], "startup_gate": None, "policy": None,
              "entries_allowed": False}
    g = protection_gate(load_wal=load_wal, venue=venue, guard_step=guard_step, journal=journal, alarm=alarm,
                        sleep=sleep, alarm_healthy=alarm_healthy)
    report.update(wal=g["wal"], protection=g["protection"], alarm_failures=g["alarm_failures"])
    if g["wal"] != "READ" or "_scan" in g["protection"]:
        return report
    try:
        startup_reconcile()
        report["startup_gate"] = "PASS"
    except Exception as e:
        report["startup_gate"] = "BLOCKED:" + str(e)[:80]
    try:
        pol = restart_reconcile()
        report["policy"] = pol.get("status") if type(pol) is dict else "INVALID"
    except Exception as e:
        report["policy"] = "FAILED:" + str(e)[:80]
    report["entries_allowed"] = (g["entries_allowed"] is True and report["startup_gate"] == "PASS"
                                 and report["policy"] in POLICY_OK)
    return report


class CoverageMonitor:
    """Evidence window for STOP coverage while a position is open. Never sends."""

    def __init__(self, *, venue, guard_step, journal, alarm, scope, symbol, stop_id, tp_ids,
                 observer_status, max_gap_ms=CANDIDATE_MAX_GAP_MS,
                 observer_max_age_ms=CANDIDATE_OBSERVER_MAX_AGE_MS,
                 max_read_failures=CANDIDATE_MAX_READ_FAILURES, alarm_healthy=None, expected_stop=None,
                 min_ticks=CANDIDATE_MIN_TICKS, min_span_ms=CANDIDATE_MIN_SPAN_MS, flat_probe=None):
        self.v, self.g, self.j = venue, guard_step, journal
        self.alarm = AlarmTap(alarm)
        self.alarm_healthy = alarm_healthy
        self.scope, self.symbol, self.stop_id, self.tp_ids = scope, symbol, stop_id, list(tp_ids)
        self.observer_status = observer_status
        self.max_gap, self.obs_age, self.max_fail = max_gap_ms, observer_max_age_ms, max_read_failures
        self.expected_stop = expected_stop
        self.min_ticks, self.min_span = min_ticks, min_span_ms
        # 038 DRAFT: after an R2 close the guard answers CLOSED_VERIFIED / ALREADY_HANDLED
        # forever (resolve-only path); a fresh signed read decides whether we are FLAT.
        self.flat_probe = flat_probe
        self.ticks = 0
        self.window_start = None
        self.last_tick = None
        self.failures = 0
        self.broken = []                 # reasons that void the current window
        self.state = "UNSTARTED"

    def _observer_fresh(self, now):
        try:
            st = self.observer_status()
            t = st.get("generated_at_ms")
            # B8 (02.11.2026): the observer is read AFTER the guard step, so its
            # advance time can be later than the tick-start clock -> negative age
            # -> false OBSERVER_STALE on almost every tick. Age is measured against
            # a fresh clock read taken after the observer read.
            now = self.v.now_ms()
            return type(t) is int and 0 <= now - t <= self.obs_age and st.get("status") not in (None, "", "DEAD")
        except Exception:
            return False

    def tick(self):
        now = self.v.now_ms()
        if self.window_start is None:
            self.window_start = now
        if self.last_tick is not None and now - self.last_tick > self.max_gap:
            self.broken.append("GAP")
            # 037: stable code (no value) so SafeAlarm repeat suppression works
            self.alarm("COVERAGE_GAP_MS_EXCEEDED")
        self.last_tick = now
        self.ticks += 1
        kw = {} if self.expected_stop is None else {"expected_stop": self.expected_stop}
        try:
            out = self.g(self.v, self.j, scope=self.scope, symbol=self.symbol, stop_id=self.stop_id,
                         tp_ids=self.tp_ids, sleep=self.v.sleep, alarm=self.alarm, **kw)
        except Exception as e:
            out = "GUARD_FAILED:" + type(e).__name__
        if out in ("CLOSED_VERIFIED", "ALREADY_HANDLED", "QUARANTINE"):
            # 043: after an R2 attempt the guard is resolve-only (E record): an unverified send
            # answers QUARANTINE forever even once the owner / exchange made the account flat.
            if out != "QUARANTINE":
                self.broken.append("R2_EMERGENCY_CLOSE:" + out)      # never a "covered" window
            try:
                if self.flat_probe is not None and self.flat_probe() is True:
                    self.broken.append("FLAT_BY_PROBE_AFTER:" + out)
                    out = "FLAT"
            except Exception:
                pass
        if out in ("FLAT", "FLAT_EXITS_OPEN"):
            self.state = "FLAT"
            return self.report()
        if out == "PROTECTED":
            self.failures = 0
        else:
            self.failures += 1
            self.broken.append(str(out))
            if self.failures >= self.max_fail:
                # 037: stable code (no per-tick counter): no alarm / e-mail on every tick
                self.alarm("COVERAGE_LOST_READS_OR_STOP:%s" % (out,))
        if not self._observer_fresh(now):
            # B8-2 (02.11.2026): the independent observer polls every ~12 s, so right after
            # the STOP is placed it cannot have seen it yet. Within the first observer max-age
            # window (60 s) the STOP is covered by this tick's signed guard read-back, so a
            # not-yet-matching observer is a warm-up, not a coverage gap. After that window
            # a stale/dead observer breaks the window exactly as before.
            # Only tolerated when THIS tick's signed guard read saw the STOP (out == "PROTECTED"):
            # no blind window; anything else breaks the window immediately (fail-closed).
            if (out == "PROTECTED" and self.window_start is not None
                    and now - self.window_start < self.obs_age):
                self.warmup_misses = getattr(self, "warmup_misses", 0) + 1
            else:
                self.broken.append("OBSERVER_STALE")
                self.alarm("COVERAGE_OBSERVER_STALE")
        self.state = "PROTECTED" if out == "PROTECTED" else str(out)
        return self.report()

    def _alarm_check(self):
        if not _healthy(self.alarm_healthy) and "ALARM_LOG_UNHEALTHY" not in self.alarm.failures:
            self.alarm.failures.append("ALARM_LOG_UNHEALTHY")
        for f in self.alarm.failures:
            if "ALARM_EMIT_FAILED:" + f not in self.broken:
                self.broken.append("ALARM_EMIT_FAILED:" + f)

    def report(self):
        self._alarm_check()
        verified = (self.state in ("PROTECTED", "FLAT") and not self.broken and self.window_start is not None)
        if verified and self.state == "PROTECTED":
            span = (self.last_tick or 0) - self.window_start
            verified = self.ticks >= self.min_ticks and span >= self.min_span
        return {"state": self.state, "window_start_ms": self.window_start, "last_tick_ms": self.last_tick,
                "ticks": self.ticks,
                "max_gap_ms": self.max_gap, "broken": list(self.broken),
                "continuous_stop_coverage_verified": verified, "orders_sent": False}


def make_observer_status(read_status, check_row, *, now_s, max_age_s, expected_sha=None):
    """037: CoverageMonitor heartbeat source over the independent observer's
    status file (observer_liveness row format). Returns a zero-argument callable
    -> {"status": "OK"|"DEAD", "generated_at_ms": int|None, "reason": str}.
    OK only if check_row() passes with the MATCHED requirement (the observer
    sees the owner STOP; a FLAT observer while our position is open fails) and
    the heartbeat keeps advancing; generated_at_ms is the local time of the
    LAST observed advance, so a stuck heartbeat ages out in the monitor.
    A pid change or a heartbeat going backwards is DEAD. Never raises."""
    seen = {"seq": None, "pid": None, "advanced_at": None}

    def dead(reason):
        return {"status": "DEAD", "generated_at_ms": None, "reason": reason}

    def status():
        try:
            row = read_status()
            now = now_s()
            err = check_row(row, now, max_age_s, expected_sha)
            if err:
                return dead(str(err))
            seq, pid = row["heartbeat_seq"], row["pid"]
            if seen["pid"] is not None and pid != seen["pid"]:
                seen.update(seq=None, pid=None, advanced_at=None)
                return dead("OBSERVER_PID_CHANGED")
            if seen["seq"] is not None and seq < seen["seq"]:
                return dead("HEARTBEAT_WENT_BACKWARDS")
            if seen["seq"] is None or seq > seen["seq"]:
                seen.update(seq=seq, pid=pid, advanced_at=now)
            return {"status": "OK", "generated_at_ms": int(seen["advanced_at"] * 1000), "reason": "MATCHED"}
        except Exception as e:
            return dead("OBSERVER_STATUS_ERROR:" + type(e).__name__)
    return status


def supervise(monitor, *, sleep, interval_s=SUPERVISE_INTERVAL_S, max_ticks=None):
    """037: run the coverage monitor until the guard reports signed FLAT.
    Never returns while a position is open; production passes max_ticks=None.
    A tick that raises is recorded (window void) and alarmed; supervision goes on."""
    n = 0
    while True:
        rep = None
        try:
            rep = monitor.tick()
        except Exception as e:
            monitor.broken.append("TICK_FAILED:" + type(e).__name__)
            monitor.alarm("COVERAGE_TICK_FAILED:" + type(e).__name__)
        n += 1
        if type(rep) is dict and rep.get("state") == "FLAT":
            return rep
        if max_ticks is not None and n >= max_ticks:
            return dict(monitor.report(), supervision="STOPPED_BY_MAX_TICKS_TEST_ONLY")
        sleep(interval_s)
