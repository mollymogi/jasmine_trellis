#!/usr/bin/env python3
"""Prepare bounded local model selection, keeping Codex auth and Agent hooks unchanged."""
from pathlib import Path
import argparse,hashlib,json,os,re,stat,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from jasmine_core.interpreter_provider import CodexProvider,MODEL
from jasmine_core.hosthook.binding import _private_bytes
from jasmine_core.hosthook.install import _backup,_write

def prepare(base,cache,confirmed=False):
 if not confirmed:raise ValueError('explicit model-management opt-in required')
 os.umask(0o077);base=base.resolve(strict=True);runtime_path=base/'config/runtime.json'
 runtime=json.loads(_private_bytes(runtime_path,'runtime'));flow=runtime['conversation_processing']
 if flow.get('enabled') is not True:raise ValueError('daily processing is not enabled')
 if cache.is_symlink() or cache.stat().st_uid!=os.getuid() or cache.stat().st_mode&0o022 or cache.stat().st_size>2*1024*1024:raise ValueError('unsafe model cache')
 cached=json.loads(cache.read_text());entries=[]
 for raw in cached['models']:
  if raw.get('visibility')!='list':continue
  slug=raw.get('slug')
  if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,79}',slug):raise ValueError('invalid model ID')
  entry={**raw,'apply_patch_tool_type':None,'experimental_supported_tools':[],
         'supports_search_tool':False,'tool_mode':None}
  entries.append(entry)
 if not entries or len({e['slug'] for e in entries})!=len(entries):raise ValueError('invalid catalog')
 catalog=base/'config/interpreter-models.json';selection=base/'config/interpreter-model.json';profile=base/'config/interpreter-control.json'
 selected=json.loads(_private_bytes(selection,'model selection')) if selection.exists() else {'version':1,'model':MODEL,'revision':1}
 if (set(selected)!={'version','model','revision'} or selected['version']!=1 or type(selected['revision']) is not int or selected['revision']<1
     or selected['model'] not in {e['slug'] for e in entries}):raise ValueError('invalid model selection')
 data=(json.dumps({'models':entries},ensure_ascii=False,indent=2)+'\n').encode()
 if len(data)>512*1024:raise ValueError('model catalog too large')
 fd,name=tempfile.mkstemp(dir=catalog.parent,prefix='.interpreter-catalog-');temp=Path(name)
 try:
  with os.fdopen(fd,'wb') as stream:stream.write(data)
  for entry in entries:
   if not CodexProvider(flow['executable'],str(temp),daily=True,model=entry['slug']).valid:raise ValueError('model is incompatible with bounded provider')
 finally:temp.unlink(missing_ok=True)
 browser=json.loads(_private_bytes(base/'config/credentials/browser-daily.json','operator credential'))
 def write(path,value):
  encoded=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
  previous=None
  if path.is_symlink():raise ValueError('symlinked configuration')
  if path.exists():
   fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
   try:
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>512*1024:raise ValueError('unsafe existing configuration')
    previous=os.read(fd,512*1024+1)
   finally:os.close(fd)
  if previous==encoded:return
  _backup(path,previous);_write(path,value);path.chmod(0o600)
 write(catalog,{'models':entries});write(selection,selected)
 write(profile,{'version':1,'enabled':True,'browser_token_sha256':hashlib.sha256(browser['token'].encode()).hexdigest(),
                'catalog':str(catalog),'catalog_sha256':hashlib.sha256(catalog.read_bytes()).hexdigest(),
                'selection':str(selection),'audit':str(base/'logs/interpreter-model-changes.jsonl')})
 write(runtime_path,{**runtime,'conversation_processing':{**flow,'catalog':str(catalog),'model_config':str(selection)},'interpreter_control_config':str(profile)})
 return {'status':'prepared','model':selected['model'],'model_count':len(entries),'authentication_changed':False,'hooks_changed':False}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True,type=Path);p.add_argument('--models-cache',type=Path,default=Path.home()/'.codex/models_cache.json');p.add_argument('--confirm-model-management',action='store_true');a=p.parse_args()
 print(json.dumps(prepare(a.base,a.models_cache,a.confirm_model_management)))
