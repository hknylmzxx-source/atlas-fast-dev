#!/usr/bin/env python3
"""ATLAS owner-only, continuous signal -> existing exact-owner executor handoff.

Owner-native trade approval, Binance signed preflight, exchange STOP readback,
and existing one-time journals are kept in owner_single_execution.py.
This watcher does not itself create a Binance client, sign or send an order.
A DRY_RUN strategy signal alone is NEVER owner consent to trade.
"""
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PRIVATE = Path("/Users/hakanyilmaz/Library/Application Support/AtlasFastOwnerSDK")
BASE = Path("/Users/Shared/Atlas/FAST_SIGNAL_LIVE_PILOT")
EXECUTOR = PRIVATE / "owner_single_execution.py"
EXE_SHA = "2f4727cb3240618e87a141728e6d89f0645049d2fa56cc9b09a8bd71f5a63a2f"
PYTHON = PRIVATE / ".venv/bin/python"
ADAPTERS = PRIVATE / "owner_signal_adapter_pinned"
JOURNAL = PRIVATE / "continuous_owner_handoff_journal"
REPORT = BASE / "fast_owner_continuous_handoff_status.json"
SRC_FILES = {
    "fast_binance_active_owner_bridge.py":"150aa24ec97be87df5a4c171bbffc284f5d4913c96f7cabc9022a5cf25780b43",
    "fast_binance_direct_paper_bridge.py":"d4404c70e119d598e66748d1aa86e2e6e034157d111cbff6a93d657f64620236",
    "fast_binance_direct_exit_blueprint.py":"6872f5a51459732194c022653fcdda09267c66de664093edf1bffc52dc824458",
    "fast_direct_binance_exit_sequencer_mock.py":"2b2eef19c38ae2232df6a1cac0775cfc637a7134145f384a1270cb05f7235c2c",
    "fast_binance_direct_exit_attestation.py":"3f303bfdc1d4ac9bc7e7d0d4fa3d80abfc3e9ac5cec0f85f06f3883d39f9e9b9",
    "fast_owner_contract_client_STAGED.py":"73e78e499df06420758fdeb13954636de301599167da466e1ad8256708095fc9"
}
def check_owner():
    if (os.geteuid()!=501 or pwd.getpwuid(os.geteuid()).pw_name!="hakanyilmaz"
        or Path.home()!=Path("/Users/hakanyilmaz")
        or sys.version_info[:2]!=(3,13)):
        raise RuntimeError("OWNER_UID501_PY313_ONLY")
    for p in (PRIVATE,ADAPTERS,JOURNAL):
        s=p.stat()
        if p.is_symlink() or not p.is_dir() or s.st_uid!=501 or s.st_mode & 0o077:
            raise RuntimeError("OWNER_PRIVATE_DIR_UNTRUSTED")
    if (EXECUTOR.is_symlink() or
        hashlib.sha256(EXECUTOR.read_bytes()).hexdigest()!=EXE_SHA):
        raise RuntimeError("EXECUTOR_PIN_MISMATCH")
    for name,sha in SRC_FILES.items():
        p=ADAPTERS/name
        if (p.is_symlink() or p.stat().st_uid!=501
            or p.stat().st_mode & 0o077
            or hashlib.sha256(p.read_bytes()).hexdigest()!=sha):
            raise RuntimeError("OWNER_ADAPTER_PIN_MISMATCH")

