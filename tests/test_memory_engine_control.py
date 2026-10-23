"""Real Core HTTP and child-process startup tests; child speaks labelled stub /version."""
import concurrent.futures,json,os,socket,sys,time
from pathlib import Path
from unittest.mock import patch
from test_api import ApiTestCase,ACTOR,HOST,free_port
from jasmine_core import auth,db,errors,ids,registry
from jasmine_core.memory.engine_control import CONFIG_ENV
from jasmine_core.memory.hindsight import HindsightTransport

STUB='''import sys,json,time,os,faulthandler
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
faulthandler.dump_traceback_later(3, repeat=False)
print('stub: starting', file=sys.stderr, flush=True)
Path(sys.argv[2]).open('a').write(str(os.getpid())+'\\n')
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  b=json.dumps({'api_version':'0.10.2','features':{}}).encode();self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def log_message(self,*a):pass
print('stub: constructing HTTPServer', file=sys.stderr, flush=True)
server=HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler)
# One bounded stack dump also covers a serving child whose probes never arrive.
print('stub: serving', file=sys.stderr, flush=True)
server.serve_forever()
'''
class EngineControlApi(ApiTestCase):
 def setUp(self):
  super().setUp();self.reader=self.key(['memory:read'],'reader');self.operator=self.key(['memory:read','memory:engine:manage'],'engine-only')
  self.root=Path(self._tmp.name);self.port_engine=free_port();self.script=self.root/'engine.py';self.script.write_text(STUB);self.started=self.root/'started';self.profile=self.root/'profile.json'
  self.write_profile([sys.executable,str(self.script),str(self.port_engine),str(self.started)])
  self.env=patch.dict(os.environ,{CONFIG_ENV:str(self.profile),'JASMINE_MEMORY_DEPLOYMENT_MODE':'single_user'});self.env.start();self.addCleanup(self.env.stop)
  # Observe only this fixture's existing readiness calls; never print bodies,
  # headers, environment, profile contents or arbitrary exception messages.
  self.startup_failure_evidence=''
  self.probe_diagnostics=[];original_version=HindsightTransport.version
  original_request=HindsightTransport._request
  def observed_request(transport,method,path,**kwargs):
   if transport.config.origin_host!=f'http://127.0.0.1:{self.port_engine}' or method!='GET' or path!='/version':
    return original_request(transport,method,path,**kwargs)
   try:return original_request(transport,method,path,**kwargs)
   except Exception as exc:
    # version() converts TransportError into a status. Capture its cause here,
    # without exception messages, URLs, headers, response bodies or environment.
    chain=[];cause=exc
    for _ in range(4):
     if cause is None:break
     entry={'type':type(cause).__name__}
     for key in ('errno','code'):
      value=getattr(cause,key,None)
      if isinstance(value,int):entry[key]=value
     chain.append(entry)
     reason=getattr(cause,'reason',None)
     cause=cause.__cause__ or (reason if isinstance(reason,BaseException) else None)
    if len(self.probe_diagnostics)<16:self.probe_diagnostics.append({'request_exception_chain':chain})
    raise
  request_observer=patch.object(HindsightTransport,'_request',observed_request);request_observer.start();self.addCleanup(request_observer.stop)
  def observed_version(transport):
   if transport.config.origin_host!=f'http://127.0.0.1:{self.port_engine}':return original_version(transport)
   try:
    result=original_version(transport)
   except Exception as exc:
    if len(self.probe_diagnostics)<16:self.probe_diagnostics.append({'exception_type':type(exc).__name__})
    raise
   if len(self.probe_diagnostics)<16:
    self.probe_diagnostics.append({'reachable':result.get('reachable'),'error_code':result.get('error_code')})
   return result
  observer=patch.object(HindsightTransport,'version',observed_version);observer.start();self.addCleanup(observer.stop)
 def write_profile(self,command,timeout=5):
  self.profile.write_text(json.dumps({'origin':f'http://127.0.0.1:{self.port_engine}','cwd':str(self.root),'command':command,'environment':{'PRIVATE_SENTINEL':'do-not-return-this'},'timeout_seconds':timeout}))
 def status(self):
  status,value=self.call('GET','/v1/memory/engine',token=self.reader);self.assertEqual(status,200,value);return value
 def wait_done(self):
  until=time.monotonic()+8
  while time.monotonic()<until:
   value=self.status()
   if value['state'] not in ('STOPPED','STARTING'):
    if value['state']=='FAILED':self.startup_diagnostics()
    return value
   time.sleep(.05)
  self.startup_diagnostics();self.fail('operation did not finish')
 def startup_diagnostics(self):
  self.startup_failure_evidence='engine fixture readiness: '+json.dumps(self.probe_diagnostics)
  print(self.startup_failure_evidence,flush=True)
  # Only the generated stub writes this log. Limit output to 16 KiB and do
  # not expose logs from alternative commands/profiles used by other tests.
  if self._testMethodName not in ('test_application_close_stops_the_owned_child','test_concurrent_clicks_share_one_process_and_health_stays_unverified','test_crashed_owned_process_is_reported_unreachable'):return
  try:
   with (self.root/'engine-start.log').open('rb') as log:content=log.read(16384)
   self.startup_failure_evidence+='\nengine fixture startup log: '+content.decode('utf-8',errors='replace')
   print(self.startup_failure_evidence,flush=True)
  except OSError as exc:
   self.startup_failure_evidence+='\nengine fixture log unavailable: '+type(exc).__name__
   print(self.startup_failure_evidence,flush=True)
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
  value=self.wait_done();self.assertEqual(value['state'],'RUNNING',{'status':value,'startup':self.startup_failure_evidence});self.assertEqual(value['api_version'],'0.10.2');self.assertEqual(len(self.started.read_text().splitlines()),1)
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
  self.call('POST','/v1/memory/engine/start',{},token=self.operator);self.assertEqual(self.wait_done()['state'],'RUNNING',self.startup_failure_evidence)
  os.kill(int(self.started.read_text().strip()),15)
  end=time.monotonic()+5
  while time.monotonic()<end:
   value=self.status()
   if value['state']=='FAILED':break
   time.sleep(.05)
  self.assertEqual(value['state'],'FAILED')
  _,health=self.call('GET','/v1/memory/health',token=self.reader);self.assertEqual(health['status'],'unreachable');self.assertFalse(health['reachable'])
 def test_application_close_stops_the_owned_child(self):
  self.call('POST','/v1/memory/engine/start',{},token=self.operator);self.assertEqual(self.wait_done()['state'],'RUNNING',self.startup_failure_evidence);pid=int(self.started.read_text().strip());self.app.close()
  with self.assertRaises(ProcessLookupError):os.kill(pid,0)
