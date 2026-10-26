#!/usr/bin/env python3
"""Fixed local daily processor; no native hooks, tools or project code loaded by the model."""
from pathlib import Path
import argparse,json,os,signal,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from jasmine_core import SCHEMA_VERSION,db
from jasmine_core.migrations import check_version
from jasmine_core.conversation_processing import ConversationProcessing
p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--max-ticks',type=int);a=p.parse_args()
os.umask(0o077);conn=db.connect(a.db);check_version(conn);stop=False
worker=ConversationProcessing(conn,schema_version=SCHEMA_VERSION)
def halt(*_):
 global stop
 stop=True
signal.signal(signal.SIGTERM,halt);signal.signal(signal.SIGINT,halt)
worker.activate();ticks=0
try:
 while not stop and (a.max_ticks is None or ticks<a.max_ticks):
  try:result=worker.tick();print(json.dumps(result),flush=True)
  except Exception as exc:print(json.dumps({'status':'worker_error','error_type':type(exc).__name__}),flush=True);result={'status':'idle'}
  ticks+=1
  if result['status']=='idle' and a.max_ticks is None:time.sleep(2)
finally:conn.close()
