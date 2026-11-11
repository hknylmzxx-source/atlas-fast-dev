"""G54: ayrı OS süreçleri, gerçek flock, pipe bariyerleri ve sentetik WAL.

Kill yalnız bu testin başlattığı geçici child'a uygulanır. Güç kesintisi veya
kalıcı epoch güvenliği iddia edilmez. Owner modülü ve SDK import edilmez.
"""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import unittest

from test_g54_owner_wal_integration import OwnerFixture,ROOT,SCOPE,SYMBOL,ROLES
from wal_writer_fence_candidate import writer_fence

CHILD=r'''
import fcntl,json,os,stat,sys
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,sys.argv[2])
sys.path.insert(0,str(Path(sys.argv[2])/"tests"))
from test_g54_owner_wal_integration import owner_namespace,ORIGINALS,SCOPE,SYMBOL
import wal_owner_write_adapter_candidate as adapter
import wal_writer_fence_candidate as fence
root=Path(sys.argv[1])
ns=owner_namespace(root)
for name,original in ORIGINALS.items():
    def redirect(directory,_original=original,**kwargs):
        assert Path(directory)==root and kwargs['expected_uid']==501
        kwargs['expected_uid']=os.getuid()
        return _original(directory,**kwargs)
    setattr(adapter,name,redirect)
held=None
lease_holder=None
intervention_holder=None
sleeps=[]
ns['time'].sleep=sleeps.append
class ProcessVenue:
    def __init__(self):
        self.fd=None;self.acquired=0;self.posted=0;self.deleted=0;self.timeout=False
    def acquire_mutation_lease(self):
        self.acquired+=1
        fd=os.open(root/'synthetic_mutation.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise ns['LeaseRefused']('SYNTHETIC_MUTATION_BUSY',transient=True)
        self.fd=fd
    def release_mutation_lease(self):
        if self.fd is not None:
            fcntl.flock(self.fd,fcntl.LOCK_UN);os.close(self.fd);self.fd=None
    def dispatch(self,verb):
        # This acquisition would fail if any caller nested dispatch under WAL.
        with fence.writer_fence(root,expected_uid=os.getuid()):pass
        own=self.fd is None
        if own:self.acquire_mutation_lease()
        try:
            if verb=='POST':
                self.posted+=1
                if self.timeout:raise TimeoutError('SYNTHETIC_UNKNOWN_POST')
            else:self.deleted+=1
        finally:
            if own:self.release_mutation_lease()
    def post_once(self,*a,**k):self.dispatch('POST')
    def delete_once(self,*a,**k):self.dispatch('DELETE')
venue=ProcessVenue()
def lock_path(name):
    fd=os.open(root/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except Exception:os.close(fd);raise
    return fd
def unlock(fd):
    fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
ns['emergency_intervention_lock']=lambda:lock_path('synthetic_intervention.lock')
def order(role):
    detail={'scope':SCOPE,'client_id':'G54_'+role}
    if role!='CANCEL_STOP':detail['symbol']=SYMBOL
    ns['journal'](role,'PROCESS_'+role,detail)
print('READY',flush=True)
for command in sys.stdin:
    command=command.strip()
    try:
        if command=='LOCK':
            held=fence.writer_fence(root,expected_uid=os.getuid());held.__enter__()
        elif command=='UNLOCK':
            held.__exit__(None,None,None);held=None
        elif command=='RESERVE':
            ns['write_policy_reserve_record'](SCOPE,SYMBOL,'G54_ENTRY')
        elif command=='INCIDENT':ns['emergency_note'](SCOPE,'G54_PROCESS_ALARM')
        elif command=='SNAPSHOT':ns['startup_reconcile']()
        elif command=='MARK':ns['mark_scope_resolved'](SCOPE,'SYNTHETIC_FLAT')
        elif command=='LEASE_LOCK':lease_holder=lock_path('synthetic_mutation.lock')
        elif command=='LEASE_UNLOCK':unlock(lease_holder);lease_holder=None
        elif command=='INTERVENTION_LOCK':intervention_holder=lock_path('synthetic_intervention.lock')
        elif command=='INTERVENTION_UNLOCK':unlock(intervention_holder);intervention_holder=None
        elif command=='TIMEOUT_POST':venue.timeout=True
        elif command=='COUNTS':
            print('COUNTS:'+json.dumps([venue.acquired,venue.posted,venue.deleted,venue.fd is not None,sleeps]),flush=True)
            continue
        elif command in ('WRITE_STOP','PROTECT_STOP'):
            wire={'symbol':SYMBOL,'clientAlgoId':'G54_PROCESS_STOP'}
            if command=='WRITE_STOP':ns['one_write'](venue,'STOP',wire,SCOPE)
            else:ns['place_stop_write'](venue,wire,SCOPE,sleep=sleeps.append)
        elif command=='CANCEL':
            stop={'symbol':SYMBOL,'clientAlgoId':'G54_PROCESS_STOP'}
            tps=[{'symbol':SYMBOL,'newClientOrderId':'G54_PROCESS_TP'+str(i)} for i in (1,2)]
            bundle={'entry':{'symbol':SYMBOL},'stop':stop,'take_profits':tps}
            samples=[({'size':'0'},[{'symbol':SYMBOL,'clientOrderId':t['newClientOrderId']} for t in tps],[stop]),({'size':'0'},[],[])]
            ns['snapshot']=lambda _:samples.pop(0)
            ns['cleanup_owned_exits'](venue,bundle,SCOPE)
        elif command=='FLATTEN':
            samples=[({'size':'2'},[],[]),({'size':'0'},[],[])]
            ns['snapshot']=lambda _:samples.pop(0)
            ns['actual_order']=lambda v,r,w:{'status':'FILLED','clientOrderId':w['newClientOrderId']}
            ns['emergency_flatten'](venue,SYMBOL,SCOPE)
        elif command=='CRASH_FILE_SYNC':
            original_sync=os.fsync
            def stop_after_file(fd):
                original_sync(fd)
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    print('FILE_SYNC_DONE',flush=True)
                    sys.stdin.readline()
            fence.os.fsync=stop_after_file
            order('ENTRY')
        elif command=='CRASH_PARTIAL':
            def partial(directory_fd,name,row):
                fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory_fd)
                os.write(fd,b'{')
                print('PARTIAL_READY',flush=True)
                sys.stdin.readline()
                os.close(fd)
            fence._write_once=partial
            order('ENTRY')
        else:order(command)
        print('OK',flush=True)
    except Exception as error:
        print('BLOCKED:'+type(error).__name__+':'+str(error),flush=True)
'''


