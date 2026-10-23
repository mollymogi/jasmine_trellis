"""Catalog reads must not be confused with inherited effective-rule/Guard lookup."""
from test_authority import AuthorityHttp
from test_api import HOST
from jasmine_core import ids

class RuleCatalog(AuthorityHttp):
 def test_all_states_paging_permissions_and_read_only(self):
  created=[]
  for n in range(3):
   status,result=self.call('POST','/v1/rules/proposals',self.proposal(rule_key=f'catalog-{n}'),token=self.manage_key)
   self.assertEqual(status,201);created.append(result['rule']['rule_id'])
  self.call('POST',f'/v1/rules/{created[0]}/approve',{'host_id':HOST,'expected_revision':1},token=self.manage_key)
  self.call('POST',f'/v1/rules/{created[0]}/retire',{'host_id':HOST,'expected_revision':2},token=self.manage_key)
  read=self.key(['events:read'])
  before=self.call('GET','/v1/events',token=read)[1]
  code,page=self.call('GET','/v1/rules?limit=2',token=self.manage_key)
  self.assertEqual(code,200);self.assertEqual(page['count'],2);self.assertTrue(page['has_more']);self.assertEqual(page['next_offset'],2)
  code,tail=self.call('GET','/v1/rules?limit=2&offset=2',token=self.manage_key)
  self.assertEqual(code,200);self.assertFalse(tail['has_more']);self.assertIsNone(tail['next_offset'])
  self.assertEqual(len({r['rule_id'] for r in page['rules']+tail['rules']}),3)
  self.assertEqual({r['status'] for r in page['rules']+tail['rules']},{'PROPOSED','RETIRED'})
  self.assertEqual(self.call('GET','/v1/events',token=read)[1],before)
  self.assertEqual(self.call('GET','/v1/rules',token=self.agent_key)[0],403)
 def test_catalog_ownership_filter_and_effective_rules_have_different_semantics(self):
  self.call('POST','/v1/rules/proposals',self.proposal(),token=self.manage_key)
  code,found=self.call('GET',f'/v1/rules?project_id={self.project_id}',token=self.manage_key)
  self.assertEqual(code,200);self.assertEqual(found['count'],1)
  self.assertEqual(self.call('GET','/v1/rules/active',token=self.manage_key)[1]['rules'],[])
  self.assertEqual(self.call('GET','/v1/rules?scope_kind=global',token=self.manage_key)[1]['rules'],[])
  self.assertEqual(self.call('GET','/v1/rules?status=ACTIVE',token=self.manage_key)[1]['rules'],[])
  for suffix in ('limit=0','offset=-1','limit=201','status=FAKE','limit=2&limit=3','scope_kind=global&project_id='+self.project_id,'bogus=x','project_id=tsk_bad','task_id=bad'):
   self.assertEqual(self.call('GET','/v1/rules?'+suffix,token=self.manage_key)[0],400,suffix)
