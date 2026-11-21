import unittest

import json
import sqlite3
from unittest import mock

from jasmine_core import resolution_policy
from jasmine_core.resolution_policy import compile_plan, repeated_candidates


def _source(text, task_id=None):
    return {'text': text, 'source_type': 'USER_EXPLICIT', 'project_id': 'prj_a', 'task_id': task_id,
            'event_id': 'evt_a', 'session_id': None, 'host_id': 'hst_a', 'actor_id': 'act_a'}


def _candidate(text, kind, scope='TASK', certainty='EXPLICIT'):
    return {'kind': kind, 'certainty': certainty, 'confidence': 0.9, 'content': text, 'impact': 'LOW',
            'rationale': '', 'scope': {'kind': scope, 'project_id': None, 'task_id': None, 'path': None, 'tool': None},
            'source_span': {'start': 0, 'end': len(text), 'quote': text}}


class Policy(unittest.TestCase):
    def test_an_acceptance_never_becomes_a_rule(self):
        text = '验收通过'
        for task in (None, 'tsk_a'):
            item = compile_plan(_source(text, task), [_candidate(text, 'ACCEPTANCE')])['items'][0]
            self.assertEqual((item['action'], item['pending'], item['reason']),
                             ('NO_ACTION', False, 'observed_memory_only'))

    def test_a_task_capability_keeps_the_original_review_policy(self):
        text = '这个任务需要用 blender'
        plan = compile_plan(_source(text), [_candidate(text, 'REQUIRED_CAPABILITY')])
        self.assertEqual(plan['disposition'], 'PENDING_REVIEW')
        self.assertEqual(plan['items'][0]['reason'], 'task_binding_required')
        plan = compile_plan(_source(text, 'tsk_a'), [_candidate(text, 'REQUIRED_CAPABILITY')])
        self.assertEqual(plan['items'][0]['action'], 'CREATE_RULE')

    def test_a_quoted_task_request_still_waits(self):
        text = '他说要新建一个任务'
        item = compile_plan(_source(text), [_candidate(text, 'TASK_CREATE_OR_ATTACH', certainty='QUOTED')])['items'][0]
        self.assertEqual((item['pending'], item['reason']), (True, 'not_explicit'))


class Durable(unittest.TestCase):
    def _plan(self, text, kind='RULE', source_system=None, certainty='EXPLICIT'):
        return compile_plan(_source(text), [_candidate(text, kind, scope='PROJECT', certainty=certainty)],
                            source_system=source_system)['items'][0]

    def test_a_question_is_not_a_durable_instruction(self):
        for text in ('以后是不是还要改钩子？', '为什么记住的全是乱的', 'Why does it always fail', '以后要不要统一用中文'):
            self.assertEqual(self._plan(text)['action'], 'NO_ACTION', text)
        self.assertEqual(self._plan('以后文档统一用中文')['action'], 'CREATE_RULE')

    def test_native_capture_task_binding_is_never_queued(self):
        text = '看一下这个'
        item = compile_plan(_source(text), [_candidate(text, 'TASK_CREATE_OR_ATTACH', certainty='AMBIGUOUS')],
                            source_system='claude-native-conversation-v1')['items'][0]
        self.assertEqual((item['action'], item['pending']), ('NO_ACTION', False))
        item = compile_plan(_source(text), [_candidate(text, 'TASK_CREATE_OR_ATTACH', certainty='AMBIGUOUS')],
                            source_system='codex-hook')['items'][0]
        self.assertTrue(item['pending'])

    def test_native_capture_never_creates_a_task(self):
        text = '用约200字解释TCP三次握手'
        plan = compile_plan(_source(text), [_candidate(text, 'TASK_CREATE_OR_ATTACH')],
                            source_system='antigravity-native-conversation-v1')
        self.assertEqual((plan['items'][0]['action'], plan['disposition']), ('NO_ACTION', 'NO_ACTION'))
        self.assertEqual(compile_plan(_source(text), [_candidate(text, 'TASK_CREATE_OR_ATTACH')],
                                      source_system='codex-hook')['items'][0]['action'], 'CREATE_TASK')


