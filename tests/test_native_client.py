import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from jasmine_core.adapter.native_client import NativeClient,NativeClientFailure
from jasmine_core.continuity_scan import Budget

class NativeClientTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
 def client(self,script,sink=None):
    p=self.root/'child.py';p.write_text(script)
    client=NativeClient([os.path.realpath(sys.executable),str(p)],{},str(self.root),sink)
    self.addCleanup(lambda:client.close(Budget(2)))
    return client
 def test_correlated_response_preserves_requests_and_notifications(self):
    receipts=[]
    c=self.client("""import sys,json,os,time,traceback,atexit,signal
def diagnostic(stage):
 with open('correlated-child.log','a') as log:
  log.write(json.dumps({'stage':stage,'pid':os.getpid(),'pgid':os.getpgrp(),'sid':os.getsid(0),'monotonic':time.monotonic(),'stack':traceback.format_stack(limit=4) if stage=='answered_and_exiting' else []})+'\\n')
diagnostic('started')
# Self-observation only: never signal a PID that may already have exited.
with open('correlated-startup.log','w') as log:
 log.write(json.dumps({'pid':os.getpid(),'ppid':os.getppid(),'uid':os.getuid(),'euid':os.geteuid(),'sigterm_default':signal.getsignal(signal.SIGTERM)==signal.SIG_DFL}))
def exit_diagnostic():
 diagnostic('atexit')
 with open('correlated-exit-stack.log','w') as log:
  log.write(''.join(traceback.format_stack(limit=4)))
atexit.register(exit_diagnostic)
r=json.loads(sys.stdin.readline())
for m in [{'method':'note','params':{}},{'id':'server-1','method':'tool','params':{}},{'id':r['id'],'result':{'ok':True}}]: print(json.dumps(m),flush=True)
a=json.loads(sys.stdin.readline()); print(json.dumps({'method':'answered','params':a}),flush=True)
diagnostic('answered_and_exiting')
""",lambda direction,message:receipts.append((direction,message)))
    ident=c.send_request('init',{},Budget())
    self.assertEqual(c.wait_response(ident,Budget()),{'ok':True})
    self.assertEqual(c.next_message(Budget())['method'],'note')
    self.assertEqual(c.next_message(Budget())['method'],'tool')
    c.respond('server-1',{'ok':True},Budget())
    self.assertEqual(c.next_message(Budget())['params']['id'],'server-1')
    self.assertTrue(any(d=='outbound' for d,m in receipts))
    # Observe only this owned fixture. Do not poll/reap the child before close:
    # that would change the exit race under investigation.
    observations=[]
    waits=[]
    wait=c.proc.wait
    def observed_wait(*args,**kwargs):
      entry={'monotonic':time.monotonic(),'timeout':kwargs.get('timeout'),
             'returncode_before':c.proc.returncode}
      waits.append(entry)
      try:
        result=wait(*args,**kwargs)
        entry['result']=result
        return result
      except (OSError,subprocess.TimeoutExpired) as exc:
        entry.update(error=type(exc).__name__,errno=getattr(exc,'errno',None))
        raise
      finally:
        entry.update(finished=time.monotonic(),returncode_after=c.proc.returncode)
        # Probe after the real wait, preserving the first signal/exit race.
        # Record exceptions rather than interpreting EPERM as absence.
        try:
          os.killpg(c.proc.pid,0)
          entry['post_wait_group_probe']={'result':'exists'}
        except OSError as exc:
          entry['post_wait_group_probe']={'error':type(exc).__name__,'errno':exc.errno}
    killpg=os.killpg
    def observed_killpg(pgid,sig):
      entry={'signal':int(sig),'monotonic':time.monotonic(),
             'returncode_before':c.proc.returncode}
      observations.append(entry)
      try:
        result=killpg(pgid,sig)
        entry['result']='ok'
        return result
      except OSError as exc:
        entry.update(error=type(exc).__name__,errno=exc.errno)
        for name,probe in (
            ('owned_child_pgid',lambda:os.getpgid(c.proc.pid)),
            ('owned_child_sid',lambda:os.getsid(c.proc.pid)),
            ('owned_group_signal_zero',lambda:killpg(pgid,0))):
          try: entry[name]={'result':probe()}
          except OSError as probe_exc:
            entry[name]={'error':type(probe_exc).__name__,'errno':probe_exc.errno}
        if isinstance(exc,PermissionError) and sig==signal.SIGTERM:
          # Only the owned fixture PID, after the failed signal. Never poll/reap
          # it here or send a stack-dump signal to a potentially recycled PID.
          entry['lifecycle_at_denial']={}
          for name in ('correlated-child.log','correlated-startup.log','correlated-exit-stack.log'):
            try:
              with (self.root/name).open() as log:
                entry['lifecycle_at_denial'][name]=log.read(2048)
            except OSError as log_exc:
              entry['lifecycle_at_denial'][name]={'error':type(log_exc).__name__,'errno':log_exc.errno}
          try:
            raw=os.read(c.proc.stderr.fileno(),2048)
            c.stderr.extend(raw)
            entry['stderr_at_denial']=raw.decode('utf-8',errors='replace')
          except OSError as read_exc:
            entry['stderr_at_denial']={'error':type(read_exc).__name__,'errno':read_exc.errno}
          # Bound the read-only process-state probe within the existing close
          # budget. Do not collect command lines, environments or other PIDs.
          remaining=cleanup_budget.end-time.monotonic()
          if remaining>0:
            try:
              state=subprocess.run(['/bin/ps','-p',str(c.proc.pid),'-o','pid=,ppid=,pgid=,uid=,stat='],
                                   capture_output=True,timeout=min(.2,remaining))
              entry['owned_process_state']={'returncode':state.returncode,
                  'stdout':state.stdout[:1024].decode('utf-8',errors='replace'),
                  'stderr':state.stderr[:1024].decode('utf-8',errors='replace')}
            except (OSError,subprocess.TimeoutExpired) as state_exc:
              entry['owned_process_state']={'error':type(state_exc).__name__,'errno':getattr(state_exc,'errno',None)}
          else:
            entry['owned_process_state']={'skipped':'cleanup budget exhausted'}
        raise
    try:
      with patch('jasmine_core.adapter.native_client.os.killpg',side_effect=observed_killpg), patch.object(c.proc,'wait',side_effect=observed_wait):
        cleanup_budget=Budget()
        cleanup=c.close(cleanup_budget)
    finally:
      child_log=self.root/'correlated-child.log'
      print('native correlated cleanup diagnostic: '+json.dumps({
          'platform':sys.platform,'python':sys.version_info[:3],
          'signals':observations,'waits':waits,'returncode':c.proc.returncode,
          'stderr':bytes(c.stderr[:2048]).decode('utf-8',errors='replace'),
          'child_log':child_log.read_text()[:2048] if child_log.exists() else 'missing',
          'parent_identity':{'pid':os.getpid(),'uid':os.getuid(),'euid':os.geteuid()},
          'child_lifecycle':{
              name:(self.root/name).read_text()[:2048] if (self.root/name).exists() else 'missing'
              for name in ('correlated-startup.log','correlated-exit-stack.log')},
          'stack_dump':'bounded self-dump in child exit log; no signal sent to a possibly exited child',
      }),file=sys.stderr,flush=True)
    self.assertEqual(cleanup['cleanup'],'complete',cleanup)
 def test_invalid_json_and_framing(self):
    for raw,code in [(b'{"jsonrpc":"2.0","method":"n","method":"b"}\n','native_invalid_message'),(b'{"jsonrpc":"2.0","method":"n","params":{"x":NaN}}\n','native_invalid_message'),(b'{"jsonrpc":"2.0","method":"n","params":{"x":"\\ud800"}}\n','native_invalid_message'),(b'{"jsonrpc":"2.0"}','native_partial_eof'),(b'x'*1048577+b'\n','native_message_too_large')]:
        with self.subTest(code=code):
            c=self.client('import sys\nsys.stdout.buffer.write('+repr(raw)+');sys.stdout.flush()\n')
            with self.assertRaises(NativeClientFailure) as caught:c.next_message(Budget(2))
            self.assertEqual(caught.exception.code,code);c.close(Budget())
 def test_timeout_and_cleanup_denial(self):
    c=self.client('import time;time.sleep(.2)\n')
    with self.assertRaises(NativeClientFailure) as caught:c.next_message(Budget(.02))
    self.assertEqual(caught.exception.code,'native_timeout')
    with patch('jasmine_core.adapter.native_client.os.killpg',side_effect=PermissionError('denied')):
        result=c.close(Budget(1))
    self.assertEqual(result['cleanup'],'incomplete_or_denied')
    self.assertTrue(any(d['stage']=='group_kill' and d['type']=='PermissionError' for d in result['cleanup_diagnostics']))
 def test_sink_failure_blocks_send(self):
    c=self.client('import sys;sys.stdin.readline()\n',lambda *args:(_ for _ in ()).throw(OSError('sink failed')))
    with self.assertRaises(NativeClientFailure) as caught:c.send_request('init',{},Budget())
    self.assertEqual(caught.exception.code,'native_receipt_failed')
    with self.assertRaises(NativeClientFailure):c.send_request('again',{},Budget())
 def test_unknown_response(self):
    c=self.client("print('{\"jsonrpc\":\"2.0\",\"id\":999,\"result\":{}}',flush=True)\n")
    with self.assertRaises(NativeClientFailure) as caught:c.next_message(Budget())
    self.assertEqual(caught.exception.code,'native_response_correlation')

 def test_buffered_second_line_cap(self):
    from jasmine_core.adapter.native_client import LIMIT
    c=self.client('import time;time.sleep(.2)\n')
    c.buffer.extend(b'{"method":"short"}\n'+b'x'*(LIMIT+1)+b'\n')
    self.assertEqual(c.next_message(Budget())['method'],'short')
    with self.assertRaises(NativeClientFailure) as caught:c.next_message(Budget())
    self.assertEqual(caught.exception.code,'native_message_too_large')

 def test_next_message_response_remains_waitable(self):
    c=self.client("import sys,json\nr=json.loads(sys.stdin.readline());print(json.dumps({'id':r['id'],'result':42}),flush=True)\n")
    ident=c.send_request('init',{},Budget())
    self.assertEqual(c.next_message(Budget())['result'],42)
    self.assertEqual(c.wait_response(ident,Budget()),42)

 def test_notification_has_no_request_id(self):
    c=self.client("import sys,json\nn=json.loads(sys.stdin.readline());print(json.dumps({'method':'observed','params':n}),flush=True)\n")
    c.send_notification('initialized',{},Budget())
    observed=c.next_message(Budget())['params']
    self.assertEqual(observed,{'method':'initialized','params':{}})
    self.assertEqual(c.outstanding,set())

 def test_pending_receive_time_precedes_slow_sink_and_queue(self):
    def sink(direction,message):
      if direction=='inbound' and message.get('method')=='tool':time.sleep(.04)
    c=self.client("import json,sys\nr=json.loads(sys.stdin.readline());print(json.dumps({'id':'server-1','method':'tool','params':{}}),flush=True);print(json.dumps({'id':r['id'],'result':{}}),flush=True);sys.stdin.readline()\n",sink)
    ident=c.send_request('init',{},Budget())
    c.wait_response(ident,Budget());time.sleep(.03)
    message=c.next_message(Budget());stamp=c.pop_received_at(message)
    self.assertGreaterEqual(time.monotonic()-stamp,.06)
    with self.assertRaises(NativeClientFailure):c.pop_received_at(message)

 def test_actual_notification_timestamp_and_unknown_fields(self):
    actual={'method':'configWarning','params':{'summary':'isolated fixture warning'},'emittedAtMs':1790822400000}
    c=self.client('import json;print(json.dumps('+repr(actual)+'),flush=True)\n')
    message=c.next_message(Budget());self.assertEqual(message,actual)
    self.assertLessEqual(c.pop_received_at(message),time.monotonic())
    for extra in ({'emittedAtMs':True},{'emittedAtMs':-1},{'emittedAtMs':1.5},{'unexpected':1}):
      c=self.client('import json;print(json.dumps('+repr({**actual,**extra})+'),flush=True)\n')
      with self.assertRaisesRegex(NativeClientFailure,'native_invalid_message'):c.next_message(Budget())
