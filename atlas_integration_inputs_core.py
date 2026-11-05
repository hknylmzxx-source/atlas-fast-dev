#!/usr/bin/env python3
"""ATLAS FAST — integration inputs (B1 + B3 wiring), OFFLINE CANDIDATE.

Claude, 19.10.2026, INTEGRATION-INPUTS-020. Future install name (owner-private):
`atlas_integration_inputs` — the module the R8 F3F4 executor imports in
integration_inputs() and calls as:

  policy_gate()                          -> PolicyGate(filters, bridge, utc, RiskLedger, verify_tag)
  policy_market(venue, bundle, account)  -> market snapshot dict for PolicyGate
  signed_income_envelope(venue)          -> HMAC-tagged /fapi/v1/income envelope (UTC day)
  stop_guard()                           -> (Journal, SafeAlarm, SignedGuardVenue)

Nothing here is owner-private and nothing is fabricated. Every owner-private
piece is INJECTED once through configure(): module objects, WAL / guard /
alarm / ledger directories, the pinned policy file + sha256, the HMAC key
SOURCE (a callable, never a key literal), alarm sinks, the signed SDK client
factory for guard reads, the SDK BadRequest type. Until configure() succeeds
every function raises InputsBlocked("INPUTS_NOT_CONFIGURED_FAIL_CLOSED").
production_config() is deliberately not installed (B1/B8 = owner install).

Safety properties (tested offline):
  * reads only: venue.read() on an allowlist of SDK GET method names and
    venue.public() for premiumIndex / exchangeInfo; no POST/DELETE path exists;
  * the guard venue is SignedGuardVenue (typed signed reads) WITHOUT any
    emergency POST method; the executor additionally wraps it with
    _R2DisabledGuardVenue, so R2 stays DISABLED twice;
  * risk values are NOT set here: leverage / isolated / limits come from the
    signed account and the pinned policy, PolicyGate enforces them unchanged;
  * every shape surprise (e.g. snake_case keys from a decoder without aliases,
    an un-unwrapped oneOf ticker, a None-dropped income field) -> InputsBlocked
    or PolicyBlocked, never a guess.
"""
from __future__ import annotations
import importlib, os, re, stat, time
from pathlib import Path

GUARD_INSTALL = "stop_ambiguity_guard"     # = contract GUARD_MODULE
ALARM_INSTALL = "alarm_quarantine"         # = contract ALARM_MODULE
EMERGENCY_LOCK_NAME = "owner_emergency_intervention.lock"
MODULE_KEYS = ("filters", "bridge", "utc", "ledger", "policy", "income", "guard", "alarm", "guard_venue")
READ_NAMES = frozenset(("get_current_position_mode", "check_server_time",
                        "get_income_history", "symbol_configuration"))      # PROPOSAL-021
# 023: last price via the executor's pinned RAW public channel. The owner V3
# refuses the SDK's oneOf SymbolPriceTickerResponse (SDK_SIGNED_RESPONSE_TYPE_NOT_VERIFIED).
PUBLIC_ENDPOINTS = frozenset(("/fapi/v1/premiumIndex", "/fapi/v1/exchangeInfo", "/fapi/v1/ticker/price"))
INCOME_LIMIT = 1000
DAY_MS = 86_400_000
MAX_SERVER_SKEW_MS = 5_000
# 024 (D-023): SDK 17.6.0 decodes LIST responses with `model_validate` (pydantic extra="ignore"), so an
# income field Binance adds is silently DROPPED before any ATLAS code sees it. Default: refuse SDK-decoded
# income unless a raw reader is present (strict key check) or the owner explicitly accepts the SDK loss.
INCOME_DECODE_POLICIES = ("REQUIRE_RAW_READER", "SDK_LIST_DECODE_ACCEPTED_BY_OWNER")
_SYMBOL = re.compile(r"[A-Z0-9]{2,30}USDT")
_SHA = re.compile(r"[0-9a-f]{64}")

_CFG = None


class InputsBlocked(RuntimeError):
    pass


def production_config():
    """The owner's private install calls configure() with real paths/sources.
    Not part of this candidate -> fail closed."""
    raise InputsBlocked("OWNER_PRIVATE_CONFIG_NOT_INSTALLED_FAIL_CLOSED")