class Promotion(unittest.TestCase):
    """Tier 4: the same correction in a second session is proposed once."""
    TEXT = '路径统一使用云盘路径，不要用本地下载目录'

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript(
            "CREATE TABLE events(event_id TEXT, session_id TEXT);"
            "CREATE TABLE memory_items(memory_id TEXT, status TEXT, source_event_id TEXT, scope_kind TEXT,"
            " project_id TEXT, admission TEXT, memory_type TEXT, sanitized_text TEXT);"
            "CREATE TABLE resolutions(source_event_id TEXT, plan_json TEXT, created_at TEXT);")
        patcher = mock.patch('jasmine_core.memory.scope.workspace_group', return_value=('prj_b',))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _memory(self, text, session, project='prj_b', admission='OBSERVED', kind='OBSERVED_CORRECTION'):
        n = self.conn.execute('SELECT count(*) FROM events').fetchone()[0]
        self.conn.execute('INSERT INTO events VALUES(?,?)', (f'evt_{n}', session))
        self.conn.execute('INSERT INTO memory_items VALUES(?,?,?,?,?,?,?,?)',
                          (f'mem_{n}', 'ACTIVE', f'evt_{n}', 'project', project, admission, kind, text))

    def _repeated(self, text=None):
        text = text or self.TEXT
        source = {**_source(text), 'session_id': 'ses_now'}
        return repeated_candidates(self.conn, source, [_candidate(text, 'CORRECTION', scope='PROJECT')])

    def test_said_in_another_session_of_the_workspace_is_proposed(self):
        self._memory('路径统一使用云盘路径，不要用本地的下载目录', 'ses_old')
        self.assertEqual(self._repeated(), {0})
        item = compile_plan(_source(self.TEXT), [_candidate(self.TEXT, 'CORRECTION', scope='PROJECT')],
                            repeated={0})['items'][0]
        self.assertEqual((item['action'], item['pending'], item['reason']),
                         ('NO_ACTION', True, 'repeated_across_sessions'))

    def test_once_or_same_session_or_unrelated_is_not(self):
        self.assertEqual(self._repeated(), set())
        self._memory(self.TEXT, 'ses_now')
        self._memory('镜头不要摇，只改高度', 'ses_old')
        self._memory(self.TEXT, 'ses_old', project='prj_other')
        self.assertEqual(self._repeated(), set())

    def test_covered_by_a_rule_or_already_proposed_is_not(self):
        self._memory(self.TEXT, 'ses_old')
        self._memory(self.TEXT, None, admission='AUTHORITY', kind='CONFIRMED_DECISION')
        self.assertEqual(self._repeated(), set())

    def test_weekly_cap(self):
        self._memory(self.TEXT, 'ses_old')
        for i in range(resolution_policy.PROMOTION_WEEKLY_CAP):
            plan = json.dumps({'items': [{'reason': 'repeated_across_sessions', 'candidate': {'content': f'完全无关的第{i}条约定内容'}}]})
            self.conn.execute('INSERT INTO resolutions VALUES(?,?,?)', (f'evt_x{i}', plan, '2999-01-01T00:00:00Z'))
        self.assertEqual(self._repeated(), set())

    def test_a_proposal_is_not_repeated(self):
        self._memory(self.TEXT, 'ses_old')
        plan = json.dumps({'items': [{'reason': 'repeated_across_sessions', 'candidate': {'content': self.TEXT}}]})
        self.conn.execute('INSERT INTO resolutions VALUES(?,?,?)', ('evt_x', plan, '2999-01-01T00:00:00Z'))
        self.assertEqual(self._repeated(), set())


if __name__ == '__main__':
    unittest.main()
