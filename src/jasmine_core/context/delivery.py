"""Read-only projection of operator-owned historical native-input receipts.

The trusted local collector is outside the model/tool boundary. A receipt never
asserts current freshness, future model behavior or independent acceptance.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import re

from ..adapter.config import private_json
from ..adapter.native_proof import typed_input
from ..canonical import sha256_hex
from ..errors import CoreError
from ..clock import parse_rfc3339

ENV = 'JASMINE_CONTEXT_DELIVERY_ROOT'
SCHEMA = 'jasmine.context-native-input.v1'
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def completion_time(value):
    if type(value) is int and 0 <= value <= 253402300799:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(value, str):
        return parse_rfc3339(value).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    raise ValueError('unsupported native completion timestamp')


def project(pack, source, *, root=None):
    result = {'context_pack_id': pack['context_pack_id'], 'status': 'UNAVAILABLE',
              'historical': True, 'current_use_proved': False,
              'trust_boundary': 'operator_owned_native_collector', 'record': None}
    selected = root if root is not None else os.environ.get(ENV)
    if not selected:
        return result
    path = Path(selected) / (pack['context_pack_id'] + '.json')
    # Missing receipt is ordinary, but an invalid/symlinked configured root must
    # not be silently presented as an empty receipt collection.
    try:
        directory = Path(selected)
        if not directory.is_absolute() or directory != directory.resolve(strict=True):
            raise ValueError('invalid root')
        info = directory.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('unsafe root')
        if not path.exists() and not path.is_symlink():
            return {**result, 'status': 'NO_RECORD'}
        receipt, digest = private_json(path, cap=4 * 1024 * 1024)
        native = source['payload'].get('native') or {}
        sid, turn = receipt.get('native_thread_id'), receipt.get('native_turn_id')
        records = receipt.get('records')
        if (receipt.get('schema') != SCHEMA
                or receipt.get('context_pack_id') != pack['context_pack_id']
                or receipt.get('source_event_id') != pack['source_event_id']
                or receipt.get('task_id') != pack['task_id']
                or receipt.get('host_id') != pack['host_id']
                or receipt.get('rendered_sha256') != pack['rendered_sha256']
                or sha256_hex(pack['rendered_content']) != pack['rendered_sha256']
                or not isinstance(sid, str) or not UUID.fullmatch(sid)
                or not isinstance(turn, str) or not UUID.fullmatch(turn)
                or native.get('session_id') != sid or native.get('turn_id') != turn
                or not isinstance(records, list) or not 1 <= len(records) <= 16
                or not all(isinstance(row, dict) and isinstance(row.get('payload'), dict) for row in records)
                or not isinstance(receipt.get('observed_at'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', receipt.get('native_rollout_sha256', ''))):
            raise ValueError('receipt binding mismatch')
        parse_rfc3339(receipt['observed_at'])
        sessions = [row['payload'].get('id') for row in records if row.get('type') == 'session_meta']
        completed = [i for i, row in enumerate(records) if row.get('type') == 'event_msg' and row['payload'].get('type') == 'task_complete' and row['payload'].get('turn_id') == turn]
        if sessions != [sid] or len(completed) != 1 or completed[0] != len(records)-1 or not typed_input(records[:completed[0]], turn, pack['rendered_content']):
            raise ValueError('no exact native input')
        if receipt['observed_at'] != completion_time(records[-1]['payload'].get('completed_at')):
            raise ValueError('native completion timestamp mismatch')
        return {**result, 'status': 'MATCHED_NATIVE_INPUT', 'record': {
            key: receipt[key] for key in ('native_thread_id', 'native_turn_id',
                'source_event_id', 'rendered_sha256', 'observed_at', 'native_rollout_sha256')
        } | {'receipt_sha256': digest}}
    except (OSError, ValueError, TypeError, AttributeError, KeyError, CoreError):
        return {**result, 'status': 'INVALID_RECORD'}
