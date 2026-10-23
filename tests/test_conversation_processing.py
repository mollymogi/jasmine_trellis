"""Daily queue integration over real Core kernels; model is a labeled simulation."""
import json,unittest,importlib.util,tempfile
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
from support import DbTestCase
from test_interpretations import Simulation,output
from jasmine_core import SCHEMA_VERSION,db,ids
from jasmine_core.registry import Registry
from jasmine_core.objects import ObjectStore
from jasmine_core.models import NewObject,NewEvent
from jasmine_core.events import EventStore
from jasmine_core.conversation_processing import ConversationProcessing
from jasmine_core.reviews import ReviewStore
from jasmine_core.memory.jobs import JobStore
from jasmine_core.memory.store import MemoryStore
from jasmine_core.memory.eligibility import SourceRegistry
from jasmine_core.interpreter_provider import ProviderFailure

class Processing(DbTestCase):
 def setUp(self):
  super().setUp();reg=Registry(self.conn);self.host=ids.new_id('hst');self.human=ids.new_id('act');self.system=ids.new_id('act');reg.upsert_host(self.host)
  reg.upsert_actor(self.human,kind='human',home_host_id=self.host);reg.upsert_actor(self.system,kind='system',home_host_id=self.host)
  self.project=ObjectStore(self.conn,schema_version=SCHEMA_VERSION).create(NewObject.project({'name':'Daily','host_id':self.host}),actor_id=self.human)['object']['project_id']
  self.kind='NO_STRUCTURE';self.fail=False
  def extract(source):
   self.assertFalse(self.conn.in_transaction)
   if self.fail:raise ProviderFailure('provider_timeout')
   result=output(source);c=result['candidates'][0];c.update(kind=self.kind,content=source['text'])
   if self.kind in ('RULE','DECISION'):c['scope'].update(kind='PROJECT',task_id=None)
   return json.dumps(result)
  self.fake=Simulation(extract);self.worker=ConversationProcessing(self.conn,schema_version=SCHEMA_VERSION,processor_actor_id=self.system,host_id=self.host,provider=self.fake)
  self.worker.activate()
 def event(self,text='你好',assistant=False,project=None,source='codex-native-conversation-v1'):
  return EventStore(self.conn,schema_version=SCHEMA_VERSION).append(NewEvent(event_type='assistant.message' if assistant else 'user.prompt',source_system=source,occurred_at=self.clock.now(),actor_id=self.system if assistant else self.human,actor_kind='system' if assistant else 'human',host_id=self.host,project_id=project or self.project,payload={'text':text}))[0]['event_id']
 def test_greeting_exactly_once_and_no_memory(self):
  ident=self.event();self.assertEqual(self.worker.tick()['status'],'NO_ACTION');self.assertEqual(self.worker.tick()['status'],'idle');self.assertEqual(self.fake.calls,1)
  self.assertEqual(self.worker.list(event_id=ident)['items'][0]['source_text'],'你好');self.assertEqual(self.conn.execute('select count(*) from memory_jobs').fetchone()[0],0)
 def test_project_rule_waits_for_review_then_memory_queue_links_back(self):
  self.kind='RULE';event=self.event('项目文档统一使用中文');self.assertEqual(self.worker.tick()['status'],'PENDING_REVIEW')
  row=self.worker.list(event_id=event)['items'][0];self.assertEqual(row['memory_items'],[]);self.assertIsNotNone(row['review_id']);self.assertEqual(self.conn.execute('select count(*) from rules').fetchone()[0],0)
  review=ReviewStore(self.worker.resolver).get(row['review_id']);p=review['preview']
  result=ReviewStore(self.worker.resolver).change(row['review_id'],'approve',{'idempotency_key':'operator-approve','host_id':self.host,'expected_revision':1,'reason':'Confirm real project convention','expected_revisions':p['expected_revisions'],'expected_context_digest':p['expected_context_digest'],'actions':[{'candidate_index':0,'action':'CREATE_RULE','payload':{}}]},actor_id=self.human,scopes={'authority:propose','authority:manage'})
  JobStore(self.conn,MemoryStore(self.conn,schema_version=SCHEMA_VERSION)).enqueue_from_sources(SourceRegistry())
  self.worker.reconcile();row=self.worker.list(event_id=event)['items'][0]
  self.assertEqual(row['status'],'APPLIED');self.assertEqual(row['effect'],'MEMORY_ELIGIBLE');self.assertEqual(len(row['memory_items']),1);self.assertEqual(row['memory_items'][0]['engine_state'],'PENDING_RETAIN');self.assertEqual(row['memory_jobs'][0]['status'],'PENDING')
 def test_assistant_message_never_becomes_system_config_or_auto_applies(self):
  self.kind='RULE';event=self.event('测试全部通过，可以上线',assistant=True);self.assertEqual(self.worker.tick()['status'],'idle')
  self.assertEqual(self.worker.enqueue({'event_id':event},actor_id=self.human)['queued'],1)
  self.assertEqual(self.worker.tick()['status'],'PENDING_REVIEW');item=self.worker.list(event_id=event)['items'][0]
  self.assertEqual(self.worker.interpretations.get(item['interpretation_id'])['source_type'],'AGENT_PROPOSED');self.assertEqual(self.conn.execute('select count(*) from evidence').fetchone()[0],0)
 def test_failed_provider_retries_as_audited_child(self):
  event=self.event();self.fail=True;self.assertEqual(self.worker.tick()['status'],'FAILED');first=self.worker.list(event_id=event)['items'][0]['interpretation_id']
  self.assertEqual(self.worker.list(event_id=event)['items'][0]['error_code'],'provider_timeout');self.fail=False
  self.worker.retry(event,actor_id=self.human)
  with patch('jasmine_core.interpretations.configured_provider',return_value=self.fake):out=self.worker.tick()
  self.assertEqual(out['status'],'NO_ACTION');second=self.worker.list(event_id=event)['items'][0]['interpretation_id'];self.assertNotEqual(first,second);self.assertEqual(self.worker.interpretations.get(second)['parent_interpretation_id'],first)
 def test_only_current_new_messages_auto_queue_history_is_explicit_and_idempotent(self):
  event=self.event();self.worker.scan();self.assertEqual(self.worker.enqueue({'event_id':event},actor_id=self.human)['queued'],0)
  self.assertEqual(self.worker.enqueue({'project_id':self.project},actor_id=self.human)['queued'],0)
  foreign=self.event(source='foreign-unconfigured-producer')
  with self.assertRaises(Exception):self.worker.enqueue({'event_id':foreign},actor_id=self.human)
  self.assertEqual(self.worker.list()['count'],1)
 def test_concurrent_claim_and_crash_reservation_replay(self):
  event=self.event();self.worker.scan();claimed=self.worker.claim('first');self.assertIsNotNone(claimed)
  other=db.connect(self.db_path)
  try:
   second=ConversationProcessing(other,schema_version=SCHEMA_VERSION,processor_actor_id=self.system,host_id=self.host,provider=self.fake)
   self.assertIsNone(second.claim('second'))
  finally:other.close()
  out=self.worker.interpretations.process({'event_id':event,'idempotency_key':'daily:'+event},actor_id=self.system,on_reserved=lambda ident:self.conn.execute('update conversation_processing set interpretation_id=? where event_id=?',(ident,event)))
  self.conn.execute("update conversation_processing set lease_until='2000-01-01T00:00:00.000000Z' where event_id=?",(event,));self.assertEqual(self.worker.tick()['status'],'NO_ACTION');self.assertEqual(self.fake.calls,1)
 def test_project_scope_is_preserved_and_arbitrary_text_cannot_be_queued(self):
  self.event();self.worker.tick();other=ObjectStore(self.conn,schema_version=SCHEMA_VERSION).create(NewObject.project({'name':'Other','host_id':self.host}),actor_id=self.human)['object']['project_id']
  self.assertEqual(self.worker.list(project_id=other)['count'],0)
  with self.assertRaises(Exception):self.worker.enqueue({'project_id':self.project,'text':'fabricated'},actor_id=self.human)
 def test_activation_does_not_backfill_existing_native_history(self):
  self.conn.execute("DELETE FROM core_meta WHERE key IN ('conversation_processing_cursor','conversation_processing_started_at')")
  past=self.event('旧消息');self.worker.activate();self.assertEqual(self.worker.tick()['status'],'idle');self.assertEqual(self.worker.list()['count'],0)
  self.assertEqual(self.worker.enqueue({'event_id':past},actor_id=self.human)['queued'],1);self.assertEqual(self.worker.tick()['status'],'NO_ACTION')
 def test_replayed_source_links_the_actual_canonical_memory(self):
  self.kind='RULE';event=self.event('本项目文档统一使用中文')
  first=self.worker.interpretations.process({'event_id':event,'idempotency_key':'earlier-extraction'},actor_id=self.system)['interpretation']['interpretation_id']
  p=self.worker.resolver.preview(first);resolved=self.worker.resolver.resolve(first,{'idempotency_key':'earlier-resolution','host_id':self.host,'policy_version':p['policy_version'],'expected_revisions':p['expected_revisions'],'expected_context_digest':p['expected_context_digest']},actor_id=self.system)['resolution']
  review=ReviewStore(self.worker.resolver).get(resolved['result']['review_id']);p=review['preview']
  ReviewStore(self.worker.resolver).change(review['review_id'],'approve',{'idempotency_key':'earlier-approval','host_id':self.host,'expected_revision':1,'reason':'Confirm project convention','expected_revisions':p['expected_revisions'],'expected_context_digest':p['expected_context_digest'],'actions':[{'candidate_index':0,'action':'CREATE_RULE','payload':{}}]},actor_id=self.human,scopes={'authority:propose','authority:manage'})
  JobStore(self.conn,MemoryStore(self.conn,schema_version=SCHEMA_VERSION)).enqueue_from_sources(SourceRegistry())
  self.assertEqual(self.worker.tick()['status'],'APPLIED');row=self.worker.list(event_id=event)['items'][0];self.assertEqual(row['effect'],'MEMORY_ELIGIBLE');self.assertEqual(len(row['memory_items']),1);self.assertEqual(self.conn.execute('select count(*) from rules').fetchone()[0],1)
 def test_deployment_recovers_metadata_after_matching_database_restore(self):
  script=Path(__file__).resolve().parents[1]/'scripts/deployment/prepare-conversation-processing.py';spec=importlib.util.spec_from_file_location('prepare_daily',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);config=root/'config';config.mkdir();(config/'runtime.json').write_text(json.dumps({'database':str(self.db_path)}));(config/'codex-conversation-device.json').write_text(json.dumps({'host_id':self.host}));orphan=ids.new_id('act');(config/'conversation-processing-actor.json').write_text(json.dumps({'actor_id':orphan,'host_id':self.host}))
   provider=Mock(valid=True,config={'model':'component-only'})
   with patch.object(module,'CodexProvider',return_value=provider):result=module.prepare(root,script,script,True)
   self.assertEqual(result['status'],'prepared');actor=self.conn.execute('SELECT kind,home_host_id FROM actors WHERE actor_id=?',(orphan,)).fetchone();self.assertEqual((actor['kind'],actor['home_host_id']),('system',self.host))
 def test_processing_audit_is_append_only(self):
  self.event();self.worker.tick()
  with self.assertRaises(Exception):self.conn.execute('delete from conversation_processing_changes')

