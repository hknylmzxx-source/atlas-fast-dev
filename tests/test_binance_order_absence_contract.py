"""Exact Binance signed-GET order-not-found classification, offline HTTP mocks.

Compiled directly from the Owner SDK AST. No network is used.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"owner_single_execution.py"
tree=ast.parse(SOURCE.read_text())
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
          and n.name=="_order_not_found")
ns={
    "json":json,
    "ORDER_NOT_FOUND_PATHS":frozenset(("/fapi/v1/order",)),
    "ORDER_NOT_FOUND_CODE":-2013,
}
exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
             str(SOURCE),"exec"),ns)

def response(code=-2013,status=400,content_type="application/json"):
    return SimpleNamespace(status_code=status,
        headers={"Content-Type":content_type},
        content=json.dumps({"code":code,"msg":"Order does not exist"}).encode())

class ExactOrderNotFoundTests(unittest.TestCase):
    def test_exact_get_400_integer_minus_2013_is_not_found(self):
        self.assertTrue(ns["_order_not_found"]("GET","/fapi/v1/order",response()))
    def test_get_429_not_misclassified(self):
        self.assertFalse(ns["_order_not_found"]("GET","/fapi/v1/order",response(status=429)))
    def test_get_500_not_misclassified(self):
        self.assertFalse(ns["_order_not_found"]("GET","/fapi/v1/order",response(status=500)))
    def test_post_never_classified_as_signed_order_absence(self):
        self.assertFalse(ns["_order_not_found"]("POST","/fapi/v1/order",response()))
    def test_algo_endpoint_not_classified(self):
        self.assertFalse(ns["_order_not_found"]("GET","/fapi/v1/algoOrder",response()))
    def test_other_binance_error_code_not_classified(self):
        self.assertFalse(ns["_order_not_found"]("GET","/fapi/v1/order",response(code=-2011)))
    def test_string_error_code_not_classified(self):
        self.assertFalse(ns["_order_not_found"]("GET","/fapi/v1/order",response(code="-2013")))
    def test_boolean_code_not_classified(self):
        self.assertFalse(ns["_order_not_found"]("GET","/fapi/v1/order",response(code=True)))
    def test_nonjson_response_not_classified(self):
        self.assertFalse(ns["_order_not_found"]("GET","/fapi/v1/order",
            response(content_type="text/html")))
    def test_malformed_response_not_classified(self):
        r=response()
        r.content=b"{broken"
        self.assertFalse(ns["_order_not_found"]("GET","/fapi/v1/order",r))

if __name__=="__main__":
    unittest.main()
