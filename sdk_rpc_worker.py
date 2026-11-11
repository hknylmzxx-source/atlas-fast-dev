#!/usr/bin/python3
"""Owner-only FAST SDK 3.13 capability bridge (STRICTLY OFFLINE).

Fixed RPCs: status, validate_bundle. Imports Binance SDK definitions and
owner-private transport.shape only. No SDK client, Keychain, credentials,
HTTP, signing, order/test POST, cancel, command execution or live release.
Runs from the owner's isolated SDK venv, never from the maintenance account.
"""
import importlib.util
import inspect
import json
import os
import pwd
import re
import stat
import sys
import time
from pathlib import Path
from importlib.metadata import version

HOME=Path("/Users/hakanyilmaz")
ROOT=HOME/"Library"/"Application Support"/"AtlasFastOwnerSDK"
PRIVATE_TRANSPORT=HOME/"Library"/"Application Support"/"AtlasFastOwnerConnector"/"transport.py"
PRIVATE_SENDER=ROOT/"sdk_signed_sender.py"
QUEUE=Path("/Users/Shared/Atlas/ATLAS_ORTAK_CALISMA/01_CALISMA/FAST_OWNER_SDK_RPC")
REQ=QUEUE/"talepler"
DONE=QUEUE/"islenenler"
ANS=QUEUE/"yanitlar"
IDENT=re.compile(r"^[a-f0-9]{32}$")
FIELDS={
  "ENTRY":{"symbol","side","positionSide","type","quantity","newOrderRespType","newClientOrderId"},
  "STOP":{"algoType","symbol","side","positionSide","type","triggerPrice","workingType","closePosition","clientAlgoId"},
  "TP1":{"symbol","side","positionSide","type","timeInForce","quantity","price","reduceOnly","newClientOrderId"},
  "TP2":{"symbol","side","positionSide","type","timeInForce","quantity","price","reduceOnly","newClientOrderId"},
}
METHODS={"ENTRY":"new_order","STOP":"new_algo_order",
         "TP1":"new_order","TP2":"new_order"}

class CapabilityRejected(ValueError):pass

def _validate_install():
    if (sys.version_info[:2]!=(3,13) or os.geteuid()!=501 or
        pwd.getpwuid(os.geteuid()).pw_name!="hakanyilmaz"):
        raise SystemExit("OWNER_PYTHON_313_REQUIRED")
    if Path(sys.prefix).resolve()!=(ROOT/".venv").resolve():
        raise SystemExit("ISOLATED_OWNER_VENV_REQUIRED")
    script=ROOT/"sdk_rpc_worker.py"
    if Path(__file__).resolve()!=script or script.is_symlink():
        raise SystemExit("OWNER_PRIVATE_INSTALL_REQUIRED")
    for p in (ROOT,script,PRIVATE_TRANSPORT):
        if (p.is_symlink() or not p.exists() or p.stat().st_uid!=501
            or p.stat().st_mode & 0o077):
            raise SystemExit("OWNER_PRIVATE_PERMISSIONS_REQUIRED")
    for p in (QUEUE,REQ,DONE,ANS):
        if p.is_symlink() or not p.is_dir():raise SystemExit("QUEUE_UNSAFE")

def _sdk_methods():
    if version("binance-sdk-derivatives-trading-usds-futures")!="17.6.0":
        raise CapabilityRejected("SDK_VERSION_DRIFT")
    from binance_sdk_derivatives_trading_usds_futures.rest_api.api.trade_api import TradeApi
    required={
      "new_order":{"symbol","side","type","quantity","new_client_order_id"},
      "new_algo_order":{"algo_type","symbol","side","type","trigger_price","client_algo_id"},
      "query_order":{"symbol","orig_client_order_id"},
      "query_algo_order":{"client_algo_id"},
    }
    for method,params in required.items():
        fn=getattr(TradeApi,method,None)
        if not callable(fn) or not params.issubset(inspect.signature(fn).parameters):
            raise CapabilityRejected("SDK_METHOD_DRIFT_"+method)
    return sorted(required)