if __name__=='__main__':unittest.main()

from test_api import ApiTestCase,HOST
from jasmine_core import registry
import os
class ProcessingHttp(ApiTestCase):
 def setUp(self):
  super().setUp();conn=db.connect(self.db_path);self.system=ids.new_id('act');registry.Registry(conn).upsert_actor(self.system,kind='system',home_host_id=HOST);conn.close()
  self.operator=self.key(['interpretations:manage','interpretations:read','events:read','resolutions:read','memory:read'])
  self.env=patch.dict(os.environ,{'JASMINE_CORE_RESOLVER_ACTOR_ID':self.system,'JASMINE_CORE_RESOLVER_HOST_ID':HOST});self.env.start();self.addCleanup(self.env.stop)
  _,created=self.call('POST','/v1/projects',{'name':'HTTP processing','host_id':HOST},token=self.admin_token);self.project=created['object']['project_id']
  _,created=self.call('POST','/v1/events',{'event_type':'user.prompt','source_system':'grok-native-conversation-v1','host_id':HOST,'project_id':self.project,'payload':{'text':'你好'}},token=self.admin_token);self.event=created['event']['event_id']
 def test_manager_can_queue_existing_event_without_model_or_truth_write(self):
  token=self.key(['interpretations:manage']);status,result=self.call('POST','/v1/conversation-processing/enqueue',{'event_id':self.event},token=token);self.assertEqual(status,202,result);self.assertEqual(result['queued'],1)
  status,result=self.call('POST','/v1/conversation-processing/enqueue',{'event_id':self.event},token=token);self.assertEqual(result['queued'],0)
  status,page=self.call('GET','/v1/conversation-processing?project_id='+self.project,token=self.operator);self.assertEqual(status,200,page);self.assertEqual(page['items'][0]['status'],'PENDING');self.assertEqual(page['items'][0]['source_text'],'你好')
 def test_reader_cannot_enqueue_or_read_message_details_without_all_read_scopes(self):
  reader=self.key(['interpretations:read']);self.assertEqual(self.call('POST','/v1/conversation-processing/enqueue',{'event_id':self.event},token=reader)[0],403);self.assertEqual(self.call('GET','/v1/conversation-processing',token=reader)[0],403)
 def test_queue_rejects_supplied_text_and_invalid_scopes(self):
  self.assertEqual(self.call('POST','/v1/conversation-processing/enqueue',{'event_id':self.event,'text':'injected'},token=self.operator)[0],400)
  self.assertEqual(self.call('POST','/v1/conversation-processing/enqueue',{},token=self.operator)[0],400)
  self.assertEqual(self.call('GET','/v1/conversation-processing?limit=500',token=self.operator)[0],400)
 def test_only_failed_processing_can_retry(self):
  self.call('POST','/v1/conversation-processing/enqueue',{'event_id':self.event},token=self.operator)
  self.assertEqual(self.call('POST','/v1/conversation-processing/'+self.event+'/retry',{},token=self.operator)[0],400)
