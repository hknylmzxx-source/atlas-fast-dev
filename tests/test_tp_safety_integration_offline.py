"""TP kanıtı, mevcut WAL kilidi ve Owner kaynak işlevleriyle offline zincir.

Gerçek SDK import edilmez. POSIX kilitleri yalnız geçici test dizinindedir;
alt süreç yalnız bu testin başlattığı yerel kilit tutucusudur.
"""
import ast
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import selectors
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from test_tp_provenance_offline import NOW, WIRE, MockOrderRead, event_and_receipt, verifier
from test_safety_chain_integration import Blocked, Integration, PreSendRefused

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wal_writer_fence_candidate import WalFenceBlocked, writer_fence
from wal_owner_write_adapter_candidate import (
    append_incident, append_order_journal, append_reservation,
    resolve_zero_reservation_offline,
)
from quarantine_review_evidence_candidate import REQUIRED, assess_quarantine_review

SCOPE = "OFFLINE_SCOPE_36"
LOCK = ".atlas_wal_writer_fence.lock"


@contextmanager
def child_holding_fence(directory):
    code = """
import sys
sys.path.insert(0, sys.argv[2])
from wal_writer_fence_candidate import writer_fence
with writer_fence(sys.argv[1], expected_uid=int(sys.argv[3])):
    print('LOCKED', flush=True)
    sys.stdin.readline()
"""
    proc = subprocess.Popen([sys.executable, "-B", "-c", code, str(directory),
                             str(ROOT), str(os.getuid())], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=5) or proc.stdout.readline().strip() != "LOCKED":
                raise AssertionError("OFFLINE_CHILD_LOCK_NOT_READY")
        yield proc
    finally:
        try:
            _, stderr = proc.communicate(input="RELEASE\n", timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AssertionError("OFFLINE_CHILD_LOCK_NOT_RELEASED") from None
        if proc.returncode != 0:
            raise AssertionError("OFFLINE_CHILD_FAILED: " + stderr)


class TpLockSafetyChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="atlas-g36-tp-safety-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uid = os.getuid()
        append_reservation(self.root, scope=SCOPE, symbol=WIRE["symbol"],
                           client_id="OFFLINE_ENTRY_36", expected_uid=self.uid)

    def files(self):
        return {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}

    def append_tp(self):
        return append_order_journal(self.root, role="TP1", identity="OFFLINE_TP_36",
            scope=SCOPE, symbol=WIRE["symbol"], client_id=WIRE["newClientOrderId"],
            expected_uid=self.uid)

    def resolve(self, proof):
        return resolve_zero_reservation_offline(self.root, scope=SCOPE,
            symbol=WIRE["symbol"], client_id="OFFLINE_ENTRY_36",
            proof=proof, expected_uid=self.uid)

    def test_matching_tp_does_not_authorize_resolution_or_owner_release(self):
        self.append_tp()
        before = self.files()
        event, receipt = event_and_receipt()
        result = verifier().inspect(event, read_order=MockOrderRead(receipt), now_ms=NOW)
        self.assertTrue(result["synthetic_tp_consistent"])
        self.assertFalse(result["may_release_owner_lock"])
        with self.assertRaisesRegex(WalFenceBlocked, "SIGNED_PROOF_NOT_CONFIRMED"):
            self.resolve(result["may_clear_latch"])
        # Çağıran bool değeri sahteleştirse bile TP WAL kaydı çözümü engeller.
        with self.assertRaisesRegex(WalFenceBlocked, "SCOPE_HAS_OTHER_WAL"):
            self.resolve(True)
        self.assertEqual(before, self.files())

    def test_forged_event_and_owner_claims_cannot_clear_existing_incident(self):
        append_incident(self.root, scope=SCOPE, reason="OFFLINE_UNRESOLVED",
                        expected_uid=self.uid)
        before = self.files()
        event, receipt = event_and_receipt()
        event["o"]["c"] = "FOREIGN-TP"
        event.update(source="OWNER_SIGNED", owner_approved=True)
        reader = MockOrderRead(receipt)
        result = verifier().inspect(event, read_order=reader, now_ms=NOW)
        self.assertFalse(result["synthetic_tp_consistent"])
        self.assertEqual(reader.calls, [])
        review = assess_quarantine_review(self.root, evidence={k: True for k in REQUIRED})
        self.assertFalse(review["entry_authorized"])
        self.assertFalse(review["may_clear_latch"])
        with self.assertRaisesRegex(WalFenceBlocked, "SCOPE_HAS_OTHER_WAL"):
            self.resolve(True)
        self.assertEqual(before, self.files())

    def test_valid_and_forged_events_do_not_release_another_process_fence(self):
        with child_holding_fence(self.root) as proc:
            before = self.files()
            inode = (self.root / LOCK).stat().st_ino
            for forged in (False, True):
                with self.subTest(forged=forged):
                    event, receipt = event_and_receipt()
                    if forged:
                        event["o"]["i"] = 999
                    result = verifier().inspect(event, read_order=MockOrderRead(receipt), now_ms=NOW)
                    self.assertEqual(result["synthetic_tp_consistent"], not forged)
                    with self.assertRaisesRegex(WalFenceBlocked, "WAL_FENCE_BUSY"):
                        self.append_tp()
                    with self.assertRaisesRegex(WalFenceBlocked, "WAL_FENCE_BUSY"):
                        self.resolve(True)
                    self.assertIsNone(proc.poll())
                    self.assertEqual((self.root / LOCK).stat().st_ino, inode)
                    self.assertEqual(before, self.files())
        self.append_tp()
        with self.assertRaisesRegex(WalFenceBlocked, "SCOPE_HAS_OTHER_WAL"):
            self.resolve(True)

    def test_exception_releases_only_own_temporary_fence(self):
        inode = (self.root / LOCK).stat().st_ino
        with self.assertRaisesRegex(RuntimeError, "OFFLINE_FAILURE"):
            with writer_fence(self.root, expected_uid=self.uid):
                raise RuntimeError("OFFLINE_FAILURE")
        self.append_tp()
        self.assertEqual((self.root / LOCK).stat().st_ino, inode)

    def test_hardlinked_fence_refused_without_overwriting_data(self):
        os.link(self.root / LOCK, self.root / "offline-lock-alias")
        before = self.files()
        with self.assertRaisesRegex(WalFenceBlocked, "WAL_FENCE_PERMISSIONS"):
            self.append_tp()
        self.assertEqual(before, self.files())

    def test_symlinked_fence_refused_without_touching_target(self):
        with TemporaryDirectory(prefix="atlas-g36-untrusted-fence-") as directory:
            target = Path(directory) / "offline-target"
            target.write_bytes(b"OFFLINE_SENTINEL")
            (Path(directory) / LOCK).symlink_to(target)
            with self.assertRaisesRegex(WalFenceBlocked, "WAL_FENCE_NOT_TRUSTED"):
                append_order_journal(directory, role="TP1", identity="OFFLINE",
                    scope=SCOPE, symbol=WIRE["symbol"], client_id=WIRE["newClientOrderId"],
                    expected_uid=self.uid)
            self.assertEqual(target.read_bytes(), b"OFFLINE_SENTINEL")


class OwnerTpReceiptChainTests(unittest.TestCase):
    def setUp(self):
        self.sim = Integration()
        self.addCleanup(self.sim.close)
        # Yalnız seçili saf/kontrol işlevleri; Owner veya canlı SDK import edilmez.
        owner = ROOT / "owner_single_execution.py"
        names = {"dec", "actual_order", "checked", "payload"}
        functions = [node for node in ast.parse(owner.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual(len(functions), len(names))
        source = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
        exec(compile(source, str(owner), "exec"), self.sim.ns)
        self.reads = []
        _, self.receipt = event_and_receipt()

    def read(self, method, **kwargs):
        self.reads.append((method, kwargs))
        return deepcopy(self.receipt)

    def test_actual_owner_tp_receipt_uses_exact_independent_query(self):
        self.sim.venue.read = self.read
        result = self.sim.ns["one_write"](self.sim.venue, "TP1", WIRE, SCOPE)
        self.assertEqual(result, self.receipt)
        self.assertEqual(self.reads, [("query_order", {"symbol": WIRE["symbol"],
            "orig_client_order_id": WIRE["newClientOrderId"], "recv_window": 5000})])
        self.assertEqual(len(self.sim.venue.post_calls), 1)
        self.assertEqual(self.sim.venue.lease, 0)

    def test_forged_post_receipt_never_replaces_failed_independent_read(self):
        original_post = self.sim.venue.post_once
        def forged_post(*args, **kwargs):
            original_post(*args, **kwargs)
            return deepcopy(self.receipt)
        def failed_get(method, **kwargs):
            self.reads.append((method, kwargs))
            raise TimeoutError("OFFLINE_GET_TIMEOUT")
        self.sim.venue.post_once, self.sim.venue.read = forged_post, failed_get
        with self.assertRaisesRegex(Blocked, "SIGNED_READBACK_MISSING_TP1"):
            self.sim.ns["one_write"](self.sim.venue, "TP1", WIRE, SCOPE)
        self.assertEqual(len(self.sim.venue.post_calls), 1)
        self.assertEqual(len(self.reads), 6)
        self.assertEqual(self.sim.venue.lease, 0)
        self.assertEqual(len(list(self.sim.wal.glob("TP1_*.json"))), 1)

    def test_foreign_read_rejected_and_post_never_retried(self):
        self.receipt["clientOrderId"] = "FOREIGN-TP"
        self.sim.venue.read = self.read
        with self.assertRaisesRegex(Blocked, "SIGNED_READBACK_MISSING_TP2"):
            self.sim.ns["one_write"](self.sim.venue, "TP2", WIRE, SCOPE)
        self.assertEqual(len(self.sim.venue.post_calls), 1)
        self.assertEqual(len(self.reads), 6)
        self.assertEqual(self.sim.venue.lease, 0)

    def test_contended_existing_fence_preserves_wal_and_refuses_post(self):
        def journal(role, identity, detail):
            append_order_journal(self.sim.wal, role=role, identity=identity,
                scope=detail["scope"], symbol=detail["symbol"],
                client_id=detail["client_id"], expected_uid=os.getuid())
        self.sim.ns["journal"] = journal
        self.sim.venue.read = self.read
        with writer_fence(self.sim.wal, expected_uid=os.getuid()):
            with self.assertRaisesRegex(PreSendRefused, "WAL_FENCE_BUSY"):
                self.sim.ns["one_write"](self.sim.venue, "TP1", WIRE, SCOPE)
            self.assertEqual(self.sim.venue.post_calls, [])
            self.assertEqual(self.reads, [])
            self.assertEqual(self.sim.venue.lease, 0)
            self.assertEqual(list(self.sim.wal.glob("*.json")), [])
        self.sim.ns["one_write"](self.sim.venue, "TP1", WIRE, SCOPE)
        self.assertEqual(len(self.sim.venue.post_calls), 1)
        self.assertEqual(self.sim.venue.lease, 0)


if __name__ == "__main__":
    unittest.main()
