"""Pinned local Grok data-only adapter and loopback Magpie cascade adapter; no project working tree."""
from __future__ import annotations
import hashlib,json,os,selectors,socket,subprocess,tempfile,time,urllib.error,urllib.request
from pathlib import Path
from .canonical import canonical_json,sha256_hex
from .interpretation_schema import OUTPUT_SCHEMA,validate_output
from .interpreter_provider import DAILY_PROMPT_VERSION,PROMPT,ProviderFailure,_base_config,_cancel_owned_process,daily_prompt

CLIENTS={'grok','magpie'}
MAGPIE_ENDPOINT='http://127.0.0.1:3425/v1/chat/completions'
MAGPIE_MESSAGES_ENDPOINT='http://127.0.0.1:3425/v1/messages'
MAGPIE_QUOTAS_ENDPOINT='http://127.0.0.1:3425/v1/magpie/quotas'
MAGPIE_CASCADE_CHAIN=(
    ('opencode-go/deepseek-v4.1-flash','low',18),
    ('zcode/GLM-5.3-Flash','low',18),
    ('opencode-go/glm-5.3-flash','low',18),
    ('codex/gpt-6-luna','low',18),
    ('codex/gpt-6.1-sol','low',22),
)
_QUOTA_CACHE={'at':0.0,'low_providers':set()}
# Qualified Grok 1.0.77 native tool inventory. Updates require requalification.
GROK_SHA256='197c66a63e5ac46e07450b0ca21d141ce665050a412180f185dadfae1c7a5b2a'
GROK_DENIED_TOOLS='run_terminal_command,read_file,search_replace,list_dir,grep,kill_command_or_subagent,todo_write,get_command_or_subagent_output,scheduler_create,scheduler_delete,scheduler_list,monitor,search_tool,use_tool,update_goal,enter_plan_mode,exit_plan_mode,ask_user_question,send_feedback,image_gen,image_edit,image_to_video,reference_to_video,write,CallMcpTool,GetMcpTools,FetchMcpResource,ListMcpResources'
GROK_CONFIG='''[cli]
use_leader = false
[compat.claude]
agents = false
hooks = false
mcps = false
rules = false
skills = false
[compat.codex]
hooks = false
skills = false
[compat.cursor]
agents = false
hooks = false
mcps = false
rules = false
skills = false
[managed_mcps]
enabled = false
gateway_tools_enabled = false
[workflows]
enabled = false
[features]
backend_tools = false
web_fetch = false
subagents = false
memory = false
'''

def sandbox_argv(executable,root,argv):
    # Even a client defect cannot run a shell or write the daily project/config.
    # Keychain's native helper is allowed for the client's existing login.
    if not Path('/usr/bin/sandbox-exec').is_file():raise ProviderFailure('provider_sandbox_unavailable')
    quoted=lambda path:json.dumps(str(Path(path).resolve()))
    policy=('(version 1)(allow default)(deny process-exec)'
        '(allow process-exec (literal '+quoted(executable)+') (literal "/usr/bin/security"))'
        '(deny file-write*)(allow file-write* (subpath '+quoted(root)+') (literal "/dev/null"))')
    return ['/usr/bin/sandbox-exec','-p',policy,*argv]

def cli_environment(client,root,profile):
    env={k:os.environ[k] for k in ('HOME','USER','PATH','TMPDIR','LANG','LC_ALL','SYSTEMROOT',
        'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy') if k in os.environ}
    env.update(TMPDIR=str(root),NO_COLOR='1')
    if client=='grok':
        home=root/'grok-home';home.mkdir(mode=0o700)
        auth=Path(profile.get('auth_file',str(Path.home()/'.grok/auth.json')))
        if auth.exists():(home/'auth.json').symlink_to(auth.resolve())
        (home/'config.toml').write_text(GROK_CONFIG)
        env.update(GROK_HOME=str(home),GROK_SUBAGENTS='0',GROK_MEMORY='0',GROK_WEB_FETCH='0',GROK_CLAUDE_HOOKS_ENABLED='0',GROK_CLAUDE_MCPS_ENABLED='0',GROK_CURSOR_HOOKS_ENABLED='0',GROK_CURSOR_MCPS_ENABLED='0')
    return env