def _private_shape():
    spec=importlib.util.spec_from_file_location("atlas_owner_transport_shape",PRIVATE_TRANSPORT)
    obj=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=obj
    spec.loader.exec_module(obj)
    return obj.shape

def validate_bundle(bundle, shape_fn):
    if not isinstance(bundle,dict) or set(bundle)!={"entry","stop","take_profits"}:
        raise CapabilityRejected("EXACT_BUNDLE_REQUIRED")
    tp=bundle["take_profits"]
    if not isinstance(tp,list) or len(tp)!=2:
        raise CapabilityRejected("TWO_TARGETS_REQUIRED")
    rows=[("ENTRY",bundle["entry"]),("STOP",bundle["stop"]),
          ("TP1",tp[0]),("TP2",tp[1])]
    ids=[]
    for role,wire in rows:
        if type(wire) is not dict or set(wire)!=FIELDS[role]:
            raise CapabilityRejected("ROLE_FIELDS_INVALID_"+role)
        verb,path=shape_fn(role,wire)
        if (verb,path)!=("POST","/fapi/v1/algoOrder" if role=="STOP"
                        else "/fapi/v1/order"):
            raise CapabilityRejected("VENUE_PATH_INVALID")
        ids.append(wire["clientAlgoId"] if role=="STOP" else wire["newClientOrderId"])
    if (len(set(ids))!=4 or
        len({r["symbol"] for _,r in rows})!=1 or
        rows[0][1]["side"]==rows[1][1]["side"] or
        rows[1][1]["side"]!=rows[2][1]["side"] or
        rows[1][1]["side"]!=rows[3][1]["side"]):
        raise CapabilityRejected("BUNDLE_ID_SYMBOL_OR_SIDE_MISMATCH")
    _sdk_methods()
    return {"status":"OWNER_SDK_BUNDLE_VALIDATED_OFFLINE",
            "roles":[k for k,_ in rows],
            "sdk_methods":[METHODS[k] for k,_ in rows],
            "sdk_version":"17.6.0",
            "sdk_client_constructed":False,"owner_sdk_connected":True,
            "order_post_enabled":False,"orders_sent":False}

def _bind_signing_transport():
    # Imported only from the owner-private installed module, never Shared.
    if (PRIVATE_SENDER.is_symlink() or not PRIVATE_SENDER.is_file()
        or PRIVATE_SENDER.stat().st_uid!=501
        or PRIVATE_SENDER.stat().st_mode & 0o077):
        raise CapabilityRejected("PRIVATE_SIGNING_BINDER_NOT_INSTALLED")
    spec=importlib.util.spec_from_file_location("atlas_owner_sdk_signed_sender",PRIVATE_SENDER)
    binder=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=binder
    spec.loader.exec_module(binder)
    proof=binder.binding_proof()
    if (proof.get("status")!="OWNER_SDK_SIGNING_TRANSPORT_BOUND_LOCKED"
        or proof.get("owner_sdk_client_constructed") is not True
        or proof.get("sdk_client_retained") is not False
        or proof.get("live_order_post_allowed") is not False
        or proof.get("orders_sent") is not False):
        raise CapabilityRejected("OWNER_SIGNING_BIND_NOT_VERIFIED")
    return proof

