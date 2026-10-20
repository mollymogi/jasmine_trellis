"""Receipt negatives are component checks; actual native acceptance is separate."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from jasmine_core.canonical import sha256_hex
from jasmine_core.context.delivery import project, SCHEMA

SID = '01a15503-d91d-7c60-ae02-51bd3b57a56c'
TURN = '01a15503-d977-79f1-ba71-44adaac4f164'


class DeliveryRecords(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.pack = {'context_pack_id':'ctx_01M5AG83CGMD7K3YMPQY32ZH3J',
            'source_event_id':'evt_source', 'task_id':'tsk_task', 'host_id':'hst_host',
            'rendered_content':'当前规则：不改日常服务。'}
        self.pack['rendered_sha256'] = sha256_hex(self.pack['rendered_content'])
        self.source = {'payload':{'native':{'session_id':SID,'turn_id':TURN}}}
        self.value = {'schema':SCHEMA, **{k:self.pack[k] for k in ('context_pack_id','source_event_id','task_id','host_id','rendered_sha256')},
            'native_thread_id':SID, 'native_turn_id':TURN, 'observed_at':'2026-10-19T17:00:00Z',
            'native_rollout_sha256':'a'*64, 'records':[
                {'type':'session_meta','payload':{'id':SID}},
                {'type':'turn_context','payload':{'turn_id':TURN}},
                {'type':'response_item','payload':{'type':'message','role':'developer','content':[{'type':'input_text','text':self.pack['rendered_content']}]}},
                {'type':'event_msg','payload':{'type':'task_complete','turn_id':TURN,'completed_at':'2026-10-19T17:00:00Z'}}]}
        self.path = self.root/(self.pack['context_pack_id']+'.json')

    def read(self, value=None):
        self.path.write_text(json.dumps(value or self.value))
        self.path.chmod(0o600)
        return project(self.pack, self.source, root=str(self.root))

    def test_matching_native_record_is_historical_and_projection_excludes_raw_records(self):
        result = self.read()
        self.assertEqual(result['status'],'MATCHED_NATIVE_INPUT')
        self.assertTrue(result['historical'])
        self.assertFalse(result['current_use_proved'])
        self.assertNotIn('records',json.dumps(result))
        self.assertNotIn(self.pack['rendered_content'],json.dumps(result,ensure_ascii=False))

    def test_unconfigured_and_missing_are_distinct(self):
        with patch.dict(os.environ, {'JASMINE_CONTEXT_DELIVERY_ROOT':''}):
            self.assertEqual(project(self.pack,self.source)['status'],'UNAVAILABLE')
        self.assertEqual(project(self.pack,self.source,root=str(self.root))['status'],'NO_RECORD')

    def test_cross_pack_source_task_host_and_hash_refused(self):
        for key in ('context_pack_id','source_event_id','task_id','host_id','rendered_sha256','native_thread_id','native_turn_id'):
            with self.subTest(key=key):
                value=copy.deepcopy(self.value);value[key]='wrong'
                self.assertEqual(self.read(value)['status'],'INVALID_RECORD')

    def test_quoted_text_wrong_role_wrong_turn_changed_bytes_and_no_completion_refused(self):
        for mode in ('assistant','user','wrong_turn','changed_text','missing_complete','early_complete'):
            with self.subTest(mode=mode):
                v=copy.deepcopy(self.value)
                if mode in ('assistant','user'):v['records'][2]['payload']['role']=mode
                if mode=='wrong_turn':v['records'][1]['payload']['turn_id']='old'
                if mode=='changed_text':v['records'][2]['payload']['content'][0]['text']+='x'
                if mode=='missing_complete':v['records'].pop()
                if mode=='early_complete':v['records'].insert(0,v['records'].pop())
                self.assertEqual(self.read(v)['status'],'INVALID_RECORD')

    def test_unsafe_symlinked_malformed_and_oversized_receipt_refused(self):
        self.read();self.path.chmod(0o644)
        self.assertEqual(project(self.pack,self.source,root=str(self.root))['status'],'INVALID_RECORD')
        self.path.unlink();target=self.root/'other';target.write_text(json.dumps(self.value));target.chmod(0o600);self.path.symlink_to(target)
        self.assertEqual(project(self.pack,self.source,root=str(self.root))['status'],'INVALID_RECORD')
        self.path.unlink()
        for text in ('[]','not json','x'*(4*1024*1024+1)):
            self.path.write_text(text);self.path.chmod(0o600)
            self.assertEqual(project(self.pack,self.source,root=str(self.root))['status'],'INVALID_RECORD')
        link=self.root/'root-link';link.symlink_to(self.root,target_is_directory=True)
        self.assertEqual(project(self.pack,self.source,root=str(link))['status'],'INVALID_RECORD')

from hosthook_support import HostHookHttp


class DeliveryApi(HostHookHttp):
    def test_real_http_scope_query_and_unconfigured_delivery_preserve_context(self):
        request = self.request('prompt.submit', {'text':'actual component source'}, native={'session_id':SID,'turn_id':TURN})
        status, result = self.send(request, token=self.human_hook_token)
        self.assertEqual(status, 201)
        ident = result['inject_context']['context_pack_id']
        snapshot = {t:self.db_rows('SELECT * FROM '+t) for t in ('events','tasks','rules','context_packs','memory_items','memory_jobs')}
        scopes = ['context:read','checkpoint:read','objects:read','state:read','authority:read','events:read','evidence:read']
        token = self.key(scopes)
        with patch.dict(os.environ, {'JASMINE_CONTEXT_DELIVERY_ROOT':''}):
            code, response = self.call('GET','/v1/context/'+ident+'/delivery',token=token)
        self.assertEqual(code,200)
        self.assertEqual(response['delivery']['status'],'UNAVAILABLE')
        self.assertEqual(self.call('GET','/v1/context/'+ident+'/delivery',token=self.key(scopes[1:]))[0],403)
        self.assertEqual(self.call('GET','/v1/context/'+ident+'/delivery?path=/etc/passwd',token=token)[0],400)
        self.assertEqual(self.call('POST','/v1/context/'+ident+'/delivery',{},token=token)[0],405)
        self.assertEqual(self.call('GET','/v1/context/'+ident,token=token)[0],200)
        self.assertEqual(snapshot,{t:self.db_rows('SELECT * FROM '+t) for t in snapshot})

class ContextProbeHttp(HostHookHttp):
    def setUp(self):
        super().setUp()
        from test_api import HOST
        from jasmine_core.hosthook.binding import load_binding
        scopes=['hosthook:report','context:build','context:read','checkpoint:read','objects:read','state:read','authority:read','events:read','evidence:read']
        base=self.root/'probe-private';base.mkdir(mode=0o700)
        for name in ('human','system'):
            path=base/(name+'.key');path.write_text(self.key(scopes));path.chmod(0o600)
        path=base/'binding.json';path.write_text(json.dumps({'version':1,'core_url':f'http://127.0.0.1:{self.port}','host_id':HOST,'project_id':self.project,'task_id':self.task,'api_key_file':str(base/'system.key'),'human_api_key_file':str(base/'human.key')}));path.chmod(0o600)
        self.bound=load_binding(path)
        header=base/'native.jsonl';header.write_text(json.dumps({'type':'session_meta','payload':{'id':SID,'cwd':str(self.root),'source':'exec'}})+'\n')
        self.native={'hook_event_name':'UserPromptSubmit','session_id':SID,'turn_id':TURN,'cwd':str(self.root),'transcript_path':str(header),'prompt':'component source'}

    def test_emission_uses_exact_core_bytes_and_is_not_claimed_as_actual_delivery(self):
        from jasmine_core.capture.context_probe import emit
        text,receipt=emit(self.native,self.bound)
        self.assertEqual(receipt['status'],'EMITTED_NOT_DELIVERY_PROOF')
        payload=json.loads(text)
        self.assertEqual(payload['hookSpecificOutput']['hookEventName'],'UserPromptSubmit')
        self.assertEqual(sha256_hex(payload['hookSpecificOutput']['additionalContext']),receipt['rendered_sha256'])

    def test_origin_missing_does_not_call_core(self):
        from jasmine_core.capture.context_probe import emit
        def forbidden(*args,**kwargs):raise AssertionError('Core must not be called')
        text,receipt=emit({**self.native,'transcript_path':None},self.bound,client_factory=forbidden)
        self.assertEqual(text,'');self.assertEqual(receipt['status'],'ignored')

    def test_corrupt_injection_is_refused_before_stdout(self):
        from jasmine_core.capture.context_probe import emit
        from jasmine_core.api.client import CoreClient
        class Corrupt(CoreClient):
            def post(client,path,body):
                result=super().post(path,body)
                if path=='/v1/host-hooks/events':result['inject_context']['text']+='wrong'
                return result
        with self.assertRaisesRegex(ValueError,'bytes or binding'):
            emit(self.native,self.bound,client_factory=Corrupt)

class CompletionTimestamps(unittest.TestCase):
    def test_native_unix_seconds_and_rfc3339_normalize_to_the_same_time(self):
        from jasmine_core.context.delivery import completion_time
        self.assertEqual(completion_time(1792462734),completion_time('2026-10-20T02:18:54Z'))
        for value in [True, False, None, -1, 253402300800, 1.5, 'not a timestamp']:
            with self.subTest(value=value),self.assertRaises((ValueError,OverflowError)):
                completion_time(value)
