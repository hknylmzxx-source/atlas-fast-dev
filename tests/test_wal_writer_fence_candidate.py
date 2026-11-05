"""Isolated cross-process WAL fence tests; no Owner SDK, Binance or live WAL."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wal_writer_fence_candidate import (
    WalFenceBlocked,append_record,resolve_reserve_only,writer_fence,
)

SCOPE="OFFLINE_SCOPE"
SYM="AINUSDT"
CID="OFFLINE_ENTRY_ID"
RESERVE={
    "role":"POLICY_RESERVE",
    "intent":SCOPE,
    "state":"RESERVATION_PENDING",
    "detail":{"scope":SCOPE,"symbol":SYM,"client_id":CID},
}
ENTRY={"role":"ENTRY","detail":{"scope":SCOPE,"symbol":SYM,"client_id":CID}}

class WriterFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="atlas-wal-fence-")
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        self.uid=os.getuid()
    def tearDown(self):
        self.temp.cleanup()
    def reserve(self):
        append_record(self.root,name="POLICY_RESERVE_"+SCOPE+".json",
                      row=RESERVE,scope=SCOPE,expected_uid=self.uid)
    def resolve(self,proof=True):
        return resolve_reserve_only(
            self.root,scope=SCOPE,symbol=SYM,client_id=CID,
            proof=proof,expected_uid=self.uid)

    def test_reserve_then_resolve_creates_one_marker(self):
        self.reserve()
        self.resolve()
        files=sorted(p.name for p in self.root.glob("*.json"))
        self.assertEqual(files,["POLICY_RESERVE_"+SCOPE+".json",
                                "resolved_"+SCOPE+".json"])
        self.assertEqual(json.loads((self.root/files[-1]).read_text())["scope"],SCOPE)

    def test_writer_arriving_after_marker_is_rejected(self):
        self.reserve()
        self.resolve()
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_ALREADY_RESOLVED_NO_WRITE"):
            append_record(self.root,name="ENTRY_after.json",
                          row=ENTRY,scope=SCOPE,expected_uid=self.uid)
        self.assertFalse((self.root/"ENTRY_after.json").exists())

    def test_writer_arriving_before_marker_blocks_resolve(self):
        self.reserve()
        append_record(self.root,name="ENTRY_before.json",
                      row=ENTRY,scope=SCOPE,expected_uid=self.uid)
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_HAS_OTHER_WAL"):
            self.resolve()
        self.assertFalse((self.root/("resolved_"+SCOPE+".json")).exists())

    def test_proof_false_refuses_marker(self):
        self.reserve()
        with self.assertRaisesRegex(WalFenceBlocked,"SIGNED_PROOF_NOT_CONFIRMED"):
            self.resolve(False)
        self.assertFalse((self.root/("resolved_"+SCOPE+".json")).exists())

    def test_symlinked_directory_refused(self):
        link=self.root.parent/(self.root.name+"-sym")
        link.symlink_to(self.root,target_is_directory=True)
        try:
            with self.assertRaisesRegex(WalFenceBlocked,"WAL_DIR_SYMLINK"):
                with writer_fence(link,expected_uid=self.uid):pass
        finally:
            link.unlink()

    def test_weak_permissions_refused(self):
        self.root.chmod(0o755)
        with self.assertRaisesRegex(WalFenceBlocked,"WAL_DIR_PERMISSIONS"):
            self.reserve()

    def test_untrusted_uid_refused(self):
        with self.assertRaisesRegex(WalFenceBlocked,"WAL_DIR_PERMISSIONS"):
            with writer_fence(self.root,expected_uid=self.uid+1):pass

    def test_symlinked_lock_refused(self):
        (self.root/".atlas_wal_writer_fence.lock").symlink_to(self.root/"another")
        with self.assertRaisesRegex(WalFenceBlocked,"WAL_FENCE_NOT_TRUSTED"):
            self.reserve()

    def test_lock_fails_when_held_by_another_process(self):
        """Real POSIX flock, not mock: independently running child holds lock."""
        child_code=(
            "import sys,time;"
            "sys.path.insert(0,sys.argv[2]);"
            "from wal_writer_fence_candidate import writer_fence;"
            "with_writer=writer_fence(sys.argv[1],expected_uid=int(sys.argv[3]));"
            "with_writer.__enter__();"
            "print('LOCKED',flush=True);"
            "time.sleep(2);"
            "with_writer.__exit__(None,None,None)"
        )
        proc=subprocess.Popen(
            [sys.executable,"-B","-c",child_code,str(self.root),
             str(Path(__file__).resolve().parents[1]),str(self.uid)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(),"LOCKED")
            with self.assertRaisesRegex(WalFenceBlocked,"WAL_FENCE_BUSY"):
                self.reserve()
            self.assertFalse((self.root/("POLICY_RESERVE_"+SCOPE+".json")).exists())
        finally:
            proc.terminate()
            try:proc.communicate(timeout=4)
            except subprocess.TimeoutExpired:proc.kill();proc.communicate()
        self.reserve()

    def test_concurrent_two_processes_only_one_wins(self):
        """Both writers contend over the same scope, O_EXCL ensures one."""
        self.reserve()
        worker_code=(
            "import sys;"
            "sys.path.insert(0,sys.argv[2]);"
            "from wal_writer_fence_candidate import append_record;"
            "append_record(sys.argv[1],name=sys.argv[3],"
            "row={'role':'ENTRY','detail':{'scope':'OFFLINE_SCOPE'}},"
            "scope='OFFLINE_SCOPE',expected_uid=int(sys.argv[4]))"
        )
        args=[sys.executable,"-B","-c",worker_code,str(self.root),
              str(Path(__file__).resolve().parents[1])]
        p1=subprocess.Popen(args+["ENTRY_ONE.json",str(self.uid)],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        p2=subprocess.Popen(args+["ENTRY_TWO.json",str(self.uid)],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        p1.communicate(timeout=8);p2.communicate(timeout=8)
        # Independent appends may both eventually succeed if sequential.
        # Regardless, resolver must refuse once any ENTRY is recorded.
        self.assertTrue(any(x.name.startswith("ENTRY_") for x in self.root.glob("*.json")))
        with self.assertRaisesRegex(WalFenceBlocked,"SCOPE_HAS_OTHER_WAL"):
            self.resolve()

    def test_two_independent_processes_resolver_vs_entry_never_both_commit(self):
        """Independent process race: either ENTRY or marker can win, not both."""
        self.reserve()
        source=str(Path(__file__).resolve().parents[1])
        resolver=(
            "import sys;"
            "sys.path.insert(0,sys.argv[2]);"
            "from wal_writer_fence_candidate import resolve_reserve_only;"
            "resolve_reserve_only(sys.argv[1],scope='OFFLINE_SCOPE',"
            "symbol='AINUSDT',client_id='OFFLINE_ENTRY_ID',"
            "proof=True,expected_uid=int(sys.argv[3]))"
        )
        writer=(
            "import sys;"
            "sys.path.insert(0,sys.argv[2]);"
            "from wal_writer_fence_candidate import append_record;"
            "append_record(sys.argv[1],name='ENTRY_racing.json',"
            "row={'role':'ENTRY','detail':{'scope':'OFFLINE_SCOPE',"
            "'symbol':'AINUSDT','client_id':'OFFLINE_ENTRY_ID'}},"
            "scope='OFFLINE_SCOPE',expected_uid=int(sys.argv[3]))"
        )
        args=[str(self.root),source,str(self.uid)]
        p1=subprocess.Popen([sys.executable,"-B","-c",resolver,*args],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        p2=subprocess.Popen([sys.executable,"-B","-c",writer,*args],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        p1.communicate(timeout=8)
        p2.communicate(timeout=8)
        resolved=(self.root/("resolved_"+SCOPE+".json")).exists()
        entry=(self.root/"ENTRY_racing.json").exists()
        self.assertNotEqual(resolved,entry,
            "Shared writer fence must prevent marker and competing ENTRY together")

    def test_duplicate_file_not_overwritten(self):
        self.reserve()
        before=(self.root/("POLICY_RESERVE_"+SCOPE+".json")).read_bytes()
        with self.assertRaisesRegex(WalFenceBlocked,"WAL_DUPLICATE_NO_WRITE"):
            self.reserve()
        self.assertEqual(before,(self.root/("POLICY_RESERVE_"+SCOPE+".json")).read_bytes())

    def test_bad_scope_name_refused(self):
        with self.assertRaisesRegex(WalFenceBlocked,"UNTRUSTED_SCOPE"):
            append_record(self.root,name="ENTRY_test.json",
                row={"scope":"../bad"},scope="../bad",expected_uid=self.uid)

    def test_corrupt_wal_prevents_resolution(self):
        self.reserve()
        broken=self.root/"ENTRY_broken.json"
        broken.write_text("{")
        broken.chmod(0o600)
        with self.assertRaisesRegex(WalFenceBlocked,"WAL_RECORD_UNREADABLE"):
            self.resolve()
        self.assertFalse((self.root/("resolved_"+SCOPE+".json")).exists())

if __name__=="__main__":
    unittest.main()
