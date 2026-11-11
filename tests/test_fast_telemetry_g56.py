"""G56: telemetri — eklemeli, WAL'dan ayrı, asla raise etmez."""
import json, os, stat, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fast_telemetry_g56 as tm


def rows(d):
    out = []
    for p in sorted(Path(d).glob("telemetry_*.jsonl")):
        out += [json.loads(l) for l in p.read_text().splitlines()]
    return out


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / tm.DIRNAME
        self.t = tm.Telemetry(self.dir, clock_ms=lambda: 1762850000000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_signal_and_pre_entry_reference_are_appended(self):
        self.assertTrue(self.t.record_signal(symbol="BTCUSDT", side="BUY", signal_price="100.5",
                                             signal_ts_ms=1762849999000, source="ATLAS_SINYAL"))
        self.assertTrue(self.t.record_pre_entry_reference(bundle_fp="ab", symbol="BTCUSDT", side="BUY",
                        market={"mark": "100.6", "last_price": "100.7", "mark_time_ms": 1}))
        r = rows(self.dir)
        self.assertEqual([x["kind"] for x in r], ["SIGNAL", "PRE_ENTRY_REFERENCE"])
        self.assertEqual(r[0]["signal_price"], "100.5")
        self.assertEqual(r[1]["mark"], "100.6")

    def test_dir_and_file_are_owner_only(self):
        self.t.record_alarm("X")
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)
        f = next(self.dir.glob("*.jsonl"))
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)

    def test_append_only_existing_rows_are_kept(self):
        self.t.record_alarm("A"); self.t.record_alarm("B")
        self.assertEqual([x["alarm"] for x in rows(self.dir)], ["A", "B"])

    def test_order_lifecycle_normal_and_algo_with_dedup(self):
        tp = {"symbol": "USUSDT", "clientOrderId": "tp1", "orderId": 7, "type": "LIMIT",
              "status": "NEW", "reduceOnly": True, "executedQty": "0"}
        self.assertTrue(self.t.record_order_observation("TP1", tp, phase="P"))
        self.assertFalse(self.t.record_order_observation("TP1", tp, phase="P"))   # tekrar yok
        self.assertTrue(self.t.record_order_observation("TP1", dict(tp, status="EXPIRED"), phase="P"))
        stop = {"clientAlgoId": "st", "algoId": 9, "orderType": "STOP_MARKET",
                "algoType": "CONDITIONAL", "algoStatus": "TRIGGERED", "closePosition": True}
        self.assertTrue(self.t.record_order_observation("STOP", stop, phase="P"))
        self.assertTrue(self.t.record_order_error("TP2", "tp2", code=-2022, msg="ReduceOnly Order is rejected."))
        r = rows(self.dir)
        self.assertEqual([x.get("state") for x in r[:3]], ["NEW", "EXPIRED", "TRIGGERED"])
        self.assertTrue(r[0]["receipt"]["reduceOnly"])
        self.assertEqual(r[2]["receipt"]["algoType"], "CONDITIONAL")
        self.assertEqual(r[3]["code"], -2022)

    def test_disappeared_recorded_once(self):
        self.assertTrue(self.t.record_order_disappeared("TP1", "tp1", last_state="NEW"))
        self.assertFalse(self.t.record_order_disappeared("TP1", "tp1", last_state="NEW"))

    def test_never_raises_on_unwritable_or_symlink_dir(self):
        target = Path(self.tmp.name) / "elsewhere"; target.mkdir()
        link = Path(self.tmp.name) / "link"; os.symlink(target, link)
        t = tm.Telemetry(link)
        self.assertFalse(t.record_alarm("X"))
        self.assertEqual(t.failures, 1)
        self.assertEqual(list(target.iterdir()), [])
        bad = tm.Telemetry(Path(self.tmp.name) / "no" / "parent")
        self.assertFalse(bad.record_signal(symbol="A", side="BUY", signal_price=1, signal_ts_ms=1, source="s"))
        self.assertFalse(self.t.record_order_observation("TP1", object(), phase="P") and False)

    def test_wrong_uid_refused(self):
        t = tm.Telemetry(self.dir, expected_uid=os.getuid() + 1)
        self.assertFalse(t.record_alarm("X"))

    def test_slippage(self):
        self.assertEqual(tm.slippage("100", "100.1", "BUY"), {"price": "0.1", "bps": "10.00"})
        self.assertEqual(tm.slippage("100", "99.9", "SELL"), {"price": "0.1", "bps": "10.00"})
        self.assertEqual(tm.slippage("100", "99.9", "BUY")["price"], "-0.1")
        self.assertIsNone(tm.slippage("0", "1", "BUY"))
        self.assertIsNone(tm.slippage("x", "1", "BUY"))
        self.assertIsNone(tm.slippage("1", "1", "LONG"))

    def test_no_network_or_order_code(self):
        src = Path(tm.__file__).read_text()
        for word in ("requests", "urllib", "socket", "subprocess", "post_once", "delete_once", "SignedVenue"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
