"""Daily native message -> existing Interpreter/Resolver -> eligible Memory sources.

The queue is a projection, never a new Truth store. Network work happens outside
write transactions. Worker restarts replay reserved interpretations/resolutions;
foreign-host sources, arbitrary text, and assistant self-reported outcomes cannot
acquire human authority. Existing P2 policy and P4 eligibility remain in force.
"""
from __future__ import annotations
import json,os,socket,uuid
from datetime import datetime,timedelta,timezone
from . import clock,db,errors,ids
from .canonical import canonical_json
from .events import EventStore
from .interpretations import InterpretationStore
from .resolver import ResolverStore
from .interpretation_maintenance import MaintenanceStore

SOURCES=('codex-native-conversation-v1','claude-native-conversation-v1','grok-native-conversation-v1')
CURSOR='conversation_processing_cursor'
ACTIVATED='conversation_processing_started_at'
# A rate-limited provider is an account-wide condition, not a property of one
# message: every claim during it fails the same way and spends quota. So the
# whole queue cools down (5 min, doubling to 1 h, reset by a success) and the
# message goes back to PENDING in its original place, failing only after
# RATE_LIMIT_ATTEMPTS consecutive rate limits since it was last (re)queued.
COOLDOWN_UNTIL='conversation_processing_cooldown_until'
COOLDOWN_SECONDS='conversation_processing_cooldown_seconds'
COOLDOWN_MIN,COOLDOWN_MAX,RATE_LIMIT_ATTEMPTS=300,3600,5

def _future(seconds):return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='microseconds').replace('+00:00','Z')