def cli_argv(client,executable,root,model):
    if client=='grok':return [str(executable),'--prompt-file',str(root/'input.txt'),'--cwd',str(root),
        '--model',model,'--tools','','--disallowed-tools',GROK_DENIED_TOOLS,'--deny','Bash,Edit,Write,Read,Grep,WebFetch,MCPTool','--no-subagents','--no-plan','--disable-web-search',
        '--permission-mode','dontAsk','--max-turns','1','--json-schema',canonical_json(OUTPUT_SCHEMA),'--output-format','streaming-messages-json',
        '--leader-socket',str(root/'leader.sock')]
    raise ProviderFailure('provider_unavailable')

def bounded_run(argv,encoded,env,root,timeout=120):
    deadline=time.monotonic()+timeout
    try:proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,cwd=root,start_new_session=True)
    except OSError as exc:raise ProviderFailure('provider_unavailable') from exc
    selector=selectors.DefaultSelector();streams={proc.stdout:bytearray(),proc.stderr:bytearray()};offset=0;failure=None
    for stream in streams:os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ)
    os.set_blocking(proc.stdin.fileno(),False);selector.register(proc.stdin,selectors.EVENT_WRITE)
    try:
        while selector.get_map():
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ProviderFailure('provider_timeout')
            for selected,_ in selector.select(min(remaining,.2)):
                stream=selected.fileobj
                if stream is proc.stdin:
                    try:offset+=os.write(stream.fileno(),encoded[offset:offset+4096])
                    except BrokenPipeError:offset=len(encoded)
                    if offset>=len(encoded):selector.unregister(stream);stream.close()
                else:
                    chunk=os.read(stream.fileno(),65536)
                    if not chunk:selector.unregister(stream)
                    else:
                        streams[stream].extend(chunk)
                        if len(streams[stream])>256*1024:raise ProviderFailure('provider_output_too_large')
        proc.wait(timeout=max(.01,deadline-time.monotonic()))
    except (ProviderFailure,subprocess.TimeoutExpired) as exc:failure=exc if isinstance(exc,ProviderFailure) else ProviderFailure('provider_timeout')
    finally:
        selector.close()
        if failure or proc.poll() is None:
            cleanup=_cancel_owned_process(proc)
            if cleanup:
                failure=failure or ProviderFailure('provider_cleanup_failed');failure.cleanup_errors=cleanup
        for stream in (proc.stdin,proc.stdout,proc.stderr):stream.close()
    if failure:raise failure
    out=bytes(streams[proc.stdout]).decode('utf-8',errors='replace');err=bytes(streams[proc.stderr]).decode('utf-8',errors='replace')
    if proc.returncode:
        combined=(out+err).lower()
        code='provider_login_required' if any(s in combined for s in ('not logged in','authentication','please log in','api key','unauthorized','login required')) else 'provider_rate_limited' if any(s in combined for s in ('rate limit','quota','usage limit','429')) else 'provider_error'
        raise ProviderFailure(code)
    return out