def _signed_protection_status():
    if (PRIVATE_SENDER.is_symlink() or not PRIVATE_SENDER.is_file()
        or PRIVATE_SENDER.stat().st_uid!=501
        or PRIVATE_SENDER.stat().st_mode & 0o077):
        raise CapabilityRejected("PRIVATE_SIGNED_READ_BINDER_NOT_INSTALLED")
    spec=importlib.util.spec_from_file_location("atlas_owner_sdk_signed_protection",PRIVATE_SENDER)
    mod=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=mod
    spec.loader.exec_module(mod)
    report=mod.signed_protection_snapshot()
    if (report.get("status")!="OWNER_SIGNED_FUTURES_PROTECTION_SNAPSHOT"
        or report.get("owner_session_uid")!=501
        or report.get("signed_owner_sdk_reads")!=4
        or report.get("order_post_enabled") is not False
        or report.get("orders_sent") is not False
        or report.get("continuous_stop_coverage_verified") is not False):
        raise CapabilityRejected("SIGNED_SNAPSHOT_PROTECTION_EVIDENCE_INVALID")
    return report

def _verify_owner_stop(bundle,shape_fn):
    validate_bundle(bundle,shape_fn)
    if (PRIVATE_SENDER.is_symlink() or not PRIVATE_SENDER.is_file()
        or PRIVATE_SENDER.stat().st_uid!=501
        or PRIVATE_SENDER.stat().st_mode & 0o077):
        raise CapabilityRejected("OWNER_SIGNED_STOP_MODULE_MISSING")
    spec=importlib.util.spec_from_file_location("atlas_owner_sdk_stop_monitor",PRIVATE_SENDER)
    mod=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=mod
    spec.loader.exec_module(mod)
    result=mod.signed_owned_stop_check(stop_wire=bundle["stop"],shape_fn=shape_fn)
    if (result.get("status") not in (
             "OWNER_SIGNED_FLAT_NO_POSITION",
             "OWNER_SIGNED_PROTECTIVE_STOP_ACTIVE_AT_READ")
        or result.get("order_post_enabled") is not False
        or result.get("live_order_allowed") is not False
        or result.get("orders_sent") is not False):
        raise CapabilityRejected("OWNER_SIGNED_STOP_READBACK_UNVERIFIED")
    return result

def _signed_trade_history_summary(symbol,from_id):
    if (PRIVATE_SENDER.is_symlink() or not PRIVATE_SENDER.is_file()
        or PRIVATE_SENDER.stat().st_uid!=501 or PRIVATE_SENDER.stat().st_mode & 0o077):
        raise CapabilityRejected("PRIVATE_SIGNED_TRADE_MODULE_MISSING")
    spec=importlib.util.spec_from_file_location("atlas_owner_sdk_usertrades",PRIVATE_SENDER)
    mod=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=mod
    spec.loader.exec_module(mod)
    report=mod.signed_trade_history_summary(symbol=symbol,from_id=from_id)
    if (type(report) is not dict
        or report.get("status")!="OWNER_SIGNED_USERTRADES_BOUNDED_READ"
        or report.get("owner_session_uid")!=501
        or type(report.get("rows_read")) is not int
        or not 0<=report["rows_read"]<=1000
        or report.get("pages_read")!=1
        or type(report.get("page_full")) is not bool
        or report["page_full"]!=(report["rows_read"]==1000)
        or report.get("history_complete_verified") is not False
        or report.get("four_role_verified") is not False
        or report.get("order_post_enabled") is not False
        or report.get("orders_sent") is not False):
        raise CapabilityRejected("SIGNED_TRADE_SUMMARY_UNVERIFIED")
    return report