def _trusted_dir(p, uid, what):
    p = Path(p) if isinstance(p, (str, Path)) else None
    if p is None or not p.is_absolute():
        raise InputsBlocked("CONFIG_%s_NOT_ABSOLUTE" % what)
    try:
        st = os.lstat(p)
    except OSError:
        raise InputsBlocked("CONFIG_%s_MISSING" % what) from None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o077:
        raise InputsBlocked("CONFIG_%s_UNTRUSTED" % what)
    return p


def configure(*, modules, wal_dir, guard_journal_dir, alarm_dir, ledger_root, policy_path, policy_sha256,
              hmac_key_source, alarm_sinks, guard_client_factory, bad_request_type, uid=501, now_ms=None,
              income_decode_policy="REQUIRE_RAW_READER", income_raw_reader=None,
              r2_activation_path=None, r2_marker_dir=None, r2_fence=None, r2_writer=None,
              r2_activation_source=None, algo_not_found_evidence=None):
    """Once per process. Validates everything it can without I/O to the venue."""
    global _CFG
    if _CFG is not None:
        raise InputsBlocked("ALREADY_CONFIGURED")
    if type(uid) is not int or uid < 0:
        raise InputsBlocked("CONFIG_UID_INVALID")
    if type(modules) is not dict or set(modules) != set(MODULE_KEYS) or any(m is None for m in modules.values()):
        raise InputsBlocked("CONFIG_MODULES_INVALID")
    need = {"filters": ("symbol_rules", "validate_bundle"), "bridge": ("signed_utc_day_pnl", "entry_gate"),
            "utc": ("utc_day_realized_pnl",), "ledger": ("RiskLedger", "load_policy"), "policy": ("PolicyGate",),
            "income": ("make_verify_tag", "collect_pages", "build_signed_income_envelope"),
            "guard": ("Journal", "guard_step", "OrderNotFound"), "alarm": ("SafeAlarm", "AlarmLog"),
            "guard_venue": ("SignedGuardVenue",)}
    for k, names in need.items():
        if any(getattr(modules[k], n, None) is None for n in names):
            raise InputsBlocked("CONFIG_MODULE_%s_INCOMPLETE" % k.upper())
    cfg = {"modules": dict(modules), "uid": uid,
           "wal_dir": _trusted_dir(wal_dir, uid, "WAL_DIR"),
           "guard_journal_dir": _trusted_dir(guard_journal_dir, uid, "GUARD_DIR"),
           "alarm_dir": _trusted_dir(alarm_dir, uid, "ALARM_DIR"),
           "ledger_root": _trusted_dir(ledger_root, uid, "LEDGER_DIR")}
    pp = Path(policy_path) if isinstance(policy_path, (str, Path)) else None
    if pp is None or not pp.is_absolute() or pp.is_symlink():
        raise InputsBlocked("CONFIG_POLICY_PATH_INVALID")
    if type(policy_sha256) is not str or not _SHA.fullmatch(policy_sha256):
        raise InputsBlocked("CONFIG_POLICY_SHA_INVALID")
    if not callable(hmac_key_source) or isinstance(hmac_key_source, (bytes, str)):
        raise InputsBlocked("CONFIG_HMAC_KEY_SOURCE_NOT_CALLABLE")
    if type(alarm_sinks) is not tuple or any(not callable(s) for s in alarm_sinks):
        raise InputsBlocked("CONFIG_ALARM_SINKS_INVALID")
    if not callable(guard_client_factory):
        raise InputsBlocked("CONFIG_GUARD_CLIENT_FACTORY_INVALID")
    if not (isinstance(bad_request_type, type) and issubclass(bad_request_type, Exception)):
        raise InputsBlocked("CONFIG_BAD_REQUEST_TYPE_INVALID")
    if income_decode_policy not in INCOME_DECODE_POLICIES:
        raise InputsBlocked("CONFIG_INCOME_DECODE_POLICY_INVALID")
    if income_raw_reader is not None and not callable(income_raw_reader):
        raise InputsBlocked("CONFIG_INCOME_RAW_READER_INVALID")
    if now_ms is not None and not callable(now_ms):
        raise InputsBlocked("CONFIG_CLOCK_INVALID")
    # 038 DRAFT: R2 is configured only if ALL four are given; partial -> refused (never "half on")
    r2 = (r2_activation_path, r2_marker_dir, r2_fence, r2_writer)
    if any(x is not None for x in r2):
        if any(x is None for x in r2):
            raise InputsBlocked("CONFIG_R2_PARTIAL")
        ap = Path(r2_activation_path) if isinstance(r2_activation_path, (str, Path)) else None
        if ap is None or not ap.is_absolute():
            raise InputsBlocked("CONFIG_R2_ACTIVATION_PATH_INVALID")
        if not callable(r2_writer) or any(not callable(getattr(r2_fence, n, None))
                                           for n in ("request_emergency", "emergency_exclusive")):
            raise InputsBlocked("CONFIG_R2_FENCE_OR_WRITER_INVALID")
        if r2_activation_source is not None and not callable(r2_activation_source):
            raise InputsBlocked("CONFIG_R2_ACTIVATION_SOURCE_INVALID")
        # 044: the R2 channel must latch the SAME owner mutation fence as the executor (shared WAL)
        fdir = getattr(r2_fence, "directory", None)
        if not isinstance(fdir, (str, Path)) or Path(fdir) != cfg["wal_dir"]:
            raise InputsBlocked("CONFIG_R2_FENCE_NOT_SHARED_WAL")
        r2 = (ap, _trusted_dir(r2_marker_dir, uid, "R2_MARKER_DIR"), r2_fence, r2_writer, r2_activation_source)
    else:
        r2 = None
    # 044: algo "not found" code only from owner-private R-1 evidence (documented -2013 pair)
    if algo_not_found_evidence is None:
        algo_codes = frozenset()
    else:
        ep = Path(algo_not_found_evidence) if isinstance(algo_not_found_evidence, (str, Path)) else None
        if ep is None or not ep.is_absolute():
            raise InputsBlocked("CONFIG_ALGO_EVIDENCE_PATH_INVALID")
        algo_codes = modules["guard_venue"].load_algo_not_found_evidence(ep, expected_uid=uid)
        if not algo_codes:
            raise InputsBlocked("CONFIG_ALGO_EVIDENCE_INVALID")
    cfg.update(income_decode_policy=income_decode_policy, income_raw_reader=income_raw_reader, policy_path=pp, policy_sha256=policy_sha256, hmac_key_source=hmac_key_source,
               alarm_sinks=alarm_sinks, guard_client_factory=guard_client_factory,
               bad_request_type=bad_request_type, now_ms=now_ms or (lambda: int(time.time() * 1000)),
               r2=r2, algo_codes=algo_codes)
    _CFG = cfg


