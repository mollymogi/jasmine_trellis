#!/usr/bin/env python3
"""Prepare dashboard-local management; does not enable any Agent Hook.

Run during a trusted local deployment, with the existing Core operator's runtime
and private browser credential. Browser actions cannot mint credentials or select
commands. No schema migration or daemon restart is performed here.
"""
from pathlib import Path
import argparse,hashlib,json,os,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from jasmine_core import auth,db,ids,registry
from jasmine_core.hosthook.install import _backup,_write

def prepare(base,confirmed=False):
    if not confirmed:raise ValueError('explicit user-scope management opt-in required')
    os.umask(0o077);base=base.resolve(strict=True);config=base/'config/runtime.json'
    runtime=json.loads(config.read_text());release=Path(__file__).resolve().parents[2]
    credentials=base/'config/credentials';browser=json.loads((credentials/'browser-daily.json').read_text())
    reference=json.loads((base/'config/codex-conversation-device.json').read_text())
    adapters={}
    for agent in ('codex','claude','grok'):
        binding=base/'config'/f'{agent}-conversation-device.json'
        if not binding.exists():
            metadata=credentials/f'{agent}-conversation-keys.json'
            if metadata.exists():raise ValueError('credential metadata exists without device binding')
            conn=db.connect(runtime['database'])
            try:
                reg=registry.Registry(conn)
                if reg.require_actor(browser['actor_id'])['kind']!='human':raise ValueError('browser operator must be a human actor')
                actor=ids.new_id('act')
                with db.transaction(conn):reg.upsert_actor(actor,kind='system',display_name=agent.capitalize()+' native conversation capture',home_host_id=reference['host_id'])
                system=auth.Auth(conn).issue_key(actor_id=actor,label=agent+' native system',scopes=['objects:write','events:write'])
                human=auth.Auth(conn).issue_key(actor_id=browser['actor_id'],label=agent+' native user prompts',scopes=['events:write'])
                paths={}
                for kind,key in [('system',system),('human',human)]:
                    path=credentials/f'{agent}-conversation-{kind}.key'
                    with path.open('x') as stream:stream.write(key['token']+'\n')
                    path.chmod(0o600);paths[kind]=str(path)
                _write(metadata,{'system_actor_id':actor,'human_actor_id':browser['actor_id'],'system':{k:v for k,v in system.items() if k not in ('token','warning')},'human':{k:v for k,v in human.items() if k not in ('token','warning')}});metadata.chmod(0o600)
                _write(binding,{'version':1,'core_url':runtime['core_origin'],'host_id':reference['host_id'],'api_key_file':paths['system'],'human_api_key_file':paths['human'],'inject_context':False,'on_unavailable':'allow','spool_dir':str(base/'data'/f'{agent}-capture-spool')});binding.chmod(0o600)
            finally:conn.close()
        adapter={'python':runtime['python'],'installer':str(release/'scripts/deployment'/f'{agent}-capture-install.py'),'device_binding':str(binding),'receipts':str(base/'logs'/f'{agent}-conversation-receipts.jsonl'),'home':str(Path.home()/('.'+agent))}
        if agent!='codex':adapter['launcher']=str(release/'scripts/deployment'/f'{agent}-capture.py')
        adapters[agent]=adapter
    profile=base/'config/agent-capture-control.json'
    document={'version':2,'enabled':True,'browser_token_sha256':hashlib.sha256(browser['token'].encode()).hexdigest(),'control_receipts':str(base/'logs/agent-capture-control.jsonl'),'adapters':adapters}
    encoded=(json.dumps(document,ensure_ascii=False,indent=2)+'\n').encode()
    if not profile.exists() or profile.read_bytes()!=encoded:
        _backup(profile,profile.read_bytes() if profile.exists() else None);_write(profile,document);profile.chmod(0o600)
    if runtime.get('agent_capture_control_config')!=str(profile):
        _backup(config,config.read_bytes());_write(config,{**runtime,'agent_capture_control_config':str(profile)});config.chmod(0o600)
    return {'status':'prepared','agents':list(adapters),'hooks_installed':False}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True);p.add_argument('--confirm-user-scope',action='store_true');a=p.parse_args()
    print(json.dumps(prepare(a.base,a.confirm_user_scope)))
