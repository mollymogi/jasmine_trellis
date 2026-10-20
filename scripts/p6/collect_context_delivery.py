#!/usr/bin/env python3
"""Collect an exact same-turn native input match; never use assistant claims."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from jasmine_core.adapter.native_proof import read_thread_rollout, typed_input
from jasmine_core.api.client import CoreClient
from jasmine_core.capture.p2_runtime import Deadline
from jasmine_core.context.delivery import SCHEMA, project, completion_time
from jasmine_core.hosthook.binding import load_binding, read_key


def collect(*, emissions, native_cli, binding, out_root):
    cli = [json.loads(line) for line in native_cli.read_text().splitlines()]
    threads = [row['thread_id'] for row in cli if row.get('type') == 'thread.started']
    if (len(threads) != 1 or sum(row.get('type') == 'turn.completed' for row in cli) != 1
            or any(row.get('type') in ('turn.failed', 'error') for row in cli)):
        raise ValueError('one completed real native turn is required')
    sid = threads[0]
    rows = [json.loads(line) for line in emissions.read_text().splitlines()]
    candidates = [r for r in rows if r.get('status') == 'EMITTED_NOT_DELIVERY_PROOF'
                  and r.get('native_session_id') == sid and r.get('native_hook_event') == 'UserPromptSubmit']
    if len(candidates) != 1:
        raise ValueError('one prompt Context emission required')
    emission = candidates[0]
    turn = emission['native_turn_id']
    records, native_path = read_thread_rollout(sid, Deadline(30))
    raw = native_path.read_bytes()
    if [json.loads(line) for line in raw.splitlines()] != records:
        raise ValueError('native transcript changed during collection')
    bound = load_binding(binding)
    client = CoreClient(bound.core_url, read_key(bound.api_key_file))
    pack = client.get('/v1/context/' + emission['context_pack_id'])['context']
    source = client.get('/v1/events/' + pack['source_event_id'])['event']
    if (emission['rendered_sha256'] != pack['rendered_sha256']
            or emission['source_event_id'] != pack['source_event_id']
            or emission['task_id'] != pack['task_id'] or emission['host_id'] != pack['host_id']
            or pack['task_id'] != bound.task_id or pack['host_id'] != bound.host_id
            or not typed_input(records, turn, pack['rendered_content'])):
        raise ValueError('native developer input does not exactly match the bound Core Context')
    selected = []
    current = None
    for row in records:
        payload = row.get('payload', {})
        if row.get('type') == 'session_meta':
            selected.append({'type': 'session_meta', 'payload': {k: payload[k] for k in ('id', 'source')}})
        elif row.get('type') == 'turn_context':
            current = payload.get('turn_id')
            if current == turn:
                selected.append({'type': 'turn_context', 'payload': {'turn_id': turn}})
        elif (row.get('type') == 'response_item' and current == turn
              and payload.get('type') == 'message' and payload.get('role') == 'developer'):
            content = [item for item in payload.get('content', []) if item.get('type') == 'input_text'
                       and item.get('text') == pack['rendered_content']]
            if content:
                selected.append({'type': 'response_item', 'turn_id': turn,
                                 'payload': {'type': 'message', 'role': 'developer', 'content': content}})
        elif (row.get('type') == 'event_msg' and payload.get('type') == 'task_complete'
              and payload.get('turn_id') == turn):
            selected.append({'type': 'event_msg', 'payload': {k: payload[k] for k in ('type', 'turn_id', 'completed_at')}})
    completed = [row for row in selected if row.get('type') == 'event_msg']
    if len(completed) != 1 or selected[-1] != completed[0]:
        raise ValueError('current native turn completion missing')
    value = {'schema': SCHEMA, 'context_pack_id': pack['context_pack_id'],
        'source_event_id': pack['source_event_id'], 'task_id': pack['task_id'], 'host_id': pack['host_id'],
        'native_thread_id': sid, 'native_turn_id': turn, 'rendered_sha256': pack['rendered_sha256'],
        'observed_at': completion_time(completed[0]['payload']['completed_at']),
        'native_rollout_sha256': hashlib.sha256(raw).hexdigest(), 'records': selected}
    out_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = out_root / (pack['context_pack_id'] + '.json')
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    # Validate in an unpublished private directory, then atomically publish
    # without overwriting any prior receipt, including an invalid old attempt.
    with tempfile.TemporaryDirectory(dir=out_root, prefix='.pending-') as pending:
        staged = Path(pending) / target.name
        fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if project(pack, source, root=pending)['status'] != 'MATCHED_NATIVE_INPUT':
            raise ValueError('candidate receipt did not pass read-side validation')
        os.link(staged, target, follow_symlinks=False)
    result = project(pack, source, root=str(out_root))
    if result['status'] != 'MATCHED_NATIVE_INPUT':
        raise ValueError('stored receipt did not pass read-side validation')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('emissions', 'native-cli', 'binding', 'out-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = collect(emissions=args.emissions, native_cli=args.native_cli,
                     binding=args.binding, out_root=args.out_root)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