class ConversationProcessing:
 def __init__(self,conn,*,schema_version,processor_actor_id=None,host_id=None,provider=None):
  self.conn=conn;self.schema_version=schema_version
  self.actor=processor_actor_id or os.environ.get('JASMINE_CORE_RESOLVER_ACTOR_ID')
  self.host=host_id or os.environ.get('JASMINE_CORE_RESOLVER_HOST_ID')
  self.interpretations=InterpretationStore(conn,schema_version=schema_version,provider=provider)
  self.resolver=ResolverStore(self.interpretations,execution_actor_id=self.actor,execution_host_id=self.host)
  self.events=EventStore(conn,schema_version=schema_version)
 def _executor(self):
  row=self.conn.execute('SELECT kind,home_host_id FROM actors WHERE actor_id=?',(self.actor,)).fetchone()
  if not row or row['kind']!='system' or row['home_host_id']!=self.host:raise errors.InvalidRequest('daily processor principal is not configured')
 def _change(self,event,action,actor=None,**details):
  self.conn.execute('INSERT INTO conversation_processing_changes(event_id,action,actor_id,detail_json,created_at) VALUES(?,?,?,?,?)',(event,action,actor,canonical_json(details),clock.now_rfc3339()))
 def activate(self,*,include_existing=False):
  self._executor()
  with db.transaction(self.conn):
   if db.get_meta(self.conn,CURSOR,None) is None:
    seq=0 if include_existing else self.conn.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    db.set_meta(self.conn,CURSOR,str(seq));db.set_meta(self.conn,ACTIVATED,clock.now_rfc3339())
 def _eligible(self,event):
  return (event['source_system'] in SOURCES and event['event_type'] in ('user.prompt','assistant.message')
          and event['project_id'] is not None and event['host_id']==self.host
          and bool(event['payload'].get('text')))
 def _enqueue(self,event,actor):
  if not self._eligible(event):raise errors.InvalidRequest('source is not a local captured conversation message')
  stamp=clock.now_rfc3339()
  changed=self.conn.execute("INSERT OR IGNORE INTO conversation_processing(event_id,status,queued_at,updated_at) VALUES(?,'PENDING',?,?)",(event['event_id'],stamp,stamp)).rowcount
  if changed:self._change(event['event_id'],'ENQUEUED',actor)
  return bool(changed)
 def scan(self,limit=200):
  self._executor();self.activate()
  with db.transaction(self.conn):
   cursor=int(db.get_meta(self.conn,CURSOR,0));rows=self.conn.execute('SELECT event_id,seq FROM events WHERE seq>? ORDER BY seq LIMIT ?',(cursor,limit)).fetchall();created=0
   for row in rows:
    event=self.events.get(row['event_id'])
    # The original daily trigger is user input. Assistant proposals can be
    # requested explicitly from the UI, and never become SYSTEM_CONFIG.
    if event['event_type']=='user.prompt' and self._eligible(event):created+=self._enqueue(event,self.actor)
   if rows:db.set_meta(self.conn,CURSOR,str(rows[-1]['seq']))
   return created
 def enqueue(self,body,*,actor_id):
  self._executor()
  if not isinstance(body,dict) or set(body)-{'project_id','session_id','event_id'} or not body:raise errors.InvalidRequest('select an event, session or project')
  if 'event_id' in body:
   event=self.events.get(body['event_id'])
   if not self._eligible(event) or any(event[name]!=body[name] for name in ('project_id','session_id') if name in body):raise errors.InvalidRequest('source scope mismatch or unsupported producer')
  clauses=['e.source_system IN (?,?,?)',"e.event_type IN ('user.prompt','assistant.message')",'e.host_id=?'];params=[*SOURCES,self.host]
  for name,prefix in [('project_id','prj'),('session_id','ses'),('event_id','evt')]:
   if name in body:
    if not ids.is_id(body[name],prefix):raise errors.InvalidRequest('invalid source scope')
    clauses.append('e.'+name+'=?');params.append(body[name])
  # Explicit project/session backfill includes user inputs; assistant text
  # requires choosing that exact Event, avoiding noisy bulk generated output.
  if 'event_id' not in body:clauses.append("e.event_type='user.prompt'")
  with db.transaction(self.conn):
   rows=self.conn.execute('SELECT e.event_id FROM events e LEFT JOIN conversation_processing q USING(event_id) WHERE '+' AND '.join(clauses)+' AND q.event_id IS NULL ORDER BY e.seq LIMIT 201',params).fetchall()
   if len(rows)>200:raise errors.InvalidRequest('select a smaller scope (at most 200 unprocessed messages)')
   created=sum(self._enqueue(self.events.get(row['event_id']),actor_id) for row in rows)
  return {'queued':created,'scope':body}
 def retry(self,event_id,*,actor_id):
  with db.transaction(self.conn):
   row=self.conn.execute('SELECT * FROM conversation_processing WHERE event_id=?',(event_id,)).fetchone()
   if not row:raise errors.NotFound('conversation_processing',event_id)
   if row['status']!='FAILED':raise errors.InvalidRequest('only failed processing may be retried')
   self.conn.execute("UPDATE conversation_processing SET status='PENDING',error_code=NULL,updated_at=? WHERE event_id=?",(clock.now_rfc3339(),event_id));self._change(event_id,'RETRY_REQUESTED',actor_id)
  return {'status':'PENDING','event_id':event_id}
 def cooldown_until(self):
  until=db.get_meta(self.conn,COOLDOWN_UNTIL,None)
  return until if until and until>clock.now_rfc3339() else None
 def _rate_limits(self,event_id):
  return self.conn.execute("SELECT COUNT(*) FROM conversation_processing_changes WHERE event_id=? AND action='RATE_LIMITED' AND seq>COALESCE((SELECT MAX(seq) FROM conversation_processing_changes WHERE event_id=? AND action IN ('ENQUEUED','RETRY_REQUESTED')),0)",(event_id,event_id)).fetchone()[0]
 def _rate_limited(self,event_id,owner,code):
  """Requeue in place and start (or lengthen) the queue-wide cooldown."""
  with db.transaction(self.conn):
   seconds=min(max(int(db.get_meta(self.conn,COOLDOWN_SECONDS,0) or 0)*2,COOLDOWN_MIN),COOLDOWN_MAX);until=_future(seconds)
   changed=self.conn.execute("UPDATE conversation_processing SET status='PENDING',error_code=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE event_id=? AND status='PROCESSING' AND lease_owner=?",(code,clock.now_rfc3339(),event_id,owner)).rowcount
   if not changed:raise errors.InvalidRequest('daily processing lease lost')
   db.set_meta(self.conn,COOLDOWN_SECONDS,str(seconds));db.set_meta(self.conn,COOLDOWN_UNTIL,until)
   self._change(event_id,'RATE_LIMITED',self.actor,error_code=code,cooldown_seconds=seconds,cooldown_until=until)
  return {'event_id':event_id,'status':'RATE_LIMITED','error_code':code,'cooldown_until':until}
 def claim(self,owner,lease_seconds=180):
  self._executor()
  if self.cooldown_until():return None
  with db.transaction(self.conn):
   now=clock.now_rfc3339();row=self.conn.execute("SELECT * FROM conversation_processing WHERE status='PENDING' OR (status='PROCESSING' AND lease_until<=?) ORDER BY queued_at,event_id LIMIT 1",(now,)).fetchone()
   if not row:return None
   self.conn.execute("UPDATE conversation_processing SET status='PROCESSING',attempt=attempt+1,lease_owner=?,lease_until=?,error_code=NULL,updated_at=? WHERE event_id=?",(owner,_future(lease_seconds),now,row['event_id']))
   self._change(row['event_id'],'CLAIMED',self.actor,attempt=row['attempt']+1)
   return dict(self.conn.execute('SELECT * FROM conversation_processing WHERE event_id=?',(row['event_id'],)).fetchone())
 def _save(self,event,owner,**values):
  with db.transaction(self.conn):
   values['updated_at']=clock.now_rfc3339()
   changed=self.conn.execute('UPDATE conversation_processing SET '+','.join(k+'=?' for k in values)+" WHERE event_id=? AND status='PROCESSING' AND lease_owner=?",[*values.values(),event,owner]).rowcount
   if not changed:raise errors.InvalidRequest('daily processing lease lost')
 def tick(self,owner=None):
  with db.transaction(self.conn):db.set_meta(self.conn,'conversation_processing_heartbeat',clock.now_rfc3339())
  self.scan();self.reconcile();owner=owner or socket.gethostname()+':'+str(os.getpid())+':'+uuid.uuid4().hex
  row=self.claim(owner)
  if not row:
   until=self.cooldown_until()
   return {'status':'idle','cooldown_until':until} if until else {'status':'idle'}
  event_id=row['event_id']
  try:
   ident=row['interpretation_id']
   if ident:
    maintenance=MaintenanceStore(self.interpretations);head=maintenance.head(ident);ident=head['current_interpretation_id'];item=self.interpretations.get(ident)
    if item['status'] in ('FAILED','INTERRUPTED'):
     out=maintenance.change(ident,'rerun',{'idempotency_key':'daily-rerun:'+event_id+':'+str(row['attempt']),'host_id':self.host,'reason':'Retry daily conversation processing','expected_revision':head['revision']},actor_id=self.actor)
     item=out.get('interpretation') or self.interpretations.get(out['result']['child_interpretation_id']);ident=item['interpretation_id']
   else:
    out=self.interpretations.process({'event_id':event_id,'idempotency_key':'daily:'+event_id},actor_id=self.actor,on_reserved=lambda ident:self.conn.execute('UPDATE conversation_processing SET interpretation_id=? WHERE event_id=? AND lease_owner=?',(ident,event_id,owner)))
    item=out['interpretation'];ident=item['interpretation_id']
   self._save(event_id,owner,interpretation_id=ident)
   if item['status']!='EXTRACTED':raise errors.InvalidRequest('interpreter_failed',provider_code=item.get('error_code') or item['status'])
   # resolve() commits before the queue projection is saved. Recover that
   # durable result before previewing context that may have changed since then.
   request_key='daily-resolve:'+ident
   prior=self.conn.execute('SELECT resolution_id FROM resolution_request_keys WHERE actor_id=? AND idempotency_key=?',(self.actor,request_key)).fetchone()
   if prior:resolution=self.resolver.get(prior['resolution_id'])
   else:
    preview=self.resolver.preview(ident)
    resolution=self.resolver.resolve(ident,{'idempotency_key':request_key,'host_id':self.host,'policy_version':preview['policy_version'],'expected_revisions':preview['expected_revisions'],'expected_context_digest':preview['expected_context_digest']},actor_id=self.actor)['resolution']
   disposition=resolution['result']['disposition'];status='APPLIED' if disposition=='SOURCE_REPLAYED' else disposition
   self._save(event_id,owner,status=status,resolution_id=resolution['resolution_id'],lease_owner=None,lease_until=None)
   with db.transaction(self.conn):
    self._change(event_id,status,self.actor,interpretation_id=ident,resolution_id=resolution['resolution_id'])
    # The provider answered: the next rate limit starts from the minimum again.
    if db.get_meta(self.conn,COOLDOWN_SECONDS,None) not in (None,'0'):db.set_meta(self.conn,COOLDOWN_SECONDS,'0')
   return {'event_id':event_id,'status':status}
  except Exception as exc:
   code=getattr(exc,'details',{}).get('provider_code') or getattr(exc,'code',type(exc).__name__)
   if str(code).startswith('provider_rate_limited') and self._rate_limits(event_id)<RATE_LIMIT_ATTEMPTS-1:
    return self._rate_limited(event_id,owner,str(code)[:160])
   self._save(event_id,owner,status='FAILED',error_code=str(code)[:160],lease_owner=None,lease_until=None)
   with db.transaction(self.conn):self._change(event_id,'FAILED',self.actor,error_code=str(code)[:160])
   return {'event_id':event_id,'status':'FAILED','error_code':str(code)[:160]}
 def reconcile(self):
  with db.transaction(self.conn):
   rows=self.conn.execute("SELECT q.event_id,v.status FROM conversation_processing q JOIN reviews v ON v.resolution_id=q.resolution_id WHERE q.status='PENDING_REVIEW' AND v.status IN ('APPROVED','SOURCE_REPLAYED','REJECTED')").fetchall()
   for row in rows:
    target='REJECTED' if row['status']=='REJECTED' else 'APPLIED'
    self.conn.execute('UPDATE conversation_processing SET status=?,updated_at=? WHERE event_id=?',(target,clock.now_rfc3339(),row['event_id']));self._change(row['event_id'],target,self.actor,reason='review_completed')
 def _view(self,row):
  out=dict(row);event=self.events.get(out['event_id']);out.update(project_id=event['project_id'],session_id=event['session_id'],host_id=event['host_id'],source_actor_kind=event['actor_kind'],source_system=event['source_system'],source_text=event['payload'].get('text',''),event_type=event['event_type'],candidates=[],review_id=None,memory_items=[],memory_jobs=[],effect='NONE')
  project=self.conn.execute('SELECT name FROM projects WHERE project_id=?',(event['project_id'],)).fetchone();out['project_name']=project['name'] if project else None
  if out['interpretation_id']:
   item=self.interpretations.get(out['interpretation_id']);out['candidates']=item['candidates'] or [];out['extractor_model']=item['extractor_model']
  if out['resolution_id']:
   resolution=self.resolver.get(out['resolution_id']);result=resolution['result'];out['review_id']=result.get('review_id');review=None
   if out['review_id']:
    review=self.conn.execute('SELECT status FROM reviews WHERE review_id=?',(out['review_id'],)).fetchone()
    if review and review['status']=='REJECTED':out['status']='REJECTED'
   try:
    canonical=self.conn.execute('SELECT canonical_resolution_id FROM source_applications WHERE source_event_id=?',(out['event_id'],)).fetchone()
    applied=self.resolver.applied_result(canonical['canonical_resolution_id'] if canonical else out['resolution_id']);out['status']='APPLIED'
   except errors.CoreError:applied=result
   applied_candidates=self.interpretations.get(applied['interpretation_id'])['candidates'] if applied.get('interpretation_id') else out['candidates']
   changes=[eid for action in applied.get('actions',[]) for eid in action.get('change_event_ids',[])]
   out['effect']='MEMORY_ELIGIBLE' if any(a.get('action') in ('CREATE_RULE','SUPERSEDE_RULE') and a['candidate_index']<len(applied_candidates) and applied_candidates[a['candidate_index']]['scope']['kind'] in ('PROJECT','GLOBAL') for a in applied.get('actions',[])) else 'TASK_ONLY' if any(a.get('target_id') for a in applied.get('actions',[])) else 'NONE'
   if changes:
    marks=','.join('?'*len(changes));out['memory_items']=[dict(r) for r in self.conn.execute('SELECT memory_id,memory_type,engine_state,status,source_event_id FROM memory_items WHERE source_event_id IN ('+marks+')',changes)]
    out['memory_jobs']=[dict(r) for r in self.conn.execute('SELECT job_id,status,last_error_code FROM memory_jobs WHERE source_event_id IN ('+marks+')',changes)]
  return out
 def list(self,*,project_id=None,session_id=None,event_id=None,status=None,limit=50,offset=0):
  if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100 or not isinstance(offset,int) or isinstance(offset,bool) or offset<0:raise errors.InvalidRequest('invalid pagination')
  clauses=[];params=[]
  for name,value,prefix in [('project_id',project_id,'prj'),('session_id',session_id,'ses'),('event_id',event_id,'evt')]:
   if value is not None:
    if not ids.is_id(value,prefix):raise errors.InvalidRequest('invalid scope')
    clauses.append('e.'+name+'=?');params.append(value)
  if status is not None:
   if status not in ('PENDING','PROCESSING','PENDING_REVIEW','APPLIED','NO_ACTION','FAILED','REJECTED'):raise errors.InvalidRequest('invalid processing status')
   clauses.append('q.status=?');params.append(status)
  where=' WHERE '+' AND '.join(clauses) if clauses else ''
  sql=' FROM conversation_processing q JOIN events e USING(event_id)'+where
  total=self.conn.execute('SELECT COUNT(*)'+sql,params).fetchone()[0]
  rows=self.conn.execute('SELECT q.*'+sql+' ORDER BY q.queued_at DESC,q.event_id DESC LIMIT ? OFFSET ?',[*params,limit,offset]).fetchall()
  counts=dict(self.conn.execute('SELECT q.status,COUNT(*)'+sql+' GROUP BY q.status',params).fetchall())
  return {'items':[self._view(row) for row in rows],'count':total,'counts':counts,'started_at':db.get_meta(self.conn,ACTIVATED,None),'worker_configured':bool(self.actor and self.host),'last_tick_at':db.get_meta(self.conn,'conversation_processing_heartbeat',None),'cooldown_until':self.cooldown_until(),'automatic_scope':'new_native_user_prompts','limit':limit,'offset':offset}
