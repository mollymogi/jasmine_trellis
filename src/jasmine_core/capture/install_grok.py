"""Own one user-level Grok Hook file; preserve all foreign configuration."""
from __future__ import annotations
import argparse,fcntl,json,os,shlex,sys
from pathlib import Path
from ..hosthook.binding import load_device,read_key
from ..hosthook.install import _backup,_write,InstallError
from .grok_session import EVENTS
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
            backup=_backup(target,original) if original else None
            if original:target.unlink()
            return {'status':'disabled','backup':str(backup) if backup else None}
        command=shlex.join([str(Path(python).absolute()),'-I','-B',str(Path(launcher).resolve(strict=True)),
            '--device-binding',str(binding.resolve(strict=True)),'--receipts',str(receipts.absolute()),'--grok-home',str(home.resolve(strict=True))])
        document={'description':LABEL,'hooks':{event:[{'hooks':[{'type':'command','command':command,'timeout':10}]}] for event in EVENTS}}
        encoded=(json.dumps(document,ensure_ascii=False,indent=2)+'\n').encode()
        if original==encoded:return {'status':'unchanged'}
        backup=_backup(target,original);_write(target,document);target.chmod(0o600)
        return {'status':'installed','backup':str(backup) if backup else None,'events':list(EVENTS),'trust':'native user-level hooks; no trust-store edits'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['device-binding','receipts','grok-home','launcher']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--confirm-user-scope',action='store_true');p.add_argument('--disable',action='store_true');a=p.parse_args();os.umask(0o077)
    print(json.dumps(install(a.grok_home,a.device_binding,a.receipts,python=sys.executable,launcher=a.launcher,confirmed=a.confirm_user_scope,disable=a.disable)))
if __name__=='__main__':main()
