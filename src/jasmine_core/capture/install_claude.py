"""Install/remove only Jasmine's four user-level Claude Code handlers."""
from __future__ import annotations
import argparse,fcntl,json,os,shlex,sys
from pathlib import Path
from ..hosthook.binding import load_device,read_key,_private_bytes
from ..hosthook.install import InstallError,_backup,_write
from .grok_session import EVENTS
from . import memory_hint
LABEL='Jasmine daily Claude conversation capture'
#: ADR 0036: a separate synchronous handler; capture's own stays async.
HINT_LABEL='Jasmine Memory hint for Claude'

def _owned_hint(h):
    if not isinstance(h,dict) or h.get('statusMessage')!=HINT_LABEL:return False
    try:
        words=shlex.split(h.get('command',''))
        return any(Path(w).name=='claude-capture.py' for w in words) and '--memory-hint' in words
    except ValueError:return False

def set_memory_hint(home,binding,receipts,*,python,launcher,enabled,confirmed=False):
    """Add/remove only the hint handler and flip the device opt-in together."""
    if not confirmed:raise InstallError('Memory hint requires explicit opt-in')
    if home.is_symlink():raise InstallError('symlinked Claude home refused')
    target=home/'settings.json'
    with (home/'.jasmine-capture.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.is_symlink():raise InstallError('symlinked Claude settings refused')
        original=target.read_bytes() if target.exists() else None
        doc=json.loads(original) if original else {}
        if not isinstance(doc,dict) or not isinstance(doc.get('hooks',{}),dict):raise InstallError('invalid Claude settings')
        hooks=doc.setdefault('hooks',{});groups=hooks.get('UserPromptSubmit',[])
        if not isinstance(groups,list):raise InstallError('invalid Claude hook groups')
        if enabled and not any(isinstance(g,dict) and any(isinstance(h,dict) and h.get('statusMessage')==LABEL for h in g.get('hooks',[])) for g in groups):
            raise InstallError('enable conversation capture before the Memory hint')
        handler={'type':'command','command':shlex.join([python,'-I','-B',str(launcher.resolve(strict=True)),'--device-binding',str(binding.resolve(strict=True)),
            '--receipts',str(receipts.absolute()),'--claude-home',str(home.resolve(strict=True)),'--memory-hint']),'timeout':3,'statusMessage':HINT_LABEL}
        current=[h for g in groups if isinstance(g,dict) and isinstance(g.get('hooks'),list) for h in g['hooks'] if _owned_hint(h)]
        if enabled and current==[handler]:
            kept=groups  # already exactly as wanted; keep its position
        else:
            kept=[]
            for group in groups:
                if not isinstance(group,dict) or not isinstance(group.get('hooks'),list):raise InstallError('invalid Claude hook handlers')
                remaining=[h for h in group['hooks'] if not _owned_hint(h)]
                if remaining:kept.append({**group,'hooks':remaining})
            if enabled:kept.append({'hooks':[handler]})
        if kept:hooks['UserPromptSubmit']=kept
        else:hooks.pop('UserPromptSubmit',None)
        if not hooks:doc.pop('hooks',None)
        # Turn the opt-in on only after the handler exists, and off before removing it.
        flag=memory_hint.set_enabled(binding,False) if not enabled else False
        encoded=(json.dumps(doc,ensure_ascii=False,indent=2)+'\n').encode()
        backup=None
        if original!=encoded:
            backup=_backup(target,original);_write(target,doc);target.chmod(0o600)
        if enabled:flag=memory_hint.set_enabled(binding,True)
        if original==encoded and not flag:return {'status':'unchanged'}
        return {'status':'memory_hint_enabled' if enabled else 'memory_hint_disabled','backup':str(backup) if backup else None}

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
                remaining=[h for h in group['hooks'] if not owned(h) and not (disable and _owned_hint(h))]
                if remaining:kept.append({**group,'hooks':remaining})
            if not disable:kept.append({'hooks':[{'type':'command','command':command,'timeout':10,'statusMessage':LABEL,**({'async':True} if event=='UserPromptSubmit' else {})}]})
            if kept:hooks[event]=kept
            else:hooks.pop(event,None)
        if not hooks:doc.pop('hooks',None)
        if disable:memory_hint.set_enabled(binding,False)  # the hint never outlives capture
        encoded=(json.dumps(doc,ensure_ascii=False,indent=2)+'\n').encode()
        if original==encoded:return {'status':'disabled' if disable else 'unchanged'}
        backup=_backup(target,original);_write(target,doc);target.chmod(0o600)
        return {'status':'disabled' if disable else 'installed','backup':str(backup) if backup else None}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['device-binding','receipts','claude-home','launcher']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--confirm-user-scope',action='store_true');p.add_argument('--disable',action='store_true')
    p.add_argument('--memory-hint',choices=['enable','disable']);a=p.parse_args();os.umask(0o077)
    if a.memory_hint:
        print(json.dumps(set_memory_hint(a.claude_home,a.device_binding,a.receipts,python=sys.executable,launcher=a.launcher,enabled=a.memory_hint=='enable',confirmed=a.confirm_user_scope)));return
    print(json.dumps(install(a.claude_home,a.device_binding,a.receipts,python=sys.executable,launcher=a.launcher,confirmed=a.confirm_user_scope,disable=a.disable)))
if __name__=='__main__':main()
