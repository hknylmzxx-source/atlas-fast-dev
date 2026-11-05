from datetime import datetime, timezone, timedelta
import unittest
from owner_handoff_contract_offline import classify_handoff,OwnerHandoffContractError

NOW=datetime(2026,11,5,14,14,20,tzinfo=timezone.utc)
def state(**overrides):
    x={"generated_at":NOW.isoformat(),"schema":"ATLAS_OWNER_CONTINUOUS_SIGNAL_HANDOFF_V1",
       "owner_uid":501,"status":"WAITING_ELIGIBLE_SIGNAL",
       "owner_native_trade_click_required":False,"phone_live_order_release_enabled":True,
       "unattended_order_post_permission":False,
       "unattended_independent_stop_close_allowed":False}
    x.update(overrides)
    return x

class ContractTests(unittest.TestCase):
    def test_phone_route_observed_but_never_authorized(self):
        r=classify_handoff(state(),now=NOW)
        self.assertTrue(r["handoff_running"])
        self.assertEqual(r["approval_route"],"PHONE")
        self.assertFalse(r["live_order_authorized"])
    def test_native_click_variant(self):
        r=classify_handoff(state(owner_native_trade_click_required=True,
                                 phone_live_order_release_enabled=False),now=NOW)
        self.assertEqual(r["approval_route"],"NATIVE_CLICK")
    def test_both_routes_ambiguous_not_authorized(self):
        r=classify_handoff(state(owner_native_trade_click_required=True),now=NOW)
        self.assertEqual(r["approval_route"],"AMBIGUOUS")
        self.assertFalse(r["binance_order_post_allowed"])
    def test_stale_rejected(self):
        x=state(generated_at=(NOW-timedelta(seconds=30)).isoformat())
        with self.assertRaises(OwnerHandoffContractError):classify_handoff(x,now=NOW)
    def test_wrong_owner_rejected(self):
        with self.assertRaises(OwnerHandoffContractError):
            classify_handoff(state(owner_uid=0),now=NOW)
    def test_unattended_post_rejected(self):
        with self.assertRaises(OwnerHandoffContractError):
            classify_handoff(state(unattended_order_post_permission=True),now=NOW)
    def test_unattended_close_rejected(self):
        with self.assertRaises(OwnerHandoffContractError):
            classify_handoff(state(unattended_independent_stop_close_allowed=True),now=NOW)
    def test_unknown_route_booleans_rejected(self):
        with self.assertRaises(OwnerHandoffContractError):
            classify_handoff(state(phone_live_order_release_enabled=1),now=NOW)
    def test_invalid_status_rejected(self):
        with self.assertRaises(OwnerHandoffContractError):
            classify_handoff(state(status="EXECUTE"),now=NOW)

if __name__=="__main__": unittest.main()