def parse_cli_output(client,out):
    if client not in CLIENTS:raise ProviderFailure('provider_unavailable')
    try:
        rows=[json.loads(line) for line in out.splitlines() if line.strip()]
        initialized=False;result=None
        for row in rows:
            if not isinstance(row,dict):raise ProviderFailure('provider_protocol_error')
            if row.get('type')=='system' and row.get('subtype')=='init':
                if initialized or row.get('tools')!=[] or row.get('mcp_servers',[]):raise ProviderFailure('provider_tool_use')
                initialized=True
            elif row.get('type')=='assistant' and initialized and result is None:
                if any(part.get('type') not in ('text','thinking') for part in row.get('message',{}).get('content',[])):raise ProviderFailure('provider_tool_use')
            elif row.get('type')=='result' and initialized and result is None:result=row
            else:raise ProviderFailure('provider_protocol_error')
        if not initialized or result is None:raise ProviderFailure('provider_protocol_error')
        if result.get('is_error') or result.get('subtype','success') not in ('success','ok'):raise ProviderFailure('provider_error')
        structured=result.get('structuredOutput',result.get('structured_output'))
        if isinstance(structured,dict):return canonical_json(structured)
        text=result.get('text',result.get('result'))
        if isinstance(text,str):return text
        if 'event_id' in result and 'candidates' in result:return canonical_json(result)
        raise ProviderFailure('provider_protocol_error')
    except (ValueError,KeyError,TypeError,AttributeError) as exc:raise ProviderFailure('provider_protocol_error') from exc

def _normalize_magpie_payload(raw_text,source):
    text=raw_text.strip()
    if text.startswith('```'):
        parts=text.split('\n',1)
        text=parts[1].rsplit('```',1)[0].strip() if len(parts)>1 else text.strip('`').strip()
    obj=json.loads(text)
    if not isinstance(obj,dict):raise ValueError('not an object')
    obj['event_id']=source['event_id']
    cands=obj.get('candidates')
    if isinstance(cands,list) and len(cands)>1 and any(isinstance(c,dict) and c.get('kind')!='NO_STRUCTURE' for c in cands):
        cands=[c for c in cands if isinstance(c,dict) and c.get('kind')!='NO_STRUCTURE']
        obj['candidates']=cands
    src_text=source.get('text') or ''
    if isinstance(cands,list):
        for c in cands:
            if not isinstance(c,dict):continue
            span=c.get('source_span')
            if isinstance(span,dict):
                quote=span.get('quote')
                start,end=span.get('start'),span.get('end')
                if isinstance(quote,str) and quote:
                    if not (isinstance(start,int) and isinstance(end,int) and 0<=start<end<=len(src_text) and src_text[start:end]==quote):
                        idx=src_text.find(quote)
                        if idx>=0:
                            span['start']=idx
                            span['end']=idx+len(quote)
                        elif len(cands)==1 and c.get('kind')=='NO_STRUCTURE' and src_text:
                            span['quote']=src_text
                            span['start']=0
                            span['end']=len(src_text)
            scope=c.get('scope')
            if isinstance(scope,dict):
                sk=scope.get('kind')
                if sk=='GLOBAL':
                    scope.update(project_id=None,task_id=None,path=None,tool=None)
                elif sk=='PROJECT':
                    if not source.get('project_id'):
                        scope.update(kind='GLOBAL',project_id=None,task_id=None,path=None,tool=None)
                    else:
                        scope.update(project_id=source['project_id'],task_id=None,path=None,tool=None)
                elif sk=='TASK':
                    if not source.get('project_id'):
                        scope.update(kind='GLOBAL',project_id=None,task_id=None,path=None,tool=None)
                    else:
                        scope.update(project_id=source['project_id'],task_id=source.get('task_id'),path=None,tool=None)
    normalized=canonical_json(obj)
    validate_output(normalized,source)
    return normalized

