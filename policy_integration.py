#!/usr/bin/env python3
"""ATLAS FAST: owner-policy integration layer (CANDIDATE, ATLAS-FAST-004).

Claude, 18.10.2026. Offline review candidate. No network, no orders, no API
key, no owner-private path. NOT wired into any executor yet: the GPT fenced R8
executor (K4) is not on GitHub. This module is the single seam the executor
will call; see reviews/CLAUDE_POLICY_INTEGRATION_CONTRACT_20261018.md.

Composes the existing candidates (all injected, nothing imported here):
  order_filters.symbol_rules + validate_bundle   1x / 100 USDT / 0.30 USDT
  risk_bridge.signed_utc_day_pnl                  HMAC-attested /fapi/v1/income
  utc_daily_pnl                                   UTC-day net from income rows
  risk_ledger.RiskLedger (+ risk_bridge.entry_gate) 5 USDT/UTC-day room,
                                                  one open trade, chain checks

Owner-confirmed limits (18.10.2026): 1x, notional <= 100 USDT worst case,
risk <= 0.30 USDT incl. slippage and fees, UTC-day loss <= 5 USDT. They live
in the filter/ledger code ceilings and can only be tightened.

NOT decided by the owner and therefore NOT chosen here: symbol / TP structure,
late fill, STOP-uncertain action, crash resume, 2-loss/24 h cooldown. NOTE: the
existing risk_ledger candidate enforces a cooldown (<= 2 losses, >= 24 h) and a
full cooldown after owner_init; that stays as-is (stricter) until decided.

Call order inside the executor (one trade):
  preflight()  before the owner approval dialog      (writes nothing)
  reserve()    after approval, right before ENTRY POST (ledger OPENED)
  settle()     only after a signed read proves FLAT and no open orders
               (normal end, verified non-execution, or restart reconcile)
  mark_unknown() if settle() cannot complete -> owner review required
Every failure raises PolicyBlocked("<LAYER>:<CODE>"); any unexpected error is
PolicyBlocked("POLICY_INTERNAL_ERROR:<type>"), never a pass.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

# Cost assumptions shared with V32_POLICY_CANDIDATE (5 / 10 / 5 bp). Not owner
# policy values; stricter (larger) is always allowed, smaller needs a decision.
COST_ASSUMPTIONS = {"entry_slippage_bps": "5", "stop_slippage_bps": "10",
                    "taker_fee_rate": "0.0005"}
SETTLE_LAG_MS = 60_000           # income rows may post after the fill
MAX_FLAT_PROOF_AGE_MS = 900_000  # signed FLAT read used for settle: <= 15 min old
MAX_CLOCK_AHEAD_MS = 5_000
SETTLE_TYPES = ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE",
                "INSURANCE_CLEAR", "LIQUIDATION_FEE")
LEDGER_SOURCE = "SIGNED_INCOME_REALIZED_PNL"


class PolicyBlocked(RuntimeError):
    pass


def _dec_exact(v):
    if isinstance(v, (bool, float)) or not isinstance(v, (str, int, Decimal)):
        return None
    try:
        x = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return x if x.is_finite() else None


class PolicyGate:
    def __init__(self, *, filters, bridge, utc, ledger, verify_tag):
        self.F, self.B, self.U = filters, bridge, utc
        self.ledger, self.verify_tag = ledger, verify_tag

    # ---- error normalisation ---------------------------------------------
    def _call(self, layer, fn, *a, **kw):
        known = tuple(c for c in (getattr(self.F, "FilterBlocked", None),
                                  getattr(self.B, "BridgeBlocked", None),
                                  getattr(self.U, "PnlBlocked", None),
                                  _ledger_error(self.ledger)) if c)
        try:
            return fn(*a, **kw)
        except PolicyBlocked:
            raise
        except known as e:
            raise PolicyBlocked("%s:%s" % (_layer_of(e, self, layer), e)) from None
        except OSError as e:
            if layer == "LEDGER":      # missing/unreadable ledger dir or file
                raise PolicyBlocked("LEDGER:LEDGER_UNREADABLE_%s" % type(e).__name__) from None
            raise PolicyBlocked("POLICY_INTERNAL_ERROR:%s" % type(e).__name__) from None
        except Exception as e:
            raise PolicyBlocked("POLICY_INTERNAL_ERROR:%s" % type(e).__name__) from None

    # ---- entry -------------------------------------------------------------
    def preflight(self, bundle, market, envelope, *, local_now_ms):
        return self._evaluate(bundle, market, envelope, local_now_ms=local_now_ms, scope=None)

    def reserve(self, bundle, market, envelope, *, local_now_ms, scope):
        if type(scope) is not str or not scope:
            raise PolicyBlocked("POLICY:SCOPE_REQUIRED")
        return self._evaluate(bundle, market, envelope, local_now_ms=local_now_ms, scope=scope)

    def _evaluate(self, bundle, market, envelope, *, local_now_ms, scope):
        if type(local_now_ms) is not int:
            raise PolicyBlocked("POLICY:LOCAL_TIME_MISSING")
        if type(market) is not dict:
            raise PolicyBlocked("POLICY:MARKET_SNAPSHOT_MISSING")
        lev = _dec_exact(market.get("signed_leverage"))
        if lev is None or lev != 3:                       # 047 live pilot: 3x isolated
            raise PolicyBlocked("POLICY:LEVERAGE_NOT_3X")
        if market.get("signed_isolated") is not True:
            raise PolicyBlocked("POLICY:MARGIN_NOT_ISOLATED")
        if type(bundle) is not dict or type(bundle.get("entry")) is not dict:
            raise PolicyBlocked("POLICY:BUNDLE_INVALID")
        symbol = bundle["entry"].get("symbol")
        rules = self._call("FILTER", self.F.symbol_rules, market.get("exchange_info"), symbol,
                           received_at_ms=market.get("exchange_info_received_ms"),
                           now_ms=local_now_ms)
        fr = self._call("FILTER", self.F.validate_bundle, bundle, rules,
                        mark=market.get("mark"), last_price=market.get("last_price"),
                        mark_time_ms=market.get("mark_time_ms"), now_ms=local_now_ms,
                        dual_side_position=market.get("dual_side_position"),
                        leverage=3, **COST_ASSUMPTIONS)
        pnl = self._signed_pnl(envelope, local_now_ms)
        decision = self._call("LEDGER", self.B.entry_gate, filter_result=fr, ledger=self.ledger,
                              signed_pnl=pnl, local_now_ms=local_now_ms, reserve_scope=scope)
        return {"risk_worst_usdt": fr["risk_worst_usdt"],
                "notional_worst_usdt": fr["notional_worst_usdt"],
                "exchange_day_net_usdt": pnl["net_usdt"],
                "pnl_provenance": pnl["provenance_sha256"],
                "reserved_scope": scope, "ledger": dict(decision)}

    def _signed_pnl(self, envelope, local_now_ms):
        if self.verify_tag is None:
            raise PolicyBlocked("PNL:SIGNED_ORIGIN_UNVERIFIABLE_NO_VERIFIER")
        return self._call("PNL", self.B.signed_utc_day_pnl, envelope, verify_tag=self.verify_tag,
                          local_now_ms=local_now_ms, utc_adapter=self.U)

    # ---- after the trade -----------------------------------------------------
    def settle(self, scope, *, symbol, entry_filled_qty, flat_proof, envelope, local_now_ms):
        """POLICY-SETTLE-001 (19.10.2026). Close the ledger OPENED scope ONLY
        with a definite close proof and verified realized PnL. Every refusal
        happens before the ledger is touched (OPENED stays -> entries blocked).

        * trade window = [OPENED.at_ms of THIS scope from the ledger itself,
          flat read]; a caller-supplied start is not accepted;
        * flat_proof (from the executor's signed reads): position exactly 0,
          zero open normal and algo orders, same symbol, read after OPENED,
          not older than MAX_FLAT_PROOF_AGE_MS, at least SETTLE_LAG_MS before
          the attested income envelope's exchange time;
        * envelope: HMAC-attested, complete /fapi/v1/income (signed_utc_day_pnl)
          whose first page starts at or before OPENED.at_ms;
        * entry_filled_qty (signed entry receipt, decimal string): > 0 needs
          at least one REALIZED_PNL row in the window; 0 must have no trading
          income for the symbol in the window;
        * trading income for the symbol AFTER the flat read -> refuse;
        * repeated settle with identical evidence -> no new event
          (already_settled=True); different evidence -> ledger conflict;
        * a realized loss above the OPENED planned risk is recorded (never
          hidden) and returned as risk_exceeded=True."""
        if type(local_now_ms) is not int:
            raise PolicyBlocked("SETTLE:TIMES_MISSING")
        if type(scope) is not str or not scope:
            raise PolicyBlocked("SETTLE:SCOPE_INVALID")
        filled = _dec_exact(entry_filled_qty) if type(entry_filled_qty) is str else None
        if filled is None or filled < 0:
            raise PolicyBlocked("SETTLE:ENTRY_FILLED_QTY_INVALID")
        opened, closed = self._scope_events(scope)
        opened_at = opened["at_ms"]
        if type(flat_proof) is not dict:
            raise PolicyBlocked("SETTLE:FLAT_PROOF_MISSING")
        amt = _dec_exact(flat_proof.get("position_amt")) if type(flat_proof.get("position_amt")) is str else None
        read_at = flat_proof.get("read_at_ms")
        if (amt is None or amt != 0 or type(flat_proof.get("open_orders")) is not int
                or flat_proof.get("open_orders") != 0
                or type(flat_proof.get("open_algo_orders")) is not int
                or flat_proof.get("open_algo_orders") != 0 or flat_proof.get("symbol") != symbol):
            raise PolicyBlocked("SETTLE:NOT_FLAT_OR_ORDERS_OPEN")
        if type(read_at) is not int or read_at < opened_at:
            raise PolicyBlocked("SETTLE:FLAT_READ_BEFORE_OPEN")
        if not -MAX_CLOCK_AHEAD_MS <= local_now_ms - read_at <= MAX_FLAT_PROOF_AGE_MS:
            raise PolicyBlocked("SETTLE:FLAT_PROOF_STALE_OR_FUTURE")
        self._signed_pnl(envelope, local_now_ms)          # integrity + completeness
        pages = envelope["pages"]
        if pages[0]["startTime"] > opened_at:
            raise PolicyBlocked("SETTLE:INCOME_WINDOW_STARTS_AFTER_OPEN")
        if envelope["exchange_server_time_ms"] < read_at + SETTLE_LAG_MS:
            raise PolicyBlocked("SETTLE:INCOME_LAG_NOT_ELAPSED")
        net, seen, realized_rows, trading_rows = Decimal("0"), set(), 0, 0
        for r in envelope["rows"]:
            if r.get("symbol") != symbol or r["incomeType"] not in SETTLE_TYPES:
                continue
            if r["time"] > read_at:
                raise PolicyBlocked("SETTLE:INCOME_AFTER_FLAT_UNEXPLAINED")
            if r["time"] < opened_at:
                continue
            key = (r.get("tranId"), r.get("incomeType"))
            if key in seen:
                continue                                  # exact duplicate (adapter checked)
            seen.add(key)
            trading_rows += 1
            realized_rows += r["incomeType"] == "REALIZED_PNL"
            net += Decimal(r["income"])
        if filled > 0 and realized_rows == 0:
            raise PolicyBlocked("SETTLE:REALIZED_PNL_MISSING_FOR_EXECUTED_ENTRY")
        if filled == 0 and trading_rows:
            raise PolicyBlocked("SETTLE:INCOME_FOR_UNEXECUTED_ENTRY")
        event = self._call("LEDGER", self.ledger.record_close, scope, realized_pnl_usdt=str(net),
                           close_time_ms=read_at, source=LEDGER_SOURCE)
        planned = Decimal(opened.get("planned_risk_usdt", "0"))
        loss = -net if net < 0 else Decimal("0")
        return {"event": event, "realized_pnl_usdt": str(net), "already_settled": closed is not None,
                "planned_risk_usdt": str(planned), "risk_exceeded": loss > planned}

    def pending_scopes(self):
        """POLICY-RESTART-002: read-only list of unfinished ledger scopes
        (validated chain, no lock, no write): OPENED without CLOSED, and
        PNL_UNKNOWN not followed by a CLOSED for that scope."""
        events = self._call("LEDGER", self.ledger._events)
        out = []
        closed_seq = {e["scope"]: e["seq"] for e in events if e.get("type") == "CLOSED"}
        for e in events:
            if e.get("type") == "OPENED" and e["scope"] not in closed_seq:
                out.append({"scope": e["scope"], "state": "OPENED", "at_ms": e.get("at_ms"),
                            "planned_risk_usdt": e.get("planned_risk_usdt")})
        unknown = {}
        for e in events:
            if e.get("type") == "PNL_UNKNOWN":
                unknown[e["scope"]] = e
        for scope, e in sorted(unknown.items()):
            if closed_seq.get(scope, -1) < e["seq"]:
                out.append({"scope": scope, "state": "PNL_UNKNOWN", "at_ms": e.get("at_ms"),
                            "planned_risk_usdt": None})
        return out

    def _scope_events(self, scope):
        """Read-only view of this scope in the validated ledger chain."""
        events = self._call("LEDGER", self.ledger._events)
        opened = [e for e in events if e.get("type") == "OPENED" and e.get("scope") == scope]
        closed = [e for e in events if e.get("type") == "CLOSED" and e.get("scope") == scope]
        if len(opened) != 1:
            raise PolicyBlocked("SETTLE:SCOPE_NOT_OPEN_IN_LEDGER")
        if type(opened[0].get("at_ms")) is not int:
            raise PolicyBlocked("SETTLE:OPENED_EVENT_INVALID")
        return opened[0], (closed[0] if closed else None)

    def mark_unknown(self, scope, *, reason, at_ms):
        return self._call("LEDGER", self.ledger.record_pnl_unknown, scope, reason=reason, at_ms=at_ms)


def _ledger_error(ledger):
    """RiskBlocked of the module that actually defined this ledger's class
    (its globals), not whatever currently sits under that name in sys.modules."""
    if ledger is None:
        return None
    init = getattr(type(ledger), "__init__", None)
    return getattr(init, "__globals__", {}).get("RiskBlocked")


def _layer_of(exc, gate, default):
    if isinstance(exc, getattr(gate.F, "FilterBlocked", ())):
        return "FILTER"
    if isinstance(exc, getattr(gate.U, "PnlBlocked", ())):
        return "PNL"
    if isinstance(exc, getattr(gate.B, "BridgeBlocked", ())):
        # bridge errors from envelope checks are PNL; from the ledger snapshot LEDGER
        return default
    return "LEDGER"
