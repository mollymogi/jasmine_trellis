#!/usr/bin/env python3
"""Open a real Codex session with explicit isolated conversation hooks.

Pass operator-owned binding/root/receipt paths. No global config, credentials,
persisted trust or daily service is changed. Review hooks in Codex before use.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import selectors
import subprocess
import sys
import time


def inherited_hooks(codex, cwd, *, timeout=20):
    """Read the inventory without stranding prefetched JSONL responses."""
    proc = subprocess.Popen([codex, 'app-server'], cwd=cwd, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    pending = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)

    def send(value):
        proc.stdin.write((json.dumps(value) + '\n').encode())
        proc.stdin.flush()

    def receive(identifier):
        deadline = time.monotonic() + timeout
        while True:
            # Drain complete frames before waiting on the OS descriptor. A
            # notification and response may arrive together or in split reads.
            while b'\n' in pending:
                line, _, remainder = pending.partition(b'\n')
                pending[:] = remainder
                result = json.loads(line)
                if result.get('id') == identifier:
                    if 'error' in result:
                        raise RuntimeError('Hook inventory was refused')
                    return result['result']
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(timeout=remaining):
                raise RuntimeError('Hook inventory timed out; refusing an unchecked launch')
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('Hook inventory closed before a complete response')
            pending.extend(chunk)

    try:
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'jasmine-isolated-hook-inventory', 'version': '1'}, 'capabilities': {'experimentalApi': True}}})
        receive(1)
        send({'method': 'initialized'})
        send({'id': 2, 'method': 'hooks/list', 'params': {'cwds': [str(cwd)]}})
        result = receive(2)
        if any(item.get('errors') for item in result['data']):
            raise RuntimeError('Hook inventory contains errors')
        return [hook for item in result['data'] for hook in item['hooks']]
    finally:
        selector.close()
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdin.close()
        proc.stdout.close()

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--binding',required=True,type=Path)
p.add_argument('--project-root',required=True,type=Path)
p.add_argument('--receipts',required=True,type=Path)
p.add_argument('--launch-receipt',type=Path)
p.add_argument('--context-probe',action='store_true',help='Opt-in task-bound Context probe for SessionStart/UserPromptSubmit; CLI invocation only')
p.add_argument('codex_args',nargs=argparse.REMAINDER)
a=p.parse_args()
os.umask(0o077)
repo=Path(__file__).resolve().parents[2]
command=shlex.join(['env','PYTHONPATH='+str(repo/'src'),sys.executable,'-B','-m','jasmine_core.capture.codex_session','--binding',str(a.binding.resolve(strict=True)),'--project-root',str(a.project_root.resolve(strict=True)),'--receipts',str(a.receipts.absolute())])
codex=shutil.which('codex')
if not codex:raise SystemExit('Codex executable is unavailable')
args=[codex,'-c','features.hooks=true','-c','projects.'+json.dumps(str(a.project_root.resolve()))+'.trust_level="trusted"']
# Worktrees inherit hook definitions from their common repository. Disable all
# inherited non-managed hooks for this invocation only before adding our four.
# Never run an inherited production capture hook against an isolation test.
inventory=inherited_hooks(codex,a.project_root.resolve(strict=True))
for hook in inventory:
    if hook.get('isManaged'):raise SystemExit('Managed hook is present; an isolated launch requires operator review')
    args+=['-c','hooks.state.'+json.dumps(hook['key'])+'.enabled=false']
for event in ('SessionStart','UserPromptSubmit','Stop','SessionEnd'):
    selected=command.replace('jasmine_core.capture.codex_session','jasmine_core.capture.context_probe') if a.context_probe and event in ('SessionStart','UserPromptSubmit') else command
    context_limit=',additionalContextLimit=0' if a.context_probe and event in ('SessionStart','UserPromptSubmit') else ''
    value='[{hooks=[{type="command",command='+json.dumps(selected,ensure_ascii=False)+context_limit+',timeout='+str(3 if event=='SessionEnd' else 60 if a.context_probe else 10)+'}]}]'
    args+=['-c','hooks.'+event+'='+value]
if a.launch_receipt:
    a.launch_receipt.write_text(json.dumps({'project_root':str(a.project_root.resolve()),'disabled_inherited_hooks':[h['key'] for h in inventory],'scope':'session-only; persisted configuration and trust unchanged','context_probe_enabled':a.context_probe,'context_probe_sha256':hashlib.sha256((repo/'src/jasmine_core/capture/context_probe.py').read_bytes()).hexdigest() if a.context_probe else None,'capture_sha256':hashlib.sha256((repo/'src/jasmine_core/capture/codex_session.py').read_bytes()).hexdigest(),'launcher_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n')
extra=a.codex_args[1:] if a.codex_args[:1]==['--'] else a.codex_args
os.chdir(a.project_root)
# Exec has its own configuration argument parser; pass overrides to that parser.
argv=[codex,extra[0],*args[1:],*extra[1:]] if extra[:1] in (['exec'],['review']) else args+extra
os.execv(codex,argv)
