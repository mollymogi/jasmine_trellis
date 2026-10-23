"""Exercise real subprocess framing and cleanup, independent of Codex trust."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / 'scripts/deployment/codex-isolated-session.py'
namespace = {'__file__': str(LAUNCHER)}
exec(compile(LAUNCHER.read_text().split('p=argparse.ArgumentParser')[0], str(LAUNCHER), 'exec'), namespace)
inherited_hooks = namespace['inherited_hooks']


class InventoryFraming(unittest.TestCase):
    def probe(self, body, timeout=2):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            script = base / 'codex'
            # A venv path may contain spaces, which a kernel shebang cannot quote.
            # This stdlib-only child can use the resolved interpreter binary.
            script.write_text('#!' + str(Path(sys.executable).resolve()) + '\nimport os,sys,json,time\n' + body)
            script.chmod(0o700)
            started = time.monotonic()
            try:
                return inherited_hooks(str(script), base, timeout=timeout), time.monotonic() - started
            finally:
                if (base / 'pid').exists():
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int((base / 'pid').read_text()), 0)

    def test_notification_and_reply_in_one_write(self):
        result, elapsed = self.probe('''open('pid','w').write(str(os.getpid()))
for line in sys.stdin:
 d=json.loads(line)
 if d.get('method')=='initialize':
  os.write(1,(json.dumps({'method':'configWarning','params':{'summary':'startup warning'}})+'\\n'+json.dumps({'id':1,'result':{}})+'\\n').encode())
 elif d.get('method')=='hooks/list':
  os.write(1,(json.dumps({'id':2,'result':{'data':[{'hooks':[{'key':'isolated'}]}]}})+'\\n').encode())
''')
        self.assertEqual(result, [{'key': 'isolated'}])
        self.assertLess(elapsed, 1.5)

    def test_frames_split_across_reads(self):
        result, _ = self.probe('''open('pid','w').write(str(os.getpid()))
for line in sys.stdin:
 d=json.loads(line)
 if 'id' not in d:continue
 r={'id':d['id'],'result':{} if d['id']==1 else {'data':[{'hooks':[]}]}}
 b=(json.dumps(r)+'\\n').encode()
 os.write(1,b[:7]);time.sleep(.02);os.write(1,b[7:])
''')
        self.assertEqual(result, [])

    def test_partial_eof_fails_and_reaps_process(self):
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            self.probe("open('pid','w').write(str(os.getpid()))\nos.write(1,b'{');sys.exit(0)\n")

    def test_silent_process_times_out_and_is_reaped(self):
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.probe("open('pid','w').write(str(os.getpid()))\ntime.sleep(30)\n", timeout=.15)

    def test_inventory_errors_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'contains errors'):
            self.probe('''for line in sys.stdin:
 d=json.loads(line)
 if 'id' in d:
  print(json.dumps({'id':d['id'],'result':{} if d['id']==1 else {'data':[{'hooks':[], 'errors':['invalid hook']}]}}),flush=True)
''')
