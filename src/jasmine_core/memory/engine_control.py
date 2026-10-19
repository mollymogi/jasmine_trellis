"""Opt-in local engine process control (ADR 0023), separate from Memory Truth.

Only the operator's local profile supplies an argv/environment/origin. HTTP
requests cannot select a command, directory, port, environment or credential.
"""
from __future__ import annotations
import atexit,json,os,signal,socket,subprocess,threading,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from .. import db,clock
from .store import MemoryStore
from .hindsight import HindsightConfig,HindsightTransport
from .worker import record_engine_probe

CONFIG_ENV='JASMINE_MEMORY_ENGINE_CONTROL_CONFIG'

class EngineController:
    def __init__(self, database: str, schema_version: int, profile_path: str | None = None):
        self.database=database;self.schema_version=schema_version;self._lock=threading.RLock();self._process=None;self._closed=False
        self.profile=None;self.state='UNAVAILABLE';self.error_code=None;self.operation_id=None;self.started_at=None;self.api_version=None
        path=profile_path if profile_path is not None else os.environ.get(CONFIG_ENV)
        if path:
            try:
                p=json.loads(Path(path).read_text());origin=urlsplit(p['origin']);command=p['command'];cwd=Path(p['cwd'])
                if origin.scheme!='http' or origin.hostname not in ('127.0.0.1','localhost','::1') or not origin.port or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('','/'):
                    raise ValueError('origin')
                if not isinstance(command,list) or not command or not all(isinstance(x,str) and x and '\x00' not in x for x in command) or not Path(command[0]).is_absolute() or not cwd.is_absolute() or not cwd.is_dir():raise ValueError('command')
                environment=p.get('environment',{})
                if not isinstance(environment,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in environment.items()):raise ValueError('environment')
                timeout=p.get('timeout_seconds',120)
                if not isinstance(timeout,(int,float)) or not 1<=timeout<=300:raise ValueError('timeout')
                self.profile={**p,'timeout_seconds':timeout};self.state='STOPPED'
            except (OSError,ValueError,KeyError,TypeError):self.error_code='engine_profile_invalid'
        atexit.register(self.close)

    @property
    def origin(self):return self.profile['origin'] if self.profile else None

    def snapshot(self):
        with self._lock:
            if self.state=='RUNNING' and self._process is not None and self._process.poll() is not None:
                self.state='FAILED';self.error_code='engine_exited';self._record_unreachable();self._terminate()
            return {'enabled':self.profile is not None,'state':self.state,'operation_id':self.operation_id,'started_at':self.started_at,'api_version':self.api_version,'error_code':self.error_code}

    def start(self):
        with self._lock:
            self.snapshot()
            if self.state in ('STARTING','RUNNING') or self.profile is None or self._closed:return self.snapshot()
            self.state='STARTING';self.error_code=None;self.api_version=None;self.operation_id=str(uuid.uuid4())
            threading.Thread(target=self._run,daemon=True,name='memory-engine-start').start()
            return self.snapshot()

    def _record_unreachable(self):
        try:
            conn=db.connect(self.database)
            try:
                with db.transaction(conn):
                    MemoryStore(conn,schema_version=self.schema_version).record_engine_status(origin_host=self.origin,configured=True,api_version=self.api_version,features={},reachable=False,error_code='engine_exited',safe_error_summary='Managed engine exited',at=clock.now_rfc3339())
            finally:conn.close()
        except Exception:pass  # Lifecycle failure remains visible even if health storage is busy.

    def _terminate(self):
        proc=self._process
        if proc is not None:
            try:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=3)
            except ProcessLookupError:pass
            finally:
                if self._process is proc:self._process=None

    def _run(self):
        error='engine_start_failed'
        try:
            self._terminate()
            parsed=urlsplit(self.origin)
            with socket.socket(socket.AF_INET6 if ':' in parsed.hostname else socket.AF_INET,socket.SOCK_STREAM) as sock:
                sock.settimeout(.3)
                if sock.connect_ex((parsed.hostname,parsed.port))==0:error='engine_port_in_use';raise RuntimeError()
            env={k:v for k,v in os.environ.items() if k in ('HOME','USER','PATH','LANG','TMPDIR')}
            env.update(self.profile.get('environment',{}));env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
            log=Path(self.profile['cwd'])/'engine-start.log';fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'ab') as output:
                with self._lock:
                    if self._closed:return
                    self._process=subprocess.Popen(self.profile['command'],cwd=self.profile['cwd'],env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            transport=HindsightTransport(HindsightConfig(self.origin,timeout_seconds=1))
            deadline=time.monotonic()+self.profile['timeout_seconds']
            error='engine_start_timeout'
            while time.monotonic()<deadline:
                if self._closed:return
                if self._process.poll() is not None:error='engine_exited';raise RuntimeError()
                version=transport.version()
                if version.get('reachable'):
                    conn=db.connect(self.database)
                    try:probe=record_engine_probe(conn,MemoryStore(conn,schema_version=self.schema_version),transport)
                    finally:conn.close()
                    if probe.get('error_code'):error='engine_probe_failed';raise RuntimeError()
                    with self._lock:
                        if self._closed:return
                        self.api_version=probe.get('api_version');self.started_at=datetime.now(timezone.utc).isoformat();self.state='RUNNING'
                    return
                time.sleep(.25)
            raise RuntimeError()
        except Exception:
            self._terminate()
            with self._lock:self.state='FAILED';self.error_code=error

    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True
        self._terminate()


# Core is reconstructed per HTTP request. Lifecycle ownership must therefore be
# process-wide and shared by concurrent requests, keyed by the actual Core DB.
_CONTROLLERS = {}
_REGISTRY_LOCK = threading.RLock()

def get_controller(database: str, schema_version: int):
    key=str(Path(database).resolve())
    with _REGISTRY_LOCK:
        if key not in _CONTROLLERS:_CONTROLLERS[key]=EngineController(key,schema_version)
        return _CONTROLLERS[key]

def running_origin(database: str):
    with _REGISTRY_LOCK:manager=_CONTROLLERS.get(str(Path(database).resolve()))
    return manager.origin if manager is not None and manager.snapshot()['state']=='RUNNING' else None

def release_controller(database: str):
    with _REGISTRY_LOCK:manager=_CONTROLLERS.pop(str(Path(database).resolve()),None)
    if manager is not None:manager.close()