def handle(obj,name,shape_fn):
    if not isinstance(obj,dict) or not isinstance(obj.get("id"),str) or (
        not IDENT.fullmatch(obj["id"]) or name!=obj["id"]+".json"):
        raise CapabilityRejected("REQUEST_ID_INVALID")
    if obj.get("op")=="status" and set(obj)=={"id","op"}:
        return {"status":"OWNER_SDK_313_CONNECTED",
                "sdk_version":"17.6.0","verified_methods":_sdk_methods(),
                "sdk_client_constructed":False,"owner_sdk_connected":True,
                "order_post_enabled":False,"orders_sent":False}
    if obj.get("op")=="validate_bundle" and set(obj)=={"id","op","bundle"}:
        return validate_bundle(obj["bundle"],shape_fn)
    if obj.get("op")=="bind_signing_transport" and set(obj)=={"id","op"}:
        return _bind_signing_transport()
    if obj.get("op")=="signed_protection_snapshot" and set(obj)=={"id","op"}:
        return _signed_protection_status()
    if obj.get("op")=="signed_trade_history_summary" and set(obj)=={"id","op","symbol","from_id"}:
        return _signed_trade_history_summary(obj["symbol"],obj["from_id"])
    if obj.get("op")=="verify_owned_stop" and set(obj)=={"id","op","bundle"}:
        return _verify_owner_stop(obj["bundle"],shape_fn)
    if obj.get("op")=="execution_handoff" and set(obj)=={"id","op","bundle"}:
        validated=validate_bundle(obj["bundle"],shape_fn)
        # TRUE owner-process handoff: the signed SDK's exact trade-method ABI
        # and all four FAST order contracts were checked inside UID501.
        # No SDK client or trading credentials, and NO Binance mutation call.
        return {"status":"OWNER_SDK_EXECUTION_HANDOFF_BOUND_LOCKED",
                "sdk_version":"17.6.0",
                "owner_execution_route_connected":True,
                "sdk_methods":validated["sdk_methods"],
                "sdk_client_constructed":False,
                "requires_exact_owner_final_trade_approval":True,
                "live_order_allowed":False,"order_post_enabled":False,
                "orders_sent":False}
    if obj.get("op")=="owner_executor_presence" and set(obj)=={"id","op"}:
        import hashlib
        exe=ROOT/"owner_single_execution.py"
        expected="2f4727cb3240618e87a141728e6d89f0645049d2fa56cc9b09a8bd71f5a63a2f"
        if (exe.is_symlink() or not exe.is_file()
            or exe.stat().st_uid!=501 or exe.stat().st_mode & 0o077
            or hashlib.sha256(exe.read_bytes()).hexdigest()!=expected):
            raise CapabilityRejected("OWNER_EXECUTOR_NOT_VERIFIED")
        return {"status":"OWNER_EXECUTOR_INSTALLED_VERIFIED_DORMANT",
                "executable_sha256":expected,
                "execution_service_connected":False,
                "live_order_allowed":False,"order_post_enabled":False,
                "orders_sent":False}
    if obj.get("op") in ("execute","submit_order","submit_bundle"):
        raise CapabilityRejected("LIVE_SDK_SEND_REQUIRES_OWNER_FINAL_TRADE_APPROVAL")
    raise CapabilityRejected("OPERATION_NOT_ALLOWED")

def _atomic_response(path,obj):
    data=(json.dumps(obj,sort_keys=True,separators=(",",":"))+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as out:
        out.write(data);out.flush();os.fsync(out.fileno())

def _process(src,shape_fn):
    if src.is_symlink() or not IDENT.fullmatch(src.stem):return
    dst=DONE/src.name
    reply=ANS/src.name
    if dst.exists() or reply.exists():return
    try:
        os.replace(src,dst)
        fd=os.open(dst,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,"rb") as inp:raw=inp.read(10001)
        if len(raw)>10000:raise CapabilityRejected("REQUEST_TOO_LONG")
        result=handle(json.loads(raw),dst.name,shape_fn)
        result["ok"]=True
    except Exception as e:
        result={"ok":False,"error":type(e).__name__}
    result.update(request_id=src.stem,owner_uid=501,
                  handled_at_unix=int(time.time()))
    try:_atomic_response(reply,result)
    except FileExistsError:pass

def main():
    os.umask(0o077)
    _validate_install()
    shape_fn=_private_shape()
    _sdk_methods()
    while True:
        for src in sorted(REQ.glob("*.json"))[:8]:
            _process(src,shape_fn)
        time.sleep(0.4)

if __name__=="__main__":
    main()
