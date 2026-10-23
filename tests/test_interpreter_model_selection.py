"""Model choice never expands the execution or authentication boundary."""
import hashlib,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from jasmine_core.interpreter_provider import CodexProvider,UnavailableProvider,configured_provider

class ModelSelection(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.catalog=self.root/'catalog.json';self.selection=self.root/'selection.json'
  self.entries=[{'slug':model,'display_name':model,'apply_patch_tool_type':None,'experimental_supported_tools':[],'supports_search_tool':False,'tool_mode':None} for model in ('gpt-6.1-sol','gpt-6-luna')]
  self.catalog.write_text(json.dumps({'models':self.entries}));self.selection.write_text(json.dumps({'version':1,'model':'gpt-6.1-sol','revision':1}));self.selection.chmod(0o600)
  fingerprint=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest();p=patch.object(CodexProvider,'SUPPORTED_EXECUTABLE_SHA256',fingerprint);p.start();self.addCleanup(p.stop)
  env=patch.dict(os.environ,{'JASMINE_CORE_INTERPRETER_CODEX':sys.executable,'JASMINE_CORE_INTERPRETER_CATALOG':str(self.catalog),'JASMINE_CORE_INTERPRETER_MODEL_CONFIG':str(self.selection)});env.start();self.addCleanup(env.stop)
 def test_next_provider_uses_new_choice_old_invocation_and_catalog_stay_frozen(self):
  old=configured_provider();self.assertTrue(old.valid)
  self.selection.write_text(json.dumps({'version':1,'model':'gpt-6-luna','revision':2}))
  new=configured_provider();self.assertTrue(new.valid)
  self.assertEqual(old.configuration()['model'],'gpt-6.1-sol');self.assertEqual(new.configuration()['model'],'gpt-6-luna')
  self.assertEqual([m['slug'] for m in json.loads(new.catalog_bytes)['models']],['gpt-6-luna'])
  argv=new._argv(self.root);self.assertEqual(argv[argv.index('-m')+1],'gpt-6-luna')
  self.assertIn('--ignore-user-config',argv);self.assertIn('features.hooks=false',argv);self.assertIn('features.shell_tool=false',argv)
 def test_unknown_model_unsafe_tools_and_invalid_private_selection_fail_closed(self):
  self.assertFalse(CodexProvider(sys.executable,str(self.catalog),model='gpt-unknown').valid)
  for mode in ({'model':'gpt-6-luna','revision':2,'version':1,'base_url':'https://evil.invalid'}, {'model':'gpt-6-luna','revision':True,'version':1}):
   self.selection.write_text(json.dumps(mode));self.assertIsInstance(configured_provider(),UnavailableProvider)
  self.selection.write_text(json.dumps({'model':'gpt-6-luna','revision':2,'version':1}));self.selection.chmod(0o644)
  self.assertIsInstance(configured_provider(),UnavailableProvider)
  self.selection.unlink();self.selection.symlink_to(self.catalog);self.assertIsInstance(configured_provider(),UnavailableProvider)
  self.entries[1]['tool_mode']='code_mode_only';self.catalog.write_text(json.dumps({'models':self.entries}))
  self.assertFalse(CodexProvider(sys.executable,str(self.catalog),model='gpt-6-luna').valid)
 def test_prepare_is_idempotent_preserves_choice_runtime_settings_and_credentials(self):
  script=Path(__file__).resolve().parents[1]/'scripts/deployment/prepare-interpreter-models.py';spec=importlib.util.spec_from_file_location('prepare_models',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  base=self.root/'install';config=base/'config';(config/'credentials').mkdir(parents=True);(base/'logs').mkdir()
  runtime={'conversation_processing':{'enabled':True,'executable':sys.executable,'catalog':str(self.catalog)},'memory':{'enabled':True},'untouched':'keep'}
  for path,value in [(config/'runtime.json',runtime),(config/'credentials/browser-daily.json',{'token':'component-only'})]:path.write_text(json.dumps(value));path.chmod(0o600)
  cache=self.root/'cache.json';entries=[{**m,'visibility':'list','model_messages':{'note':'x'*40000}} for m in self.entries];cache.write_text(json.dumps({'models':entries}));cache.chmod(0o600)
  with self.assertRaises(ValueError):module.prepare(base,cache)
  first=module.prepare(base,cache,True);self.assertEqual(first['model_count'],2)
  choice=config/'interpreter-model.json';choice.write_text(json.dumps({'version':1,'model':'gpt-6-luna','revision':2}))
  second=module.prepare(base,cache,True);self.assertEqual(second['model'],'gpt-6-luna')
  self.assertEqual(json.loads((config/'runtime.json').read_text())['memory'],runtime['memory'])
  self.assertEqual(json.loads((config/'credentials/browser-daily.json').read_text()),{'token':'component-only'})

if __name__=='__main__':unittest.main()
