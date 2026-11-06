"""Own one user-level Grok Hook file; preserve all foreign configuration."""
from __future__ import annotations
import argparse,fcntl,json,os,shlex,sys
from pathlib import Path
from ..hosthook.binding import load_device,read_key
from ..hosthook.install import _backup,_write,InstallError
from . import memory_hint
from .grok_session import DELIVERY_EVENT,EVENTS,WORK_EVENTS
LABEL='Jasmine daily Grok conversation capture'
NAME='jasmine-conversation.json'

def install(home,binding,receipts,*,python,launcher,confirmed=False,disable=False):
    if not confirmed:raise InstallError('user-wide Grok capture requires explicit opt-in')
    if home.is_symlink() or (home/'hooks').is_symlink():raise InstallError('symlinked Grok configuration refused')
    device=load_device(binding);read_key(device.api_key_file)
    if device.human_api_key_file is None:raise InstallError('separate prompt credential required')
    read_key(device.human_api_key_file)
    target=home/'hooks'/NAME
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (target.parent/'.jasmine-conversation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.is_symlink():raise InstallError('symlinked hook file refused')
        original=target.read_bytes() if target.exists() else None
        if original and json.loads(original).get('description')!=LABEL:raise InstallError('foreign hook file will not be overwritten')
        if disable:
            memory_hint.set_enabled(binding,False)  # the hint never outlives capture
            backup=_backup(target,original) if original else None
            if original:target.unlink()
            return {'status':'disabled','backup':str(backup) if backup else None}
        command=shlex.join([str(Path(python).absolute()),'-I','-B',str(Path(launcher).resolve(strict=True)),
            '--device-binding',str(binding.resolve(strict=True)),'--receipts',str(receipts.absolute()),'--grok-home',str(home.resolve(strict=True))])
        # PreToolUse runs before every tool call, so it is registered only while the
        # Memory hint (and with it the resume block) is on: it is their only way in.
        events=[*EVENTS,*WORK_EVENTS]+([DELIVERY_EVENT] if device.memory_hint else [])
        document={'description':LABEL,'hooks':{event:[{'hooks':[{'type':'command','command':command,'timeout':3 if event==DELIVERY_EVENT else 10}]}] for event in events}}
        encoded=(json.dumps(document,ensure_ascii=False,indent=2)+'\n').encode()
        if original==encoded:return {'status':'unchanged'}
        backup=_backup(target,original);_write(target,document);target.chmod(0o600)
        return {'status':'installed','backup':str(backup) if backup else None,'events':events,'trust':'native user-level hooks; no trust-store edits'}

def set_memory_hint(home,binding,receipts,*,python,launcher,enabled,confirmed=False):
    """ADR 0036/0039. The opt-in lives in the device binding; the hook file gains or
    loses its PreToolUse delivery handler to match."""
    if not confirmed:raise InstallError('Memory hint requires explicit opt-in')
    target=home/'hooks'/NAME
    if enabled and (target.is_symlink() or not target.exists() or json.loads(target.read_bytes()).get('description')!=LABEL):
        raise InstallError('enable conversation capture before the Memory hint')
    changed=memory_hint.set_enabled(binding,enabled)
    if target.exists():install(home,binding,receipts,python=python,launcher=launcher,confirmed=True)
    if not changed:return {'status':'unchanged'}
    return {'status':'memory_hint_enabled' if enabled else 'memory_hint_disabled'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['device-binding','receipts','grok-home','launcher']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--confirm-user-scope',action='store_true');p.add_argument('--disable',action='store_true')
    p.add_argument('--memory-hint',choices=['enable','disable']);a=p.parse_args();os.umask(0o077)
    if a.memory_hint:
        print(json.dumps(set_memory_hint(a.grok_home,a.device_binding,a.receipts,python=sys.executable,launcher=a.launcher,
                                         enabled=a.memory_hint=='enable',confirmed=a.confirm_user_scope)));return
    print(json.dumps(install(a.grok_home,a.device_binding,a.receipts,python=sys.executable,launcher=a.launcher,confirmed=a.confirm_user_scope,disable=a.disable)))
if __name__=='__main__':main()
