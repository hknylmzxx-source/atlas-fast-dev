"""Quarantine owner review needs independent evidence, never automatic release."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from quarantine_review_evidence_candidate import (
    REQUIRED,assess_quarantine_review)
from emergency_latch_candidate import publish_alarm_latch

class QuarantineReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix="atlas-review-evidence-")
        self.root=Path(self.tmp.name)
        self.root.chmod(0o700)
        self.evidence={key:True for key in REQUIRED}
    def tearDown(self):
        self.tmp.cleanup()
    _DEFAULT=object()
    def report(self,evidence=_DEFAULT):
        return assess_quarantine_review(
            self.root,evidence=self.evidence if evidence is self._DEFAULT
            else evidence)
    def latch(self):
        publish_alarm_latch(self.root,scope="OFFLINE_SCOPE",
            reason="PROTECTION_NOT_VERIFIED",expected_uid=os.getuid())
    def test_all_claimed_evidence_still_does_not_grant_release(self):
        rep=self.report()
        self.assertEqual(rep["decision"],"OWNER_MANUAL_VERIFICATION_REQUIRED")
        self.assertFalse(rep["entry_authorized"])
        self.assertFalse(rep["may_clear_latch"])
        self.assertFalse(rep["signed_exchange_evidence_independently_verified"])
    def test_existing_latch_is_not_removed_by_claims(self):
        self.latch()
        rep=self.report()
        self.assertTrue(rep["emergency_latch_detected_or_unreadable"])
        self.assertTrue((self.root/"emergency_latch_OFFLINE_SCOPE.json").exists())
        self.assertFalse(rep["may_clear_latch"])
    def test_each_missing_evidence_field_reported(self):
        for name in REQUIRED:
            with self.subTest(name=name):
                ev=dict(self.evidence)
                del ev[name]
                rep=self.report(ev)
                self.assertIn(name,rep["missing"])
                self.assertFalse(rep["entry_authorized"])
    def test_false_and_string_true_do_not_pass(self):
        for value in (False,"true",1,None,0):
            with self.subTest(value=value):
                ev=dict(self.evidence)
                ev["signed_position_flat"]=value
                rep=self.report(ev)
                self.assertIn("signed_position_flat",rep["missing"])
    def test_invalid_evidence_type_rejected(self):
        for value in (None,[],True,"confirmed"):
            with self.subTest(value=value):
                rep=self.report(value)
                self.assertEqual(rep["decision"],"OWNER_REVIEW_EVIDENCE_MISSING")
                self.assertFalse(rep["entry_authorized"])
    def test_corrupt_latch_does_not_reenable(self):
        (self.root/"emergency_latch_OFFLINE_SCOPE.json").write_text("{garbage")
        rep=self.report()
        self.assertTrue(rep["emergency_latch_detected_or_unreadable"])
        self.assertFalse(rep["may_clear_latch"])
    def test_untrusted_wal_directory_never_releases(self):
        self.root.chmod(0o755)
        rep=self.report()
        self.assertTrue(rep["emergency_latch_detected_or_unreadable"])
        self.assertFalse(rep["may_clear_latch"])
        self.root.chmod(0o700)
    def test_no_files_are_written_by_review(self):
        before=set(self.root.iterdir())
        self.report()
        after=set(self.root.iterdir())
        self.assertEqual(before,after)

if __name__=="__main__":
    unittest.main()
