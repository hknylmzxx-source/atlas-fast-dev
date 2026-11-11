#!/usr/bin/env python3
"""ATLAS FAST single-use OWNER USD-M executor (source; not deployed).

ONE explicit native macOS approval -> one MARKET entry -> signed fill GET ->
one CLOSE_ALL exchange STOP -> signed STOP readback -> two reduce-only TPs ->
signed protection polling. Fail closed and quarantine ambiguous outcomes.
This program is never called by the DRY_RUN controller. Owner-private
installation and independent operational verification are required before use.
"""
from __future__ import annotations

"""ATLAS OFFLINE STAGED FENCED OWNER SOURCE, NEVER LIVE-EXECUTABLE.

F3/F4 EXECUTOR INTEGRATION CANDIDATE (Claude, 19.10.2026 10:47, owner-approved
offline integration) on top of the F1/F2/R1 candidate (sha256 d1b59905...):
  * contract: fast_r8_offline_integration_contract_F3F4_CANDIDATE (rev 2);
  * run_once: F4 stop-guard config + quarantine check, then PolicyPhase
    preflight BEFORE the owner dialog, owner_approved(grant_consumed) for the
    same bundle, reserve right before ENTRY (risk_ledger_entry_gate now calls
    the contract and requires the computed risk_worst_usdt <= 0.30);
  * all policy/stop-guard inputs come from the owner-private module
    atlas_integration_inputs, which does NOT exist yet -> fail closed;
  * STOP absent/uncertain after entry: guard reconciliation through the
    contract with the emergency POST disabled (R2 NOT enabled), then the
    existing staged emergency path (still disabled).
F1/F2/R1 functions are byte-for-byte (AST) unchanged.
POLICY-SETTLE-001 (19.10.2026 10:55): after a signed FLAT with owned exits
cleared (or a verified never-executed / pre-send-refused ENTRY) the ledger
scope is settled through PolicyGate.settle with the signed entry fill and a
flat proof from signed reads; failure keeps OPENED + durable incident.
POLICY-RESTART-002 (19.10.2026 11:05): a durable POLICY_RESERVE_<scope>.json
(symbol + entry clientOrderId) is written BEFORE every reservation (resolved
again if the reservation was refused without a ledger change);
restart_reconcile() settles pending OPENED scopes after a crash ONLY with
that record, the signed entry status, a signed FLAT read with no open orders
and the attested income (PolicyGate.settle). PNL_UNKNOWN is never auto-cleared.

F1/F2 OFFLINE FIX CANDIDATE (Claude, 19.10.2026, owner-approved offline fix)
on top of GPT R8_INTEGRATION_OFFLINE_CANDIDATE (sha256 8b76132b...). All
staged fences unchanged (owner_only, SignedVenue.__init__, production_entry,
emergency_flatten, risk_ledger_entry_gate). Changes:
  F1 one_write takes the owner mutation-fence lease BEFORE the WAL record;
     lease refusal / missing fence module = LeaseRefused (PreSendRefused, no
     WAL, nothing sent). WAL + SDK call run inside the lease; any error after
     the SDK call (incl. lease release) = UNKNOWN, never retried. A refused
     STOP is retried at most STOP_LEASE_RETRIES times (place_stop_write).
  F2 risk_authority / post-fill check are cost-inclusive: worst-case entry
     slippage, STOP slippage and taker fees count toward 0.30 USDT; notional
     uses the worst-case entry price against 100 USDT.

Guarded normal POST/DELETE boundary is tested only through simulated REST.
OWNER UID501 execution, Binance POST and emergency closure are hard-blocked.
Derived from hash-pinned original; not installed in owner-private service.
"""
import fcntl, hashlib, json, os, pwd, stat, sys, time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
import requests  # 050: live SignedVenue needs the real requests.Session type check (staged placeholder removed 26.10)

HOME=Path("/Users/hakanyilmaz")
PRIVATE=HOME/"Library/Application Support/AtlasFastOwnerSDK"
SELF=PRIVATE/"owner_single_execution.py"
LOG=PRIVATE/"one_trade_wal"
LIVE_AUTH_USED_DIR=PRIVATE/"live_auth_used"   # 045 G3: owner-created 0700; one-use trade-auth markers
VENUE="https://fapi.binance.com"
GET_PATHS=frozenset(("/fapi/v3/account","/fapi/v1/positionSide/dual","/fapi/v1/openOrders","/fapi/v1/openAlgoOrders","/fapi/v1/order","/fapi/v1/algoOrder","/fapi/v1/premiumIndex","/fapi/v1/exchangeInfo",
                      # SIGNEDVENUE-OFFLINE-007: signed income (preflight/settle),
                      # server time (income envelope), last price (CONTRACT_PRICE)
                      "/fapi/v1/income","/fapi/v1/time","/fapi/v1/ticker/price",
                      # PROPOSAL-021: signed leverage / margin type source
                      "/fapi/v1/symbolConfig"))
# 007: only a signed GET order query may prove "order does not exist". The
# algo "not found" code is unverified -> no algo path maps to OrderNotFound.
ORDER_NOT_FOUND_PATHS=frozenset(("/fapi/v1/order",))
ORDER_NOT_FOUND_CODE=-2013
SDK_PINS=(("binance-sdk-derivatives-trading-usds-futures","17.6.0","SDK_VERSION_NOT_PINNED"),
          ("binance-common","4.5.1","SDK_COMMON_VERSION_NOT_PINNED"))
POST_PATHS=frozenset(("/fapi/v1/order","/fapi/v1/algoOrder"))
DELETE_PATHS=POST_PATHS
MAX_MARGIN=Decimal("200")     # sahip 29.10: atlas_limits.json      # 047 live pilot (owner 23.10.2026): 3x isolated, margin <= 95 USDT
MAX_RISK=Decimal("0.75")      # 047: worst-case loss incl. slippage and fees <= 2.70 USDT
LOSS_LOCK_USDT=Decimal("1.00")  # B3 sahip 07.11.2026: giris kilidi esigi (MAX_RISK degismedi)
MAX_NOTIONAL=Decimal("600")   # 047: = MAX_MARGIN x MAX_LEVERAGE
MAX_LEVERAGE=Decimal("3")     # 047
ENTRY_RESOLVE_POLLS=15
ENTRY_RESOLVE_SLEEP=1
ENTRY_GUARD_POLLS=150
ENTRY_GUARD_SLEEP=2
MONITOR_MAX_READ_FAILURES=30
OBSERVER_MAX_AGE=45
OBSERVER_STATUS_NAME="independent_stop_guard_status.json"
STOP_MISSING_CONFIRMATIONS=2
# F2: conservative cost assumptions (same as V32_POLICY / PolicyGate). They
# only make checks stricter; smaller values would loosen the owner policy.
ENTRY_SLIPPAGE=Decimal("0.0005")
STOP_SLIPPAGE=Decimal("0.0010")
TAKER_FEE=Decimal("0.0005")
# F1: bounded retries for a STOP refused by the mutation fence (never sent).
# R1: only for this exact transient reason of fast_owner_emergency_fence_STAGED
# (sha256 7a379152...); any other refusal is permanent -> no retry.
TRANSIENT_FENCE_REASONS=frozenset(("OWNER_MUTATION_ALREADY_IN_PROGRESS",))
STOP_LEASE_RETRIES=3
STOP_LEASE_RETRY_SLEEP=1
class Blocked(RuntimeError): pass
class OrderNotFound(Exception):
    """007: exact HTTP 400 + JSON int code -2013 on a signed GET order query.
    Timeouts, 5xx, other codes, other paths/methods and broken bodies are NOT
    this. Deliberately not a Blocked subclass."""
class PreSendRefused(Blocked):
    """Refused before any exchange write; never treat as uncertain."""
class LeaseRefused(PreSendRefused):
    """F1: mutation-fence lease not obtained; no WAL record, nothing sent.
    R1: transient=True ONLY for the real fence's exact non-blocking busy
    refusal (another writer holds the lease right now). Everything else
    (durable emergency latch, untrusted/corrupt files, OSError, missing
    module, unknown text) is transient=False and is never retried."""
    def __init__(self,message,transient=False):
        super().__init__(message)
        self.transient=transient is True

def owner_only():
    """047 (owner 23.10.2026): the unconditional lock became a real gate. Only the owner account,
    and only while the owner-opened live session (LIVE_ENABLE, Terminal, <= 24 h) is valid."""
    if (os.geteuid()!=501 or pwd.getpwuid(os.geteuid()).pw_name!="hakanyilmaz"
        or Path.home()!=Path("/Users/hakanyilmaz")):
        raise Blocked("OWNER_UID501_REQUIRED")
    from fast_r8_offline_integration_contract_F3F4_CANDIDATE import live_enabled
    if live_enabled() is not True:
        raise Blocked("LIVE_SESSION_NOT_OPEN_NO_LIVE_POST")

def dec(v):
    if isinstance(v,bool) or not isinstance(v,(str,int,float)):
        raise Blocked("INVALID_DECIMAL")
    try:x=Decimal(str(v))
    except Exception:raise Blocked("INVALID_DECIMAL") from None
    if not x.is_finite() or x<=0:raise Blocked("INVALID_POSITIVE")
    return x