def _cfg():
    if _CFG is None:
        raise InputsBlocked("INPUTS_NOT_CONFIGURED_FAIL_CLOSED")
    return _CFG


def _read(venue, name, **kw):
    if name not in READ_NAMES:
        raise InputsBlocked("READ_NOT_ALLOWED:" + name)
    return venue.read(name, **kw)


def sdk_income_row(row):
    """023: one income row as decoded by the owner V3 (pydantic model_dump(by_alias=True,
    exclude_none=True)), normalized ONLY to what SDK to_dict() would give for a row WITHOUT
    unknown fields: an EMPTY `additional_properties` dict is dropped. Anything else stays
    strict: a NON-empty additional_properties (Binance sent fields the SDK model does not
    know) or a non-dict one is refused; a dropped None field (e.g. tradeId) is NOT invented
    and the envelope then refuses the row (ROW_FIELDS_INVALID). Input is not modified."""
    if type(row) is not dict:
        raise InputsBlocked("INCOME_ROW_NOT_DICT")
    if "additional_properties" not in row:
        return dict(row)
    extra = row["additional_properties"]
    if type(extra) is not dict:
        raise InputsBlocked("INCOME_ROW_ADDITIONAL_PROPERTIES_INVALID")
    if extra:
        raise InputsBlocked("INCOME_ROW_UNKNOWN_FIELDS:%s" % ",".join(sorted(map(str, extra)))[:60])
    return {k: v for k, v in row.items() if k != "additional_properties"}


def _public(venue, endpoint, symbol):
    if endpoint not in PUBLIC_ENDPOINTS:
        raise InputsBlocked("PUBLIC_NOT_ALLOWED")
    return venue.public(endpoint, symbol)


# ---- PolicyGate --------------------------------------------------------------

