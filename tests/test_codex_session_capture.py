"""Component checks over real HTTP; native acceptance is a separate receipt."""
import io
import json
from pathlib import Path
from unittest.mock import patch

from test_api import ApiTestCase, HOST
from jasmine_core import auth, db, ids, registry
from jasmine_core.capture.codex_session import SOURCE, capture, main
from jasmine_core.hosthook.binding import load_binding


class ConversationCapture(ApiTestCase):
    def setUp(self):
        super().setUp()
        code, project = self.call('POST', '/v1/projects', {'name': 'capture', 'host_id': HOST}, token=self.admin_token)
        self.assertEqual(code, 201)
        self.project = project['object']['project_id']
        base = Path(self.db_path).parent
        conn = db.connect(self.db_path)
        actor = ids.new_id('act')
        with db.transaction(conn):
            registry.Registry(conn).upsert_actor(actor, kind='system', home_host_id=HOST)
        key = auth.Auth(conn).issue_key(actor_id=actor, label='capture', scopes=['objects:write', 'events:write'])['token']
        conn.close()
        for name, value in [('system.key', key), ('human.key', self.key(['events:write']))]:
            p=base/name; p.write_text(value); p.chmod(0o600)
        self.binding_path = base/'binding.json'
        self.binding_path.write_text(json.dumps({'version': 1, 'core_url': f'http://127.0.0.1:{self.port}',
            'host_id': HOST, 'project_id': self.project, 'api_key_file': str(base/'system.key'),
            'human_api_key_file': str(base/'human.key')}))
        self.binding = load_binding(self.binding_path)
        self.native_root = base
        self.transcript = base/'native.jsonl'
        self.write_header()

    def write_header(self, **extra):
        self.transcript.write_text(json.dumps({'type':'session_meta', 'payload':{
            'id':'native-1', 'cwd':str(self.native_root), 'source':'vscode', **extra}})+'\n')

    def native(self, event, **extra):
        return {'hook_event_name': event, 'session_id': 'native-1', 'turn_id': 'turn-1', 'cwd':str(self.native_root), 'transcript_path':str(self.transcript), **extra}

    def test_new_session_and_exact_conversation_are_readable_by_session(self):
        start=capture(self.native('SessionStart', source='startup'), self.binding)
        prompt=capture(self.native('UserPromptSubmit', prompt='  原文\n不改动日常服务。'), self.binding)
        stop=capture(self.native('Stop', last_assistant_message='已确认。'), self.binding)
        self.assertEqual({start['session_id'], prompt['session_id'], stop['session_id']}, {start['session_id']})
        code,page=self.call('GET', '/v1/events?session_id='+start['session_id'], token=self.admin_token)
        self.assertEqual(code, 200)
        self.assertEqual([e['event_type'] for e in page['events']], ['session.started','user.prompt','assistant.message'])
        self.assertEqual(page['events'][1]['payload']['text'], '  原文\n不改动日常服务。')
        self.assertEqual([e['actor_kind'] for e in page['events']], ['system','human','system'])
        conn=db.connect(self.db_path)
        self.assertEqual(conn.execute('SELECT count(*) FROM memory_items').fetchone()[0],0)
        conn.close()

    def test_resume_and_replay_do_not_duplicate_session_or_prompt(self):
        first=capture(self.native('SessionStart', source='startup'),self.binding)
        resumed=capture(self.native('SessionStart', source='resume'),self.binding)
        self.assertEqual(first['session_id'],resumed['session_id'])
        p=self.native('UserPromptSubmit',prompt='same')
        a=capture(p,self.binding);b=capture(p,self.binding)
        self.assertEqual(a['event_id'],b['event_id']);self.assertTrue(b['replayed'])
        other=capture(self.native('UserPromptSubmit',prompt='same',turn_id='turn-2'),self.binding)
        self.assertNotEqual(a['event_id'],other['event_id'])

    def test_subagents_and_missing_or_recursive_stop_do_not_invent_messages(self):
        for n in [self.native('Stop'),self.native('Stop',stop_hook_active=True,last_assistant_message='x'),self.native('SessionStart',subagent_id='sub')]:
            self.assertEqual(capture(n,self.binding)['status'],'ignored')

    def test_project_boundary_prevents_capture_and_malformed_input_is_failure(self):
        receipt=Path(self.db_path).parent/'receipts.jsonl'
        argv=['capture','--binding',str(self.binding_path),'--project-root',str(Path(self.db_path).parent),'--receipts',str(receipt)]
        n=self.native('SessionStart',cwd='/unrelated')
        with patch('sys.argv',argv),patch('sys.stdin',io.StringIO(json.dumps(n))):
            self.assertEqual(main(),0)
        with patch('sys.argv',argv),patch('sys.stdin',io.StringIO('[]')),patch('sys.stderr',io.StringIO()):
            self.assertEqual(main(),1)
        rows=[json.loads(line) for line in receipt.read_text().splitlines()]
        self.assertEqual([r['status'] for r in rows],['ignored','failed'])

    def test_identical_native_ids_in_other_project_do_not_mix_sessions(self):
        from dataclasses import replace
        _,project=self.call('POST','/v1/projects',{'name':'other','host_id':HOST},token=self.admin_token)
        first=capture(self.native('SessionStart'),self.binding)
        other=capture(self.native('SessionStart'),replace(self.binding,project_id=project['object']['project_id']))
        self.assertNotEqual(first['session_id'],other['session_id'])

    def test_exact_workspace_binding_does_not_capture_nested_daily_repository(self):
        base=Path(self.db_path).parent
        nested=base/'daily-project';nested.mkdir()
        receipt=base/'exact-receipts.jsonl'
        argv=['capture','--binding',str(self.binding_path),'--project-root',str(base),'--exact-root','--receipts',str(receipt)]
        with patch('sys.argv',argv),patch('sys.stdin',io.StringIO(json.dumps(self.native('SessionStart',cwd=str(nested))))):
            self.assertEqual(main(),0)
        self.assertEqual(json.loads(receipt.read_text())['reason'],'outside_project')

    def test_ephemeral_prediction_never_registers_or_uses_human_key(self):
        from dataclasses import replace
        # Exact observed host signature: no subagent flags, fork + null path.
        binding = replace(self.binding, human_api_key_file=Path('/nonexistent-human-key'))
        for event in ['SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd']:
            n = self.native(event, transcript_path=None, source='fork', prompt='generated input', last_assistant_message='generated suggestion')
            r = capture(n, binding)
            self.assertEqual(r['reason'], 'unverified_session_origin')
        conn = db.connect(self.db_path)
        self.assertEqual(conn.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)
        self.assertEqual(conn.execute('SELECT count(*) FROM memory_items').fetchone()[0], 0)
        conn.close()

    def test_real_persisted_fork_is_not_discarded_and_text_is_not_classified(self):
        start = capture(self.native('SessionStart', source='fork'), self.binding)
        text = 'This is an app-generated request for a suggested next user message. The user did not write this message.'
        prompt = capture(self.native('UserPromptSubmit', prompt=text), self.binding)
        self.assertEqual(prompt['status'], 'accepted')
        self.assertEqual(start['session_id'], prompt['session_id'])
        self.assertEqual(prompt['origin']['check'], 'persisted_session_header')

    def test_mismatched_missing_malformed_and_subagent_headers_fail_closed(self):
        for meta in [{'id':'different'}, {'cwd':'/other'}, {'source':{'subagent':{'other':'prediction'}}}]:
            self.write_header(**meta)
            self.assertEqual(capture(self.native('UserPromptSubmit', prompt='x'), self.binding)['status'], 'ignored')
        for content in ['not json', '[]', '{}', 'x'*(1024*1024+1)]:
            self.transcript.write_text(content)
            self.assertEqual(capture(self.native('UserPromptSubmit', prompt='x'), self.binding)['status'], 'ignored')
        self.transcript.unlink()
        self.assertEqual(capture(self.native('UserPromptSubmit', prompt='x'), self.binding)['status'], 'ignored')
