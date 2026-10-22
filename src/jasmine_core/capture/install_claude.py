"""Install/remove only Jasmine's four user-level Claude Code handlers."""
from __future__ import annotations
import argparse,fcntl,json,os,shlex,sys
from pathlib import Path
from ..hosthook.binding import load_device,read_key,_private_bytes
from ..hosthook.install import InstallError,_backup,_write
from .grok_session import EVENTS
LABEL='Jasmine daily Claude conversation capture'

def install(home,binding,receipts,*,python,launcher,confirmed=False,disable=False):
    if not confirmed:raise InstallError('user-wide Claude capture requires explicit opt-in')
    if home.is_symlink():raise InstallError('symlinked Claude home refused')
    device=load_device(binding);read_key(device.api_key_file)
    if device.human_api_key_file is None:raise InstallError('separate prompt credential required')
    read_key(device.human_api_key_file);home.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=home/'settings.json'
    with (home/'.jasmine-capture.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.is_symlink():raise InstallError('symlinked Claude settings refused')
        original=target.read_bytes() if target.exists() else None
        doc=json.loads(original) if original else {}
        if not isinstance(doc,dict) or not isinstance(doc.get('hooks',{}),dict):raise InstallError('invalid Claude settings')
        if not disable and doc.get('disableAllHooks') is True:raise InstallError('Claude has disabled all hooks; change that setting in Claude first')
        hooks=doc.setdefault('hooks',{})
        command=shlex.join([python,'-I','-B',str(launcher.resolve(strict=True)),'--device-binding',str(binding.resolve(strict=True)),
            '--receipts',str(receipts.absolute()),'--claude-home',str(home.resolve(strict=True))])
        for event in EVENTS:
            groups=hooks.get(event,[])
            if not isinstance(groups,list):raise InstallError('invalid Claude hook groups')
            kept=[]
            for group in groups:
                if not isinstance(group,dict) or not isinstance(group.get('hooks'),list):raise InstallError('invalid Claude hook handlers')
                # Do not remove a foreign handler merely because its display label matches.
                def owned(h):
                    if not isinstance(h,dict) or h.get('statusMessage')!=LABEL:return False
                    try:
                        words=shlex.split(h.get('command',''))
                        return any(Path(w).name=='claude-capture.py' for w in words) and '--device-binding' in words
                    except ValueError:return False
                remaining=[h for h in group['hooks'] if not owned(h)]
                if remaining:kept.append({**group,'hooks':remaining})
            if not disable:kept.append({'hooks':[{'type':'command','command':command,'timeout':10,'statusMessage':LABEL}]})
            if kept:hooks[event]=kept
            else:hooks.pop(event,None)
        if not hooks:doc.pop('hooks',None)
        encoded=(json.dumps(doc,ensure_ascii=False,indent=2)+'\n').encode()
        if original==encoded:return {'status':'disabled' if disable else 'unchanged'}
        backup=_backup(target,original);_write(target,doc);target.chmod(0o600)
        return {'status':'disabled' if disable else 'installed','backup':str(backup) if backup else None}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['device-binding','receipts','claude-home','launcher']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--confirm-user-scope',action='store_true');p.add_argument('--disable',action='store_true');a=p.parse_args();os.umask(0o077)
    print(json.dumps(install(a.claude_home,a.device_binding,a.receipts,python=sys.executable,launcher=a.launcher,confirmed=a.confirm_user_scope,disable=a.disable)))
if __name__=='__main__':main()
