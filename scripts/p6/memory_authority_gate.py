#!/usr/bin/env python3
"""P6 isolated Core/Chrome comparison gate; engine is a labelled boundary double.

--out must be a NEW private directory. No deployment, user hooks or real engine.
Daily-base, if supplied, is read only (normal health reads may append audit).
"""
from __future__ import annotations
import argparse, hashlib, json, os, socket, sqlite3, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--daily-base',type=Path);args=p.parse_args()
    os.umask(0o077);out=args.out.resolve();out.mkdir(parents=True,exist_ok=False);(out/'logs').mkdir();(out/'runtime').mkdir()
    report={'status':'RUNNING','scope':'P6 linked historical Memory / current Authority; author verification only','synthetic_sources':True,'engine':'tests.memory_support.FakeTransport; not real Hindsight','checks':[],'http':[],'started_at':datetime.now(timezone.utc).isoformat()};procs=[];conn=None
    def save(): (out/'gate.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok,details=None):
        report['checks'].append({'name':name,'passed':bool(ok),'details':details});save();print(json.dumps({'check':name,'passed':bool(ok)}),flush=True)
        if not ok:raise AssertionError(name)
    def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    def pids(port):
        r=subprocess.run(['lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'],text=True,capture_output=True);assert r.returncode in (0,1);return sorted(set(r.stdout.split()))
    def daily():
        if not args.daily_base:return None
        base=args.daily_base.resolve();config=json.loads((base/'config/runtime.json').read_text());paths=[base/'config/runtime.json']+list((base/'bin').glob('*.py'))+list((Path.home()/'Library/LaunchAgents').glob('com.jasminecore.v1.local.*.plist'))
        with urllib.request.urlopen(config['core_origin']+'/v1/health',timeout=4) as r:health=json.load(r)
        return {'sha256':{str(p):sha(p) for p in paths},'pids':{str(p):pids(p) for p in (config['core_port'],config['dashboard_port'],18084,15434)},'schema_version':health['schema_version'],'health':health['status'],'commit':config['commit']}
    def port():
        with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
    def start(argv,env,name):
        with (out/'logs'/name).open('wb') as log:proc=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        procs.append(proc);return proc
    def wait(url,proc):
        for _ in range(100):
            if proc.poll() is not None:raise RuntimeError('isolated process exited: '+str(proc.returncode))
            try:urllib.request.urlopen(url,timeout=1).close();return
            except OSError:time.sleep(.1)
        raise TimeoutError('isolated listener')
    def call(method,path,body=None,key='setup',expected=200):
        req=urllib.request.Request(core+path,method=method,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+keys[key]['token'],'Content-Type':'application/json'})
        try:r=urllib.request.urlopen(req,timeout=20)
        except urllib.error.HTTPError as e:r=e
        body=json.load(r);report['http'].append({'method':method,'path':path,'status':r.status,'request_id':r.headers.get('x-request-id'),'response':body});check(method+' '+path,r.status==expected,{'status':r.status});return body
    def truth():
        names=('rules','rule_versions','rule_changes','events','tasks','steps','memory_items','memory_item_sources','memory_jobs','memory_job_attempts')
        return {t:hashlib.sha256(repr([tuple(x) for x in conn.execute('SELECT * FROM '+t+' ORDER BY rowid')]).encode()).hexdigest() for t in names}
    def browser(phase):
        desc={**descriptor,'phase':phase};r=subprocess.run(['node',str(ROOT/'scripts/p6/memory_authority_ui.mjs')],input=json.dumps(desc),text=True,capture_output=True,timeout=150)
        (out/'logs'/('browser-'+phase+'.log')).write_text(r.stdout+r.stderr);check('actual Chrome '+phase,r.returncode==0,{'returncode':r.returncode,'stdout':r.stdout[-2000:],'stderr':r.stderr[-1000:]})
    try:
        report['daily_before']=daily();report['candidate_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        check('candidate clean',not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
        report['source_sha256']={str(p.relative_to(ROOT)):sha(p) for tree in ('src','web/src','scripts/p6') for p in (ROOT/tree).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
        from jasmine_core import SCHEMA_VERSION,db,ids,registry,auth,clock
        from jasmine_core.migrations import migrate
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.memory.jobs import JobStore
        from jasmine_core.memory.eligibility import SourceRegistry
        from jasmine_core.memory.worker import MemoryWorker
        from jasmine_core.memory.adapter import MemoryAdapter
        from jasmine_core.memory.lifecycle import MemoryLifecycle
        from jasmine_core.memory.types import RecallRequest,Provenance,BankRef
        from memory_support import FakeTransport
        database=out/'runtime/core.db';conn=db.connect(database);migrate(conn);host=ids.new_id('hst');actor=ids.new_id('act')
        with db.transaction(conn):reg=registry.Registry(conn);reg.upsert_host(host,display_name='Isolated P6 fixture');reg.upsert_actor(actor,kind='human',home_host_id=host)
        scopes=['objects:read','objects:write','events:read','events:write','authority:read','authority:propose','authority:manage','memory:read']
        keys={name:auth.Auth(conn).issue_key(actor_id=actor,label='SCRATCH '+name,scopes=s) for name,s in [('setup',scopes),('read',['objects:read','events:read','authority:read','memory:read']),('limited',['objects:read','events:read','memory:read'])]}
        cp,up=port(),port();assert cp!=up and not {cp,up}&{18787,15180,18084,15434};core=f'http://127.0.0.1:{cp}';ui=f'http://127.0.0.1:{up}';report['ports']={'core':cp,'ui':up}
        env={k:v for k,v in os.environ.items() if k in ('HOME','USER','PATH','LANG','TMPDIR')};env.update(PYTHONPATH=str(ROOT/'src'),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',JASMINE_MEMORY_DEPLOYMENT_MODE='single_user')
        server=start([sys.executable,'-B','-m','jasmine_core.cli','serve','--db',str(database),'--port',str(cp)],env,'core.log');wait(core+'/v1/health',server)
        project=call('POST','/v1/projects',{'name':'SYNTHETIC P6 Memory conflict fixture','host_id':host},expected=201)['object']['project_id']
        def seed(key,content):
            event=call('POST','/v1/events',{'event_type':'user.prompt','source_system':'labelled-p6-isolated-fixture','host_id':host,'project_id':project,'payload':{'text':'SYNTHETIC fixture: '+content}},expected=201)['event']['event_id']
            rule=call('POST','/v1/rules/proposals',{'rule_key':key,'kind':'DECISION','severity':'HARD','enforcement':'CONTEXT','content':content,'matcher':{},'scope':{'kind':'project','project_id':project},'origin_event_id':event,'host_id':host},expected=201)['rule']['rule_id']
            call('POST','/v1/rules/'+rule+'/approve',{'host_id':host,'expected_revision':1})
            return {'rule':rule,'origin':event}
        live=seed('live.engine','SYNTHETIC old decision: use engine A <script>globalThis.p6Injected=1</script>')
        retired=seed('retired.engine','SYNTHETIC retired decision: use engine C')
        store=MemoryStore(conn,schema_version=SCHEMA_VERSION);jobs=JobStore(conn,store);jobs.enqueue_from_sources(SourceRegistry());transport=FakeTransport(store);worker=MemoryWorker(conn,store,transport)
        for _ in range(8):
            if not jobs.jobs(status='PENDING'):break
            worker.tick(scan=False)
        for entry in (live,retired):
            row=next(i for i in store.items() if i['origin_rule_id']==entry['rule']);entry['memory']=row['memory_id'];check('derived memory retained by labelled double',row['engine_state']=='RETAINED')
        call('POST','/v1/rules/'+retired['rule']+'/retire',{'expected_revision':2,'host_id':host})
        # Direct store fixture intentionally lacks a Rule link; never labelled as
        # a captured/retained user decision. It exercises unknown-data display.
        text='SYNTHETIC unlinked historical note; not a current rule'
        with db.transaction(conn):
            unlinked=store.insert_item(bank=BankRef('jasmine-project-'+project,'project',project),memory_type='CONFIRMED_DECISION',document_id='synthetic-unlinked',source_event_id=live['origin'],provenance=Provenance(source_event_ids=(live['origin'],)),sanitized_text=text,content_sha256=hashlib.sha256(text.encode()).hexdigest(),task_id=None,at=clock.now_rfc3339())
        ui_proc=start(['node',str(ROOT/'web/server/serve.mjs'),'--root',str(ROOT/'web/dist'),'--host','127.0.0.1','--port',str(up)],{**env,'P6_CORE_ORIGIN':core,'P6_ENABLE_P4_READS':'1','P6_ENABLE_P4_WRITES':'0'},'ui.log');wait(ui,ui_proc)
        descriptor={'ui':ui,'core':core,'out':str(out),'host':host,'project':project,'live':live,'retired':retired,'unlinked':unlinked,'readToken':keys['read']['token'],'limitedToken':keys['limited']['token'],'setupToken':keys['setup']['token']}
        memory_before=store.item(live['memory']);browser('live');check('browser comparison and Rule write preserve old Memory',store.item(live['memory'])==memory_before)
        MemoryLifecycle(conn,store).pin(live['memory'],{'host_id':host,'expected_revision':1,'reason':'SYNTHETIC pin cannot override Authority'},actor_id=actor,actor_kind='human',host_id=host)
        for entry in (live,retired):
            row=store.item(entry['memory']);transport.answer_for(row['bank_id'],row['memory_id'],row['sanitized_text'])
        result=MemoryAdapter(conn,store,transport,schema_version=SCHEMA_VERSION).recall(RecallRequest(project_id=project,actor_id=actor,query='SYNTHETIC engine choice',max_tokens=600,authority_refs=(live['rule'],retired['rule'])))
        check('existing recall refuses superseded and retired even pinned',not result.items and {f.reason for f in result.filtered}=={'superseded_decision','retired_decision'},result.as_dict())
        descriptor['trace']=result.trace_id;before=truth();browser('readonly');check('read-only browser phase preserves all fixture Truth Memory and jobs',truth()==before)
        limited=call('GET','/v1/memory/items/'+live['memory'],key='limited')['authority_comparison'];check('limited response does not expose rule snapshot',limited['status']=='UNAVAILABLE' and limited['current_rule'] is None and limited['historical_rule'] is None)
        report['fixture']={k:descriptor[k] for k in ('host','project','live','retired','unlinked','trace')};report['browser_checks']={phase:len(json.loads((out/('browser-'+phase+'.json')).read_text())['checks']) for phase in ('live','readonly')}
        report['status']='PASS_P6_MEMORY_AUTHORITY_SCOPED_AUTHOR_VERIFICATION'
    except BaseException as exc:report['status']='FAIL';report['error']=repr(exc);raise
    finally:
        for proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=8)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        if conn is not None:conn.close()
        if 'cp' in locals():check('isolated services stopped',not pids(cp) and not pids(up))
        report['daily_after']=daily();check('daily service config PIDs and schema unchanged',report.get('daily_before')==report['daily_after']);report['finished_at']=datetime.now(timezone.utc).isoformat();save()
        print(json.dumps({'status':report['status'],'checks':len(report['checks']),'browser':report.get('browser_checks')}),flush=True)

if __name__=='__main__':main()
