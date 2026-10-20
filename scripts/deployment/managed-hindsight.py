#!/usr/bin/env python3
"""Start an operator-selected Hindsight binary with an isolated HOME/database.

The browser cannot supply these arguments. LLM credentials remain server-side;
existing Keychain access is attempted once without changing its permissions.
"""
import argparse,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--executable',required=True);p.add_argument('--port',required=True,type=int);p.add_argument('--database-port',required=True,type=int);a=p.parse_args()
base=Path(a.base).resolve();executable=Path(a.executable).absolute()
if not executable.is_file() or not 1024<a.port<65536 or not 1024<a.database_port<65536 or a.port==a.database_port:raise SystemExit('Invalid operator runtime profile')
os.umask(0o077);home=base/'home';home.mkdir(parents=True,exist_ok=True,mode=0o700)
service=os.environ.get('JASMINE_ENGINE_LLM_KEYCHAIN_SERVICE','')
account=os.environ.get('JASMINE_ENGINE_LLM_KEYCHAIN_ACCOUNT','api-key')
if not service:raise SystemExit('LLM credential source is not configured')
try:key=subprocess.run(['/usr/bin/security','find-generic-password','-s',service,'-a',account,'-w'],capture_output=True,text=True,timeout=10)
except subprocess.TimeoutExpired:raise SystemExit('Existing Keychain access timed out')
if key.returncode or not key.stdout.strip():raise SystemExit('Existing LLM credential is unavailable; no permission was changed')
env={'HOME':str(home),'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','PYTHONDONTWRITEBYTECODE':'1','TOKENIZERS_PARALLELISM':'false','HINDSIGHT_API_DATABASE_URL':f'pg0://jasmine-isolated-engine:{a.database_port}','HINDSIGHT_API_LLM_PROVIDER':'deepseek','HINDSIGHT_API_LLM_MODEL':'deepseek-chat','HINDSIGHT_API_LLM_API_KEY':key.stdout.strip(),'HINDSIGHT_API_EMBEDDINGS_PROVIDER':'onnx','HINDSIGHT_API_RERANKER_PROVIDER':'flashrank','HINDSIGHT_API_LLM_TIMEOUT':'120','HINDSIGHT_API_REFLECT_LLM_TIMEOUT':'120'}
os.chdir(base);os.execve(str(executable),[str(executable),'--host','127.0.0.1','--port',str(a.port),'--workers','1','--no-access-log'],env)
