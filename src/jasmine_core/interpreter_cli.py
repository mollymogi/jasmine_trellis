"""Pinned local Grok data-only adapter; no project working tree."""
from __future__ import annotations
import hashlib,json,os,selectors,subprocess,tempfile,time
from pathlib import Path
from .canonical import canonical_json,sha256_hex
from .interpretation_schema import OUTPUT_SCHEMA
from .interpreter_provider import DAILY_PROMPT_VERSION,PROMPT,ProviderFailure,_base_config,_cancel_owned_process,daily_prompt

CLIENTS={'grok'}
# Qualified Grok 1.0.67 native tool inventory. Updates require requalification.
GROK_SHA256='036a47ce2987adae04de827f7803bf0cc460bf169b640db168f18ec7acb734db'
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

class LocalCliProvider:
    def __init__(self,client,profile,model,*,daily=False):
        self.client=client;self.profile=json.loads(json.dumps(profile));self.model=model;self.executable=Path(profile['executable'])
        self.config=_base_config();self.valid=False;self.prompt=PROMPT
        if daily:self.prompt=daily_prompt()
        if daily:self.config['prompt_version']=DAILY_PROMPT_VERSION
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