def _low_quota_providers():
    now=time.monotonic()
    if now-_QUOTA_CACHE['at']<60.0:
        return _QUOTA_CACHE['low_providers']
    low=set()
    try:
        with urllib.request.urlopen(MAGPIE_QUOTAS_ENDPOINT,timeout=0.5) as resp:
            raw=json.loads(resp.read(65536).decode('utf-8',errors='replace'))
        entries=raw.get('data',raw) if isinstance(raw,dict) else raw
        by_provider={}
        for item in entries if isinstance(entries,list) else []:
            prov=item.get('provider')
            if not isinstance(prov,str):continue
            if item.get('error'):
                by_provider.setdefault(prov,[]).append(False)
                continue
            wins=[w for w in (item.get('windows') or []) if isinstance(w,dict) and not w.get('lane') and isinstance(w.get('remaining'),(int,float))]
            ok=all(w['remaining']>5 for w in wins) if wins else True
            by_provider.setdefault(prov,[]).append(ok)
        low={prov for prov,flags in by_provider.items() if flags and not any(flags)}
    except Exception:
        pass
    _QUOTA_CACHE['at']=now;_QUOTA_CACHE['low_providers']=low
    return low

def _magpie_extract_cascade(prompt,selected_model,source,max_input_bytes,max_output_bytes):
    prompt_text=prompt+canonical_json(source)+'\nReturn ONLY valid JSON matching this schema (no markdown or prose):\n'+canonical_json(OUTPUT_SCHEMA)
    if len(prompt_text.encode('utf-8'))>max_input_bytes:
        raise ProviderFailure('provider_input_too_large')
    tier_map={m:(m,effort,timeout) for m,effort,timeout in MAGPIE_CASCADE_CHAIN}
    raw_tiers=[tier_map.get(selected_model,(selected_model,'low',18))]
    for item in MAGPIE_CASCADE_CHAIN:
        if item[0]!=selected_model:
            raw_tiers.append(item)
    low_provs=_low_quota_providers()
    healthy=[t for t in raw_tiers if t[0].split('/',1)[0] not in low_provs]
    depleted=[t for t in raw_tiers if t[0].split('/',1)[0] in low_provs]
    tiers=healthy+depleted
    last_code='provider_error'
    for model_id,effort,tier_timeout in tiers:
        if model_id.startswith('zcode/'):
            body={
                'model':model_id,
                'max_tokens':4096,
                'temperature':0,
                'tools':[{'name':'emit_output','description':'Emit structured interpretation output','input_schema':OUTPUT_SCHEMA}],
                'tool_choice':{'type':'tool','name':'emit_output'},
                'messages':[{'role':'user','content':prompt_text}],
            }
            req=urllib.request.Request(
                MAGPIE_MESSAGES_ENDPOINT,
                data=json.dumps(body).encode('utf-8'),
                headers={'Content-Type':'application/json','x-api-key':'magpie-local','anthropic-version':'2023-06-01'},
                method='POST',
            )
        else:
            body={
                'model':model_id,
                'reasoning_effort':effort,
                'messages':[{'role':'user','content':prompt_text}],
                'response_format':{
                    'type':'json_schema',
                    'json_schema':{'name':'interpretation_output','strict':True,'schema':OUTPUT_SCHEMA},
                },
                'temperature':0,
            }
            req=urllib.request.Request(
                MAGPIE_ENDPOINT,
                data=json.dumps(body).encode('utf-8'),
                headers={'Content-Type':'application/json'},
                method='POST',
            )
        try:
            with urllib.request.urlopen(req,timeout=tier_timeout) as resp:
                payload=json.loads(resp.read(max_output_bytes*2).decode('utf-8',errors='replace'))
            if model_id.startswith('zcode/'):
                blocks=payload.get('content') or []
                tool_in=next((b.get('input') for b in blocks if isinstance(b,dict) and b.get('type')=='tool_use'),None)
                content=json.dumps(tool_in,ensure_ascii=False) if isinstance(tool_in,dict) else next((b.get('text') for b in blocks if isinstance(b,dict) and b.get('type')=='text'),'')
            else:
                content=payload['choices'][0]['message']['content']
            if not isinstance(content,str) or not content.strip():
                last_code='provider_protocol_error'
                continue
            normalized=_normalize_magpie_payload(content,source)
            if len(normalized.encode('utf-8'))>max_output_bytes:
                last_code='provider_output_too_large'
                continue
            return normalized
        except urllib.error.HTTPError as exc:
            last_code='provider_rate_limited' if exc.code==429 else 'provider_login_required' if exc.code in (401,403) else 'provider_error'
            continue
        except (TimeoutError,socket.timeout,urllib.error.URLError):
            last_code='provider_timeout'
            continue
        except Exception:
            last_code='provider_protocol_error'
            continue
    raise ProviderFailure(last_code)

