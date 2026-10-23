#!/usr/bin/env python3
"""Pin installed CLI executables and model candidates; no login or inference."""
from pathlib import Path
import argparse,hashlib,json,os,re,subprocess,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from jasmine_core.hosthook.binding import _private_bytes
from jasmine_core.hosthook.install import _backup,_write
from jasmine_core.interpreter_cli import cli_environment,GROK_SHA256

def prepare(base,confirmed=False):
 if not confirmed:raise ValueError('explicit local CLI opt-in required')
 os.umask(0o077);base=base.resolve(strict=True);runtime=json.loads(_private_bytes(base/'config/runtime.json','runtime'))
 profile_path=Path(runtime['interpreter_control_config']);registry=json.loads(_private_bytes(profile_path,'model control'))
 codex_catalog=json.loads(Path(registry['catalog']).read_text());exe=Path(runtime['conversation_processing']['executable']).resolve()
 providers=[{'id':'codex','label':'Codex','detected':True,'enabled':True,'executable':str(exe),'executable_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
             'version':subprocess.check_output([str(exe),'--version'],text=True).strip(),'models':[{'id':m['slug'],'label':m.get('display_name',m['slug'])} for m in codex_catalog['models']]}]
 for client,label,locations in [('claude','Claude',['/opt/homebrew/bin/claude',str(Path.home()/'.local/bin/claude')]),('grok','Grok',[str(Path.home()/'.grok/bin/grok')]),('opencode','OpenCode',[str(Path.home()/'.opencode/bin/opencode')])]:
  candidate=next((Path(p).resolve() for p in locations if Path(p).is_file()),None)
  if candidate is None:
   providers.append({'id':client,'label':label,'detected':False,'models':[],'version':None});continue
  if client in ('claude','opencode'):
   providers.append({'id':client,'label':label,'detected':True,'enabled':False,'deferred':True,'models':[],'version':None});continue
  entry={'id':client,'label':label,'detected':True,'executable':str(candidate),'executable_sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),
         'version':subprocess.check_output([str(candidate),'--version'],text=True,timeout=10).strip(),'models':[]}
  if entry['executable_sha256']!=GROK_SHA256:raise ValueError('Grok binary requires data-only profile qualification')
  entry['enabled']=True
  if client=='grok':
   with tempfile.TemporaryDirectory(prefix='jasmine-model-discovery-') as name:
    root=Path(name);env=cli_environment(client,root,entry)
    result=subprocess.run([str(candidate),'models'],env=env,cwd=root,capture_output=True,text=True,timeout=45)
    models=[]
    # Discover candidates only; a catalog row is not an inference success.
    for line in result.stdout.splitlines():
     for value in re.findall(r'grok-[a-zA-Z0-9._-]+',line):
      if value not in models:models.append(value)
    entry['models']=[{'id':m,'label':m.split('/',1)[-1]} for m in models[:48]]
  providers.append(entry)
 registry.update(version=2,providers=providers,python=runtime['python'],test_runner=str(Path(__file__).with_name('check-interpreter.py')),profile_path=str(profile_path))
 encoded=(json.dumps(registry,ensure_ascii=False,indent=2)+'\n').encode()
 if len(encoded)>65536:raise ValueError('provider registry too large')
 if profile_path.read_bytes()!=encoded:_backup(profile_path,profile_path.read_bytes());_write(profile_path,registry);profile_path.chmod(0o600)
 return {'status':'prepared','clients':[{'id':p['id'],'detected':p['detected'],'models':len(p['models'])} for p in providers],'hooks_changed':False,'login_changed':False}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True,type=Path);p.add_argument('--confirm-local-clients',action='store_true');a=p.parse_args();print(json.dumps(prepare(a.base,a.confirm_local_clients)))
