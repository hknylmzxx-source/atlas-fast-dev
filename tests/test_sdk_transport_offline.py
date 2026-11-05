"""Live package / Owner transport contract integration tests -- ALL OFFLINE.

Uses the *installed* Binance SDK's Python REST signatures, and the selected
Owner HTTP guard functions compiled from their source AST. No SDK instance,
Keychain, account, network, POST or DELETE is ever executed.
"""
from __future__ import annotations

import ast
from importlib.metadata import version
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
import unittest

ROOT=Path(__file__).resolve().parents[1]
OWNER=ROOT/"owner_single_execution.py"
TREE=ast.parse(OWNER.read_text())

def source_const(name):
    matches=[
        n for n in TREE.body if isinstance(n,ast.Assign)
        and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)
        and n.targets[0].id==name
    ]
    assert len(matches)==1,name
    v=matches[0].value
    if isinstance(v,ast.Name) and v.id in {"POST_PATHS","GET_PATHS"}:
        return source_const(v.id)
    if isinstance(v,ast.Call) and isinstance(v.func,ast.Name) and v.func.id=="frozenset":
        return frozenset(ast.literal_eval(v.args[0]))
    return ast.literal_eval(v)

def offline_owner_guard():
    names={"_order_not_found","make_guarded_request"}
    nodes=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert {x.name for x in nodes}==names
    class Blocked(RuntimeError):pass
    class OrderNotFound(RuntimeError):pass
    ns={
        "json":json,"urlsplit":urlsplit,
        "GET_PATHS":source_const("GET_PATHS"),
        "POST_PATHS":source_const("POST_PATHS"),
        "DELETE_PATHS":source_const("DELETE_PATHS"),
        "ORDER_NOT_FOUND_PATHS":source_const("ORDER_NOT_FOUND_PATHS"),
        "ORDER_NOT_FOUND_CODE":source_const("ORDER_NOT_FOUND_CODE"),
        "Blocked":Blocked,"OrderNotFound":OrderNotFound,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),
                 str(OWNER),"exec"),ns)
    return ns

HTTP="https://fapi.binance.com/fapi/v1/order"
class FakeResponse:
    def __init__(self,status=400,code=-2013,location=HTTP,content_type="application/json"):
        self.status_code=status
        self.headers={"Content-Type":content_type}
        self.url=location
        self.content=json.dumps({"code":code,"msg":"synthetic"}).encode()

class OwnerTransportContractTests(unittest.TestCase):
    def setUp(self):
        self.ns=offline_owner_guard()
        self.calls=[]
        self.result=FakeResponse()
        def fake_original(method,url,**kwargs):
            self.calls.append((method,url,kwargs))
            return self.result
        self.venue=SimpleNamespace(armed=None,write_count=0)
        self.guarded=self.ns["make_guarded_request"](fake_original,self.venue)

    def test_exact_signed_get_400_minus_2013_becomes_OrderNotFound(self):
        with self.assertRaisesRegex(self.ns["OrderNotFound"],"ORDER_DOES_NOT_EXIST"):
            self.guarded("GET",HTTP)
        self.assertEqual(len(self.calls),1)
        self.assertIs(self.calls[0][2]["allow_redirects"],False)
        self.assertEqual(self.venue.write_count,0)

    def test_signed_get_429_is_not_absence(self):
        self.result.status_code=429
        with self.assertRaisesRegex(self.ns["Blocked"],"EXCHANGE_RESPONSE_NOT_TRUSTED"):
            self.guarded("GET",HTTP)

    def test_signed_get_500_is_not_absence(self):
        self.result.status_code=500
        with self.assertRaisesRegex(self.ns["Blocked"],"EXCHANGE_RESPONSE_NOT_TRUSTED"):
            self.guarded("GET",HTTP)

    def test_signed_get_wrong_code_is_not_absence(self):
        self.result.content=b'{"code":-2011,"msg":"synthetic"}'
        with self.assertRaisesRegex(self.ns["Blocked"],"EXCHANGE_RESPONSE_NOT_TRUSTED"):
            self.guarded("GET",HTTP)

    def test_unpinned_host_is_rejected_before_transport(self):
        with self.assertRaisesRegex(self.ns["Blocked"],"UNPINNED_EXCHANGE_REQUEST"):
            self.guarded("GET","https://example.com/fapi/v1/order")
        self.assertEqual(len(self.calls),0)

    def test_http_url_is_rejected_before_transport(self):
        with self.assertRaisesRegex(self.ns["Blocked"],"UNPINNED_EXCHANGE_REQUEST"):
            self.guarded("GET","http://fapi.binance.com/fapi/v1/order")
        self.assertEqual(len(self.calls),0)

    def test_untrusted_redirect_target_is_rejected(self):
        self.result.url="https://example.com/fapi/v1/order"
        with self.assertRaisesRegex(self.ns["Blocked"],"EXCHANGE_RESPONSE_NOT_TRUSTED"):
            self.guarded("GET",HTTP)

    def test_post_unarmed_is_rejected_without_transport(self):
        with self.assertRaisesRegex(self.ns["Blocked"],"EXCHANGE_MUTATION_NEEDS_ONE_SHOT_LOCAL_ARM"):
            self.guarded("POST",HTTP)
        self.assertEqual(len(self.calls),0)
        self.assertEqual(self.venue.write_count,0)

    def test_delete_unarmed_is_rejected_without_transport(self):
        with self.assertRaisesRegex(self.ns["Blocked"],"EXCHANGE_MUTATION_NEEDS_ONE_SHOT_LOCAL_ARM"):
            self.guarded("DELETE",HTTP)
        self.assertEqual(len(self.calls),0)

    def test_unknown_get_endpoint_rejected_before_transport(self):
        with self.assertRaisesRegex(self.ns["Blocked"],"GET_PATH_NOT_ALLOWED"):
            self.guarded("GET","https://fapi.binance.com/fapi/v3/not-real")
        self.assertEqual(len(self.calls),0)

    def test_bad_http_method_rejected_before_transport(self):
        with self.assertRaisesRegex(self.ns["Blocked"],"METHOD_FORBIDDEN"):
            self.guarded("PUT",HTTP)
        self.assertEqual(len(self.calls),0)

    def test_order_not_found_source_limit_applies_only_to_normal_order(self):
        self.assertEqual(self.ns["ORDER_NOT_FOUND_PATHS"],frozenset(("/fapi/v1/order",)))
        self.assertEqual(self.ns["ORDER_NOT_FOUND_CODE"],-2013)

class InstalledBinanceSdkContracts(unittest.TestCase):
    def test_exact_sdk_dependency_versions(self):
        for pkg,pin,code in source_const("SDK_PINS"):
            self.assertEqual(version(pkg),pin,code)

    def test_actual_rest_class_offers_signed_gets(self):
        from binance_sdk_derivatives_trading_usds_futures.rest_api.rest_api import (
            DerivativesTradingUsdsFuturesRestAPI,
        )
        cls=DerivativesTradingUsdsFuturesRestAPI
        for name in ("query_order","account_information_v3",
                     "current_all_open_orders","current_all_algo_open_orders"):
            self.assertTrue(callable(getattr(cls,name,None)),name)
        params=inspect.signature(cls.query_order).parameters
        for required in ("symbol","orig_client_order_id","recv_window"):
            self.assertIn(required,params)
        self.assertIn("recv_window",inspect.signature(cls.account_information_v3).parameters)

if __name__=="__main__":
    unittest.main()
