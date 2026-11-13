"""Opt-in native Grok Hook capture; no history scraping or conversation wrapper."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import stat
import tempfile
from urllib.parse import quote
import uuid

from ..api.client import CoreClient
from ..canonical import canonical_json
from ..evidence import _deterministic_event_id
from ..hosthook.binding import Binding, load_device, read_key, _private_bytes, BindingError
from . import memory_hint, worklog_client
from .codex_session import RECENT_SECONDS, append_receipt, bind_workspace as _bind_workspace, workspace_root
from .hook_budget import arm_tailnet, disarm

SOURCE = 'grok-native-conversation-v1'
EVENTS = {'SessionStart':'session.started','UserPromptSubmit':'user.prompt','Stop':'assistant.message','SessionEnd':'session.ended'}
NAMES = {'session_start':'SessionStart','user_prompt_submit':'UserPromptSubmit','stop':'Stop','session_end':'SessionEnd',
         'pre_compact':'PreCompact','pre_tool_use':'PreToolUse'}
#: Capture handles two more native events than it publishes (ADR 0039): PreCompact
#: writes a session Checkpoint, PreToolUse delivers the staged resume block and
#: Memory hint, the only place Grok reads hook context. Neither is an Event.
WORK_EVENTS = ('PreCompact',)
DELIVERY_EVENT = 'PreToolUse'

def valid_id(value):
    try: return isinstance(value,str) and str(uuid.UUID(value)) == value.lower()
    except (ValueError,TypeError,AttributeError): return False

def normalize(native):
    event = native.get('hookEventName')
    event = NAMES.get(event, event)
    if (event not in EVENTS and event not in WORK_EVENTS) or native.get('subagentType') or native.get('subagent_type') or native.get('subagentId'):
        return None
    sid=native.get('sessionId');cwd=native.get('cwd')
    if not valid_id(sid) or not isinstance(cwd,str) or not Path(cwd).is_absolute(): raise ValueError('invalid native identity')
    if native.get('session_id',sid)!=sid or native.get('hook_event_name',event)!=event: raise ValueError('native alias conflict')
    stamp=native.get('timestamp')
    if not isinstance(stamp,str) or datetime.fromisoformat(stamp.replace('Z','+00:00')).tzinfo is None: raise ValueError('native timestamp required')
    turn=native.get('promptId')
    # Grok's PromptOrigin encodes auto-wakes/parent messages/plan resumes in
    # non-UUID prompt ids. Observe those without granting human attribution.
    if event in ('UserPromptSubmit','Stop') and not valid_id(turn): return None
    if event=='Stop' and (native.get('stopHookActive') or not isinstance(native.get('lastAssistantMessage'),str)): return None
    text=native.get('prompt') if event=='UserPromptSubmit' else native.get('lastAssistantMessage') if event=='Stop' else ''
    if not isinstance(text,str): raise ValueError('native message is not text')
    return {**native,'event':event,'sid':sid,'turn':turn,'text':text,'native_cwd':cwd,'cwd':str(Path(cwd).resolve(strict=True))}

def session_origin(n, home):
    home=home.resolve(strict=True)
    folder=home/'sessions'/quote(n['native_cwd'],safe='')/n['sid']
    supplied=n.get('transcriptPath')
    if supplied is not None and (not isinstance(supplied,str) or Path(supplied).name!='updates.jsonl' or Path(supplied).parent.resolve()!=folder.resolve()): return None
    if folder.is_symlink() or folder.parent.is_symlink() or (home/'sessions').is_symlink(): return None
    summary=folder/'summary.json'
    try:
        directory=folder.stat()
        if directory.st_uid!=os.getuid() or directory.st_mode & 0o077: return None
        # Grok writes summary.json as 0644 inside its owner-only session dir.
        # Do not change Grok's file permissions or read conversation bodies.
        fd=os.open(summary,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info_stat=os.fstat(fd)
            if not stat.S_ISREG(info_stat.st_mode) or info_stat.st_uid!=os.getuid() or info_stat.st_mode & 0o022 or info_stat.st_size>1024*1024:return None
            with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(1024*1024+1)
        finally:os.close(fd)
        meta=json.loads(raw);info=meta.get('info',{})
        if info.get('id')!=n['sid'] or not isinstance(info.get('cwd'),str) or Path(info['cwd']).resolve()!=Path(n['cwd']) or meta.get('hidden') is True or str(meta.get('session_kind','')).startswith('subagent'): return None
        identity=canonical_json([info['id'],n['cwd']])
        return {'check':'persisted_main_session_summary','identity_sha256':hashlib.sha256(identity.encode()).hexdigest()}
    except (OSError,ValueError,TypeError,KeyError,AttributeError): return None

def bind_workspace(n, device, source=SOURCE, *, timeout=2):
    return _bind_workspace(n, device, source=source, timeout=timeout)

def _start_path(device,sid): return device.spool_dir/'grok-starts'/(sid+'.json')
def _stage_start(device,n):
    target=_start_path(device,n['sid']);target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    body={k:v for k,v in n.items() if k not in ('prompt','lastAssistantMessage')}
    if target.is_symlink():raise BindingError('symlinked pending start refused')
    fd,temp=tempfile.mkstemp(prefix='.start-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write((json.dumps(body,ensure_ascii=False)+'\n').encode())
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def _publish(n,binding,origin,source=SOURCE,agent="grok"):
    client=CoreClient(binding.core_url,read_key(binding.api_key_file),timeout=3)
    identity=canonical_json([source,binding.host_id,binding.project_id,n['sid']])
    session=client.post('/v1/sessions',{'title':agent.capitalize()+' '+n['sid'],'project_id':binding.project_id,'task_id':None,'host_id':binding.host_id,
        'source_system':source+'-registration','source_event_id':identity,'event_id':_deterministic_event_id(source,identity,'registration')})['object']['session_id']
    payload={'text':n['text'],'native_session_id':n['sid'],'native_turn_id':n['turn'],'native_hook_event':n['event'],
             'trust':'NATIVE_HOOK','origin':origin}
    timestamp_field={'native_typed_prompt':'native_prompt_timestamp','hook_observation':'hook_observed_at'}.get(n.get('timestamp_source'),'native_timestamp')
    payload[timestamp_field]=n['timestamp']
    if n['event']=='SessionStart':payload['source']=n.get('source')
    if n['event']=='SessionEnd':payload['reason']=n.get('reason')
    source_id=hashlib.sha256(canonical_json([identity,n['event'],n['turn'],n['timestamp'],n['text']]).encode()).hexdigest()
    if n['event']=='UserPromptSubmit':
        if binding.human_api_key_file is None: raise ValueError('separate prompt credential required')
        client=CoreClient(binding.core_url,read_key(binding.human_api_key_file),timeout=3)
    response=client.post('/v1/events',{'event_type':EVENTS[n['event']],'source_system':source,'source_event_id':source_id,
        'event_id':_deterministic_event_id(source,source_id),'host_id':binding.host_id,'project_id':binding.project_id,
        'task_id':None,'session_id':session,'payload':payload})
    return {'status':'accepted','agent':agent,'native_hook_event':n['event'],'native_session_id':n['sid'],
        'native_turn_id':n['turn'],'project_id':binding.project_id,'session_id':session,'event_id':response['event']['event_id'],
        'event_type':EVENTS[n['event']],'replayed':response['replayed'],'origin':origin,'text_sha256':hashlib.sha256(n['text'].encode()).hexdigest()}

def capture_device(native,device,grok_home):
    event = NAMES.get(native.get('hookEventName'), native.get('hookEventName')) if isinstance(native, dict) else None
    token = arm_tailnet(device.core_url, event)
    try:
        return _capture_device(native, device, grok_home)
    finally:
        disarm(token)

def _capture_device(native,device,grok_home):
    n=normalize(native)
    if n is None:return {'status':'ignored','reason':'unsupported_or_non_user_origin'}
    origin=session_origin(n,grok_home)
    if origin is None:
        if n['event']=='SessionStart':
            _stage_start(device,n)
            return {'status':'pending','reason':'session_summary_not_persisted_yet','native_session_id':n['sid']}
        return {'status':'ignored','reason':'unverified_session_origin','native_session_id':n['sid']}
    binding=bind_workspace(n,device)
    flushed,started=False,None
    if n['event']!='SessionStart':
        path=_start_path(device,n['sid'])
        if path.exists():
            staged=json.loads(_private_bytes(path,'pending Grok start'))
            start=normalize(staged)
            if start and start['sid']==n['sid'] and start['cwd']==n['cwd'] and start['event']=='SessionStart':
                _publish(start,binding,origin);flushed,started=True,start.get('source')
            path.unlink()
    if n['event']=='PreCompact':
        return {'status':'accepted','agent':'grok','native_hook_event':'PreCompact','native_session_id':n['sid'],
                'project_id':binding.project_id,
                'worklog':worklog_client.record(binding,project_id=binding.project_id,native_session_id=n['sid'],
                                                reason='PRE_COMPACT',workspace_root=workspace_root(n['cwd']))}
    receipt=_publish(n,binding,origin)
    # A start staged before Grok persisted the session is published only now.
    if flushed:receipt['start_source']=started
    return receipt

def work_record(native,device,receipt,*,prompt=None):
    """ADR 0039 for one accepted event: the work record, then the resume block and
    Memory hint, staged for the next tool call. Updates `receipt`; never raises."""
    event=receipt.get('event_type');sid=receipt['native_session_id'];root=workspace_root(str(Path(native['cwd']).resolve()))
    flushed='start_source' in receipt
    if event=='session.ended':
        receipt['worklog']=worklog_client.record(device,project_id=receipt['project_id'],native_session_id=sid,
                                                 reason='SESSION_END',workspace_root=root)
    elif event=='session.started' or flushed:
        receipt['worklog']=worklog_client.recover(device,workspace_root=root,native_session_id=sid)
    if not device.memory_hint:return
    # Grok's start sources: 'new' (including /new and /clear) or 'load' (a saved session).
    starting=((event=='session.started' and native.get('source')=='new')
              or (flushed and receipt['start_source']=='new' and worklog_client.recent_query(prompt) is None))
    resume=hint=None
    if starting or event=='user.prompt':
        resume,answer=worklog_client.resume_block(device,native_session_id=sid,workspace_root=root,starting=starting,
            prompt=prompt,start_seconds=memory_hint.BUDGET_SECONDS,prompt_seconds=RECENT_SECONDS)
        if answer is not None:receipt['recent_activity']=answer
    if event=='user.prompt':
        hint,receipt['memory_hint']=memory_hint.request(device,project_id=receipt['project_id'],native_session_id=sid,
            session_id=receipt['session_id'],prompt=prompt)
    if resume or hint:receipt['staged']=worklog_client.stage(device.spool_dir,sid,resume=resume,hint=hint)

def deliver(native,device):
    """PreToolUse: hand the staged text to the model once. `(stdout, receipt or None)`."""
    sid=native.get('sessionId')
    if not valid_id(sid) or native.get('subagentType') or native.get('subagent_type'):return '{}',None
    text=worklog_client.take_staged(device.spool_dir,sid)
    if text is None:return '{}',None
    return (worklog_client.envelope('PreToolUse',text),
            {'status':'delivered','agent':'grok','native_hook_event':'PreToolUse','native_session_id':sid,'chars':len(text)})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--device-binding',required=True,type=Path);p.add_argument('--receipts',required=True,type=Path)
    p.add_argument('--grok-home',type=Path,default=Path.home()/'.grok');args=p.parse_args();os.umask(0o077)
    native={}
    budget=None
    try:
        native=json.load(sys.stdin)
        if not isinstance(native,dict):raise ValueError('native event must be an object')
        device=load_device(args.device_binding)
        if NAMES.get(native.get('hookEventName'),native.get('hookEventName'))==DELIVERY_EVENT:
            # Runs before every tool call: no Core request, no receipt unless delivered.
            out,receipt=deliver(native,device)
            if receipt is not None:
                receipt['checked_at']=datetime.now(timezone.utc).isoformat();append_receipt(args.receipts,receipt)
            print(out);return 0
        budget=arm_tailnet(device.core_url, NAMES.get(native.get('hookEventName'), native.get('hookEventName')))
        receipt=capture_device(native,device,args.grok_home)
        if receipt.get('status')=='accepted' and receipt.get('event_type'):
            try:work_record(native,device,receipt,prompt=native.get('prompt') if receipt['event_type']=='user.prompt' else None)
            except Exception as exc:receipt['work_record']={'status':'failed','error_type':type(exc).__name__}
    except Exception as exc:
        receipt={'status':'failed','agent':'grok','error_type':type(exc).__name__,'native_hook_event':native.get('hookEventName') if isinstance(native,dict) else None}
        print('Jasmine Grok capture failed; inspect private receipts.',file=sys.stderr)
    finally:
        disarm(budget)
    receipt['checked_at']=datetime.now(timezone.utc).isoformat();append_receipt(args.receipts,receipt)
    # Never block a prompt or request a continuation. Grok ignores context printed
    # here; the staged block reaches the model through PreToolUse instead.
    print('{}')
    return 1 if receipt['status']=='failed' else 0

if __name__=='__main__':raise SystemExit(main())
