"""Real Core HTTP and child-process startup tests; child speaks labelled stub /version."""
import concurrent.futures,json,os,socket,sys,time
from collections import deque
from pathlib import Path
from unittest.mock import patch
from test_api import ApiTestCase,ACTOR,HOST,free_port
from jasmine_core import auth,db,errors,ids,registry
from jasmine_core.memory.engine_control import CONFIG_ENV
from jasmine_core.memory.hindsight import HindsightTransport

STUB='''import sys,json,time,os
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
Path(sys.argv[2]).open('a').write(str(os.getpid())+'\\n')
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  b=json.dumps({'api_version':'0.10.2','features':{}}).encode();self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def log_message(self,*a):pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
'''
class EngineControlApi(ApiTestCase):
 def setUp(self):
  super().setUp();self.reader=self.key(['memory:read'],'reader');self.operator=self.key(['memory:read','memory:engine:manage'],'engine-only')
  self.root=Path(self._tmp.name);self.port_engine=free_port();self.script=self.root/'engine.py';self.script.write_text(STUB);self.started=self.root/'started';self.profile=self.root/'profile.json'
  self.write_profile([sys.executable,str(self.script),str(self.port_engine),str(self.started)])
  self.env=patch.dict(os.environ,{CONFIG_ENV:str(self.profile),'JASMINE_MEMORY_DEPLOYMENT_MODE':'single_user'});self.env.start();self.addCleanup(self.env.stop)
  self.startup_diagnostics = self._testMethodName in (
   'test_application_close_stops_the_owned_child',
   'test_concurrent_clicks_share_one_process_and_health_stays_unverified',
   'test_crashed_owned_process_is_reported_unreachable')
  if self.startup_diagnostics:
   # Only this known, single-threaded stub is dumped. Never signal a PID read
   # from disk or inspect another process. Cancel once HTTPServer has bound.
   self.script.write_text('import faulthandler\nfaulthandler.dump_traceback_later(3, repeat=False)\n' +
    STUB.replace("HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()",
     "server=HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler)\nfaulthandler.cancel_dump_traceback_later()\nserver.serve_forever()"))
   self.probe_diagnostics = deque(maxlen=12)
   records = self.probe_diagnostics
   class DiagnosticTransport(HindsightTransport):
    def _request(self, *args, **kwargs):
     try:
      result = super()._request(*args, **kwargs)
     except Exception as exc:
      # Exception messages, URLs, headers and bodies may contain secrets.
      # Keep only exception classes and numeric OS/HTTP error codes.
      chain = []
      current = exc
      for _ in range(4):
       if current is None:break
       chain.append({'type':type(current).__name__,
        'errno':getattr(current,'errno',None) if isinstance(getattr(current,'errno',None),int) else None})
       current = current.__cause__
      records.append({'exceptions':chain,
       'http_status':getattr(exc,'http_status',None) if isinstance(getattr(exc,'http_status',None),int) else None})
      raise
     records.append({'request':'completed'})
     return result
    def version(self):
     result = super().version()
     records.append({'reachable':result.get('reachable'),
      'error_code':result.get('error_code')})
     return result
   diagnostic_patch = patch('jasmine_core.memory.engine_control.HindsightTransport',DiagnosticTransport)
   diagnostic_patch.start();self.addCleanup(diagnostic_patch.stop)
 def write_profile(self,command,timeout=5):
  self.profile.write_text(json.dumps({'origin':f'http://127.0.0.1:{self.port_engine}','cwd':str(self.root),'command':command,'environment':{'PRIVATE_SENTINEL':'do-not-return-this'},'timeout_seconds':timeout}))
 def status(self):
  status,value=self.call('GET','/v1/memory/engine',token=self.reader);self.assertEqual(status,200,value);return value
 def wait_done(self):
  until=time.monotonic()+8
  while time.monotonic()<until:
   value=self.status()
   if value['state'] not in ('STOPPED','STARTING'):
    if self.startup_diagnostics and value['state'] != 'RUNNING':self.report_startup_diagnostics()
    return value
   time.sleep(.05)
  if self.startup_diagnostics:self.report_startup_diagnostics()
  self.fail('operation did not finish')
 def report_startup_diagnostics(self):
  print('\nengine startup diagnostics: '+self._testMethodName,file=sys.stderr)
  print(json.dumps(list(self.probe_diagnostics)),file=sys.stderr)
  # This file contains only output from the fixed test stub (no credentials,
  # environment dump or request headers). Bound the read and emitted output.
  try:
   with (self.root/'engine-start.log').open('rb') as log:
    output = log.read(16384)
   print('stub engine-start.log (first 16384 bytes):\n'+output.decode('utf-8',errors='replace'),file=sys.stderr)
  except OSError as exc:
   print('stub log unavailable: '+type(exc).__name__,file=sys.stderr)
 def test_unauthenticated_and_readonly_start_are_refused(self):
  self.assertEqual(self.call('POST','/v1/memory/engine/start',{})[0],401)
  self.assertFalse(self.status()['can_start'])
  self.assertEqual(self.call('POST','/v1/memory/engine/start',{},token=self.reader)[0],403)
  self.assertFalse(self.started.exists())
 def test_agent_cannot_receive_or_use_engine_management(self):
  conn=db.connect(self.db_path);self.addCleanup(conn.close);agent=ids.new_id('act')
  with db.transaction(conn):registry.Registry(conn).upsert_actor(agent,kind='agent',home_host_id=HOST)
  with self.assertRaises(errors.ForbiddenActorKind):auth.Auth(conn).issue_key(actor_id=agent,label='agent',scopes=['memory:engine:manage'])
  key=auth.Auth(conn).issue_key(actor_id=agent,label='legacy-bad-grant',scopes=['memory:read'])
  with db.transaction(conn):conn.execute('UPDATE api_keys SET scopes=? WHERE key_id=?',(json.dumps(['memory:read','memory:engine:manage']),key['key_id']))
  self.assertEqual(self.call('POST','/v1/memory/engine/start',{},token=key['token'])[0],403)
  self.assertFalse(self.started.exists())
 def test_request_cannot_supply_a_command_or_destination(self):
  for body in [{'command':['touch','bad']},{'origin':'http://example.com'},{'environment':{'HOME':'bad'}}]:
   self.assertEqual(self.call('POST','/v1/memory/engine/start',body,token=self.operator)[0],400)
  self.assertEqual(self.call('POST','/v1/memory/engine/start?command=bad',{},token=self.operator)[0],400)
  self.assertFalse(self.started.exists())
 def test_concurrent_clicks_share_one_process_and_health_stays_unverified(self):
  conn=db.connect(self.db_path);self.addCleanup(conn.close)
  before={t:list(conn.execute('SELECT * FROM '+t)) for t in ['events','rules','memory_items','memory_jobs']}
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
   values=list(pool.map(lambda _:self.call('POST','/v1/memory/engine/start',{},token=self.operator),range(4)))
  self.assertTrue(all(s==202 for s,_ in values));self.assertEqual(len({v['operation_id'] for _,v in values}),1)
  value=self.wait_done();self.assertEqual(value['state'],'RUNNING',value);self.assertEqual(value['api_version'],'0.10.2');self.assertEqual(len(self.started.read_text().splitlines()),1)
  self.assertNotIn('command',value);self.assertNotIn('do-not-return-this',json.dumps(value))
  status,health=self.call('GET','/v1/memory/health',token=self.reader);self.assertEqual(status,200,health);self.assertEqual(health['status'],'idle');self.assertTrue(health['configured']);self.assertTrue(health['reachable']);self.assertIsNone(health['last_success_at'])
  self.assertEqual(before,{t:list(conn.execute('SELECT * FROM '+t)) for t in before})
  self.assertEqual(self.call('POST','/v1/memory/engine/start',{},token=self.operator)[1]['operation_id'],value['operation_id'])
 def test_failed_exit_is_not_running_and_retry_is_a_new_operation(self):
  self.write_profile([sys.executable,'-c','raise SystemExit(3)'])
  _,first=self.call('POST','/v1/memory/engine/start',{},token=self.operator);value=self.wait_done();self.assertEqual(value['state'],'FAILED');self.assertEqual(value['error_code'],'engine_exited')
  _,second=self.call('POST','/v1/memory/engine/start',{},token=self.operator);self.assertNotEqual(first['operation_id'],second['operation_id']);self.wait_done()
 def test_busy_port_is_not_adopted_or_stopped(self):
  with socket.socket() as sock:
   sock.bind(('127.0.0.1',self.port_engine));sock.listen()
   self.call('POST','/v1/memory/engine/start',{},token=self.operator);value=self.wait_done();self.assertEqual(value['error_code'],'engine_port_in_use');self.assertFalse(self.started.exists());self.assertGreater(sock.fileno(),0)
 def test_timeout_cleans_up_the_owned_process(self):
  sleeper=self.root/'sleeper.py';sleeper.write_text("import os,time,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(30)\n")
  self.write_profile([sys.executable,str(sleeper),str(self.started)],timeout=1)
  self.call('POST','/v1/memory/engine/start',{},token=self.operator);value=self.wait_done();self.assertEqual(value['error_code'],'engine_start_timeout')
  with self.assertRaises(ProcessLookupError):os.kill(int(self.started.read_text()),0)
 def test_no_operator_profile_has_no_start_capability(self):
  with patch.dict(os.environ,{CONFIG_ENV:''}):
   self.assertFalse(self.status()['enabled']);self.assertEqual(self.call('POST','/v1/memory/engine/start',{},token=self.operator)[0],400)
 def test_crashed_owned_process_is_reported_unreachable(self):
  self.call('POST','/v1/memory/engine/start',{},token=self.operator);self.assertEqual(self.wait_done()['state'],'RUNNING')
  os.kill(int(self.started.read_text().strip()),15)
  end=time.monotonic()+5
  while time.monotonic()<end:
   value=self.status()
   if value['state']=='FAILED':break
   time.sleep(.05)
  self.assertEqual(value['state'],'FAILED')
  _,health=self.call('GET','/v1/memory/health',token=self.reader);self.assertEqual(health['status'],'unreachable');self.assertFalse(health['reachable'])
 def test_application_close_stops_the_owned_child(self):
  self.call('POST','/v1/memory/engine/start',{},token=self.operator);self.assertEqual(self.wait_done()['state'],'RUNNING');pid=int(self.started.read_text().strip());self.app.close()
  with self.assertRaises(ProcessLookupError):os.kill(pid,0)