class LocalCliProvider:
    def __init__(self,client,profile,model,*,daily=False):
        self.client=client;self.profile=json.loads(json.dumps(profile));self.model=model;self.executable=Path(profile.get('executable','/usr/bin/true'))
        self.config=_base_config();self.valid=False;self.prompt=PROMPT
        if daily:self.prompt=daily_prompt()
        if daily:self.config['prompt_version']=DAILY_PROMPT_VERSION
        if client=='magpie':
            self.config.update(provider='magpie-local',model=model,version=profile.get('version','magpie-gateway-v1'),
                prompt_digest=sha256_hex(self.prompt),executable_sha256=profile.get('executable_sha256','magpie-http'),
                tool_profile='http-no-execution.v1',inference_provider_id='magpie',inference_provider_name='Magpie Gateway',
                wire_api='chat_completions',native_protocol_version='magpie.cascade.v1',
                requires_openai_auth=False,transport='loopback-http-cascade',
                cascade_chain=[m for m,_,_ in MAGPIE_CASCADE_CHAIN],
                disabled_features=['tools','mcp','hooks','subagents'])
            try:
                self.valid=any(m['id']==model for m in profile.get('models',[]))
            except (KeyError,TypeError):
                self.valid=False
            return
        self.config.update(provider=client+'-local',model=model,version=profile['version'],prompt_digest=sha256_hex(self.prompt),
            executable_sha256=profile['executable_sha256'],tool_profile='cli-no-execution.v1',inference_provider_id=client,
            inference_provider_name=client,wire_api='native-cli',native_protocol_version=client+'.json.v1',
            requires_openai_auth=False,transport='client-managed',disabled_features=['tools','mcp','hooks','subagents'])
        try:
            if client not in CLIENTS or not self.executable.is_absolute() or not any(m['id']==model for m in profile['models']):return
            if profile['executable_sha256']!=GROK_SHA256 or hashlib.sha256(self.executable.read_bytes()).hexdigest()!=profile['executable_sha256']:return
            self.valid=Path('/usr/bin/sandbox-exec').is_file()
        except (OSError,KeyError,TypeError):pass
    def configuration(self):return dict(self.config)
    def extract(self,source):
        if not self.valid:raise ProviderFailure('provider_unavailable')
        if self.client=='magpie':
            return _magpie_extract_cascade(self.prompt,self.model,source,self.config['max_input_bytes'],self.config['max_output_bytes'])
        if hashlib.sha256(self.executable.read_bytes()).hexdigest()!=self.config['executable_sha256']:raise ProviderFailure('provider_configuration_changed')
        encoded=(self.prompt+canonical_json(source)+'\nRequired JSON Schema:\n'+canonical_json(OUTPUT_SCHEMA)).encode()
        if len(encoded)>self.config['max_input_bytes']:raise ProviderFailure('provider_input_too_large')
        with tempfile.TemporaryDirectory(prefix='jasmine-extract-'+self.client+'-') as name:
            root=Path(name);env=cli_environment(self.client,root,self.profile)
            if self.client=='grok':(root/'input.txt').write_bytes(encoded)
            argv=sandbox_argv(self.executable,root,cli_argv(self.client,self.executable,root,self.model))
            raw=parse_cli_output(self.client,bounded_run(argv,encoded if self.client!='grok' else b'',env,root,self.config['timeout_seconds']))
            if len(raw.encode())>self.config['max_output_bytes']:raise ProviderFailure('provider_output_too_large')
            return raw
