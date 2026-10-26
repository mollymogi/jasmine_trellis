"""Local CLI selection preserves source validation and cannot execute source instructions."""
import hashlib,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from jasmine_core.interpreter_cli import LocalCliProvider,parse_cli_output,cli_environment,cli_argv,sandbox_argv,bounded_run
from jasmine_core.interpreter_provider import ProviderFailure,configured_provider,UnavailableProvider,CodexProvider

class LocalCliTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.profile={'executable':sys.executable,'executable_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),'models':[{'id':'grok-4.6','label':'Grok'}],'version':'component-test'}
  mocked=patch('jasmine_core.interpreter_cli.GROK_SHA256',self.profile['executable_sha256']);mocked.start();self.addCleanup(mocked.stop)
 def test_native_camel_case_output_and_nonobject_fail_closed(self):
  doc={'event_id':'source-id','candidates':[]}
  stream='\n'.join(json.dumps(r) for r in ({'type':'system','subtype':'init','tools':[]},{'type':'assistant','message':{'content':[{'type':'text','text':'ok'}]}},{'type':'result','subtype':'success','structured_output':doc}))
  self.assertEqual(json.loads(parse_cli_output('grok',stream)),doc)
  with self.assertRaises(ProviderFailure):parse_cli_output('grok',stream.replace('"tools": []','"tools": ["read_file"]'))
  for raw in ('[]','not-json','{}','{"is_error":true}'):
   with self.assertRaises(ProviderFailure):parse_cli_output('grok',raw)
  with self.assertRaises(ProviderFailure):parse_cli_output('opencode','{}')
 def test_fixed_argv_private_home_and_disabled_hooks_without_auth_copy(self):
  print('local CLI fixture capability:',json.dumps({'platform':sys.platform,'sandbox_exec_is_file':Path('/usr/bin/sandbox-exec').is_file()}),flush=True)
  auth=self.root/'native-auth';auth.write_text('component-secret');auth.chmod(0o600);work=self.root/'work';work.mkdir()
  env=cli_environment('grok',work,{'auth_file':str(auth)})
  self.assertTrue((Path(env['GROK_HOME'])/'auth.json').is_symlink());self.assertEqual(auth.read_text(),'component-secret')
  self.assertIn('hooks = false',(Path(env['GROK_HOME'])/'config.toml').read_text())
  argv=cli_argv('grok',sys.executable,work,'grok-4.6');self.assertEqual(argv[argv.index('--tools')+1],'');self.assertIn('--deny',argv);self.assertIn('--no-subagents',argv)
  # Policy construction is a component test of an explicitly available sandbox.
  with patch('jasmine_core.interpreter_cli.Path.is_file',return_value=True):
   wrapped=sandbox_argv(sys.executable,work,argv)
  self.assertIn('(deny process-exec)',wrapped[2]);self.assertIn('(deny file-write*)',wrapped[2])
 def test_selection_routes_next_invocation_and_preserves_previous_configuration(self):
  print('local CLI fixture capability:',json.dumps({'platform':sys.platform,'sandbox_exec_is_file':Path('/usr/bin/sandbox-exec').is_file()}),flush=True)
  choice=self.root/'choice.json';registry=self.root/'registry.json'
  registry.write_text(json.dumps({'version':2,'providers':[{'id':'grok','detected':True,'enabled':True,**self.profile}]}));registry.chmod(0o600)
  choice.write_text(json.dumps({'version':2,'provider':'grok','model':'grok-4.6','revision':1}));choice.chmod(0o600)
  with patch.dict(os.environ,{'JASMINE_CORE_INTERPRETER_PROVIDERS':str(registry),'JASMINE_CORE_INTERPRETER_MODEL_CONFIG':str(choice),'JASMINE_CORE_DAILY_INTERPRETER':'1'}), patch('jasmine_core.interpreter_cli.Path.is_file',return_value=True):
   first=configured_provider();self.assertIsInstance(first,LocalCliProvider);self.assertTrue(first.valid)
   self.assertEqual(first.configuration()['prompt_version'],'jasmine.interpreter.daily.v2')
   choice.write_text(json.dumps({'version':2,'provider':'opencode','model':'arbitrary','revision':2}));self.assertIsInstance(configured_provider(),UnavailableProvider)
   self.assertEqual(first.configuration()['model'],'grok-4.6')
 def test_private_registry_and_binary_are_pinned_and_no_fallback_is_used(self):
  p=LocalCliProvider('grok',self.profile,'unknown');self.assertFalse(p.valid)
  p=LocalCliProvider('opencode',self.profile,'grok-4.6');self.assertFalse(p.valid)
  wrong={**self.profile,'executable_sha256':'0'*64};p=LocalCliProvider('grok',wrong,'grok-4.6');self.assertFalse(p.valid)
 def test_bounded_child_timeout_large_output_and_auth_error_are_reported(self):
  env={'PATH':os.environ['PATH']}
  with self.assertRaises(ProviderFailure) as failure:bounded_run([sys.executable,'-c','import time;time.sleep(10)'],b'',env,self.root,.1)
  self.assertEqual(failure.exception.code,'provider_timeout')
  with self.assertRaises(ProviderFailure) as failure:bounded_run([sys.executable,'-c','print("x"*300000)'],b'',env,self.root,5)
  self.assertEqual(failure.exception.code,'provider_output_too_large')
  with self.assertRaises(ProviderFailure) as failure:bounded_run([sys.executable,'-c','import sys;print("not logged in");sys.exit(1)'],b'',env,self.root,5)
  self.assertEqual(failure.exception.code,'provider_login_required')

 def test_missing_native_sandbox_fails_closed_without_running_client(self):
  with patch('jasmine_core.interpreter_cli.Path.is_file',return_value=False), patch('jasmine_core.interpreter_cli.bounded_run') as run:
   with self.assertRaises(ProviderFailure) as failure:sandbox_argv(sys.executable,self.root,[sys.executable])
   self.assertEqual(failure.exception.code,'provider_sandbox_unavailable')
   provider=LocalCliProvider('grok',self.profile,'grok-4.6')
   self.assertFalse(provider.valid)
   with self.assertRaises(ProviderFailure):provider.extract({})
   run.assert_not_called()

 def test_native_sandbox_capability_matches_this_runner(self):
  provider=LocalCliProvider('grok',self.profile,'grok-4.6')
  available=Path('/usr/bin/sandbox-exec').is_file()
  self.assertEqual(provider.valid,available)
  if available:
   self.assertEqual(sandbox_argv(sys.executable,self.root,[sys.executable])[0],'/usr/bin/sandbox-exec')
  else:
   with self.assertRaises(ProviderFailure) as failure:sandbox_argv(sys.executable,self.root,[sys.executable])
   self.assertEqual(failure.exception.code,'provider_sandbox_unavailable')
