"""Opt-in isolated Context emission probe; emission alone is not delivery proof."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from ..api.client import CoreClient
from ..hosthook.binding import load_binding, read_key
from ..hosthook.dialects import Outcome
from ..hosthook.dialects import codex
from ..hosthook.runtime import build_request
from .codex_session import append_receipt, session_origin


def emit(native, binding, *, client_factory=CoreClient, now=None):
    if native.get('hook_event_name') not in ('SessionStart', 'UserPromptSubmit'):
        raise ValueError('unsupported probe event')
    if session_origin(native) is None or native.get('subagent_id') or native.get('subagent_type'):
        return '', {'status': 'ignored', 'reason': 'unverified_session_origin'}
    if not binding.task_id or not binding.project_id or not binding.inject_context:
        raise ValueError('explicit task/project injection binding required')
    tr = codex.translate_in(native, now or datetime.now(timezone.utc))
    if tr is None:
        raise ValueError('invalid native event')
    token = read_key(binding.key_file_for(tr.event))
    client = client_factory(binding.core_url, token, timeout=20)
    request = build_request(codex, tr, binding, now or datetime.now(timezone.utc))
    response = client.post('/v1/host-hooks/events', request)
    inject = response.get('inject_context')
    if response.get('degraded') or not isinstance(inject, dict):
        raise ValueError('Context unavailable')
    pack = client.get('/v1/context/' + inject['context_pack_id'])['context']
    content = inject.get('text')
    if (not isinstance(content, str) or content != pack['rendered_content']
            or hashlib.sha256(content.encode()).hexdigest() != pack['rendered_sha256']
            or pack['task_id'] != binding.task_id or pack['project_id'] != binding.project_id
            or pack['host_id'] != binding.host_id
            or pack['source_event_id'] not in response['recorded']['event_ids']):
        raise ValueError('Context bytes or binding mismatch')
    checking = {k: pack[k] for k in ('host_id', 'source_event_id', 'session_id', 'current_step_id') if pack.get(k) is not None}
    fresh = client.post('/v1/context/' + pack['context_pack_id'] + '/check-current', checking)
    if (fresh.get('current') is not True or fresh.get('rendered_sha256') != pack['rendered_sha256']
            or fresh.get('context_pack_id') != pack['context_pack_id']):
        raise ValueError('Context stale before emission')
    text, code = codex.translate_out(tr, Outcome('ok', response=response))
    value = json.loads(text)
    if code or value.get('hookSpecificOutput', {}).get('additionalContext') != content:
        raise ValueError('native output is not exact Core content')
    return text, {'status': 'EMITTED_NOT_DELIVERY_PROOF', 'context_pack_id': pack['context_pack_id'],
        'source_event_id': pack['source_event_id'], 'task_id': binding.task_id, 'host_id': binding.host_id,
        'native_session_id': native['session_id'], 'native_turn_id': native.get('turn_id'),
        'native_hook_event': native['hook_event_name'], 'rendered_sha256': pack['rendered_sha256'],
        'output_sha256': hashlib.sha256(text.encode()).hexdigest(), 'checked_at': fresh['checked_at']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', type=Path, required=True)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--receipts', type=Path, required=True)
    args = parser.parse_args()
    try:
        native = json.load(sys.stdin)
        if not isinstance(native, dict):
            raise ValueError('native object required')
        cwd = native.get('cwd')
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or Path(cwd).resolve() != args.project_root.resolve(strict=True):
            append_receipt(args.receipts, {'status': 'ignored', 'reason': 'outside_project'})
            return 0
        text, receipt = emit(native, load_binding(args.binding))
        append_receipt(args.receipts, receipt)
        # Private emission receipt precedes stdout. Only a later collector may
        # claim actual input matching after the native turn has completed.
        sys.stdout.write(text)
        return 0
    except Exception as exc:
        append_receipt(args.receipts, {'status': 'failed', 'error_type': type(exc).__name__})
        print('Isolated Context probe failed; inspect private receipts.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