def policy_gate():
    c = _cfg(); M = c["modules"]
    ledger = M["ledger"].RiskLedger(c["ledger_root"],
                                    M["ledger"].load_policy(c["policy_path"], c["policy_sha256"], uid=c["uid"]),
                                    uid=c["uid"])
    return M["policy"].PolicyGate(filters=M["filters"], bridge=M["bridge"], utc=M["utc"], ledger=ledger,
                                  verify_tag=M["income"].make_verify_tag(c["hmac_key_source"]))


# ---- market snapshot ---------------------------------------------------------

def policy_market(venue, bundle, account):
    c = _cfg()
    entry = bundle.get("entry") if type(bundle) is dict else None
    symbol = entry.get("symbol") if type(entry) is dict else None
    if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
        raise InputsBlocked("MARKET_SYMBOL_INVALID")
    positions = account.get("positions") if type(account) is dict else None
    rows = [p for p in positions or () if type(p) is dict and p.get("symbol") == symbol]
    # 055 (28.10): /fapi/v3/account omits flat symbols -> 0 rows is a valid signed FLAT.
    if (type(positions) is not list or len(rows) > 1
            or (rows and rows[0].get("positionSide") not in ("BOTH", None))):
        raise InputsBlocked("MARKET_SIGNED_POSITION_ROW_INVALID")
    # PROPOSAL-021: /fapi/v3/account rows carry no leverage / margin type -> signed symbolConfig.
    cfg = _read(venue, "symbol_configuration", symbol=symbol, recv_window=5000)
    cfg = [r for r in cfg if type(r) is dict and r.get("symbol") == symbol] if type(cfg) is list else []
    if len(cfg) != 1 or type(cfg[0].get("leverage")) is not int or type(cfg[0].get("marginType")) is not str:
        raise InputsBlocked("MARKET_SYMBOL_CONFIG_INVALID")
    mode = _read(venue, "get_current_position_mode", recv_window=5000)
    if type(mode) is not dict or type(mode.get("dualSidePosition")) is not bool:
        raise InputsBlocked("MARKET_POSITION_MODE_INVALID")
    info = _public(venue, "/fapi/v1/exchangeInfo", symbol)
    info_at = c["now_ms"]()
    if type(info) is not dict:
        raise InputsBlocked("MARKET_EXCHANGE_INFO_INVALID")
    mk = _public(venue, "/fapi/v1/premiumIndex", symbol)
    if type(mk) is not dict or mk.get("symbol") != symbol or type(mk.get("time")) is not int \
            or type(mk.get("markPrice")) is not str:
        raise InputsBlocked("MARKET_MARK_INVALID")
    tk = _public(venue, "/fapi/v1/ticker/price", symbol)
    if type(tk) is list:
        tk = [t for t in tk if type(t) is dict and t.get("symbol") == symbol]
        tk = tk[0] if len(tk) == 1 else None
    if type(tk) is not dict or tk.get("symbol") != symbol or type(tk.get("price")) is not str:
        raise InputsBlocked("MARKET_LAST_PRICE_INVALID")
    return {"exchange_info": info, "exchange_info_received_ms": info_at,
            "mark": mk["markPrice"], "mark_time_ms": mk["time"], "last_price": tk["price"],
            "dual_side_position": mode["dualSidePosition"], "signed_leverage": str(cfg[0]["leverage"]),
            "signed_isolated": cfg[0]["marginType"] == "ISOLATED"}


# ---- signed income envelope (UTC day of exchange time) -------------------------

