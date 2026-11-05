"""Source-level offline tests for explicit emergency latch startup blocking.

Compile ONLY the actual startup_reconcile AST function with a temporary WAL.
No Owner SDK import, Binance access, production WAL, or network operation.
"""
import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
TREE=ast.parse((ROOT/"owner_single_execution.py").read_text())
FN=next(n for n in TREE.body if isinstance(n,ast.FunctionDef)
        and n.name=="startup_reconcile")

class Blocked(RuntimeError):
    pass

class StartupEmergencyLatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix="atlas-startup-latch-offline-")
        self.root=Path(self.tmp.name)
        self.wal=self.root/"wal"
        self.private=self.root/"private"
        self.wal.mkdir(mode=0o700)
        self.private.mkdir(mode=0o700)
        env={"Blocked":Blocked,"LOG":self.wal,"PRIVATE":self.private,"json":json}
        exec(compile(ast.Module(body=[FN],type_ignores=[]),
                     "<offline-source-startup>","exec"),env)
        self.check=env["startup_reconcile"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_unlatched_empty_wal_does_not_fail(self):
        self.assertIsNone(self.check())

    def test_present_alarm_blocks(self):
        (self.wal/"emergency_latch_SCOPE.json").write_text(
            '{"scope":"SCOPE","new_entry_permitted":false}')
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_EMERGENCY_LATCH"):
            self.check()

    def test_corrupt_alarm_blocks_without_parsing(self):
        (self.wal/"emergency_latch_SCOPE.json").write_text("{")
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_EMERGENCY_LATCH"):
            self.check()

    def test_alarm_and_existing_marker_still_block(self):
        (self.wal/"resolved_SCOPE.json").write_text('{"scope":"SCOPE"}')
        (self.wal/"emergency_latch_SCOPE.json").write_text('{"scope":"SCOPE"}')
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_EMERGENCY_LATCH"):
            self.check()

    def test_symlink_named_as_alarm_blocks(self):
        target=self.root/"target.txt"
        target.write_text("offline")
        (self.wal/"emergency_latch_SCOPE.json").symlink_to(target)
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_EMERGENCY_LATCH"):
            self.check()

    def test_alarm_filename_without_json_suffix_blocks(self):
        (self.wal/"emergency_latch_SCOPE.partial").write_text("offline")
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_EMERGENCY_LATCH"):
            self.check()

    def test_incident_still_blocks(self):
        (self.wal/"incident_SCOPE.json").write_text('{"scope":"SCOPE"}')
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_INCIDENT"):
            self.check()

    def test_missing_resolved_marker_still_blocks_reservation(self):
        (self.wal/"POLICY_RESERVE_SCOPE.json").write_text(
            '{"detail":{"scope":"SCOPE"}}')
        with self.assertRaisesRegex(Blocked,"UNRESOLVED_WAL_SCOPE"):
            self.check()

if __name__=="__main__":
    unittest.main()