class ProcessFenceTests(OwnerFixture):
    def child(self):
        proc=subprocess.Popen([sys.executable,"-B","-c",CHILD,str(self.root),str(ROOT)],
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,text=True,bufsize=1)
        def cleanup():
            if proc.poll() is None:proc.terminate()
            try:proc.communicate(timeout=4)
            except subprocess.TimeoutExpired:proc.kill();proc.communicate(timeout=4)
        self.addCleanup(cleanup)
        self.assertEqual(self.line(proc),"READY")
        return proc
    def line(self,proc):
        ready,_,_=select.select([proc.stdout],[],[],8)
        if not ready:self.fail("Sentetik child pipe bariyeri zamanında gelmedi")
        line=proc.stdout.readline().strip()
        if not line:
            _,err=proc.communicate(timeout=3)
            self.fail("Sentetik child erken kapandı: "+err[-1200:])
        return line
    def send(self,proc,command):
        proc.stdin.write(command+"\n");proc.stdin.flush()
        return self.line(proc)
    def counts(self,proc):
        reply=self.send(proc,"COUNTS")
        self.assertTrue(reply.startswith("COUNTS:"),reply)
        return json.loads(reply[len("COUNTS:"):])
    def busy_role(self,role):
        holder=self.child();writer=self.child()
        self.assertEqual(self.send(holder,"LOCK"),"OK")
        reply=self.send(writer,role)
        self.assertIn("BLOCKED:Blocked:",reply)
        self.assertIn("WAL_FENCE_BUSY",reply)
        self.assertEqual(list(self.root.glob("*.json")),[])
        self.assertEqual(self.send(holder,"UNLOCK"),"OK")
        # No automatic retry: only a fresh, explicit synthetic request writes.
        self.assertEqual(list(self.root.glob("*.json")),[])
        self.assertEqual(self.send(writer,role),"OK")
        self.assertEqual(len(list(self.root.glob("*.json"))),1)
        self.assert_no_marker()
    def test_entry_cross_process_busy(self):self.busy_role("ENTRY")
    def test_stop_cross_process_busy(self):self.busy_role("STOP")
    def test_tp1_cross_process_busy(self):self.busy_role("TP1")
    def test_tp2_cross_process_busy(self):self.busy_role("TP2")
    def test_emergency_cross_process_busy(self):self.busy_role("EMERGENCY")
    def test_cancel_stop_cross_process_busy(self):self.busy_role("CANCEL_STOP")
    def test_cancel_tp1_cross_process_busy(self):self.busy_role("CANCEL_TP1")
    def test_cancel_tp2_cross_process_busy(self):self.busy_role("CANCEL_TP2")
    def test_reserve_cross_process_busy(self):self.busy_role("RESERVE")
    def test_incident_cross_process_busy(self):self.busy_role("INCIDENT")
    def test_preloaded_late_incident_after_clean_snapshot_still_blocks_restart(self):
        writer=self.child();reader=self.child()
        self.assertEqual(self.send(reader,"SNAPSHOT"),"OK")
        self.assertEqual(self.send(writer,"INCIDENT"),"OK")
        self.assertIn("UNRESOLVED_INCIDENT",self.send(reader,"SNAPSHOT"))
        self.assertIn("WAL_RESOLUTION_MARKER_DISABLED",self.send(reader,"MARK"))
        self.assert_no_marker()
    def test_preloaded_late_entry_after_two_equal_reads_stays_unresolved(self):
        writer=self.child();reader=self.child()
        self.assertEqual(self.send(reader,"SNAPSHOT"),"OK")
        self.assertEqual(self.send(reader,"SNAPSHOT"),"OK")
        self.assertEqual(self.send(writer,"ENTRY"),"OK")
        self.assertIn("UNRESOLVED_WAL_SCOPE",self.send(reader,"SNAPSHOT"))
        self.assertIn("WAL_RESOLUTION_MARKER_DISABLED",self.send(reader,"MARK"))
        self.assert_no_marker()
    def test_same_identity_two_processes_never_overwrite(self):
        # Establish the shared inode first: this test isolates flock/O_EXCL
        # contention from a platform's concurrent first-create failure.
        with writer_fence(self.root,expected_uid=os.getuid()):pass
        first=self.child();second=self.child()
        # Both independent processes are ready before either request is sent.
        for proc in (first,second):proc.stdin.write("ENTRY\n");proc.stdin.flush()
        replies=[self.line(proc) for proc in (first,second)]
        self.assertEqual(replies.count("OK"),1,replies)
        rejected=next(r for r in replies if r!="OK")
        self.assertTrue("WAL_FENCE_BUSY" in rejected or "DUPLICATE_WAL_ATTEMPT" in rejected,replies)
        self.assertEqual(len(list(self.root.glob("ENTRY_*.json"))),1)
        before=next(self.root.glob("ENTRY_*.json")).read_bytes()
        self.assertIn("DUPLICATE_WAL_ATTEMPT",self.send(second,"ENTRY"))
        self.assertEqual(next(self.root.glob("ENTRY_*.json")).read_bytes(),before)
    def test_first_fence_creation_race_fails_closed_without_order_retry(self):
        first=self.child();second=self.child()
        for proc in (first,second):proc.stdin.write("ENTRY\n");proc.stdin.flush()
        replies=[self.line(proc) for proc in (first,second)]
        self.assertEqual(replies.count("OK"),1,replies)
        denied=next(r for r in replies if r!="OK")
        # In the managed macOS filesystem, concurrent first open(O_CREAT)
        # may return ENOENT. It is a permanent refusal, never lease-busy retry.
        self.assertTrue(any(reason in denied for reason in
                            ("WAL_FENCE_BUSY","DUPLICATE_WAL_ATTEMPT","WAL_FENCE_NOT_TRUSTED")),replies)
        self.assertEqual(len(list(self.root.glob("ENTRY_*.json"))),1)
        self.assert_no_marker()
    def test_killed_holder_releases_os_lock_without_unlocking_scope(self):
        holder=self.child();writer=self.child()
        self.assertEqual(self.send(holder,"RESERVE"),"OK")
        self.assertEqual(self.send(holder,"LOCK"),"OK")
        holder.kill();holder.communicate(timeout=4)
        self.assertEqual(self.send(writer,"INCIDENT"),"OK")
        self.assertIn("UNRESOLVED_INCIDENT",self.send(writer,"SNAPSHOT"))
        self.assert_no_marker()
    def test_kill_after_file_fsync_keeps_unknown_wal_and_no_retry(self):
        writer=self.child()
        self.assertEqual(self.send(writer,"CRASH_FILE_SYNC"),"FILE_SYNC_DONE")
        writer.kill();writer.communicate(timeout=4)
        reader=self.child()
        self.assertIn("DUPLICATE_WAL_ATTEMPT",self.send(reader,"ENTRY"))
        self.assertIn("UNRESOLVED_WAL_SCOPE",self.send(reader,"SNAPSHOT"))
        self.assert_no_marker()
    def test_kill_after_partial_write_blocks_all_following_writers(self):
        writer=self.child()
        self.assertEqual(self.send(writer,"CRASH_PARTIAL"),"PARTIAL_READY")
        writer.kill();writer.communicate(timeout=4)
        reader=self.child()
        for role in ("ENTRY","STOP","INCIDENT","RESERVE"):
            self.assertIn("WAL_RECORD_UNREADABLE",self.send(reader,role))
        self.assertIn("UNREADABLE_WAL",self.send(reader,"SNAPSHOT"))
        self.assert_no_marker()
    def test_real_mutation_lease_then_busy_wal_has_no_stop_retry_or_sleep(self):
        holder=self.child();writer=self.child()
        self.assertEqual(self.send(holder,"LOCK"),"OK")
        self.assertIn("PreSendRefused",self.send(writer,"PROTECT_STOP"))
        self.assertEqual(self.counts(writer),[1,0,0,False,[]])
        self.assertEqual(self.send(holder,"UNLOCK"),"OK")
        self.assertEqual(self.send(writer,"PROTECT_STOP"),"OK")
        self.assertEqual(self.counts(writer),[2,1,0,False,[]])
    def test_real_busy_mutation_lease_never_creates_stop_wal(self):
        holder=self.child();writer=self.child()
        self.assertEqual(self.send(holder,"LEASE_LOCK"),"OK")
        self.assertIn("LeaseRefused",self.send(writer,"WRITE_STOP"))
        self.assertEqual(self.counts(writer),[1,0,0,False,[]])
        self.assertEqual(list(self.root.glob("*.json")),[])
        # Incident writer does not wait for a mutation lease it never needs.
        self.assertEqual(self.send(holder,"INCIDENT"),"OK")
        self.assertEqual(self.send(holder,"LEASE_UNLOCK"),"OK")
    def test_normal_delete_under_real_process_leases_never_nests_wal(self):
        writer=self.child()
        self.assertEqual(self.send(writer,"CANCEL"),"OK")
        self.assertEqual(self.counts(writer),[3,0,3,False,[]])
        self.assertEqual(len(list(self.root.glob("CANCEL_*.json"))),3)
        self.assert_no_marker()
    def test_busy_delete_journal_never_takes_mutation_lease(self):
        holder=self.child();writer=self.child()
        self.assertEqual(self.send(holder,"LOCK"),"OK")
        self.assertIn("WAL_FENCE_BUSY",self.send(writer,"CANCEL"))
        self.assertEqual(self.counts(writer),[0,0,0,False,[]])
    def test_emergency_busy_releases_real_intervention_before_other_process(self):
        holder=self.child();writer=self.child()
        self.assertEqual(self.send(holder,"LOCK"),"OK")
        self.assertIn("WAL_FENCE_BUSY",self.send(writer,"FLATTEN"))
        self.assertEqual(self.counts(writer),[0,0,0,False,[]])
        self.assertEqual(self.send(holder,"INTERVENTION_LOCK"),"OK")
        self.assertEqual(self.send(holder,"INTERVENTION_UNLOCK"),"OK")
        self.assertEqual(self.send(holder,"UNLOCK"),"OK")
        self.assertEqual(self.send(writer,"FLATTEN"),"OK")
        self.assertEqual(self.counts(writer),[1,1,0,False,[]])
    def test_unknown_stop_post_survives_process_restart_without_resend(self):
        writer=self.child()
        self.assertEqual(self.send(writer,"TIMEOUT_POST"),"OK")
        self.assertIn("UNKNOWN_WRITE_RESULT_DO_NOT_RETRY",self.send(writer,"PROTECT_STOP"))
        self.assertEqual(self.counts(writer),[1,1,0,False,[]])
        reader=self.child()
        self.assertIn("DUPLICATE_WAL_ATTEMPT",self.send(reader,"PROTECT_STOP"))
        self.assertEqual(self.counts(reader),[1,0,0,False,[]])
        self.assert_no_marker()
    def test_closed_marker_entrypoint_does_not_wait_for_held_wal(self):
        holder=self.child();reader=self.child()
        self.assertEqual(self.send(holder,"LOCK"),"OK")
        self.assertIn("WAL_RESOLUTION_MARKER_DISABLED",self.send(reader,"MARK"))
        self.assert_no_marker()
    def test_preloaded_late_reservation_keeps_restart_and_marker_blocked(self):
        writer=self.child();reader=self.child()
        self.assertEqual(self.send(reader,"SNAPSHOT"),"OK")
        self.assertEqual(self.send(reader,"SNAPSHOT"),"OK")
        self.assertEqual(self.send(writer,"RESERVE"),"OK")
        self.assertIn("UNRESOLVED_WAL_SCOPE",self.send(reader,"SNAPSHOT"))
        self.assertIn("WAL_RESOLUTION_MARKER_DISABLED",self.send(reader,"MARK"))
        self.assert_no_marker()


if __name__=="__main__":unittest.main()
