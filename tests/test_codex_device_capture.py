"""Real Core API checks for unbound, ordinary project conversations."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import test_codex_session_capture as bound_capture
from jasmine_core.capture.codex_session import capture_device, workspace_root
from jasmine_core.capture.install_codex import install, LABEL
from jasmine_core.hosthook.install import InstallError


class DeviceCapture(bound_capture.ConversationCapture):
    def setUp(self):
        super().setUp()
        self.device = replace(self.binding, project_id=None, task_id=None, inject_context=False)

    def test_unbound_plain_hello_registers_workspace_session_and_messages(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: capture_device(self.native('SessionStart'), self.device), range(3)))
        prompt = capture_device(self.native('UserPromptSubmit', prompt='你好'), self.device)
        stop = capture_device(self.native('Stop', last_assistant_message='你好！'), self.device)
        self.assertEqual({r['project_id'] for r in [*results, prompt, stop]}, {prompt['project_id']})
        self.assertEqual({r['session_id'] for r in [*results, prompt, stop]}, {prompt['session_id']})
        _, page = self.call('GET', '/v1/events?session_id='+prompt['session_id'], token=self.admin_token)
        messages = [e for e in page['events'] if e['event_type'] in ('user.prompt', 'assistant.message')]
        self.assertEqual([e['payload']['text'] for e in messages], ['你好', '你好！'])
        self.assertTrue(all(e['task_id'] is None for e in page['events']))
        _, page = self.call('GET', '/v1/projects', token=self.admin_token)
        self.assertEqual(len(page['projects']), 2)  # explicitly bound setup + automatic workspace
        project = next(p for p in page['projects'] if p['project_id'] == prompt['project_id'])
        self.assertEqual(project['description'], str(self.native_root.resolve()))

    def test_prediction_leaves_no_automatic_project(self):
        self.assertEqual(capture_device(self.native('UserPromptSubmit', transcript_path=None, prompt='generated'), self.device)['status'], 'ignored')
        _, page = self.call('GET', '/v1/projects', token=self.admin_token)
        self.assertEqual(len(page['projects']), 1)

    def test_different_plain_workspaces_never_merge_even_when_names_match(self):
        a = capture_device(self.native('UserPromptSubmit', prompt='你好'), self.device)
        root = self.native_root/'other'/self.native_root.name
        root.mkdir(parents=True)
        self.native_root = root
        self.write_header()
        b = capture_device(self.native('UserPromptSubmit', prompt='你好'), self.device)
        self.assertNotEqual(a['project_id'], b['project_id'])
        self.assertNotEqual(a['session_id'], b['session_id'])

    def test_user_installer_preserves_foreign_hooks_backs_up_and_never_trusts(self):
        home = self.native_root/'codex';home.mkdir()
        path = home/'hooks.json'
        foreign = {'hooks':[{'type':'command','command':'echo foreign'}]}
        original = json.dumps({'description':'keep', 'hooks':{'Stop':[foreign]}})
        path.write_text(original)
        device_path = self.native_root/'device.json'
        data = json.loads(self.binding_path.read_text());data.pop('project_id')
        device_path.write_text(json.dumps(data))
        with self.assertRaises(InstallError):
            install(home, device_path, home/'receipts')
        result = install(home, device_path, home/'receipts', confirmed=True)
        self.assertEqual(Path(result['backup']).read_text(), original)
        after = json.loads(path.read_text())
        self.assertEqual(after['description'], 'keep')
        self.assertEqual(after['hooks']['Stop'][0], foreign)
        self.assertNotIn('trusted_hash', path.read_text())
        self.assertEqual(install(home, device_path, home/'receipts', confirmed=True)['status'], 'unchanged')
        self.assertEqual(sum(h.get('statusMessage') == LABEL for groups in after['hooks'].values() for g in groups for h in g['hooks']),4)
        self.assertEqual(install(home, device_path, home/'receipts', confirmed=True, disable=True)['status'], 'disabled')
        disabled=json.loads(path.read_text())
        self.assertEqual(disabled['description'],'keep');self.assertEqual(disabled['hooks']['Stop'],[foreign])
        self.assertFalse(any(h.get('statusMessage')==LABEL for groups in disabled['hooks'].values() for group in groups for h in group['hooks']))
        self.assertEqual(install(home,device_path,home/'receipts',confirmed=True)['status'],'installed')
        self.assertEqual(json.loads(path.read_text()),after)



class WorkspaceRoots(unittest.TestCase):
    def test_git_subfolders_symlinks_nested_repositories_and_worktrees(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve();repo=base/'repo';repo.mkdir();(repo/'.git').mkdir()
            sub=repo/'src';sub.mkdir()
            alias=base/'alias';alias.symlink_to(sub)
            self.assertEqual(workspace_root(str(alias)),repo)
            (sub/'.git').write_text('gitdir: /private/worktree/admin')
            self.assertEqual(workspace_root(str(sub)),sub)
            plain=base/'plain';plain.mkdir()
            self.assertEqual(workspace_root(str(plain)),plain)
            with self.assertRaises(ValueError):workspace_root('relative')