def signed_income_envelope(venue):
    c = _cfg(); I = c["modules"]["income"]
    srv = _read(venue, "check_server_time")
    server = srv.get("serverTime") if type(srv) is dict else None
    if type(server) is not int or server <= 0:
        raise InputsBlocked("INCOME_SERVER_TIME_INVALID")
    if abs(c["now_ms"]() - server) > MAX_SERVER_SKEW_MS:
        raise InputsBlocked("INCOME_CLOCK_SKEW")
    day_start = server - server % DAY_MS
    if server == day_start:
        raise InputsBlocked("INCOME_AT_UTC_DAY_BOUNDARY_RETRY")

    refused = []          # keep the precise refusal code (collect_pages would mask it as FETCH_FAILED)
    # 025: configured raw reader (raw_income_reader.make_reader(owner_keychain_bind)) wins; a venue
    # attribute is accepted too. Raw rows: NO normalization -> any unknown key is refused.
    raw_reader = c["income_raw_reader"] or getattr(venue, "read_income_raw", None)
    if callable(raw_reader):
        def page(p):
            try:
                return raw_reader(start_time=p["startTime"], end_time=p["endTime"], limit=p["limit"])
            except Exception as e:
                raise InputsBlocked("INCOME_RAW:%s" % str(e)[:100]) from None
        normalize = dict
    elif c["income_decode_policy"] == "SDK_LIST_DECODE_ACCEPTED_BY_OWNER":
        def page(p):
            return _read(venue, "get_income_history", start_time=p["startTime"], end_time=p["endTime"],
                         limit=p["limit"], recv_window=5000)
        normalize = sdk_income_row
    else:
        raise InputsBlocked("INCOME_UNKNOWN_FIELDS_UNDETECTABLE_VIA_SDK_LIST_DECODE_D023")

    def fetch_page(p):
        try:
            rows = page(p)
            if type(rows) is not list:
                raise InputsBlocked("INCOME_PAGE_NOT_LIST")
            if any(type(r) is not dict for r in rows):
                raise InputsBlocked("INCOME_ROW_NOT_DICT")
            return [normalize(r) for r in rows]
        except InputsBlocked as e:
            refused.append(e)
            raise
    try:
        fetched = I.collect_pages(fetch_page, start_ms=day_start, end_ms=server, limit=INCOME_LIMIT)
        return I.build_signed_income_envelope(fetched, received_at_ms=c["now_ms"](), exchange_server_time_ms=server,
                                              key=c["hmac_key_source"], must_cover_from_ms=day_start)
    except InputsBlocked:
        raise
    except Exception as e:
        if refused:
            raise refused[-1] from None
        raise InputsBlocked("INCOME:%s" % str(e)[:80]) from None


# ---- STOP guard inputs (B3) ----------------------------------------------------

def stop_guard():
    c = _cfg(); M = c["modules"]
    try:
        installed_guard = importlib.import_module(GUARD_INSTALL)
        installed_alarm = importlib.import_module(ALARM_INSTALL)
    except Exception:
        raise InputsBlocked("STOP_GUARD_MODULES_NOT_INSTALLED") from None
    if installed_guard is not M["guard"] or installed_alarm is not M["alarm"]:
        raise InputsBlocked("STOP_GUARD_MODULE_NOT_PINNED_INSTALL")
    G, A = M["guard"], M["alarm"]
    journal = G.Journal(c["guard_journal_dir"], uid=c["uid"], lock_path=c["wal_dir"] / EMERGENCY_LOCK_NAME)
    alarm = A.SafeAlarm(A.AlarmLog(c["alarm_dir"], uid=c["uid"]), sinks=c["alarm_sinks"])
    try:
        client = c["guard_client_factory"]()
    except Exception:
        raise InputsBlocked("STOP_GUARD_SIGNED_CLIENT_UNAVAILABLE") from None
    gvenue = M["guard_venue"].SignedGuardVenue(client, order_not_found=G.OrderNotFound,
                                               bad_request_type=c["bad_request_type"], now_ms=c["now_ms"],
                                               algo_not_found_codes=c.get("algo_codes", frozenset()))
    if hasattr(gvenue, "post_reduce_only_market"):
        raise InputsBlocked("STOP_GUARD_VENUE_HAS_WRITE_CHANNEL")
    return (journal, alarm, gvenue)


# ---- R2 emergency channel (038 DRAFT; optional, disabled unless fully configured) ----------

def r2_emergency_channel(bundle, scope=None):
    """Per-trade R2 channel or None. emergency_post_enabled stays False unless the
    owner-private activation record is valid at call time AND a real writer is
    connected; the quantity is capped at THIS bundle's entry quantity."""
    c = _cfg()
    if c.get("r2") is None or type(scope) is not str:
        return None                                  # 039: no trade scope -> no channel
    ap, marker_dir, fence, writer, source = c["r2"]
    try:
        qty = bundle["entry"]["quantity"]
    except Exception:
        raise InputsBlocked("R2_BUNDLE_INVALID") from None
    return c["modules"]["guard_venue"].R2EmergencyChannel(
        activation_path=ap, fence=fence, marker_dir=marker_dir, writer=writer,
        expected_uid=c["uid"], quantity_cap=qty, scope=scope, activation_source=source)
