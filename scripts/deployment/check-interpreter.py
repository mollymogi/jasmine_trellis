#!/usr/bin/env python3
"""A labeled synthetic health check; never writes a conversation or Memory."""
from pathlib import Path
import argparse,json,os,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from jasmine_core import ids
from jasmine_core.hosthook.binding import _private_bytes
from jasmine_core.interpreter_provider import CodexProvider,ProviderFailure
from jasmine_core.interpreter_cli import LocalCliProvider
from jasmine_core.interpretation_schema import validate_output
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--profile',type=Path,required=True);p.add_argument('--provider',required=True);p.add_argument('--model',required=True);a=p.parse_args();os.umask(0o077)
result={'provider':a.provider,'model':a.model,'status':'failed','error_code':'provider_unavailable'}
try:
 registry=json.loads(_private_bytes(a.profile,'Interpreter registry'));matches=[p for p in registry['providers'] if p['id']==a.provider and p['detected'] and p.get('enabled') is True]
 if registry['version']!=2 or len(matches)!=1:raise ValueError('unsupported provider')
 profile=matches[0]
 if not any(m['id']==a.model for m in profile['models']):raise ValueError('unsupported model')
 provider=CodexProvider(profile['executable'],registry['catalog'],daily=True,model=a.model) if a.provider=='codex' else LocalCliProvider(a.provider,profile,a.model,daily=True)
 source={'event_id':ids.new_id('evt'),'project_id':None,'task_id':None,'session_id':None,'host_id':None,'actor_id':None,'actor_kind':'human','event_type':'user.prompt','source_type':'USER_EXPLICIT','text':'你好'}
 raw=provider.extract(source);validate_output(raw,source)
 result.update(status='passed',error_code=None)
except ProviderFailure as exc:result['error_code']=exc.code
except Exception:pass
print(json.dumps(result))