def read_doc(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
        raise RuntimeError("UNTRUSTED_SIGNAL_SOURCE")
    obj=json.loads(path.read_text())
    if type(obj) is not dict:raise RuntimeError("INVALID_SIGNAL_DOCUMENT")
    return obj

def fresh_signal(state, rotation):
    from fast_binance_direct_paper_bridge import candidate
    now=datetime.now(timezone.utc)
    try:
        at=datetime.fromisoformat(state["generated_at"])
        if at.tzinfo is None or not 0<=(now-at).total_seconds()<=15:
            return None
        if (state.get("blockers")!=[]
            or state.get("status")!="READY_DRY_RUN_PLAN"
            # Match the signed-source paper candidate's 40s horizon.
            # The candidate() verifier and a second post-GET read both
            # recheck the actual source timestamp before any owner prompt.
            or state.get("signal_age_seconds",999)>40
            or state.get("orders_sent") is not False
            or state.get("live_enable") is not False
            or state.get("account",{}).get("can_trade") is not True):
            return None
        if (Decimal(str(state.get("effective_min_score",0)))<68
            or Decimal(str(state.get("effective_min_net_rr",0)))<Decimal("1.5")):
            return None
        plan=state["plan"]
        if (Decimal(str(plan.get("score",0)))<68
            or Decimal(str(plan.get("estimated_net_rr",0)))<Decimal("1.5")
            or Decimal(str(state["account"]["available_balance_usdt"]))<95
            or Decimal(str(plan["margin_usdt"]))>200):
            return None
        return candidate(state,rotation,now=now)
    except (KeyError,TypeError,ValueError,ArithmeticError):
        return None

def safe_report(status, **fields):
    obj={"schema":"ATLAS_OWNER_CONTINUOUS_SIGNAL_HANDOFF_V1",
         "generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "status":status,"owner_uid":501,
         "actual_executor_installed":True,"owner_native_trade_click_required":False,
         "active_phone_consent_route":True,
         "active_phone_route_mac_dialog_invoked":False,
         "legacy_executor_native_click_still_in_source":False,
         "phone_live_order_release_enabled":_live_open(),
         "live_pilot_symbols":_pilot_symbols(),
         "unattended_independent_stop_close_allowed":False,
         "unattended_order_post_permission":False}
    obj.update(fields)
    tmp=REPORT.with_name("."+REPORT.name+"."+str(os.getpid())+".tmp")
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(obj,f,ensure_ascii=False,sort_keys=True)
        f.write("\n");f.flush();os.fsync(f.fileno())
    os.replace(tmp,REPORT)
    _decision_log(status, fields)

def _pilot():
    sys.path.insert(0,str(PRIVATE))
    import live_pilot
    return live_pilot

def _pilot_symbols():
    try:
        return sorted(_pilot().SYMBOLS)
    except Exception:
        return None

def _live_open():
    try:
        from fast_r8_offline_integration_contract_F3F4_CANDIDATE import live_enabled
        return live_enabled() is True
    except Exception:
        return False

def _tick_align(paper, plan):
    """048: Binance yalnız tickSize katı fiyat kabul eder. KOPYA döner; stop girişten uzağa, hedefler girişe
    doğru yuvarlanır. Risk size_bundle'da yuvarlanmış stoptan yeniden hesaplanır (<= 2,70 USDT)."""
    from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
    tick=Decimal(str(plan["tick_size"]))
    if not tick.is_finite() or tick<=0:
        raise ValueError("TICK_SIZE_INVALID")
    side=paper.get("side")
    if side not in ("LONG","SHORT"):
        raise ValueError("SIDE_INVALID")
    mode=ROUND_FLOOR if side=="LONG" else ROUND_CEILING
    def r(v):
        x=Decimal(str(v))
        if not x.is_finite() or x<=0:
            raise ValueError("PRICE_INVALID")
        return format(((x/tick).to_integral_value(rounding=mode)*tick).normalize(),"f")
    out=dict(paper)
    for k in ("stop","tp1","tp2"):
        out[k]=r(paper[k])
    return out

def _num_str(v):
    """048: JSON'dan gelen float adımı (ör. 0.1) live_pilot'un kabul ettiği kesin ondalık metne çevirir."""
    from decimal import Decimal
    if isinstance(v, bool):
        raise ValueError("NUMBER_INVALID")
    x=Decimal(str(v))
    if not x.is_finite() or x<=0:
        raise ValueError("NUMBER_INVALID")
    return format(x.normalize(),"f")

def prepare_owner_bundle(state, rotation, paper):
    from fast_binance_active_owner_bridge import paper_bundle, _validate_owner_bundle
    plan=state["plan"]
    bundle=paper_bundle(_tick_align(paper,plan),plan)
    # 047: the phone approves EXACTLY the live-pilot-sized bundle (3x, margin <= 95, risk <= 2.70).
    bundle=_pilot().size_bundle(bundle,paper["entry"],_num_str(plan["qty_step"]))
    if _validate_owner_bundle(bundle) is not True:
        raise RuntimeError("OWNER_SIGNED_SDK_PREFLIGHT_FAILED")
    # Signals can expire while four signed SDK GETs complete.
    fresh_state=read_doc(BASE/"fast_signal_live_dry_run_state.json")
    fresh_rotation=read_doc(BASE/"fast_pair_rotation_policy.json")
    again=fresh_signal(fresh_state,fresh_rotation)
    if (again is None or again["ticket_id"]!=paper["ticket_id"]
        or fresh_state.get("plan")!=state.get("plan")
        or fresh_rotation.get("live_entry_enabled_pairs")!=
           rotation.get("live_entry_enabled_pairs")):
        raise RuntimeError("SIGNAL_CHANGED_DURING_SIGNED_PREFLIGHT")
    return bundle

# 049: karar günlüğü — davranışı değiştirmez, yalnız "neden" kaydı tutar.
DECISION_LOG = BASE / "fast_handoff_decision_log.jsonl"
_DL = {"key": None, "ctx": {}}

def _safe_code(v):
    s = str(v)[:120]
    ok = s and all(c.isalnum() or c in "_:. -" for c in s)
    return s if ok else "GIZLI"

def _why_not(state):
    """fresh_signal ile AYNI sırada ilk başarısız kural; READY olmayan durum için None."""
    try:
        if state.get("status") != "READY_DRY_RUN_PLAN":
            return None
        at = datetime.fromisoformat(state["generated_at"])
        if at.tzinfo is None:
            return "STATE_TIME_NAIVE"
        age = (datetime.now(timezone.utc) - at).total_seconds()
        if not 0 <= age <= 15:
            return "STATE_STALE_GT15S"
        if state.get("blockers") != []:
            return "DRYRUN_BLOCKERS"
        if state.get("signal_age_seconds", 999) > 40:
            return "SIGNAL_AGE_GT40S"
        if (state.get("orders_sent") is not False or state.get("live_enable") is not False
                or state.get("account", {}).get("can_trade") is not True):
            return "ACCOUNT_OR_MODE_FLAGS"
        if (Decimal(str(state.get("effective_min_score", 0))) < 68
                or Decimal(str(state.get("effective_min_net_rr", 0))) < Decimal("1.5")):
            return "EFFECTIVE_THRESHOLDS_LOW"
        plan = state["plan"]
        if Decimal(str(plan.get("score", 0))) < 68:
            return "SCORE_LT68"
        if Decimal(str(plan.get("estimated_net_rr", 0))) < Decimal("1.5"):
            return "RR_LT1_5"
        if Decimal(str(state["account"]["available_balance_usdt"])) < 95:
            return "BALANCE_LT95"
        if Decimal(str(plan["margin_usdt"])) > 200:
            return "MARGIN_GT200"
        return "PAPER_CANDIDATE_REJECTED"
    except Exception:
        return "STATE_UNPARSEABLE"

def _ctx(state, paper):
    try:
        plan = state.get("plan") or {}
        c = {"sig_symbol": plan.get("symbol"), "sig_side": plan.get("side"), "sig_score": plan.get("score"),
             "sig_rr": plan.get("estimated_net_rr"), "sig_source_at": plan.get("source_generated_at")}
        if state.get("status") != "READY_DRY_RUN_PLAN":
            c = {}
        elif paper is None:
            c["why_not"] = _why_not(state)
        else:
            c["paper_ticket"] = paper.get("ticket_id")
        _DL["ctx"] = {k: v for k, v in c.items() if v is not None}
    except Exception:
        _DL["ctx"] = {}

def _decision_log(status, fields):
    try:
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": _safe_code(status)}
        for k in ("error_type", "error_code", "ticket_id", "exit_code"):
            if fields.get(k) is not None:
                row[k] = _safe_code(fields[k])
        row.update(_DL["ctx"])
        key = json.dumps({k: v for k, v in row.items() if k != "at"}, sort_keys=True)
        if key == _DL["key"]:
            return
        _DL["key"] = key
        if DECISION_LOG.exists() and DECISION_LOG.stat().st_size > 2_000_000:
            os.replace(DECISION_LOG, DECISION_LOG.with_name(DECISION_LOG.name + ".1"))
        fd = os.open(DECISION_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass

def start_exact_trade(ticket,bundle):
    # Durable consume-before-spawn: no repeat after crash or ambiguous handoff.
    identity=ticket["ticket_id"]
    if (not isinstance(identity,str) or len(identity)!=16 or
        not identity.isalnum()):
        raise RuntimeError("INVALID_TICKET")
    marker=JOURNAL/("intent_"+identity+".json")
    document={"schema":"ATLAS_OWNER_EXECUTOR_ONCE_INTENT_V1",
              "ticket_id":identity,"created_at_unix":int(time.time()),
              "no_auto_retry":True,"requires_owner_phone_p256_approval":True}
    fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as f:
        json.dump(document,f,sort_keys=True);f.write("\n");f.flush();os.fsync(f.fileno())
    dirfd=os.open(JOURNAL,os.O_RDONLY)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    target=PRIVATE/("atlas_fast_owner_exact_"+uuid.uuid4().hex+".json")
    raw=(json.dumps(bundle,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    if len(raw)>7500:raise RuntimeError("EXACT_BUNDLE_TOO_LONG")
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
    logfile=JOURNAL/("execution_"+identity+".log")
    fd=os.open(logfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        proc=subprocess.Popen((str(PYTHON),str(EXECUTOR),"--exact-bundle",str(target)),
                              stdin=subprocess.DEVNULL,stdout=fd,stderr=fd,
                              close_fds=True,start_new_session=False)
    finally:os.close(fd)
    # Executor reads this file immediately and validates its owner + freshness.
    return proc,target,identity

def step():
    sys.path.insert(0,str(ADAPTERS))
    check_owner()
    from owner_phone_exact_workflow import step as phone_step, PENDING
    # Once a fixed owner-issued exact consent is pending, process the signed
    # response independently of subsequent fast-changing strategy files.
    # UID501 verifies the P-256 proof, exact bundle and 25s expiry.
    # This does not invoke V3.1 or grant Binance POST permission.
    if PENDING.exists() or PENDING.is_symlink():
        return phone_step(None,lambda:None,spawn=start_exact_trade)
    state=read_doc(BASE/"fast_signal_live_dry_run_state.json")
    rotation=read_doc(BASE/"fast_pair_rotation_policy.json")
    paper=fresh_signal(state,rotation)
    _ctx(state, paper)
    # 047: a phone challenge only for a pilot symbol, while the live session is open and the pilot
    # is not exhausted; otherwise nothing is asked and nothing is started.
    if paper is not None:
        lp=_pilot()
        if paper.get("symbol") not in lp.SYMBOLS:
            return "SIGNAL_NOT_IN_LIVE_PILOT_SYMBOLS",None
        if not _live_open():
            return "WAITING_OWNER_LIVE_SESSION",None
        try:
            lp.pilot_status()
        except lp.PilotBlocked as e:
            return "LIVE_PILOT_CLOSED:"+str(e),None
    return phone_step(paper,lambda:prepare_owner_bundle(state,rotation,paper),spawn=start_exact_trade)

def main():
    check_owner()
    sys.path.insert(0,str(ADAPTERS))
    if len(sys.argv)==2 and sys.argv[1]=="--probe":
        state=read_doc(BASE/"fast_signal_live_dry_run_state.json")
        rotation=read_doc(BASE/"fast_pair_rotation_policy.json")
        ticket=fresh_signal(state,rotation)
        print("OWNER_UID501_PY313_REAL_EXECUTOR_READY")
        print("CURRENT_SIGNAL", "ELIGIBLE" if ticket else "WAIT")
        print("REAL_ORDER_SUBMITTED",False)
        return 0
    if sys.argv!=[sys.argv[0],"--service"]:
        raise RuntimeError("ONLY_PROBE_OR_SERVICE_ALLOWED")
    active=None
    while True:
        try:
            check_owner()
            if active is not None:
                proc,target,identity=active
                exitcode=proc.poll()
                if exitcode is None:
                    safe_report("OWNER_EXECUTOR_RUNNING",ticket_id=identity)
                else:
                    target.unlink(missing_ok=True)
                    safe_report("EXECUTOR_EXITED_OWNER_REVIEW",exit_code=exitcode,
                                ticket_id=identity)
                    active=None
            else:
                state,spawned=step()
                if spawned is not None:active=spawned
                safe_report(state)
        except FileExistsError:
            safe_report("SIGNAL_ALREADY_CONSUMED_NO_RETRY")
        except Exception as error:
            safe_report("FAIL_CLOSED_OWNER_REVIEW",error_type=type(error).__name__,
                        error_code=_safe_code(error))
        # A 25s signed phone challenge should not wait up to five seconds
        # for owner receipt processing. No exchange actions occur here.
        # 056 (28.10): idle poll 5s -> 1s (sinyal->telefon gecikmesi); yalnız dosya okuma, borsa yok.
        time.sleep(1)

if __name__=="__main__":
    raise SystemExit(main())