def fp(bundle):
    return hashlib.sha256(json.dumps(bundle,sort_keys=True,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()

def journal(role,identity,body):
    if LOG.is_symlink():raise Blocked("JOURNAL_SYMLINK")
    LOG.mkdir(mode=0o700,exist_ok=True)
    st=LOG.stat()
    if st.st_uid!=501 or st.st_mode & 0o077:
        raise Blocked("JOURNAL_PERMISSIONS")
    name=LOG/(role+"_"+identity+".json")
    raw=(json.dumps({"role":role,"intent":identity,"at":time.time(),
                     "state":"WRITE_OUTCOME_UNKNOWN_NO_RETRY",
                     "detail":body},sort_keys=True)+"\n").encode()
    try:fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:raise Blocked("DUPLICATE_WAL_ATTEMPT_NO_RESEND") from None
    with os.fdopen(fd,"wb") as out:
        out.write(raw);out.flush();os.fsync(out.fileno())
    dirfd=os.open(LOG,os.O_RDONLY)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)

def global_one_trade_lock():
    """Never allow two owner processes to preflight a flat account together."""
    owner_only()
    if LOG.is_symlink():raise Blocked("OWNER_WAL_SYMLINK")
    LOG.mkdir(mode=0o700,exist_ok=True)
    st=LOG.stat()
    if st.st_uid!=501 or st.st_mode & 0o077:
        raise Blocked("OWNER_WAL_PERMISSIONS")
    f=LOG/"owner_single_trade.lock"
    fd=os.open(f,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        s=os.fstat(fd)
        if s.st_uid!=501 or s.st_mode & 0o077 or not stat.S_ISREG(s.st_mode):
            raise Blocked("GLOBAL_LOCK_PERMISSIONS")
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise Blocked("ANOTHER_FAST_OWNER_TRADE_IS_RUNNING") from None

def emergency_intervention_lock():
    """Separate from the long-held entry lock; serialize emergency exits."""
    target=LOG/"owner_emergency_intervention.lock"
    fd=os.open(target,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=501 or st.st_mode & 0o077:
            raise Blocked("EMERGENCY_LOCK_UNTRUSTED")
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise Blocked("EMERGENCY_INTERVENTION_ALREADY_RUNNING") from None

def emergency_note(identity,reason):
    path=LOG/("incident_"+identity+".json")
    raw=json.dumps({"scope":identity,"at":time.time(),"reason":reason,
        "new_entry_permitted":False,"retry_permitted":False},
        sort_keys=True).encode()+b"\n"
    try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:return
    with os.fdopen(fd,"wb") as out:
        out.write(raw);out.flush();os.fsync(out.fileno())

def startup_reconcile():
    """B2: refuse any new trade while a previous trade is unresolved.

    Blocks on: any incident_*.json, any WAL scope without resolved_<scope>.json,
    or a leftover active_stop_manifest.json. Only the owner clears these after
    signed review; this function never deletes or edits them.
    """
    if LOG.is_symlink():raise Blocked("OWNER_WAL_SYMLINK")
    if (PRIVATE/"active_stop_manifest.json").exists():
        raise Blocked("UNRESOLVED_STOP_MANIFEST_OWNER_REVIEW_REQUIRED")
    if not LOG.exists():return
    open_scopes=set();resolved=set()
    for f in LOG.iterdir():
        n=f.name
        if n.startswith("incident_"):
            raise Blocked("UNRESOLVED_INCIDENT_OWNER_REVIEW_REQUIRED")
        if n.startswith("resolved_") and n.endswith(".json"):
            resolved.add(n[len("resolved_"):-len(".json")]);continue
        if n.endswith(".json"):
            try:row=json.loads(f.read_text())
            except Exception:raise Blocked("UNREADABLE_WAL_OWNER_REVIEW_REQUIRED") from None
            scope=(row.get("detail") or {}).get("scope")
            if type(scope) is not str:raise Blocked("WAL_WITHOUT_SCOPE_OWNER_REVIEW_REQUIRED")
            open_scopes.add(scope)
    if open_scopes-resolved:
        raise Blocked("UNRESOLVED_WAL_SCOPE_OWNER_REVIEW_REQUIRED")

def mark_scope_resolved(scope,outcome):
    """Durable marker written ONLY after signed FLAT + owned exits cleared."""
    path=LOG/("resolved_"+scope+".json")
    raw=(json.dumps({"scope":scope,"outcome":outcome,"at":time.time()},
                    sort_keys=True)+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as out:
        out.write(raw);out.flush();os.fsync(out.fileno())
    dirfd=os.open(LOG,os.O_RDONLY)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)

def verify_sdk_versions(version):
    """007: exact pins for the USD-M SDK and its binance-common dependency."""
    for package,pinned,code in SDK_PINS:
        try:
            found=version(package)
        except Exception:
            raise Blocked(code) from None
        if found!=pinned:
            raise Blocked(code)

def _order_not_found(method,path,response):
    if (method!="GET" or path not in ORDER_NOT_FOUND_PATHS
        or response.status_code!=400
        or not (response.headers.get("Content-Type") or "").startswith("application/json")):
        return False
    try:
        body=json.loads(response.content)
    except (ValueError,TypeError):
        return False
    code=body.get("code") if type(body) is dict else None
    return type(code) is int and code==ORDER_NOT_FOUND_CODE

def make_guarded_request(original,self):
    """Session request guard (moved out of SignedVenue.__init__ unchanged, 007:
    plus the narrow OrderNotFound classification of non-2xx responses)."""
    def guarded(method,url,**kwargs):
        u=urlsplit(url)
        if (u.scheme!="https" or u.hostname!="fapi.binance.com"
            or u.port not in (None,443) or u.username or u.password
            or u.fragment or kwargs.get("proxies") not in (None,{})):
            raise Blocked("UNPINNED_EXCHANGE_REQUEST")
        if method=="GET":
            if u.path not in GET_PATHS:raise Blocked("GET_PATH_NOT_ALLOWED")
        elif method in ("POST","DELETE"):
            permitted=POST_PATHS if method=="POST" else DELETE_PATHS
            if (u.path not in permitted
                or self.armed!=(method,u.path) or self.write_count!=0):
                raise Blocked("EXCHANGE_MUTATION_NEEDS_ONE_SHOT_LOCAL_ARM")
            self.write_count=1
        else:raise Blocked("METHOD_FORBIDDEN")
        kwargs["allow_redirects"]=False
        response=original(method,url,**kwargs)
        final=urlsplit(response.url)
        if (final.scheme!="https" or final.hostname!="fapi.binance.com"
            or final.path!=u.path or final.port not in (None,443)
            or final.username or final.password or final.fragment):
            raise Blocked("EXCHANGE_RESPONSE_NOT_TRUSTED")
        if response.status_code<200 or response.status_code>=300:
            if _order_not_found(method,u.path,response):
                raise OrderNotFound("ORDER_DOES_NOT_EXIST")
            raise Blocked("EXCHANGE_RESPONSE_NOT_TRUSTED")
        return response
    return guarded

def _live_gate_open():
    """045 G4: owner-private master flag via the contract; any error -> closed."""
    try:
        from fast_r8_offline_integration_contract_F3F4_CANDIDATE import live_enabled
        return live_enabled() is True
    except Exception:
        return False

def _live_token_ok(token):
    try:
        from fast_r8_offline_integration_contract_F3F4_CANDIDATE import verify_live_token
        return verify_live_token(token) is True
    except Exception:
        return False

class SignedVenue:
    """Official USD-M SDK v17.6; pinned HTTPS; writes one role at a time."""
    def __init__(self):
        # Never touch SDK, Keychain or Binance in any STAGED invocation.
        # 045 G4: closed unless the owner-private live master flag is valid (absent by default).
        if _live_gate_open() is not True:
            raise Blocked("STAGED_SDK_INSTANTIATION_DISABLED_NO_NETWORK")
        from importlib.metadata import version
        from binance_common.configuration import ConfigurationRestAPI
        from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
        from sdk_signed_sender import _owner_keychain_value,_sdk_payload
        verify_sdk_versions(version)
        key=_owner_keychain_value(account="atlas-binance-api-key",
                                  service="Atlas Binance API Key")
        secret=_owner_keychain_value(account="atlas-binance-api-secret",
                                     service="Atlas Binance API Secret")
        try:
            self.sdk=DerivativesTradingUsdsFutures(
                config_rest_api=ConfigurationRestAPI(
                    api_key=key,api_secret=secret,timeout=5000,retries=0,
                    base_path=VENUE))
        finally:
            del key,secret
        self.decode=_sdk_payload
        self.rest=self.sdk.rest_api
        session=self.rest._session
        if type(session) is not requests.Session or self.rest.configuration.retries!=0:
            raise Blocked("UNVERIFIED_SDK_SESSION")
        self._session=session
        session.trust_env=False;session.proxies={};session.max_redirects=0
        original=session.request
        self.armed=None
        self.write_count=0
        session.request=make_guarded_request(original,self)
    def arm_trade(self,token):
        """045 G4: the ONE trade this venue may write for (owner-native, one-use, scope-bound)."""
        if getattr(self,"_trade_token",None) is not None or _live_token_ok(token) is not True:
            raise Blocked("LIVE_TRADE_TOKEN_INVALID")
        self._trade_token=token
    def read(self,name,**kwargs):
        if self.armed is not None:raise Blocked("SIGNED_GET_DURING_POST")
        return self.decode(getattr(self.rest,name)(**kwargs))
    def _owner_fence(self):
        # Owner-private module is installed as a dormant hash-checked component.
        # Tests override this method to use an isolated mock UID + WAL.
        from owner_mutation_fence import OwnerMutationFence
        return OwnerMutationFence(LOG,expected_uid=501)

    def acquire_mutation_lease(self):
        """F1: enter the owner mutation fence; returns the open lease. Any
        failure here happens before WAL and SDK -> LeaseRefused."""
        if getattr(self,"_held_lease",None) is not None:
            raise LeaseRefused("MUTATION_LEASE_ALREADY_HELD")
        fence_blocked=None
        try:
            from owner_mutation_fence import FenceBlocked
            fence_blocked=FenceBlocked
            lease=self._owner_fence().ordinary_mutation()
            lease.__enter__()
        except Exception as e:
            transient=(fence_blocked is not None and isinstance(e,fence_blocked)
                       and str(e) in TRANSIENT_FENCE_REASONS)
            raise LeaseRefused("OWNER_MUTATION_FENCE_REFUSED_NO_WRITE:"
                               +type(e).__name__+":"+str(e)[:80],
                               transient=transient) from None
        self._held_lease=lease
        return lease

    def release_mutation_lease(self):
        lease=getattr(self,"_held_lease",None)
        self._held_lease=None
        if lease is not None:
            lease.__exit__(None,None,None)

    def _mutation_once(self,verb,name,path,**kwargs):
        # Single signed venue POST/DELETE choke point: all normal roles share
        # the SAME owner WAL mutex as the independent recovery coordinator.
        if _live_token_ok(getattr(self,"_trade_token",None)) is not True:
            raise Blocked("LIVE_TRADE_TOKEN_REQUIRED_NO_WRITE")          # 045 G4 (defence in depth)
        allowed=(POST_PATHS if verb=="POST" else
                 DELETE_PATHS if verb=="DELETE" else frozenset())
        if self.armed is not None or path not in allowed:
            raise Blocked("SDK_MUTATION_NOT_ALLOWED")
        if getattr(self,"_held_lease",None) is not None:
            # F1: one_write already holds the lease (taken before its WAL).
            self.armed=(verb,path);self.write_count=0
            try:
                result=self.decode(getattr(self.rest,name)(**kwargs))
                if self.write_count!=1:
                    raise Blocked("SDK_MUTATION_COUNT_NOT_ONE")
                return result
            finally:
                self.armed=None
        from owner_mutation_fence import FenceBlocked
        try:
            # No SDK write can begin outside this lease in the staged model.
            with self._owner_fence().ordinary_mutation():
                # Defensive recheck under lock before touching SDK.
                if self.armed is not None:
                    raise Blocked("SDK_MUTATION_ALREADY_ARMED")
                self.armed=(verb,path);self.write_count=0
                try:
                    result=self.decode(getattr(self.rest,name)(**kwargs))
                    if self.write_count!=1:
                        raise Blocked("SDK_MUTATION_COUNT_NOT_ONE")
                    return result
                finally:
                    self.armed=None
        except FenceBlocked:
            raise Blocked("OWNER_MUTATION_FENCE_DENIED_NO_WRITE") from None
    def post_once(self,name,path,**kwargs):
        return self._mutation_once("POST",name,path,**kwargs)
    def delete_once(self,name,path,**kwargs):
        return self._mutation_once("DELETE",name,path,**kwargs)
    def public(self,endpoint,symbol):
        if endpoint not in ("/fapi/v1/premiumIndex","/fapi/v1/exchangeInfo","/fapi/v1/ticker/price"):
            raise Blocked("PUBLIC_RISK_ENDPOINT_NOT_ALLOWED")
        if not isinstance(symbol,str) or not symbol.endswith("USDT"):
            raise Blocked("PUBLIC_SYMBOL_INVALID")
        response=self._session.request(
            "GET",VENUE+endpoint,params={"symbol":symbol},timeout=5)
        # 050s (26.10): fapi exchangeInfo ignores ?symbol= and returns every contract (~1.16 MB, 936 symbols);
        # keep a hard size cap, but one that fits the full exchange list.
        if len(response.content)>(8388608 if endpoint=="/fapi/v1/exchangeInfo" else 1048576):
            raise Blocked("PUBLIC_EXCHANGE_RESPONSE_TOO_LARGE")
        return response.json()
    def close(self):
        self._session.close()

def snapshot(venue):
    mode=venue.read("get_current_position_mode",recv_window=5000)
    account=venue.read("account_information_v3",recv_window=5000)
    normal=venue.read("current_all_open_orders",recv_window=5000)
    algo=venue.read("current_all_algo_open_orders",recv_window=5000)
    if (type(mode) is not dict or mode.get("dualSidePosition") is not False
        or type(account) is not dict or type(account.get("positions")) is not list
        or type(normal) is not list or type(algo) is not list):
        raise Blocked("SIGNED_PREFLIGHT_NOT_VALID")
    return account,normal,algo

def exposure(account,symbol):
    rows=[r for r in account["positions"] if r.get("symbol")==symbol]
    # 055 (28.10): GET /fapi/v3/account lists ONLY symbols with a position or open orders;
    # no row for the symbol is the signed FLAT for that symbol (amount 0).
    if not rows:return Decimal(0)
    if len(rows)!=1 or rows[0].get("positionSide") not in ("BOTH",None):
        raise Blocked("POSITION_SCOPE_INVALID")
    amount=Decimal(str(rows[0]["positionAmt"]))
    if not amount.is_finite():raise Blocked("POSITION_AMOUNT_INVALID")
    return amount

def risk_authority(venue,bundle,account):
    """Fresh pinned exchange mark + filters and signed isolated 1x balance."""
    symbol=bundle["entry"]["symbol"]
    quantity=dec(bundle["entry"]["quantity"])
    stop=dec(bundle["stop"]["triggerPrice"])
    mark_response=venue.public("/fapi/v1/premiumIndex",symbol)
    if (type(mark_response) is not dict
        or mark_response.get("symbol")!=symbol
        or type(mark_response.get("time")) is not int
        or abs(int(time.time()*1000)-mark_response["time"])>10000):
        raise Blocked("EXCHANGE_MARK_STALE_OR_UNKNOWN")
    mark=dec(mark_response.get("markPrice"))
    side=bundle["entry"]["side"]
    if not ((stop<mark) if side=="BUY" else (stop>mark)):
        raise Blocked("STOP_NOT_PROTECTIVE_AT_CURRENT_MARK")
    tp1=dec(bundle["take_profits"][0]["price"])
    tp2=dec(bundle["take_profits"][1]["price"])
    if not ((stop<mark<tp1<tp2) if side=="BUY"
            else (tp2<tp1<mark<stop)):
        raise Blocked("MARK_AND_EXITS_NOT_CONSISTENT")
    info=venue.public("/fapi/v1/exchangeInfo",symbol)
    if type(info) is not dict or type(info.get("symbols")) is not list:
        raise Blocked("EXCHANGE_RULES_MISSING")
    market=[row for row in info["symbols"]
            if type(row) is dict and row.get("symbol")==symbol]
    if len(market)!=1 or market[0].get("status")!="TRADING":
        raise Blocked("INSTRUMENT_NOT_TRADING")
    filters={r["filterType"]:r for r in market[0].get("filters",[])
             if type(r) is dict and type(r.get("filterType")) is str}
    if "LOT_SIZE" not in filters or "PRICE_FILTER" not in filters:
        raise Blocked("INSTRUMENT_FILTERS_MISSING")
    price_tick=dec(filters["PRICE_FILTER"]["tickSize"])
    for p in (stop,tp1,tp2):
        if p % price_tick:raise Blocked("PRICE_OFF_EXCHANGE_TICK")
    for typ in ("LOT_SIZE","MARKET_LOT_SIZE"):
        row=filters.get(typ)
        if row is None:continue
        try:
            step=Decimal(str(row["stepSize"]))
            lo=Decimal(str(row["minQty"]))
            hi=Decimal(str(row["maxQty"]))
        except Exception:raise Blocked("LOT_FILTER_INVALID") from None
        if step>0 and quantity%step:raise Blocked("QUANTITY_OFF_EXCHANGE_STEP")
        if lo>0 and quantity<lo:raise Blocked("QUANTITY_BELOW_MINIMUM")
        if hi>0 and quantity>hi:raise Blocked("QUANTITY_ABOVE_MAXIMUM")
    minrow=filters.get("MIN_NOTIONAL",{})
    minimum=max(Decimal("5"),Decimal(str(minrow.get("notional","5"))))
    # F2: worst-case entry price for the notional cap, cost-inclusive risk.
    notional=quantity*mark*(1+ENTRY_SLIPPAGE)
    if quantity*mark*(1-ENTRY_SLIPPAGE)<minimum or notional>MAX_NOTIONAL:
        raise Blocked("NOTIONAL_OUT_OF_PILOT_BOUNDS")
    risk=worst_risk(side,quantity,mark,stop,ENTRY_SLIPPAGE)
    if risk>MAX_RISK:
        raise Blocked("STOP_RISK_EXCEEDS_FIXED_030_USDT_LIMIT_INCL_COSTS")
    positions=[p for p in account["positions"] if p.get("symbol")==symbol]
    # 055 (28.10): v3 omits flat symbols (0 rows is normal); leverage and margin type are
    # verified from signed symbolConfig below.
    if len(positions)>1:
        raise Blocked("SIGNED_SYMBOL_POSITION_SCOPE_INVALID")
    # PROPOSAL-021: GET /fapi/v3/account position rows carry no leverage or
    # margin type; the signed source is GET /fapi/v1/symbolConfig.
    config=venue.read("symbol_configuration",symbol=symbol,recv_window=5000)
    rows=([r for r in config if type(r) is dict and r.get("symbol")==symbol]
          if type(config) is list else [])
    if (len(rows)!=1 or type(rows[0].get("leverage")) is not int
        or Decimal(rows[0]["leverage"])!=MAX_LEVERAGE):
        raise Blocked("SIGNED_SYMBOL_LEVERAGE_NOT_1X")
    if rows[0].get("marginType")!="ISOLATED":
        raise Blocked("SIGNED_SYMBOL_NOT_ISOLATED_MARGIN")
    margin=notional/MAX_LEVERAGE
    available=dec(account.get("availableBalance"))
    if (available<Decimal("15") or margin>MAX_MARGIN
        or available<margin*Decimal("1.05")+MAX_RISK):
        raise Blocked("PILOT_BALANCE_OR_MARGIN_INSUFFICIENT")
    return mark

def worst_risk(side,quantity,entry,stop,entry_slippage):
    """F2: loss if stopped out = worse entry, worse STOP fill, taker fees on
    both legs (same formula as order_filters / V32_POLICY)."""
    if side=="BUY":
        worst_entry=entry*(1+entry_slippage);stop_fill=stop*(1-STOP_SLIPPAGE)
    elif side=="SELL":
        worst_entry=entry*(1-entry_slippage);stop_fill=stop*(1+STOP_SLIPPAGE)
    else:
        raise Blocked("ENTRY_SIDE_INVALID")
    if ((stop_fill>=worst_entry) if side=="BUY" else (stop_fill<=worst_entry)):
        raise Blocked("STOP_NOT_PROTECTIVE_AFTER_SLIPPAGE")
    return (quantity*abs(worst_entry-stop_fill)
            +quantity*worst_entry*TAKER_FEE+quantity*stop_fill*TAKER_FEE)

def fill_breaches_policy(side,quantity,fill,stop):
    """F2 post-fill: True if the signed fill breaks 0.30 (costs incl.) or 100."""
    try:
        risk=worst_risk(side,quantity,fill,stop,Decimal("0"))
    except Blocked:
        return True
    return risk>MAX_RISK or quantity*fill>MAX_NOTIONAL

def actual_order(venue,role,wire):
    if role=="STOP":
        return venue.read("query_algo_order",client_algo_id=wire["clientAlgoId"],recv_window=5000)
    return venue.read("query_order",symbol=wire["symbol"],
                      orig_client_order_id=wire["newClientOrderId"],
                      recv_window=5000)

def checked(venue,role,wire):
    """A separate signed Binance GET is the only accepted receipt."""
    r=actual_order(venue,role,wire)
    try:_telemetry_observe(role,wire,r,"SIGNED_READBACK")   # G56: append-only, never blocks
    except Exception:pass
    if type(r) is not dict:
        raise Blocked("SIGNED_RECEIPT_NOT_OBJECT")
    if any(r.get(k)!=wire.get(k) for k in ("symbol","side","positionSide")):
        raise Blocked("SIGNED_RECEIPT_SYMBOL_DIRECTION_MISMATCH")
    if role=="STOP":
        if (r.get("clientAlgoId")!=wire["clientAlgoId"]
            or r.get("algoType")!="CONDITIONAL"
            or r.get("orderType",r.get("type"))!="STOP_MARKET"
            or r.get("closePosition") not in (True,"true","TRUE")
            or r.get("algoStatus") not in ("NEW","WORKING")
            or r.get("workingType")!=wire["workingType"]
            or dec(r.get("triggerPrice"))!=dec(wire["triggerPrice"])
            or type(r.get("algoId")) is not int or r["algoId"]<=0):
            raise Blocked("SIGNED_STOP_NOT_ACTIVE")
    else:
        if (r.get("clientOrderId")!=wire["newClientOrderId"]
            or type(r.get("orderId")) is not int or r["orderId"]<=0
            or r.get("type")!=wire["type"]
            or dec(r.get("origQty"))!=dec(wire["quantity"])):
            raise Blocked("SIGNED_ORDER_ID_QUANTITY_MISMATCH")
        if role=="ENTRY":
            if (r.get("status")!="FILLED"
                or dec(r.get("executedQty"))!=dec(wire["quantity"])
                or dec(r.get("avgPrice"))<=0):
                raise Blocked("ENTRY_NOT_FILLED_BY_SIGNED_GET")
        else:
            filled=Decimal(str(r.get("executedQty","0")))
            if (not filled.is_finite() or filled<0
                or r.get("status") not in ("NEW","PARTIALLY_FILLED","FILLED")
                or r.get("reduceOnly") not in (True,"true","TRUE")
                or r.get("timeInForce")!="GTC"
                or dec(r.get("price"))!=dec(wire["price"])
                or filled>dec(wire["quantity"])
                or (r.get("status")=="NEW" and filled!=0)
                or (r.get("status")=="PARTIALLY_FILLED"
                    and not (Decimal("0")<filled<dec(wire["quantity"])))
                or (r.get("status")=="FILLED"
                    and filled!=dec(wire["quantity"]))):
                raise Blocked("SIGNED_REDUCE_ONLY_TP_INVALID")
    return r

def payload(role,wire):
    standard={
        "symbol":"symbol","side":"side","positionSide":"position_side",
        "type":"type","quantity":"quantity","price":"price",
        "timeInForce":"time_in_force","reduceOnly":"reduce_only",
        "newClientOrderId":"new_client_order_id",
        "newOrderRespType":"new_order_resp_type"}
    conditional={
        "algoType":"algo_type","symbol":"symbol","side":"side",
        "positionSide":"position_side","type":"type",
        "triggerPrice":"trigger_price","workingType":"working_type",
        "closePosition":"close_position","clientAlgoId":"client_algo_id"}
    expected={
        "ENTRY":{"symbol","side","positionSide","type","quantity",
                 "newOrderRespType","newClientOrderId"},
        "STOP":{"algoType","symbol","side","positionSide","type",
                "triggerPrice","workingType","closePosition","clientAlgoId"},
        "TP1":{"symbol","side","positionSide","type","timeInForce",
               "quantity","price","reduceOnly","newClientOrderId"},
        "TP2":{"symbol","side","positionSide","type","timeInForce",
               "quantity","price","reduceOnly","newClientOrderId"}}
    if role not in expected or set(wire)!=expected[role]:
        raise Blocked("EXACT_ROLE_WIRE_MISMATCH")
    if role=="STOP":
        return ("new_algo_order","/fapi/v1/algoOrder",
                {dest:wire[src] for src,dest in conditional.items()})
    return ("new_order","/fapi/v1/order",
            {dest:wire[src] for src,dest in standard.items() if src in wire})

def stop_shape(role,wire):
    if (role!="STOP" or wire.get("algoType")!="CONDITIONAL"
        or wire.get("type")!="STOP_MARKET"
        or wire.get("closePosition")!="true"):
        raise Blocked("OWNER_STOP_CONTRACT_MISMATCH")
    payload("STOP",wire)
    return "POST","/fapi/v1/algoOrder"

def one_write(venue,role,wire,scope):
    # 045 G4: a real venue writes only for the armed one-use trade token (certain refusal: no WAL)
    if type(venue) is SignedVenue and _live_token_ok(getattr(venue,"_trade_token",None)) is not True:
        raise PreSendRefused("LIVE_TRADE_TOKEN_REQUIRED_NO_WRITE")
    method,endpoint,kwargs=payload(role,wire)
    name=wire["clientAlgoId"] if role=="STOP" else wire["newClientOrderId"]
    if endpoint not in POST_PATHS:
        raise PreSendRefused("WRITE_ENDPOINT_NOT_ALLOWED")
    # F1: lease FIRST. A refusal here is certain: no WAL, no SDK call.
    venue.acquire_mutation_lease()
    sent_phase=False
    try:
        try:
            journal(role,hashlib.sha256((scope+role+name).encode()).hexdigest(),{
                "scope":scope,"symbol":wire["symbol"],"client_id":name})
        except Exception as e:
            raise PreSendRefused(str(e)) from None
        sent_phase=True   # from here on the outcome may be unknown
        try:
            venue.post_once(method,endpoint,**kwargs)
        except Exception:
            raise Blocked("UNKNOWN_WRITE_RESULT_DO_NOT_RETRY_"+role) from None
    finally:
        try:
            venue.release_mutation_lease()
        except Exception:
            if sent_phase:
                raise Blocked("UNKNOWN_WRITE_RESULT_LEASE_RELEASE_FAILED_"+role) from None
            raise PreSendRefused("LEASE_RELEASE_FAILED_BEFORE_SEND") from None
    # 01.11.2026 (Claude, sahip onayi): algo emri POST'tan hemen sonra sorgulanamayabilir
    # (AIN: createTime 11:17:12.523, geri okuma hatasi 11:17:12.946). Yalniz OKUMA tekrarlanir;
    # POST asla tekrarlanmaz. Son denemede de olmazsa eski davranis aynen (Blocked + incident).
    last_readback="?"
    for readback_try in range(6):
        if readback_try:time.sleep(0.5)
        try:return checked(venue,role,wire)
        except Exception as e:last_readback=type(e).__name__+":"+str(e)[:120]
    raise Blocked("SIGNED_READBACK_MISSING_"+role+"|"+last_readback) from None

def place_stop_write(venue,wire,scope,sleep=None):
    """F1/R1: a STOP refused by the mutation fence was never journaled or
    sent. Retry (bounded) ONLY the transient busy refusal; a durable latch or
    any other refusal stops at once. Duplicate WAL / unknown result after the
    SDK call: never retried."""
    sleep=sleep or time.sleep
    for attempt in range(STOP_LEASE_RETRIES+1):
        try:
            return one_write(venue,"STOP",wire,scope)
        except LeaseRefused as e:
            if not e.transient or attempt==STOP_LEASE_RETRIES:
                raise
            sleep(STOP_LEASE_RETRY_SLEEP)

def emergency_flatten(venue,symbol,scope):
    """One reduce-only MARKET emergency action, then signed position recheck."""
    if type(venue) is SignedVenue:
        raise Blocked("STAGED_EMERGENCY_CLOSE_DISABLED_PENDING_NATIVE_OWNER_APPROVAL")
    fd=emergency_intervention_lock()
    try:
        return _emergency_flatten_locked(venue,symbol,scope)
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)

def _emergency_flatten_locked(venue,symbol,scope):
    account,_,_=snapshot(venue)
    size=exposure(account,symbol)
    if size==0:return True
    side="SELL" if size>0 else "BUY"
    # Exit is covered by the exact native consent to emergency risk control.
    wire={"symbol":symbol,"side":side,"positionSide":"BOTH","type":"MARKET",
          "quantity":format(abs(size),"f"),"reduceOnly":"true",
          "newOrderRespType":"RESULT",
          "newClientOrderId":"AFE"+scope[:18]+"X"}
    name=wire["newClientOrderId"]
    journal("EMERGENCY",hashlib.sha256((scope+name).encode()).hexdigest(),{
        "scope":scope,"symbol":symbol,"client_id":name})
    try:venue.post_once("new_order","/fapi/v1/order",**{
        "symbol":wire["symbol"],"side":wire["side"],
        "position_side":"BOTH","type":"MARKET",
        "quantity":wire["quantity"],"reduce_only":"true",
        "new_order_resp_type":"RESULT","new_client_order_id":name})
    except Exception:raise Blocked("EMERGENCY_WRITE_UNKNOWN_OWNER_REVIEW_NOW") from None
    result=actual_order(venue,"EMERGENCY",wire)
    if result.get("status")!="FILLED" or result.get("clientOrderId")!=name:
        raise Blocked("EMERGENCY_FILL_NOT_VERIFIED_OWNER_REVIEW_NOW")
    account,_,_=snapshot(venue)
    if exposure(account,symbol)!=0:raise Blocked("EMERGENCY_STILL_OPEN_OWNER_REVIEW_NOW")
    return True

def cleanup_owned_exits(venue,bundle,scope):
    """After signed FLAT, cancel only our exact STOP + TP identifiers."""
    symbol=bundle["entry"]["symbol"]
    account,normal,algo=snapshot(venue)
    if exposure(account,symbol)!=0:
        raise Blocked("CLEANUP_ONLY_AFTER_SIGNED_FLAT")
    stop=bundle["stop"]
    stop_id=stop["clientAlgoId"]
    active_stop=[r for r in algo
                 if r.get("clientAlgoId")==stop_id and r.get("symbol")==symbol]
    if len(active_stop)>1:raise Blocked("MULTIPLE_MATCHING_ALGO_ORDERS")
    if active_stop:
        label=hashlib.sha256((scope+"CANCEL_STOP"+stop_id).encode()).hexdigest()
        journal("CANCEL_STOP",label,{"scope":scope,"client_id":stop_id})
        try:
            venue.delete_once("cancel_algo_order","/fapi/v1/algoOrder",
                              client_algo_id=stop_id,recv_window=5000)
        except Exception:
            raise Blocked("STOP_CANCEL_UNCERTAIN_MANUAL_REVIEW") from None
    for index,t in enumerate(bundle["take_profits"],1):
        ident=t["newClientOrderId"]
        active=[r for r in normal
                if r.get("clientOrderId")==ident and r.get("symbol")==symbol]
        if len(active)>1:raise Blocked("DUPLICATE_OWNED_TP_ACTIVE")
        if not active:continue
        label=hashlib.sha256((scope+"CANCEL_TP"+ident).encode()).hexdigest()
        journal("CANCEL_TP"+str(index),label,{
            "scope":scope,"symbol":symbol,"client_id":ident})
        try:
            venue.delete_once("cancel_order","/fapi/v1/order",
                              symbol=symbol,orig_client_order_id=ident,
                              recv_window=5000)
        except Exception:
            raise Blocked("TP_CANCEL_UNCERTAIN_MANUAL_REVIEW") from None
    account,normal,algo=snapshot(venue)
    if (exposure(account,symbol)!=0
        or any(r.get("clientAlgoId")==stop_id for r in algo)
        or any(r.get("clientOrderId")==x["newClientOrderId"]
               for r in normal for x in bundle["take_profits"])):
        raise Blocked("POST_CANCEL_SIGNED_FLAT_OR_EXITS_NOT_VERIFIED")
    clear_owner_stop_manifest_after_flat(bundle)
    return True

def clear_owner_stop_manifest_after_flat(bundle):
    """Only after signed-flat and exact owned exit cleanup."""
    path=PRIVATE/"active_stop_manifest.json"
    if path.is_symlink():raise Blocked("OWNER_STOP_MANIFEST_SYMLINK")
    if not path.exists():return
    st=path.stat()
    if st.st_uid!=501 or st.st_mode & 0o077 or not 0<st.st_size<4096:
        raise Blocked("OWNER_STOP_MANIFEST_UNTRUSTED")
    row=json.loads(path.read_text())
    if (row.get("schema")!="FAST_OWNER_EXACT_STOP_MANIFEST_V3"
        or row.get("tpClientIds")!=[x["newClientOrderId"] for x in bundle["take_profits"]]
        or row.get("symbol")!=bundle["stop"]["symbol"]
        or row.get("clientAlgoId")!=bundle["stop"]["clientAlgoId"]):
        raise Blocked("OWNER_STOP_MANIFEST_MISMATCH")
    path.unlink()

def publish_owner_stop_manifest(bundle,entry_receipt,scope):
    """Owner-private atomic ownership proof after signed exchange STOP receipt."""
    path=PRIVATE/"active_stop_manifest.json"
    if path.exists() or path.is_symlink():
        raise Blocked("OWNER_STOP_MANIFEST_ALREADY_EXISTS_RECONCILE")
    wire=bundle["stop"]
    entry=bundle["entry"]
    entry_time=entry_receipt.get("time",entry_receipt.get("updateTime"))
    if (type(scope) is not str or len(scope)<16
        or type(entry_receipt.get("orderId")) is not int
        or entry_receipt["orderId"]<=0
        or type(entry_time) is not int or entry_time<=0
        or entry_receipt.get("status")!="FILLED"
        or entry_receipt.get("clientOrderId")!=entry["newClientOrderId"]
        or entry_receipt.get("side")!=entry["side"]
        or dec(entry_receipt.get("executedQty"))!=dec(entry["quantity"])):
        raise Blocked("SIGNED_OWNER_ENTRY_PROVENANCE_REQUIRED")
    row={"schema":"FAST_OWNER_EXACT_STOP_MANIFEST_V3",
         "scope":scope,
         "entryClientId":entry_receipt["clientOrderId"],
         "entryOrderId":entry_receipt["orderId"],
         "entrySide":entry_receipt["side"],
         "entryQty":entry_receipt["executedQty"],
         "entryTimeMs":entry_time,
         "tpClientIds":[x["newClientOrderId"] for x in bundle["take_profits"]],
         "symbol":wire["symbol"],"side":wire["side"],
         "clientAlgoId":wire["clientAlgoId"],
         "triggerPrice":wire["triggerPrice"],
         "created_at_unix":int(time.time())}
    raw=(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    dirfd=os.open(PRIVATE,os.O_RDONLY)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)

TERMINAL_NOT_FILLED=("CANCELED","EXPIRED","REJECTED","EXPIRED_IN_MATCH")

def resolve_uncertain_entry(venue,wire):
    """B1: after an ENTRY write with unknown outcome, NEVER resend.

    Phase 1 (fast) and phase 2 (guard, ~5 min) poll the exact deterministic
    clientOrderId AND the signed position every round. A signed FILLED
    receipt is returned so the caller places the STOP; a position that
    appears without a FILLED receipt raises (caller emergency-closes).
    Returns None only for a terminal, zero-fill order with a signed flat
    position. If the outcome is still unknown after both phases it raises
    ENTRY_OUTCOME_UNKNOWN: the incident written by the caller stays, so
    startup_reconcile blocks every new trade until owner review. There is
    no automatic release on an unknown entry outcome.
    """
    rounds=[ENTRY_RESOLVE_SLEEP]*ENTRY_RESOLVE_POLLS+[ENTRY_GUARD_SLEEP]*ENTRY_GUARD_POLLS
    for pause in rounds:
        try:
            r=actual_order(venue,"ENTRY",wire)
        except Blocked:
            raise
        except Exception:
            r=None   # not visible yet / transient read error: keep polling
        if type(r) is dict:
            status=r.get("status")
            if status=="FILLED":
                return checked(venue,"ENTRY",wire)
            if status in TERMINAL_NOT_FILLED:
                filled=Decimal(str(r.get("executedQty","0")))
                if filled!=0:raise Blocked("ENTRY_TERMINAL_PARTIAL_FILL")
                account,_,_=snapshot(venue)
                if exposure(account,wire["symbol"])!=0:
                    raise Blocked("ENTRY_TERMINAL_BUT_POSITION_OPEN")
                return None
        try:
            account,_,_=snapshot(venue)
            if exposure(account,wire["symbol"])!=0:
                raise Blocked("ENTRY_POSITION_OPEN_WITHOUT_FILLED_RECEIPT")
        except Blocked as e:
            if str(e)=="ENTRY_POSITION_OPEN_WITHOUT_FILLED_RECEIPT":raise
        except Exception:
            pass
        time.sleep(pause)
    raise Blocked("ENTRY_OUTCOME_UNKNOWN_OWNER_REVIEW_NO_RELEASE")

OBSERVER_EXPECTED_SHA256="85ecbf4ddb1c7b04067ac686a9650c16e647e56913896d91eb19dfb04b14c127"   # pinned owner-local observer; NO LIVE

def observer_handover_ready():
    """B3: hand supervision over ONLY to a verified-live independent observer.

    Delegates to observer_liveness.verify_observer_alive: fresh owner status
    file with heartbeat fields, observer sees THIS owner STOP, launchd reports
    the same pid running, the pid runs independent_stop_guard.py, and a second
    read after one observer period shows the heartbeat advancing. A status
    file alone is never enough. Any doubt returns False (keep monitoring).
    """
    try:
        import observer_liveness
        ok,_reason=observer_liveness.verify_observer_alive(
            **observer_liveness.production_adapters(PRIVATE,OBSERVER_EXPECTED_SHA256),
            max_age=OBSERVER_MAX_AGE)
        return ok is True
    except Exception:
        return False

INTEGRATION_INPUTS="atlas_integration_inputs"   # owner-private; not written yet
INPUT_FUNCTIONS=("policy_gate","policy_market","signed_income_envelope","stop_guard")

def _now_ms():
    return int(time.time()*1000)

def integration_inputs():
    """Owner-private adapters: PolicyGate (filters + signed income + ledger),
    market snapshot, signed income envelope, STOP guard journal/alarm/venue.
    Missing or incomplete -> fail closed; nothing is fabricated here."""
    import importlib
    try:
        mod=importlib.import_module(INTEGRATION_INPUTS)
    except Exception:
        raise Blocked("INTEGRATION_INPUTS_MISSING_FAIL_CLOSED") from None
    if mod is None or any(not callable(getattr(mod,n,None)) for n in INPUT_FUNCTIONS):
        raise Blocked("INTEGRATION_INPUTS_INCOMPLETE_FAIL_CLOSED")
    return mod

def _contract(fn,*a,**kw):
    """Contract errors keep their code: INTEGRATION:<code>."""
    from fast_r8_offline_integration_contract_F3F4_CANDIDATE import IntegrationBlocked
    try:
        return fn(*a,**kw)
    except IntegrationBlocked as e:
        raise Blocked("INTEGRATION:"+str(e)[:180]) from None

SETTLE_WAIT_S=65          # income rows may post after the last fill
SETTLE_ATTEMPTS=3
SETTLE_RETRY_S=30

def policy_settle_after_flat(gate,inputs,venue,bundle,scope,entry_filled_qty):
    """POLICY-SETTLE-001: settle ONLY with a signed FLAT read and the signed
    entry fill; income is fetched after the lag. Returns the settle result or
    None. On failure the ledger OPENED stays (entries blocked) and a durable
    incident is written; the WAL scope is NOT marked resolved."""
    symbol=bundle["entry"]["symbol"]
    last="NOT_ATTEMPTED"
    try:
        account,normal,algo=snapshot(venue)
        read_at=_now_ms()
        flat_proof={"symbol":symbol,"position_amt":format(exposure(account,symbol),"f"),
                    "open_orders":len(normal),"open_algo_orders":len(algo),
                    "read_at_ms":read_at}
    except Exception as e:
        flat_proof=None;last="FLAT_READ_FAILED:"+type(e).__name__
    if flat_proof is not None:
        time.sleep(SETTLE_WAIT_S)
        for attempt in range(SETTLE_ATTEMPTS):
            if attempt:time.sleep(SETTLE_RETRY_S)
            try:
                result=gate.settle(scope,symbol=symbol,entry_filled_qty=str(entry_filled_qty),
                                   flat_proof=flat_proof,
                                   envelope=inputs.signed_income_envelope(venue),
                                   local_now_ms=_now_ms())
            except Exception as e:
                last=str(e)[:120] or type(e).__name__
                continue
            if type(result) is dict and result.get("risk_exceeded") is True:
                # B3 (sahip 07.11.2026, secenek A): planlanan risk asimi defterde kayitli kalir;
                # giris kilidi yalniz gerceklesen kayip LOSS_LOCK_USDT'yi asarsa (ya da okunamazsa) acilir.
                try:loss=-Decimal(str(result.get("realized_pnl_usdt")))
                except Exception:loss=None
                if loss is None or not loss.is_finite() or loss>LOSS_LOCK_USDT:
                    emergency_note(scope,"REALIZED_LOSS_ABOVE_LOCK_THRESHOLD_OWNER_REVIEW")
            return result
    emergency_note(scope,"POLICY_SETTLE_PENDING_OPENED_KEPT:"+last)
    return None

def write_policy_reserve_record(scope,symbol,client_id):
    """POLICY-RESTART-002: durable link scope -> symbol + entry clientOrderId,
    written BEFORE the ledger reservation (O_EXCL, fsync). startup_reconcile
    treats it like any WAL scope (blocks until resolved_<scope>.json)."""
    if LOG.is_symlink():raise Blocked("JOURNAL_SYMLINK")
    LOG.mkdir(mode=0o700,exist_ok=True)
    st=LOG.stat()
    if st.st_uid!=501 or st.st_mode & 0o077:
        raise Blocked("JOURNAL_PERMISSIONS")
    raw=(json.dumps({"role":"POLICY_RESERVE","intent":scope,"at":time.time(),
                     "state":"RESERVATION_PENDING",
                     "detail":{"scope":scope,"symbol":symbol,"client_id":client_id}},
                    sort_keys=True)+"\n").encode()
    try:fd=os.open(LOG/("POLICY_RESERVE_"+scope+".json"),
                   os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:raise Blocked("POLICY_RESERVE_RECORD_EXISTS") from None
    with os.fdopen(fd,"wb") as out:
        out.write(raw);out.flush();os.fsync(out.fileno())
    dirfd=os.open(LOG,os.O_RDONLY)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)

def risk_ledger_entry_gate(phase,bundle,market,envelope,scope,gate=None):
    """F3: the real reservation (ledger OPENED) via the contract, only after
    the owner approved this exact bundle. The planned risk is the policy
    gate's COMPUTED risk_worst_usdt, never a constant."""
    write_policy_reserve_record(scope,bundle["entry"]["symbol"],
                                bundle["entry"]["newClientOrderId"])
    try:
        reservation=_contract(phase.reserve,bundle,market,envelope,
                              local_now_ms=_now_ms(),scope=scope)
    except Blocked:
        # Resolve the record ONLY if the ledger provably has no OPENED for it.
        try:
            still=[p for p in gate.pending_scopes() if p.get("scope")==scope]
        except Exception:
            still=None
        if still==[]:
            mark_scope_resolved(scope,"RESERVATION_REFUSED_NO_LEDGER_CHANGE")
        raise
    raw=reservation.get("risk_worst_usdt") if type(reservation) is dict else None
    if type(raw) is not str:
        raise Blocked("RESERVED_RISK_NOT_COMPUTED")
    try:risk=Decimal(raw)
    except Exception:raise Blocked("RESERVED_RISK_INVALID") from None
    if not risk.is_finite() or not Decimal("0")<risk<=MAX_RISK:
        raise Blocked("RESERVED_RISK_OUTSIDE_POLICY")
    return reservation

def _restart_wal_records():
    """Every *.json in the owner WAL dir; any unreadable file blocks."""
    if LOG.is_symlink():raise Blocked("RESTART_WAL_SYMLINK_KEEP_OPENED")
    if not LOG.exists():return {}
    out={}
    for f in LOG.iterdir():
        if not f.name.endswith(".json"):continue
        try:row=json.loads(f.read_text())
        except Exception:raise Blocked("RESTART_WAL_UNREADABLE_KEEP_OPENED") from None
        if type(row) is not dict:raise Blocked("RESTART_WAL_UNREADABLE_KEEP_OPENED")
        out[f.name]=row
    return out

def _restart_entry_fill(venue,records,scope,symbol,cid):
    """Signed entry status. No WAL ENTRY record -> never sent -> '0'."""
    name="ENTRY_"+hashlib.sha256((scope+"ENTRY"+cid).encode()).hexdigest()+".json"
    if name not in records:
        return "0"
    try:
        r=venue.read("query_order",symbol=symbol,orig_client_order_id=cid,recv_window=5000)
    except Exception:
        return None
    if type(r) is not dict or r.get("clientOrderId")!=cid or r.get("symbol")!=symbol:
        return None
    if r.get("status")!="FILLED" and r.get("status") not in TERMINAL_NOT_FILLED:
        return "NOT_TERMINAL"
    raw=r.get("executedQty")
    try:qty=Decimal(str(raw))
    except Exception:return None
    if type(raw) is not str or not qty.is_finite() or qty<0:
        return None
    return raw

def _restart_one(gate,inputs,venue,records,pending):
    scope=pending.get("scope")
    if pending.get("state")=="PNL_UNKNOWN":
        return "KEPT_OWNER_REVIEW_PNL_UNKNOWN"
    if pending.get("state")!="OPENED" or type(scope) is not str:
        return "KEPT_UNKNOWN_LEDGER_STATE"
    rec=records.get("POLICY_RESERVE_"+scope+".json") or {}
    d=rec.get("detail") if type(rec.get("detail")) is dict else {}
    symbol,cid=d.get("symbol"),d.get("client_id")
    if d.get("scope")!=scope or type(symbol) is not str or type(cid) is not str:
        return "KEPT_RESERVE_RECORD_MISSING"
    filled=_restart_entry_fill(venue,records,scope,symbol,cid)
    if filled is None:
        return "KEPT_ENTRY_STATUS_UNKNOWN"
    if filled=="NOT_TERMINAL":
        return "KEPT_ENTRY_NOT_TERMINAL"
    try:
        account,normal,algo=snapshot(venue)
        if exposure(account,symbol)!=0 or normal or algo:
            return "KEPT_POSITION_OR_ORDERS_OPEN"
    except Exception:
        return "KEPT_SIGNED_READ_FAILED"
    result=policy_settle_after_flat(gate,inputs,venue,{"entry":{"symbol":symbol}},scope,filled)
    if result is None:
        return "KEPT_SETTLE_REFUSED"
    if not (LOG/("resolved_"+scope+".json")).exists():
        mark_scope_resolved(scope,"RESTART_SETTLED_SIGNED_FLAT")
    return "SETTLED"

def restart_reconcile():
    """POLICY-RESTART-002: owner-run after a crash. Reads + PolicyGate.settle
    only; never sends, cancels or closes anything; never deletes incidents."""
    from fast_r8_offline_integration_contract_F3F4_CANDIDATE import production_entry
    production_entry()
    owner_only()
    inputs=integration_inputs()
    gate=inputs.policy_gate()
    try:
        pending=gate.pending_scopes()
    except Exception as e:
        raise Blocked("RESTART_LEDGER_UNREADABLE:"+str(e)[:120]) from None
    if type(pending) is not list:
        raise Blocked("RESTART_LEDGER_UNREADABLE:PENDING_NOT_LIST")
    if not pending:
        return {"status":"NO_PENDING_SCOPES","scopes":{}}
    records=_restart_wal_records()
    venue=SignedVenue()
    out={}
    try:
        for p in pending:
            out[p.get("scope")]=_restart_one(gate,inputs,venue,records,p)
    finally:
        venue.close()
    done=all(v=="SETTLED" for v in out.values())
    return {"status":"RECONCILED" if done else "KEPT_ENTRIES_BLOCKED","scopes":out}

class _R2DisabledGuardVenue:
    """Guard reads pass through; the emergency POST channel (R2) is closed.
    013 (port of V3COMPAT-010): declared to the guard, so it quarantines with
    MANUAL_CLOSE_REQUIRED instead of journaling/alarming a send it cannot make."""
    emergency_post_enabled=False
    def __init__(self,inner):self._inner=inner
    def __getattr__(self,name):
        if name=="post_reduce_only_market":
            raise AttributeError(name)
        return getattr(self._inner,name)
    def post_reduce_only_market(self,**kwargs):
        raise Blocked("R2_EMERGENCY_POST_CHANNEL_DISABLED")

R2_INPUT_FUNCTION="r2_emergency_channel"
def guard_write_venue(gvenue,inputs,bundle,scope=None):
    fn=getattr(inputs,R2_INPUT_FUNCTION,None) if inputs is not None else None
    if not callable(fn) or type(scope) is not str:
        return _R2DisabledGuardVenue(gvenue)
    try:
        channel=fn(bundle,scope)
        if channel is None or channel.emergency_post_enabled is not True:
            return _R2DisabledGuardVenue(gvenue)
        import r2_guard_venue
        return r2_guard_venue.GuardVenueWithR2(gvenue,channel)
    except Exception:
        return _R2DisabledGuardVenue(gvenue)

def stop_guard_reconcile(guard,scope,symbol,bundle,inputs=None):
    """F4 via the contract; R2 disabled -> at most alarm + quarantine."""
    import stop_ambiguity_guard as guard_mod
    from fast_r8_offline_integration_contract_F3F4_CANDIDATE import (
        require_guarded_stop_reconciliation)
    journal,alarm,gvenue=guard
    return _contract(require_guarded_stop_reconciliation,
        guard_write_venue(gvenue,inputs,bundle,scope),journal,guard_mod.guard_step,wal_dir=LOG,
        scope=scope,symbol=symbol,stop_id=bundle["stop"]["clientAlgoId"],
        tp_ids=[t["newClientOrderId"] for t in bundle["take_profits"]],
        sleep=time.sleep,alarm=alarm)

def restart_protection_gate(guard):
    """B6-019 (offline candidate): BEFORE the quarantine/startup gates, check
    STOP protection of every unresolved WAL scope with the real guard on the
    R2-DISABLED guard venue (never sends, cancels or deletes). Any scope not
    signed-FLAT, a failed read, an unreadable WAL or an alarm that could not be
    emitted/persisted -> Blocked: no new entry. Alarms are raised first."""
    import restart_coverage as rc_mod
    import stop_ambiguity_guard as guard_mod
    if type(guard) is not tuple or len(guard)!=3:
        raise Blocked("STOP_GUARD_INPUTS_INVALID")
    journal,alarm,gvenue=guard
    log=getattr(alarm,"log",None)
    rep=rc_mod.protection_gate(load_wal=_restart_wal_records,venue=_R2DisabledGuardVenue(gvenue),
        guard_step=guard_mod.guard_step,journal=journal,alarm=alarm,sleep=time.sleep,
        alarm_healthy=lambda:getattr(log,"healthy",None) is True)
    if type(rep) is not dict or rep.get("entries_allowed") is not True:
        rep=rep if type(rep) is dict else {}
        states=sorted({str(v) for v in (rep.get("protection") or {}).values()})
        raise Blocked("RESTART_PROTECTION_NOT_CLEAR_NO_ENTRY:wal=%s:states=%s:alarm_failures=%d"%(
            rep.get("wal"),",".join(states)[:80],len(rep.get("alarm_failures") or ())))
    return rep

_TELEMETRY_ARMED=False   # G56: set only by main() (real owner run); imports/tests never write

def _telemetry():
    """G56: owner-private append-only telemetry (separate from the WAL). None on any error
    and None unless main() armed it, so test harnesses never touch the owner directory."""
    if _TELEMETRY_ARMED is not True:
        return None
    try:
        import fast_telemetry_g56
        return fast_telemetry_g56.default(PRIVATE)
    except Exception:
        return None

def _telemetry_observe(role,wire,receipt,phase):
    """G56: record a signed order receipt (status/algoStatus, ids, type, reduceOnly).
    Never raises; never changes the receipt or the checks that follow."""
    try:
        t=_telemetry()
        if t is None:return
        cid=wire.get("clientAlgoId") if role=="STOP" else wire.get("newClientOrderId")
        if type(receipt) is dict:
            t.record_order_observation(role,receipt,phase=phase,client_id=cid)
        else:
            t.record_order_error(role,cid,code="RECEIPT_NOT_OBJECT",msg=type(receipt).__name__)
    except Exception:
        pass

def _tp_exposure_sleep(guard,bundle):
    """G56: supervision sleep that first runs the read-only TP-exposure audit
    (<= every 30 s, signed GETs via the guard venue). Alarm channel = guard[1]
    (same owner alarm as MANUAL_CLOSE_REQUIRED). Sends/cancels nothing; never raises;
    if the audit cannot be built, raises an alarm once and falls back to time.sleep."""
    try:
        import stop_ambiguity_guard as guard_mod
        import tp_exposure_monitor_g56 as tpm
        gvenue=guard[2]
        symbol=bundle["entry"]["symbol"]
        stop_id=bundle["stop"]["clientAlgoId"]
        tp_ids=[t["newClientOrderId"] for t in bundle["take_profits"]]
        own={stop_id:"STOP"}
        for i,tid in enumerate(tp_ids):own[tid]="TP%d"%(i+1)
        mon=tpm.TpExposureMonitor(alarm=guard[1],symbol=symbol,own_ids=own,telemetry=_telemetry())
        def read():
            return guard_mod.gather(gvenue,symbol=symbol,stop_id=stop_id,tp_ids=tp_ids)
    except Exception as e:
        try:guard[1]("TP_EXPOSURE_AUDIT_UNAVAILABLE:%s"%type(e).__name__)
        except Exception:pass
        return time.sleep
    def sleep(seconds):
        try:mon.maybe_audit(read)
        except Exception:pass
        time.sleep(seconds)
    sleep.monitor=mon
    return sleep

def make_coverage_monitor(guard,bundle,scope,observer_status,inputs=None):
    """B6-019: CoverageMonitor over the R2-DISABLED guard venue with the
    CONSERVATIVE CANDIDATE thresholds (not owner-approved). 037: run_once
    supervises the open position with it (supervise_until_flat); the guard
    is bound to the PLANNED STOP wire bundle["stop"] (036 G-1)."""
    import restart_coverage as rc_mod
    import stop_ambiguity_guard as guard_mod
    journal,alarm,gvenue=guard
    log=getattr(alarm,"log",None)
    symbol=bundle["entry"]["symbol"]
    def flat_probe():
        ev=guard_mod.gather(gvenue,symbol=symbol,stop_id=bundle["stop"]["clientAlgoId"],
            tp_ids=[t["newClientOrderId"] for t in bundle["take_profits"]])
        return guard_mod.classify(ev)[0] in ("FLAT","FLAT_EXITS_OPEN")
    return rc_mod.CoverageMonitor(venue=guard_write_venue(gvenue,inputs,bundle,scope),guard_step=guard_mod.guard_step,
        journal=journal,alarm=alarm,scope=scope,symbol=bundle["entry"]["symbol"],
        stop_id=bundle["stop"]["clientAlgoId"],
        tp_ids=[t["newClientOrderId"] for t in bundle["take_profits"]],
        observer_status=observer_status,alarm_healthy=lambda:getattr(log,"healthy",None) is True,
        expected_stop=bundle["stop"],flat_probe=flat_probe)

def observer_coverage_status():
    """037: heartbeat source for the coverage monitor = the independent
    observer's owner-private status file (launchd com.atlas.fast.stop-observer).
    Fresh only while MATCHED (it sees THIS owner STOP) and advancing."""
    import observer_liveness
    import restart_coverage as rc_mod
    ad=observer_liveness.production_adapters(PRIVATE,OBSERVER_EXPECTED_SHA256)
    return rc_mod.make_observer_status(ad["read_status"],observer_liveness._check_row,
        now_s=ad["now"],max_age_s=OBSERVER_MAX_AGE,expected_sha=OBSERVER_EXPECTED_SHA256)

def supervise_until_flat(guard,bundle,scope,inputs=None):
    """037 (Issue #4): supervision of the open position after STOP + TPs.
    Returns ONLY when the guard reports signed FLAT. R2 stays disabled: a lost
    / unplanned STOP -> MANUAL_CLOSE alarm + quarantine and supervision goes on
    until the owner has closed by hand. No observer handover, no silent exit.
    An unusable observer source does not stop supervision: it only makes the
    window unverified (DEAD heartbeat)."""
    import restart_coverage as rc_mod
    try:
        observer=observer_coverage_status()
    except Exception:
        observer=lambda:{"status":"DEAD","generated_at_ms":None,"reason":"OBSERVER_SOURCE_UNAVAILABLE"}
    monitor=make_coverage_monitor(guard,bundle,scope,observer,inputs)
    try:sleep=_tp_exposure_sleep(guard,bundle)   # G56: read-only TP audit; never blocks
    except Exception:sleep=time.sleep
    return rc_mod.supervise(monitor,sleep=sleep)

def flatten_or_supervise(venue,guard,bundle,scope,symbol,reason,inputs=None):
    """037 (Issue #4): emergency_flatten is fenced in production (R2 disabled ->
    STAGED raise). Instead of leaving an open position unattended, raise the
    MANUAL_CLOSE alarm and supervise until signed FLAT (the owner closes by
    hand). Returns True only if the (offline / future) flatten itself verified
    FLAT, False after a manual close was supervised to signed FLAT."""
    try:
        return emergency_flatten(venue,symbol,scope)
    except Exception as e:
        try:
            guard[1]("MANUAL_CLOSE_REQUIRED:%s:%s:%s"%(reason[:60],symbol,type(e).__name__))
        except Exception:
            pass
        supervise_until_flat(guard,bundle,scope,inputs)
        return False

def live_prerequisites(guard):
    """045 fail-closed: require reviewed observer, alarm and M-7 evidence."""
    if not isinstance(OBSERVER_EXPECTED_SHA256, str) or len(OBSERVER_EXPECTED_SHA256) != 64:
        raise Blocked("LIVE_PREREQ_OBSERVER_SHA_NOT_PINNED")
    if not tuple(getattr(guard[1], "sinks", ()) or ()):
        raise Blocked("LIVE_PREREQ_ALARM_CHANNEL_NOT_CONFIGURED")
    if not getattr(guard[2], "algo_not_found_codes", None):
        raise Blocked("LIVE_PREREQ_ALGO_NOT_FOUND_CODE_UNVERIFIED")
    # 045 G1 option A (owner decision 22.10.2026): reviewed observer must be ALIVE and see a FLAT account.
    try:
        import observer_liveness
        ok,why=observer_liveness.verify_observer_alive(
            **observer_liveness.production_adapters(PRIVATE,OBSERVER_EXPECTED_SHA256),
            max_age=OBSERVER_MAX_AGE,required_status=observer_liveness.FLAT)
    except Exception:
        ok,why=False,"LIVENESS_ERROR"
    if ok is not True:
        raise Blocked("LIVE_PREREQ_OBSERVER_NOT_ALIVE_FLAT:"+str(why)[:60])

def live_r2_prerequisite(guard,inputs,bundle,scope):
    """045 G2: the scope-bound R2 emergency channel must be ENABLED for this trade (owner-native
    activation) before ENTRY; otherwise no entry."""
    if type(guard_write_venue(guard[2],inputs,bundle,scope)).__name__!="GuardVenueWithR2":
        raise Blocked("LIVE_PREREQ_R2_NOT_ENABLED_FOR_SCOPE")

def run_once(bundle):
    """Invoked only by the owner handoff for ONE exact trade the owner approved on the paired iPhone."""
    # Integration candidate only: explicit fail-closed boundary before any owner or venue action.
    from fast_r8_offline_integration_contract_F3F4_CANDIDATE import (
        production_entry, PolicyPhase, validate_stop_guard_config)
    # Offline integration candidate: fail closed until signed adapters are present.
    # This block must remain before owner-only / exchange access.
    production_entry()
    owner_only()
    from exact_trade_approval import owner_phone_grant,consume_exact_grant,_canonical
    from sdk_signed_sender import signed_owned_stop_check
    inputs=integration_inputs()
    # F4: verified stop-guard configuration and no pending quarantine BEFORE
    # any venue access or owner dialog.
    import stop_ambiguity_guard as guard_mod
    guard=inputs.stop_guard()
    if type(guard) is not tuple or len(guard)!=3:
        raise Blocked("STOP_GUARD_INPUTS_INVALID")
    _contract(validate_stop_guard_config,guard[0],guard_mod.guard_step,
              wal_dir=LOG,alarm=guard[1])
    # B6-019: protection of every unresolved WAL scope BEFORE quarantine/startup gates.
    restart_protection_gate(guard)
    # 045: require reviewed live prerequisites before any venue access.
    live_prerequisites(guard)
    # 047: live pilot gate (symbols, trade count, cumulative loss) BEFORE venue access and owner grant.
    import live_pilot
    try:
        live_pilot.check_symbol(bundle["entry"]["symbol"])
        live_pilot.pilot_status()
    except live_pilot.PilotBlocked as e:
        raise Blocked("LIVE_PILOT:"+str(e)) from None
    ok,_pending=guard_mod.entry_allowed(guard[0])
    if ok is not True:
        raise Blocked("STOP_GUARD_QUARANTINE_PENDING_NO_ENTRY")
    scope,symbol,side,qty,stop,_=_canonical(bundle)
    startup_reconcile()
    venue=SignedVenue()
    try:
        account,normal,algo=snapshot(venue)
        if normal or algo or any(Decimal(str(p["positionAmt"]))!=0
                              for p in account["positions"]):
            raise Blocked("ACCOUNT_NOT_FLAT_NO_ENTRY")
        risk_authority(venue,bundle,account)
        # F3: policy preflight (filters, signed UTC PnL, ledger room; writes
        # nothing) BEFORE the owner is asked.
        gate=inputs.policy_gate()
        phase=_contract(PolicyPhase,gate)
        _contract(phase.preflight,bundle,inputs.policy_market(venue,bundle,account),
                  inputs.signed_income_envelope(venue),local_now_ms=_now_ms())
        # 046: the owner's signed iPhone APPROVE for exactly this bundle, consumed once in UID501 and
        # re-verified here (P-256, nonce, fingerprint, freshness); a JSON boolean or RPC reply never is.
        grant=owner_phone_grant(bundle)
        consumed=consume_exact_grant(grant,bundle)
        _contract(phase.owner_approved,bundle,grant_consumed=consumed)
        approved_at=time.monotonic()
        # 045 G3: owner-native ONE-USE trade authorization for exactly this bundle (consumed here),
        # G4: arm the venue with it, G2: the scope-bound R2 channel must be enabled. Nothing written yet.
        import exact_trade_approval as approval_mod
        live_auth=getattr(approval_mod,"owner_live_authorization",None)
        if not callable(live_auth):
            raise Blocked("LIVE_AUTH_SOURCE_MISSING")
        try:
            record=live_auth(grant,bundle)
        except Exception:
            raise Blocked("LIVE_AUTH_SOURCE_FAILED") from None
        from fast_r8_offline_integration_contract_F3F4_CANDIDATE import authorize_live_trade
        # one-use markers live OUTSIDE the WAL dir (startup_reconcile treats every WAL json as a row)
        venue.arm_trade(_contract(authorize_live_trade,scope,bundle,record,used_dir=LIVE_AUTH_USED_DIR))
        live_r2_prerequisite(guard,inputs,bundle,scope)
        # Verify position, open orders and price filters once more after
        # owner consent; a price move may invalidate the earlier preflight.
        account,normal,algo=snapshot(venue)
        if normal or algo or any(Decimal(str(p["positionAmt"]))!=0
                              for p in account["positions"]):
            raise Blocked("ACCOUNT_NOT_FLAT_AFTER_OWNER_APPROVAL")
        risk_authority(venue,bundle,account)
        if time.monotonic()-approved_at>15:
            raise Blocked("OWNER_APPROVAL_TIMED_OUT_BEFORE_ENTRY")
        # F3: real reservation right before ENTRY, computed risk only.
        risk_ledger_entry_gate(phase,bundle,inputs.policy_market(venue,bundle,account),
                               inputs.signed_income_envelope(venue),scope,gate=gate)
        try:
            entry_receipt=one_write(venue,"ENTRY",bundle["entry"],scope)
        except PreSendRefused:
            # nothing was sent: settle the reservation with zero fill
            policy_settle_after_flat(gate,inputs,venue,bundle,scope,"0")
            raise
        except Exception:
            emergency_note(scope,"ENTRY_WRITE_OR_RECEIPT_UNCERTAIN")
            # B1: no resend. Resolve the exact clientOrderId to a terminal
            # state; a late fill continues to STOP placement (protection).
            try:
                entry_receipt=resolve_uncertain_entry(venue,bundle["entry"])
            except Exception:
                try:
                    after,_,_=snapshot(venue)
                    open_now=exposure(after,symbol)!=0
                except Exception:
                    open_now=True              # unknown exposure: supervise, never leave
                if open_now:
                    flatten_or_supervise(venue,guard,bundle,scope,symbol,"ENTRY_UNRESOLVED",inputs)
                raise Blocked("ENTRY_UNRESOLVED_INCIDENT_LEFT_NO_RELEASE") from None
            if entry_receipt is None:
                policy_settle_after_flat(gate,inputs,venue,bundle,scope,"0")
                raise Blocked("ENTRY_NOT_EXECUTED_VERIFIED_NO_RETRY")
        # Market fill price can differ from the preflight mark. Abort and
        # flatten if the signed fill breaks the 0.30 USDT (costs incl.) risk
        # or the 100 USDT notional policy (F2).
        actual_fill=dec(entry_receipt["avgPrice"])
        if ((stop>=actual_fill) if side=="BUY" else (stop<=actual_fill)):
            emergency_note(scope,"ACTUAL_FILL_STOP_DIRECTION_INVALID")
            flatten_or_supervise(venue,guard,bundle,scope,symbol,"FILL_STOP_DIRECTION_INVALID",inputs)
            raise Blocked("ENTRY_FILL_STOP_DIRECTION_INVALID_FLATTENED")
        if fill_breaches_policy(side,qty,actual_fill,stop):
            emergency_note(scope,"ACTUAL_FILL_RISK_EXCEEDED")
            flatten_or_supervise(venue,guard,bundle,scope,symbol,"FILL_RISK_EXCEEDED",inputs)
            raise Blocked("ENTRY_FILL_RISK_EXCEEDED_FLATTENED")
        # Independently signed entry fill is required before STOP placement.
        try:
            place_stop_write(venue,bundle["stop"],scope)   # F1 bounded retry
            stop_proof=signed_owned_stop_check(
                stop_wire=bundle["stop"],shape_fn=stop_shape)
            if stop_proof.get("status")!="OWNER_SIGNED_PROTECTIVE_STOP_ACTIVE_AT_READ":
                raise Blocked("EXCHANGE_STOP_NOT_ACTIVE")
            publish_owner_stop_manifest(bundle,entry_receipt,scope)
        except Exception:
            emergency_note(scope,"STOP_ABSENT_OR_UNCERTAIN_AFTER_ENTRY")
            # F4: proof-based guard via the contract (SafeAlarm, shared lock);
            # R2 emergency POST disabled -> at most alarm + quarantine.
            try:
                stop_guard_reconcile(guard,scope,symbol,bundle,inputs)
            except Exception as guard_error:
                emergency_note(scope,"STOP_GUARD_RECONCILE_FAILED:"
                               +str(guard_error)[:80])
            flatten_or_supervise(venue,guard,bundle,scope,symbol,"STOP_ABSENT_OR_UNCERTAIN",inputs)
            cleanup_owned_exits(venue,bundle,scope)
            raise
        for role,wire in zip(("TP1","TP2"),bundle["take_profits"]):
            try:one_write(venue,role,wire,scope)
            except Exception:
                emergency_note(scope,role+"_UNVERIFIED_EMERGENCY_CLOSE")
                flatten_or_supervise(venue,guard,bundle,scope,symbol,role+"_UNVERIFIED",inputs)
                cleanup_owned_exits(venue,bundle,scope)
                raise Blocked("TP_UNKNOWN_POSITION_EMERGENCY_FLATTENED") from None
        # 037 (Issue #4): supervision = CoverageMonitor bound to the planned
        # STOP, observer heartbeat (MATCHED + advancing) and alarm-log health.
        # It returns only on signed FLAT; R2 stays disabled (STOP problem ->
        # MANUAL_CLOSE alarm, owner closes, supervision continues until FLAT).
        # A window that is not continuously verified leaves an incident: no
        # new entry until owner review (startup_reconcile).
        verified=True
        while True:
            rep=supervise_until_flat(guard,bundle,scope,inputs)
            if type(rep) is not dict or rep.get("state")!="FLAT":
                raise Blocked("SUPERVISION_ENDED_WITHOUT_SIGNED_FLAT")
            if rep.get("continuous_stop_coverage_verified") is not True:
                verified=False
                emergency_note(scope,"STOP_COVERAGE_NOT_CONTINUOUSLY_VERIFIED:"
                               +",".join(str(b) for b in rep.get("broken") or ())[:120])
            # the executor's own signed snapshot must agree before settling;
            # if it does not (or the read fails), supervision starts again.
            try:
                account,_,_=snapshot(venue)
                if exposure(account,symbol)==0:
                    break
                verified=False
                emergency_note(scope,"SUPERVISION_FLAT_NOT_CONFIRMED_BY_SNAPSHOT")
            except Exception:
                verified=False
                emergency_note(scope,"SUPERVISION_FLAT_SNAPSHOT_READ_FAILED")
            time.sleep(5)
        cleanup_owned_exits(venue,bundle,scope)
        settled=policy_settle_after_flat(gate,inputs,venue,bundle,scope,
                                         str(entry_receipt["executedQty"]))
        if settled is None:
            return "FLAT_POLICY_SETTLE_PENDING_NO_NEW_ENTRY"
        if verified is not True:
            return "FLAT_SETTLED_COVERAGE_NOT_VERIFIED_OWNER_REVIEW"
        mark_scope_resolved(scope,"FLAT_AND_OWNED_EXITS_VERIFIED_CLEARED")
        return "FLAT_AND_OWNED_EXITS_VERIFIED_CLEARED"
    finally:
        venue.close()

def main():
    # No trading or network side effects simply by importing this source.
    if len(sys.argv)!=3 or sys.argv[1]!="--exact-bundle":
        print("OWNER_PRIVATE_INSTALL_AND_EXACT_BUNDLE_ARGUMENT_REQUIRED")
        return 2
    owner_only()
    global _TELEMETRY_ARMED
    _TELEMETRY_ARMED=True   # G56: append-only telemetry for this real owner run only
    target=Path(sys.argv[2])
    if target.is_symlink() or target.parent!=PRIVATE or not target.is_file():
        raise Blocked("EXACT_OWNER_PRIVATE_BUNDLE_REQUIRED")
    st=target.stat()
    if st.st_uid!=501 or st.st_mode & 0o077 or not 0<st.st_size<7500:
        raise Blocked("UNTRUSTED_BUNDLE_FILE")
    if time.time()-st.st_mtime>90 or st.st_mtime>time.time()+3:
        raise Blocked("EXACT_BUNDLE_FILE_STALE")
    bundle=json.loads(target.read_text())
    lockfd=global_one_trade_lock()
    try:
        run_once(bundle)
    finally:
        fcntl.flock(lockfd,fcntl.LOCK_UN)
        os.close(lockfd)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
