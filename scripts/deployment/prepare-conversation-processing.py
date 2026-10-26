#!/usr/bin/env python3
"""Prepare one explicit daily processor for a migrated local deployment.

No historical backfill and no Hook/trust edits. A registered system actor executes
existing P2 policy; the browser keeps its existing scopes and review ownership.
"""
from pathlib import Path
import argparse,json,os,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from jasmine_core import SCHEMA_VERSION,db,ids,registry
from jasmine_core.migrations import check_version
from jasmine_core.interpreter_provider import CodexProvider
from jasmine_core.conversation_processing import ConversationProcessing
from jasmine_core.hosthook.install import _backup,_write

def prepare(base,executable,catalog,confirmed):
 if not confirmed:raise ValueError('explicit daily processing opt-in required')
 os.umask(0o077);base=base.resolve(strict=True);path=base/'config/runtime.json';runtime=json.loads(path.read_text());provider=CodexProvider(str(executable.resolve(strict=True)),str(catalog.resolve(strict=True)),daily=True)
 if not provider.valid:raise ValueError('Interpreter executable/catalog has not passed boundary validation')
 binding=json.loads((base/'config/codex-conversation-device.json').read_text());host=binding['host_id'];metadata=base/'config/conversation-processing-actor.json';conn=db.connect(runtime['database'])
 try:
  check_version(conn)
  saved=json.loads(metadata.read_text()) if metadata.exists() else None
  if saved and saved.get('host_id')!=host:raise ValueError('processor metadata belongs to a different host')
  actor=saved['actor_id'] if saved else ids.new_id('act')
  row=conn.execute('SELECT kind,home_host_id FROM actors WHERE actor_id=?',(actor,)).fetchone()
  if row and (row['kind']!='system' or row['home_host_id']!=host):raise ValueError('processor principal differs from registered metadata')
  # A matched restored database may predate this private metadata file. Recover
  # only the fixed local system principal, never overwrite an existing actor.
  if not row:
   with db.transaction(conn):registry.Registry(conn).upsert_actor(actor,kind='system',display_name='Daily conversation Interpreter and Resolver',home_host_id=host)
  if not saved:_write(metadata,{'actor_id':actor,'host_id':host});metadata.chmod(0o600)
  ConversationProcessing(conn,schema_version=SCHEMA_VERSION,processor_actor_id=actor,host_id=host,provider=provider).activate()
 finally:conn.close()
 document={**runtime,'conversation_processing':{'enabled':True,'actor_id':actor,'host_id':host,'executable':str(executable.resolve()),'catalog':str(catalog.resolve())}}
 if runtime!=document:_backup(path,path.read_bytes());_write(path,document);path.chmod(0o600)
 return {'status':'prepared','new_messages_only':True,'historical_backfill':False,'model':provider.config['model']}
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for name in ['base','executable','catalog']:p.add_argument('--'+name,required=True,type=Path)
 p.add_argument('--confirm-user-scope',action='store_true');a=p.parse_args();print(json.dumps(prepare(a.base,a.executable,a.catalog,a.confirm_user_scope)))
