import unittest

from jasmine_core.resolution_policy import compile_plan


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


if __name__ == '__main__':
    unittest.main()
