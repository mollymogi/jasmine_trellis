"""Claude current-input provenance and shared-settings preservation over real Core HTTP."""
import json,uuid,unittest
from datetime import datetime,timezone
from test_codex_session_capture import ConversationCapture
from jasmine_core.capture.claude_session import capture_device,SOURCE
from jasmine_core.capture.install_claude import install,LABEL
from jasmine_core.hosthook.binding import load_device
from jasmine_core.hosthook.install import InstallError

class ClaudeCapture(ConversationCapture):
    def setUp(self):
        super().setUp()
        body=json.loads(self.binding_path.read_text());body.pop('project_id');self.binding_path.write_text(json.dumps(body));self.device=load_device(self.binding_path)
        self.home=self.native_root/'claude';self.home.mkdir(mode=0o700)
        self.sid=str(uuid.uuid4());self.turn=str(uuid.uuid4());self.history=self.home/'history.jsonl'
        self.folder=self.home/'projects'/'workspace';self.folder.mkdir(parents=True)
        self.transcript=self.folder/(self.sid+'.jsonl');self.transcript.write_text('')
    def packet(self,event,**kwargs):
        return {'hook_event_name':event,'session_id':self.sid,'prompt_id':self.turn,'cwd':str(self.native_root),'transcript_path':str(self.transcript),**kwargs}
    def typed(self,text,**kwargs):
        self.folder.mkdir(parents=True,exist_ok=True)
        row={'type':'user','sessionId':self.sid,'promptId':self.turn,'cwd':str(self.native_root),'timestamp':datetime.now(timezone.utc).isoformat(),
             'uuid':str(uuid.uuid4()),'isSidechain':False,'origin':{'kind':'human'},'promptSource':'typed','turnOrigin':'human','userType':'external','entrypoint':'cli','message':{'role':'user','content':text},**kwargs}
        with self.transcript.open('a') as stream:stream.write(json.dumps(row)+'\n')
        self.transcript.chmod(0o600)
    def test_native_typed_prompt_exact_reply_resume_and_replay(self):
        self.assertEqual(capture_device(self.packet('SessionStart',source='startup'),self.device,self.home)['status'],'pending')
        text='  你好\n原文';self.typed(text)
        p=self.packet('UserPromptSubmit',prompt=text);first=capture_device(p,self.device,self.home);replay=capture_device(p,self.device,self.home)
        self.assertEqual(first['event_id'],replay['event_id']);self.assertTrue(replay['replayed'])
        stop=capture_device(self.packet('Stop',last_assistant_message='你好！'),self.device,self.home)
        self.assertEqual(first['session_id'],stop['session_id'])
        capture_device(self.packet('SessionStart',source='resume'),self.device,self.home)
        self.turn=str(uuid.uuid4());self.typed(text)
        second=capture_device(self.packet('UserPromptSubmit',prompt=text),self.device,self.home)
        self.assertEqual(first['session_id'],second['session_id']);self.assertNotEqual(first['event_id'],second['event_id'])
        _,events=self.call('GET','/v1/events?session_id='+first['session_id'],token=self.admin_token)
        self.assertEqual([e['payload']['text'] for e in events['events'] if e['event_type']=='user.prompt'],[text,text])
        self.assertTrue(all(e['source_system']==SOURCE for e in events['events']))
        self.assertEqual([e['actor_kind'] for e in events['events'] if e['event_type']=='user.prompt'],['human','human'])
    def test_first_prompt_before_transcript_directory_persistence(self):
        self.transcript.unlink();self.folder.rmdir()
        self.assertEqual(capture_device(self.packet('SessionStart'),self.device,self.home)['status'],'pending')
        self.typed('你好')
        first=capture_device(self.packet('UserPromptSubmit',prompt='你好'),self.device,self.home)
        self.assertEqual(first['status'],'accepted')
        _,events=self.call('GET','/v1/events?session_id='+first['session_id'],token=self.admin_token)
        self.assertEqual([e['event_type'] for e in events['events']],['session.started','user.prompt'])

    def test_desktop_code_sdk_human_turn_and_reply_keep_native_provenance(self):
        capture_device(self.packet('SessionStart'),self.device,self.home)
        self.typed('桌面消息',entrypoint='claude-desktop',promptSource='sdk')
        first=capture_device(self.packet('UserPromptSubmit',prompt='桌面消息'),self.device,self.home)
        self.assertEqual(first['status'],'accepted')
        self.assertEqual(first['origin']['native_entrypoint'],'claude-desktop')
        self.assertEqual(first['origin']['native_prompt_source'],'sdk')
        reply=capture_device(self.packet('Stop',last_assistant_message='桌面回复'),self.device,self.home)
        self.assertEqual(reply['session_id'],first['session_id'])
        _,events=self.call('GET','/v1/events?session_id='+first['session_id'],token=self.admin_token)
        self.assertEqual([e['payload']['text'] for e in events['events']],['','桌面消息','桌面回复'])
        self.assertEqual(events['events'][1]['actor_kind'],'human')

    def test_desktop_transport_alone_never_grants_human_attribution(self):
        from jasmine_core.capture.claude_session import _prompt_origin
        for metadata in [
            {'entrypoint':'sdk','promptSource':'sdk'},
            {'entrypoint':'cli','promptSource':'sdk'},
            {'entrypoint':'claude-desktop','promptSource':'typed'},
            {'entrypoint':'claude-desktop','promptSource':'sdk','origin':{'kind':'agent'}},
            {'entrypoint':'claude-desktop','promptSource':'sdk','turnOrigin':'agent'},
            {'entrypoint':'claude-desktop','promptSource':'sdk','isSidechain':True},
            {'entrypoint':'claude-desktop','promptSource':'sdk','sourceToolAssistantUUID':str(uuid.uuid4())},
            {'entrypoint':'claude-desktop','promptSource':'sdk','isMeta':True},
        ]:
            with self.subTest(metadata=metadata):
                self.transcript.write_text('');self.typed('ignored',**metadata)
                self.assertIsNone(_prompt_origin({'transcript_path':str(self.transcript),'turn':self.turn,'sid':self.sid,'cwd':str(self.native_root)}))

    def test_autowake_cannot_reuse_typed_turn_or_publish_reply(self):
        self.typed('你好');p=self.packet('UserPromptSubmit',prompt='你好')
        self.assertEqual(capture_device(p,self.device,self.home)['status'],'accepted')
        self.turn=str(uuid.uuid4())
        self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='你好'),self.device,self.home)['status'],'ignored')
        self.assertEqual(capture_device(self.packet('Stop',last_assistant_message='auto'),self.device,self.home)['status'],'ignored')
    def test_children_unverified_paths_nonhuman_metadata_and_changed_text_are_ignored(self):
        self.typed('你好',origin={'kind':'teammate'},turnOrigin='agent',promptSource='agent')
        self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='你好'),self.device,self.home)['status'],'ignored')
        self.typed('你好')
        for extra in [{'agent_id':'child'},{'agent_type':'Explore'},{'transcript_path':'/tmp/'+self.sid+'.jsonl'},{'prompt_id':'auto-task'}]:
            self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='你好',**extra),self.device,self.home)['status'],'ignored')
        self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='你好'),self.device,self.home)['status'],'accepted')
        self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='altered'),self.device,self.home)['status'],'ignored')
    def test_native_transcript_symlink_is_refused(self):
        self.typed('你好');real=self.transcript.with_name('real.jsonl');self.transcript.rename(real);self.transcript.symlink_to(real)
        self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='你好'),self.device,self.home)['status'],'ignored')
    def test_fast_stop_waits_for_matching_user_prompt_commit(self):
        self.typed('你好')
        self.assertEqual(capture_device(self.packet('Stop',last_assistant_message='你好！'),self.device,self.home)['status'],'pending')
        first=capture_device(self.packet('UserPromptSubmit',prompt='你好'),self.device,self.home)
        _,events=self.call('GET','/v1/events?session_id='+first['session_id'],token=self.admin_token)
        self.assertEqual([e['payload']['text'] for e in events['events']],['你好','你好！'])

    def queued(self,text,origin=None,**kwargs):
        """The attachment row Claude writes when a queued message joins the running turn."""
        item={'type':'queued_command','prompt':text,'source_uuid':str(uuid.uuid4()),'delivery_id':str(uuid.uuid4()),
              'commandMode':'prompt','origin':origin or {'kind':'human'},'timestamp':datetime.now(timezone.utc).isoformat(),'humanTurn':True}
        row={'type':'attachment','sessionId':self.sid,'cwd':str(self.native_root),'timestamp':item['timestamp'],'uuid':str(uuid.uuid4()),
             'isSidechain':False,'userType':'external','entrypoint':'claude-desktop','attachment':item}
        for key,value in kwargs.items():(item if key in item else row)[key]=value
        with self.transcript.open('a') as stream:stream.write(json.dumps(row)+'\n')
        return row

    def test_a_message_typed_while_claude_works_is_its_own_prompt(self):
        """Live 2026-10-29: queued messages reuse the running turn's prompt_id and
        were all refused as `native_turn_text_changed`."""
        capture_device(self.packet('SessionStart'),self.device,self.home)
        self.typed('先把第四段做完',entrypoint='claude-desktop',promptSource='sdk')
        first=capture_device(self.packet('UserPromptSubmit',prompt='先把第四段做完'),self.device,self.home)
        self.assertEqual(first['status'],'accepted',first)
        row=self.queued('跑完以后帮我关机')
        p=self.packet('UserPromptSubmit',prompt='跑完以后帮我关机')
        later=capture_device(p,self.device,self.home);replay=capture_device(p,self.device,self.home)
        self.assertEqual(later['status'],'accepted',later)
        self.assertEqual(later['session_id'],first['session_id']);self.assertNotEqual(later['event_id'],first['event_id'])
        self.assertEqual((replay['event_id'],replay['replayed']),(later['event_id'],True))
        self.assertEqual(later['origin']['check'],'native_queued_typed_human_metadata')
        self.assertEqual((later['origin']['native_prompt_id'],later['origin']['native_message_id']),(self.turn,row['uuid']))
        self.assertEqual(later['origin']['native_prompt_timestamp'],row['timestamp'])
        reply=capture_device(self.packet('Stop',last_assistant_message='好的'),self.device,self.home)
        self.assertEqual(reply['status'],'accepted',reply)
        _,events=self.call('GET','/v1/events?session_id='+first['session_id'],token=self.admin_token)
        prompts=[e for e in events['events'] if e['event_type']=='user.prompt']
        self.assertEqual([e['payload']['text'] for e in prompts],['先把第四段做完','跑完以后帮我关机'])
        self.assertEqual([e['actor_kind'] for e in prompts],['human','human'])
        self.assertEqual(events['events'][-1]['payload']['text'],'好的')

    def test_queued_input_without_typed_markers_is_never_human(self):
        self.typed('你好');self.assertEqual(capture_device(self.packet('UserPromptSubmit',prompt='你好'),self.device,self.home)['status'],'accepted')
        notice='<task-notification><task-id>b1</task-id></task-notification>'
        for index,extra in enumerate([
            {'origin':{'kind':'task-notification','producer':'session-task'},'commandMode':'task-notification','humanTurn':None},
            {'humanTurn':None},{'commandMode':'bash'},{'origin':{'kind':'agent'}},
            {'isSidechain':True},{'entrypoint':'sdk'},{'userType':'internal'},{'cwd':'/elsewhere'},
        ]):
            with self.subTest(extra=extra):
                text=f'{notice} {index}';self.queued(text,**extra)
                got=capture_device(self.packet('UserPromptSubmit',prompt=text),self.device,self.home)
                self.assertEqual((got['status'],got['reason']),('ignored','non_human_queued_input'))
        # Changed text with no queued row at all keeps the old refusal.
        got=capture_device(self.packet('UserPromptSubmit',prompt='altered'),self.device,self.home)
        self.assertEqual((got['status'],got['reason']),('ignored','native_turn_text_changed'))
        # A row of another session, or with other text, is not a match.
        self.queued('同一句话',sessionId=str(uuid.uuid4()));self.queued('另一句话')
        got=capture_device(self.packet('UserPromptSubmit',prompt='同一句话'),self.device,self.home)
        self.assertEqual(got['reason'],'native_turn_text_changed')

    def test_subdirectory_is_the_same_workspace_and_a_sibling_is_not(self):
        sub=self.native_root/'sub'/'deeper';sub.mkdir(parents=True)
        sibling=self.native_root.parent/(self.native_root.name+'-sibling');sibling.mkdir()
        self.assertEqual(capture_device(self.packet('SessionStart',source='startup'),self.device,self.home)['status'],'pending')
        root_text='在项目根目录发的一条';self.typed(root_text)
        root=capture_device(self.packet('UserPromptSubmit',prompt=root_text),self.device,self.home)
        self.assertEqual(root['status'],'accepted',root)
        # `cd` into a subdirectory: same session and project, not a new one.
        self.turn=str(uuid.uuid4());sub_text='在子目录里发的一条';self.typed(sub_text,cwd=str(sub))
        inside=capture_device(self.packet('UserPromptSubmit',prompt=sub_text,cwd=str(sub)),self.device,self.home)
        self.assertEqual(inside['status'],'accepted',inside)
        self.assertEqual(inside['session_id'],root['session_id'])
        # A directory beside the starting one is still a different workspace.
        outside=capture_device(self.packet('UserPromptSubmit',prompt='other',cwd=str(sibling)),self.device,self.home)
        self.assertEqual((outside['status'],outside['reason']),('ignored','native_workspace_changed'))

    def test_installer_preserves_settings_and_foreign_handlers_enable_disable(self):
        target=self.home/'settings.json';foreign={'type':'command','command':'echo foreign'}
        initial={'model':'keep','hooks':{'Stop':[{'hooks':[foreign]}]}};target.write_text(json.dumps(initial))
        launcher=self.native_root/'claude-capture.py';launcher.write_text('pass');receipts=self.native_root/'claude-receipts.jsonl'
        options={'python':'/usr/bin/python3','launcher':launcher,'confirmed':True}
        self.assertEqual(install(self.home,self.binding_path,receipts,**options)['status'],'installed')
        installed=json.loads(target.read_text());self.assertEqual(installed['model'],'keep');self.assertEqual(installed['hooks']['Stop'][0]['hooks'],[foreign])
        self.assertEqual(install(self.home,self.binding_path,receipts,**options)['status'],'unchanged')
        self.assertEqual(install(self.home,self.binding_path,receipts,disable=True,**options)['status'],'disabled')
        self.assertEqual(json.loads(target.read_text()),initial)
        initial['disableAllHooks']=True;target.write_text(json.dumps(initial))
        with self.assertRaises(InstallError):install(self.home,self.binding_path,receipts,**options)
        target.unlink();target.symlink_to(launcher)
        with self.assertRaises(InstallError):install(self.home,self.binding_path,receipts,**options)

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(ClaudeCapture(name) for name in ClaudeCapture.__dict__ if name.startswith('test_'))
