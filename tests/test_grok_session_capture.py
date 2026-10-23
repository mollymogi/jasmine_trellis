"""Real Core HTTP component checks; ordinary native Grok acceptance is separate."""
import json
from pathlib import Path
from urllib.parse import quote
import uuid
from test_codex_session_capture import ConversationCapture
from jasmine_core.capture.grok_session import capture_device,SOURCE
from jasmine_core.capture.install_grok import install,LABEL
from jasmine_core.hosthook.binding import load_device
from jasmine_core.hosthook.install import InstallError

class GrokCapture(ConversationCapture):
    def setUp(self):
        super().setUp()
        data=json.loads(self.binding_path.read_text());data.pop('project_id');self.binding_path.write_text(json.dumps(data));self.device=load_device(self.binding_path)
        self.home=self.native_root/'grok';self.home.mkdir(mode=0o700);self.sid=str(uuid.uuid4());self.turn=str(uuid.uuid4())
        self.folder=self.home/'sessions'/quote(str(self.native_root),safe='')/self.sid;self.folder.mkdir(parents=True,mode=0o700)
        self.summary=self.folder/'summary.json';self.summary.write_text(json.dumps({'info':{'id':self.sid,'cwd':str(self.native_root)}}))
    def packet(self,event,**extra):
        names={'SessionStart':'session_start','UserPromptSubmit':'user_prompt_submit','Stop':'stop','SessionEnd':'session_end'}
        return {'hookEventName':names[event],'hook_event_name':event,'sessionId':self.sid,'cwd':str(self.native_root),
            'timestamp':'2026-10-22T09:00:00Z','promptId':self.turn,'transcriptPath':str(self.folder/'updates.jsonl'),**extra}
    def test_grok_exact_text_source_replay_and_immutable_summary_identity(self):
        first=capture_device(self.packet('UserPromptSubmit',prompt='  你好\n原文'),self.device,self.home)
        meta=json.loads(self.summary.read_text());meta['updated_at']='later';self.summary.write_text(json.dumps(meta))
        replay=capture_device(self.packet('UserPromptSubmit',prompt='  你好\n原文'),self.device,self.home)
        self.assertEqual(first['event_id'],replay['event_id']);self.assertTrue(replay['replayed'])
        stop=capture_device(self.packet('Stop',lastAssistantMessage='你好！',stopHookActive=False),self.device,self.home)
        self.assertEqual(first['session_id'],stop['session_id'])
        _,events=self.call('GET','/v1/events?session_id='+first['session_id'],token=self.admin_token)
        self.assertEqual([e['payload']['text'] for e in events['events']],['  你好\n原文','你好！'])
        self.assertEqual([e['actor_kind'] for e in events['events']],['human','system'])
        self.assertTrue(all(e['source_system']==SOURCE for e in events['events']))
    def test_early_start_waits_for_durable_summary_then_flushes_before_prompt(self):
        self.summary.unlink();start=self.packet('SessionStart',source='new');start.pop('transcriptPath')
        self.assertEqual(capture_device(start,self.device,self.home)['status'],'pending')
        self.summary.write_text(json.dumps({'info':{'id':self.sid,'cwd':str(self.native_root)}}))
        receipt=capture_device(self.packet('UserPromptSubmit',prompt='你好'),self.device,self.home)
        _,events=self.call('GET','/v1/events?session_id='+receipt['session_id'],token=self.admin_token)
        self.assertEqual([e['event_type'] for e in events['events']],['session.started','user.prompt'])
    def test_synthetic_turns_hidden_children_and_transcript_substitution_are_not_prompts(self):
        for change in [{'promptId':'scheduler-fired-test'},{'promptId':'parent-message-test'},{'subagentType':'explore'}, {'transcriptPath':'/tmp/foreign/updates.jsonl'}]:
            self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='synthetic',**change),self.device,self.home)['status'],'ignored')
        self.summary.write_text(json.dumps({'info':{'id':self.sid,'cwd':str(self.native_root)},'session_kind':'subagent_fork'}))
        self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='synthetic'),self.device,self.home)['status'],'ignored')
        _,projects=self.call('GET','/v1/projects',token=self.admin_token);self.assertEqual(len(projects['projects']),1)
    def test_writable_or_symlinked_native_identity_never_authorizes_capture(self):
        self.summary.chmod(0o666)
        packet=self.packet('UserPromptSubmit',prompt='你好')
        self.assertEqual(capture_device(packet,self.device,self.home)['status'],'ignored')
        self.summary.chmod(0o644);other=self.summary.with_name('real.json');self.summary.rename(other);self.summary.symlink_to(other)
        self.assertEqual(capture_device(packet,self.device,self.home)['status'],'ignored')
        self.summary.unlink();other.rename(self.summary)
        packet['session_id']=str(uuid.uuid4())
        with self.assertRaises(ValueError):capture_device(packet,self.device,self.home)

    def test_installer_owns_one_file_preserves_foreign_hooks_and_can_disable(self):
        folder=self.home/'hooks';folder.mkdir();foreign=folder/'my-hook.json';foreign.write_text('{"keep":true}')
        launcher=self.native_root/'launcher.py';launcher.write_text('pass')
        opts={'python':'/usr/bin/python3','launcher':launcher,'confirmed':True}
        receipt=self.native_root/'grok-receipts.jsonl'
        with self.assertRaises(InstallError):install(self.home,self.binding_path,receipt,python='/usr/bin/python3',launcher=launcher)
        self.assertEqual(install(self.home,self.binding_path,receipt,**opts)['status'],'installed')
        self.assertEqual(install(self.home,self.binding_path,receipt,**opts)['status'],'unchanged')
        target=folder/'jasmine-conversation.json';self.assertEqual(json.loads(target.read_text())['description'],LABEL)
        self.assertEqual(foreign.read_text(),'{"keep":true}');self.assertEqual(target.stat().st_mode&0o777,0o600)
        self.assertEqual(install(self.home,self.binding_path,receipt,disable=True,**opts)['status'],'disabled');self.assertFalse(target.exists());self.assertTrue(foreign.exists())


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(GrokCapture(name) for name in GrokCapture.__dict__ if name.startswith("test_"))
