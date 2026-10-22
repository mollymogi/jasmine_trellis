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
SOURCE='claude-native-conversation-v1'

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
                    or row.get('origin')!={'kind':'human'} or row.get('promptSource')!='typed'
                    or row.get('turnOrigin')!='human' or row.get('userType')!='external'
                    or row.get('entrypoint')!='cli' or not isinstance(row.get('cwd'),str)
                    or Path(row['cwd']).resolve()!=Path(n['cwd'])):continue
                stamp=row.get('timestamp');uid=row.get('uuid')
                if not valid_id(uid) or not isinstance(stamp,str) or datetime.fromisoformat(stamp.replace('Z','+00:00')).tzinfo is None:continue
                return {'check':'native_current_turn_typed_human_metadata','native_prompt_id':n['turn'],'native_message_id':uid,'native_prompt_timestamp':stamp}
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
        if state['cwd']!=n['cwd']:return {'status':'ignored','reason':'native_workspace_changed'}
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
        binding=bind_workspace(n,device,source=SOURCE)
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

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['device-binding','receipts','claude-home']:p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args();os.umask(0o077);native={}
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
