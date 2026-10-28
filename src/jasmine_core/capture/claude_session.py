"""Opt-in Claude Code main-session capture with native per-turn provenance.

Only current Hook packets are posted. A bounded current-transcript metadata read proves
that this exact native turn is typed human input; auto-wakes/teammate inputs cannot
acquire a human attribution merely by firing UserPromptSubmit. No old messages
are imported, no message body is used from the transcript, and no context/decision is returned.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,json,os,stat,sys,time
from datetime import datetime,timezone
from pathlib import Path
from ..hosthook.binding import load_device,_private_bytes
from ..hosthook.install import _write
from .codex_session import append_receipt
from .grok_session import EVENTS,valid_id,bind_workspace,_publish
from . import memory_hint
SOURCE='claude-native-conversation-v1'
# Observed native entrypoint/source pairs. Desktop Code uses the SDK transport
# even for a human submission; a generic SDK invocation is not human evidence.
HUMAN_INPUT_SOURCES={('cli','typed'),('claude-desktop','sdk')}

def _prompt_origin(n):
    """Read current-turn metadata only, never import earlier message bodies."""
    try:
        fd=os.open(n['transcript_path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077:return None
            size=min(st.st_size,1024*1024);os.lseek(fd,st.st_size-size,os.SEEK_SET)
            data=os.read(fd,size).splitlines()
            if st.st_size>size:data=data[1:]
            for line in reversed(data):
                try:row=json.loads(line)
                except ValueError:continue
                if not isinstance(row,dict) or row.get('promptId')!=n['turn'] or row.get('type')!='user':continue
                if (row.get('sessionId')!=n['sid'] or row.get('isSidechain') is not False
                    or row.get('origin')!={'kind':'human'}
                    or row.get('turnOrigin')!='human' or row.get('userType')!='external'
                    or (row.get('entrypoint'),row.get('promptSource')) not in HUMAN_INPUT_SOURCES
                    or row.get('sourceToolAssistantUUID') or row.get('isMeta')
                    or not isinstance(row.get('cwd'),str)
                    or Path(row['cwd']).resolve()!=Path(n['cwd'])):continue
                stamp=row.get('timestamp');uid=row.get('uuid')
                if not valid_id(uid) or not isinstance(stamp,str) or datetime.fromisoformat(stamp.replace('Z','+00:00')).tzinfo is None:continue
                return {'check':'native_current_turn_typed_human_metadata','native_prompt_id':n['turn'],'native_message_id':uid,'native_prompt_timestamp':stamp,
                        'native_entrypoint':row['entrypoint'],'native_prompt_source':row['promptSource']}
        finally:os.close(fd)
    except (OSError,ValueError,TypeError):pass
    return None

def _native(native,home):
    event=native.get('hook_event_name')
    if event not in EVENTS or any(native.get(k) for k in ('agent_id','agent_type','subagent_id','subagent_type')):return None
    sid=native.get('session_id');cwd=native.get('cwd');turn=native.get('prompt_id')
    if not valid_id(sid) or not isinstance(cwd,str) or not Path(cwd).is_absolute():raise ValueError('invalid native identity')
    cwd=Path(cwd).resolve(strict=True)
    path=Path(native.get('transcript_path',''))
    if not path.is_absolute() or path.name!=sid+'.jsonl':return None
    projects=home/'projects'
    if home.is_symlink() or projects.is_symlink() or path.is_symlink() or path.parent.is_symlink():return None
    try:
        relative=path.relative_to(projects)
        if len(relative.parts)!=2 or path.parent.resolve().parent!=projects.resolve():return None
        # Claude creates the transcript directory asynchronously. A first prompt
        # can arrive before it exists; per-turn native metadata is checked
        # after persistence to prove its origin. Do not create or chmod any client-owned session directory.
        try:info=path.parent.stat()
        except FileNotFoundError:info=projects.stat()
        if info.st_uid!=os.getuid() or info.st_mode&0o022:return None
    except (OSError,ValueError):return None
    if event in ('UserPromptSubmit','Stop') and not valid_id(turn):return None
    if event=='Stop' and (native.get('stop_hook_active') or not isinstance(native.get('last_assistant_message'),str)):return None
    text=native.get('prompt') if event=='UserPromptSubmit' else native.get('last_assistant_message') if event=='Stop' else ''
    if not isinstance(text,str):raise ValueError('native text required')
    return {**native,'event':event,'sid':sid,'turn':turn,'cwd':str(cwd),'text':text}

def capture_device(native,device,claude_home):
    n=_native(native,claude_home)
    if n is None:return {'status':'ignored','reason':'unsupported_or_unverified_main_session'}
    folder=device.spool_dir/'claude-sessions';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=folder/(n['sid']+'.json');lock_path=folder/(n['sid']+'.lock')
    fd=os.open(lock_path,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        state=json.loads(_private_bytes(path,'Claude capture state')) if path.exists() else {'cwd':n['cwd'],'turns':{}}
        state['turns']={turn:stamp for turn,stamp in state['turns'].items() if isinstance(stamp,dict) and stamp.get('origin',{}).get('check')=='native_current_turn_typed_human_metadata'}
        if not _within(n['cwd'],state.get('cwd')):return {'status':'ignored','reason':'native_workspace_changed'}
        now=datetime.now(timezone.utc).isoformat()
        if n['event']=='SessionStart':
            # A fresh main session may not yet have any persisted typed input.
            n['timestamp']=now;n['timestamp_source']='hook_observation';state['start']=n
            _write(path,state);path.chmod(0o600)
            return {'status':'pending','reason':'awaiting_native_user_input','native_session_id':n['sid']}
        if n['event']=='UserPromptSubmit':
            stamp=state['turns'].get(n['turn'])
            if stamp is None:
                origin=None
                # Native transcript writes can lag Hook dispatch. The configured
                # UserPromptSubmit is async, so this wait cannot stall the prompt.
                deadline=time.monotonic()+1.5
                while origin is None:
                    origin=_prompt_origin(n)
                    if origin or time.monotonic()>=deadline:break
                    time.sleep(.05)
                if origin is None:return {'status':'ignored','reason':'no_current_typed_input_evidence','native_session_id':n['sid']}
                stamp={'origin':origin,'text_sha256':hashlib.sha256(n['text'].encode()).hexdigest()}
                state['turns'][n['turn']]=stamp
                _write(path,state);path.chmod(0o600)
            if stamp['text_sha256']!=hashlib.sha256(n['text'].encode()).hexdigest():return {'status':'ignored','reason':'native_turn_text_changed'}
        elif n['event']=='Stop':
            stamp=state['turns'].get(n['turn'])
            if stamp is None:
                if _prompt_origin(n) is None:return {'status':'ignored','reason':'no_verified_user_turn'}
                # A very fast reply may beat the async prompt handler. Keep its
                # native text privately until that prompt has actually committed.
                state.setdefault('pending_stops',{})[n['turn']]=n
                _write(path,state);path.chmod(0o600)
                return {'status':'pending','reason':'awaiting_prompt_commit'}
        else:
            if not state['turns']:return {'status':'ignored','reason':'no_verified_user_session'}
            stamp=list(state['turns'].values())[-1]
        origin=stamp['origin'];n['timestamp']=origin['native_prompt_timestamp'];n['timestamp_source']='native_typed_prompt'
        # The project is the session's starting directory, not the current one.
        binding=bind_workspace({**n,'cwd':state['cwd']},device,source=SOURCE)
        if 'start' in state:
            _publish(state['start'],binding,origin,source=SOURCE,agent='claude')
            del state['start'];_write(path,state);path.chmod(0o600)
        receipt=_publish(n,binding,origin,source=SOURCE,agent='claude')
        pending=state.get('pending_stops',{}).get(n['turn']) if n['event']=='UserPromptSubmit' else None
        if pending:
            pending['timestamp']=n['timestamp'];pending['timestamp_source']='native_typed_prompt'
            _publish(pending,binding,origin,source=SOURCE,agent='claude')
            del state['pending_stops'][n['turn']];_write(path,state);path.chmod(0o600)
        return receipt
    finally:os.close(fd)

#: Claude writes a turn to its transcript only after the synchronous
#: UserPromptSubmit hooks have returned (observed live 2026-10-27), so the hint
#: handler can never prove that *this* turn is typed input. It relies instead on
#: capture having verified an earlier typed turn of the same main session; the
#: first turn of a session gets no hint (ADR 0036 amendment).
VERIFIED_CHECK='native_current_turn_typed_human_metadata'

def _within(cwd,root):
    """Whether `cwd` is the session's starting directory or below it.

    A session that `cd`s into a subdirectory is still one workspace (live
    2026-10-28: most turns of one long session were refused for this reason).
    Anything outside the starting directory is a different workspace.
    """
    return isinstance(cwd,str) and isinstance(root,str) and Path(cwd).is_relative_to(root)

def _session_verified(device,n):
    """The newest capture-verified typed turn of this session, or None.

    Read without capture's lock: capture holds it while it waits for the
    transcript, and replaces the state file atomically, so a lock-free read sees
    either the old or the new state, never a torn one.
    """
    path=device.spool_dir/'claude-sessions'/(n['sid']+'.json')
    try:
        if path.is_symlink() or not path.is_file():return None
        state=json.loads(_private_bytes(path,'Claude capture state'))
        if not isinstance(state,dict) or not _within(n['cwd'],state.get('cwd')) or not isinstance(state.get('turns'),dict):return None
        verified=[turn for turn,stamp in state['turns'].items()
                  if isinstance(stamp,dict) and isinstance(stamp.get('origin'),dict) and stamp['origin'].get('check')==VERIFIED_CHECK]
    except Exception:  # unreadable state is "not verified", never an error
        return None
    return (verified[-1],state['cwd']) if verified else None

def memory_hint_device(native,device,claude_home,*,now=time.monotonic):
    """Return `(text, receipt)` for the separate synchronous hint handler.

    Separate from capture because capture's UserPromptSubmit handler is async and
    Claude discards an async handler's output. This handler records nothing.
    """
    deadline=now()+memory_hint.BUDGET_SECONDS
    if not device.memory_hint:return None,{'status':'disabled'}
    n=_native(native,claude_home)
    if n is None or n['event']!='UserPromptSubmit':return None,{'status':'ignored','reason':'unsupported_or_unverified_main_session'}
    verified=_session_verified(device,n)
    if verified is None:return None,{'status':'ignored','reason':'no_verified_turn_in_session','native_session_id':n['sid']}
    verified_turn,root=verified
    remaining=deadline-now()
    if remaining<=0.05:return None,{'status':'skipped','reason':'no_time_left','native_session_id':n['sid']}
    binding=bind_workspace({**n,'cwd':root},device,source=SOURCE,timeout=remaining)
    text,receipt=memory_hint.request(binding,project_id=binding.project_id,native_session_id=n['sid'],prompt=n['text'],deadline=deadline)
    return text,{**receipt,'native_session_id':n['sid'],'verified_turn':verified_turn}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['device-binding','receipts','claude-home']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--memory-hint',action='store_true',help='synchronous UserPromptSubmit Memory hint (ADR 0036); records nothing')
    a=p.parse_args();os.umask(0o077);native={}
    if a.memory_hint:
        text=None
        try:
            native=json.load(sys.stdin)
            if not isinstance(native,dict):raise ValueError('native event must be an object')
            text,receipt=memory_hint_device(native,load_device(a.device_binding),a.claude_home)
        except Exception as exc:
            receipt={'status':'failed','error_type':type(exc).__name__}
        receipt={'status':'memory_hint','agent':'claude','memory_hint':receipt,'checked_at':datetime.now(timezone.utc).isoformat()}
        try:append_receipt(a.receipts,receipt)
        except OSError:pass
        # Always exit 0 with valid output: a hint must never block a prompt.
        print(memory_hint.envelope(text) if text else '{}');return 0
    try:
        native=json.load(sys.stdin)
        if not isinstance(native,dict):raise ValueError('native event must be an object')
        receipt=capture_device(native,load_device(a.device_binding),a.claude_home)
    except Exception as exc:
        receipt={'status':'failed','agent':'claude','error_type':type(exc).__name__}
        print('Jasmine Claude capture failed; inspect private receipts.',file=sys.stderr)
    receipt['checked_at']=datetime.now(timezone.utc).isoformat();append_receipt(a.receipts,receipt)
    print('{}');return 1 if receipt['status']=='failed' else 0
if __name__=='__main__':raise SystemExit(main())
