"""ADR 0022: explicit links only, disclosure boundaries, and read-only snapshots."""
from unittest.mock import patch

from memory_support import MemoryTestCase, HOST, HUMAN
from test_memory_api import MemoryApiTestCase
from jasmine_core import SCHEMA_VERSION, ids
from jasmine_core.authority import AuthorityStore
from jasmine_core.memory.authority_comparison import compare_authority


class ComparisonTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.project_id = self.project()
        self.rule_id, _ = self.active_decision(project_id=self.project_id)
        self.scan()
        self.item = self.items()[0]
        self.authority = AuthorityStore(self.conn, schema_version=SCHEMA_VERSION)

    def compare(self, item=None, allowed=True):
        return compare_authority(self.conn, item or self.item, may_read_authority=allowed)

    def supersede(self):
        self.authority.transition(self.rule_id, 'supersede', {
            'expected_revision': 2, 'host_id': HOST, 'kind': 'DECISION',
            'severity': 'HARD', 'enforcement': 'CONTEXT', 'content': 'Use engine B',
            'matcher': {}, 'scope': {'kind': 'project', 'project_id': self.project_id},
            'origin_event_id': self.event('user.prompt', project_id=self.project_id, text='switch to B'),
        }, actor_id=HUMAN)

    def test_matching_version_is_not_a_semantic_success(self):
        result = self.compare()
        self.assertEqual(result['status'], 'VERSION_MATCH')
        self.assertEqual(result['reason'], 'same_linked_version_only')
        self.assertEqual(result['current_rule']['version'], result['historical_rule']['version'])

    def test_superseded_keeps_both_bodies_and_never_changes_truth(self):
        self.supersede()
        before = '\n'.join(self.conn.iterdump())
        result = self.compare()
        self.assertEqual(result['status'], 'VERSION_CHANGED')
        self.assertEqual(result['historical_rule']['version'], 1)
        self.assertEqual(result['current_rule']['version'], 2)
        self.assertIn('Hindsight', result['historical_rule']['content'])
        self.assertEqual(result['current_rule']['content'], 'Use engine B')
        self.assertEqual('\n'.join(self.conn.iterdump()), before)
        self.assertEqual(self.store.item(self.item['memory_id']), self.item)

    def test_retired_is_not_a_current_active_decision(self):
        self.authority.transition(self.rule_id, 'retire', {'expected_revision': 2, 'host_id': HOST}, actor_id=HUMAN)
        result = self.compare()
        self.assertEqual(result['status'], 'ORIGIN_RETIRED')
        self.assertEqual(result['current_rule']['status'], 'RETIRED')
        self.assertTrue(result['current_rule']['status_event_id'])

    def test_authority_scope_is_required_before_querying_or_revealing_rule(self):
        result = self.compare(allowed=False)
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertIsNone(result['current_rule'])
        self.assertIsNone(result['historical_rule'])

    def test_unlinked_or_missing_versions_are_unverifiable(self):
        for fields in ({'origin_rule_id': None}, {'origin_rule_version': None},
                       {'origin_rule_id': ids.new_id('rul')}, {'origin_rule_version': 999}):
            with self.subTest(fields=fields):
                result = self.compare({**self.item, **fields})
                self.assertEqual(result['status'], 'UNVERIFIABLE')
                self.assertIsNone(result['current_rule'])

    def test_foreign_project_and_global_item_cannot_disclose_project_rule(self):
        for fields in ({'project_id': self.project('foreign')},
                       {'scope_kind': 'global', 'project_id': None, 'task_id': None}):
            result = self.compare({**self.item, **fields})
            self.assertEqual(result['reason'], 'origin_scope_mismatch')
            self.assertIsNone(result['current_rule'])

    def test_task_link_cannot_be_compared_from_another_task(self):
        task = self.task(self.project_id)
        rule, _ = self.active_decision(project_id=self.project_id, task_id=task, rule_key='task.only')
        result = self.compare({**self.item, 'origin_rule_id': rule, 'task_id': self.task(self.project_id)})
        self.assertEqual(result['reason'], 'origin_scope_mismatch')
        self.assertIsNone(result['current_rule'])

    def test_global_rule_is_visible_for_a_project_memory(self):
        rule = self.global_decision()
        result = self.compare({**self.item, 'origin_rule_id': rule})
        self.assertEqual(result['status'], 'VERSION_MATCH')
        self.assertEqual(result['current_rule']['scope']['kind'], 'global')

    def test_other_memory_type_has_no_invented_comparison(self):
        self.assertEqual(self.compare({**self.item, 'memory_type': 'FAILURE_LESSON'})['status'], 'NOT_APPLICABLE')

    def test_pair_is_obtained_with_one_select(self):
        statements = []
        self.conn.set_trace_callback(statements.append)
        try:
            self.compare()
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith('SELECT '))


class ComparisonApiTests(MemoryApiTestCase):
    def test_memory_reader_without_authority_gets_explicit_unavailable(self):
        status, body = self.call('GET', f'/v1/memory/items/{self.memory_id}', token=self.reader)
        self.assertEqual(status, 200)
        comparison = body['authority_comparison']
        self.assertEqual(comparison['reason'], 'authority_read_required')
        self.assertIsNone(comparison['current_rule'])

    def test_authority_reader_without_memory_scope_cannot_read_item(self):
        status, _ = self.call('GET', f'/v1/memory/items/{self.memory_id}', token=self.write)
        self.assertEqual(status, 403)

    def test_combined_scopes_get_core_linked_snapshot(self):
        reader = self.key(['memory:read', 'authority:read'], 'comparison')
        status, body = self.call('GET', f'/v1/memory/items/{self.memory_id}', token=reader)
        self.assertEqual(status, 200)
        self.assertEqual(body['authority_comparison']['status'], 'VERSION_MATCH')
        self.assertEqual(body['authority_comparison']['current_rule']['rule_id'], self.rule_id)

    def test_acl_refuses_memory_before_any_authority_comparison(self):
        reader = self.key(['memory:read', 'authority:read'], 'comparison')
        from jasmine_core import clock, db
        with db.transaction(self.conn):
            self.conn.execute('INSERT INTO memory_project_actors(project_id,actor_id,granted_by_event_id,created_at) VALUES (?,?,?,?)',
                              (self.project_id, self.system_actor_id if hasattr(self, 'system_actor_id') else self.actor_for_acl(), self.origin_event(), clock.now_rfc3339()))
        with patch.dict('os.environ', {'JASMINE_MEMORY_DEPLOYMENT_MODE': 'acl'}):
            status, denied = self.call('GET', f'/v1/memory/items/{self.memory_id}', token=reader)
            missing_status, missing = self.call('GET', f'/v1/memory/items/{ids.new_id("mem")}', token=reader)
        self.assertEqual(status, missing_status)
        self.assertEqual(status, 404)
        self.assertEqual(denied['error']['code'], 'project_not_found')
        self.assertEqual(missing['error']['code'], 'memory_not_found')
        self.assertNotIn('authority_comparison', denied)

    def actor_for_acl(self):
        from jasmine_core.registry import Registry
        actor = ids.new_id('act')
        Registry(self.conn).upsert_actor(actor, kind='human', home_host_id=HOST)
        return actor

    def origin_event(self):
        return self.conn.execute('SELECT source_event_id FROM memory_items WHERE memory_id=?', (self.memory_id,)).fetchone()[0]
