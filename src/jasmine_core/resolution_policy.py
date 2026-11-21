"""Ordered, bounded semantic policy. Model impact is never rewritten.

Three tiers decide what an interpreted user message may do to Authority:

1. Every Rule/Decision/Correction the model extracts reaches Memory as OBSERVED history anyway
   (memory/observed.py reads the candidates whatever the disposition), so a
   candidate is applied as NO_ACTION and never queued for review just because
   it exists. The review queue used to fill with one-off corrections, quoted
   text and group-chat persona headers that nobody would ever approve.
2. A durable instruction the user typed themselves ("以后都…", "记住…",
   "from now on…") at the project they are working in becomes an active
   project Rule straight away. Their own words are the confirmation.
3. Two things still wait for one explicit approval: a user statement aimed at
   every project (a global Rule changes every agent everywhere), and a user
   correction to a Task that already exists (it may supersede that Task's
   Rules, which only a reviewer can choose).

4. A plain project correction or decision the user already made in another
   session of the same workspace is proposed once for approval
   ("repeated_across_sessions"): saying the same thing twice is the signal that
   it is a standing preference, not a one-off. At most PROMOTION_WEEKLY_CAP such
   proposals a week, and never one already covered by an active Rule or a
   proposal from the last PROMOTION_DEDUP_DAYS days.

Task binding (TASK_CREATE_OR_ATTACH, REQUIRED_CAPABILITY) keeps the original
review policy, because the p2/p3 hooks wait on those reviews.
"""
import re

POLICY_VERSION = 'jasmine.resolution.v1'

DURABLE_KINDS = ('RULE', 'DECISION', 'CORRECTION')
TASK_BINDING_KINDS = ('TASK_CREATE_OR_ATTACH', 'REQUIRED_CAPABILITY')
_DURABLE_RE = re.compile(
    r'以后|今后|往后|从现在起|从今以后|永远|一律|始终|每次都|一直都|记住|'
    r'\balways\b|\bnever\b|from now on|\bremember\b|going forward', re.I)
#: "以后是不是还要改？" / "为什么记住的全是乱的" / "Why does it always fail?" ask, not instruct.
_QUESTION_RE = re.compile(
    r'[?？]|吗|是不是|是否|要不要|能不能|可不可以|会不会|有没有|为什么|为啥|怎么|如何|什么|哪|'
    r'^\s*(?:why|how|what|when|where|which|who|do|does|did|is|are|can|could|should|would|will)\b', re.I)
#: Daily capture of native CLI sessions: no host hook waits on its reviews.
NATIVE_CAPTURE_SUFFIX = '-native-conversation-v1'
PROMOTION_SIMILARITY = 0.6
PROMOTION_WEEKLY_CAP = 3
PROMOTION_DEDUP_DAYS = 30
PROMOTION_REASON = 'repeated_across_sessions'
_PRIOR_TYPES = ('OBSERVED_CORRECTION', 'OBSERVED_DECISION', 'OBSERVED_CONVENTION')


def _user_span(source, candidate):
    """The candidate's quote, if it lies in what the user typed themselves."""
    from .memory.observed import span_is_user_text
    span = candidate.get('source_span') or {}
    text = source.get('text') or ''
    start, end, quote = span.get('start'), span.get('end'), span.get('quote')
    if not isinstance(start, int) or not isinstance(end, int) or not quote:
        return None
    if not (0 <= start < end <= len(text)) or text[start:end] != quote:
        return None
    return quote if span_is_user_text(text, start, end) else None


def _user_statement(source, candidate):
    """The quote of an explicit Rule/Decision/Correction the user typed, or None."""
    if source['source_type'] != 'USER_EXPLICIT' or candidate['certainty'] != 'EXPLICIT':
        return None
    if candidate['kind'] not in DURABLE_KINDS:
        return None
    return _user_span(source, candidate)


def durable_instruction(source, candidate):
    """Tier 2: a lasting project instruction in the user's own words."""
    quote = _user_statement(source, candidate)
    return bool(quote and candidate['impact'] != 'HIGH' and candidate['scope']['kind'] == 'PROJECT'
                and _DURABLE_RE.search(quote) and not _QUESTION_RE.search(quote))


def _task_binding(source, candidate):
    """Task binding keeps the original policy: the p2/p3 hooks wait on it."""
    if candidate['certainty'] != 'EXPLICIT':
        return 'NO_ACTION', True, 'not_explicit'
    if source['source_type'] != 'USER_EXPLICIT':
        return 'NO_ACTION', True, 'source_not_user_explicit'
    if candidate['scope']['kind'] != 'TASK' or candidate['impact'] == 'HIGH':
        return 'NO_ACTION', True, 'scope_or_impact_requires_review'
    if candidate['kind'] == 'TASK_CREATE_OR_ATTACH':
        return ('ATTACH_TASK' if source['task_id'] else 'CREATE_TASK'), False, 'explicit_task_binding'
    return 'CREATE_RULE', False, 'task_additive_normal_context'


def _durable(source, candidate, repeated=False):
    """Tiers 1-4; anything that is not a durable kind (e.g. ACCEPTANCE) stays observed."""
    scope = candidate['scope']['kind']
    if durable_instruction(source, candidate):
        project = candidate['scope'].get('project_id') or source['project_id']
        if source['project_id'] and project == source['project_id'] and not source['task_id']:
            candidate = {**candidate, 'scope': {**candidate['scope'], 'project_id': project}}
            return candidate, 'CREATE_RULE', False, 'user_durable_instruction'
        return candidate, 'NO_ACTION', True, 'scope_or_impact_requires_review'
    if _user_statement(source, candidate):
        if scope == 'GLOBAL':
            return candidate, 'NO_ACTION', True, 'global_scope_requires_review'
        if scope == 'TASK' and source['task_id']:
            return candidate, 'NO_ACTION', True, 'task_correction_requires_review'
        if repeated:
            return candidate, 'NO_ACTION', True, PROMOTION_REASON
    return candidate, 'NO_ACTION', False, 'observed_memory_only'


def source_system(conn, source):
    row = conn.execute('SELECT source_system FROM events WHERE event_id=?', (source['event_id'],)).fetchone()
    return row[0] if row else None


def _ago(days):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')


def repeated_candidates(conn, source, candidates):
    """Indexes of candidates the user already said in another session of this workspace (tier 4)."""
    if source['source_type'] != 'USER_EXPLICIT' or not source['project_id'] or source['task_id']:
        return frozenset()
    from .memory.hint import similarity
    from .memory.scope import workspace_group
    wanted = {}
    for index, candidate in enumerate(candidates):
        if (candidate['kind'] in ('DECISION', 'CORRECTION') and candidate['scope']['kind'] == 'PROJECT'
                and _user_statement(source, candidate) and not durable_instruction(source, candidate)
                and not _QUESTION_RE.search(candidate.get('content') or '')):
            wanted[index] = candidate['content']
    if not wanted:
        return frozenset()
    proposed = []
    for (plan_json,) in conn.execute('SELECT plan_json FROM resolutions WHERE created_at>=?', (_ago(PROMOTION_DEDUP_DAYS),)):
        if PROMOTION_REASON not in plan_json:
            continue
        import json
        for item in json.loads(plan_json).get('items', []):
            if item.get('reason') == PROMOTION_REASON:
                proposed.append(item['candidate'].get('content') or '')
    # One plan per source; counting recent proposals, not reviews, keeps replays idempotent.
    recent = sum(1 for (plan_json,) in conn.execute(
        'SELECT plan_json FROM resolutions WHERE created_at>=? AND source_event_id!=?',
        (_ago(7), source['event_id'])) if PROMOTION_REASON in plan_json)
    if recent >= PROMOTION_WEEKLY_CAP:
        return frozenset()
    projects = (source['project_id'], *workspace_group(conn, source['project_id']))
    marks = ','.join('?' * len(projects))
    rows = conn.execute(
        f"SELECT m.sanitized_text, m.admission, e.session_id FROM memory_items m"
        f" LEFT JOIN events e ON e.event_id = m.source_event_id"
        f" WHERE m.status='ACTIVE' AND m.source_event_id IS NOT ? AND"
        f" (m.scope_kind='global' OR m.project_id IN ({marks}))"
        f" AND (m.admission='AUTHORITY' OR m.memory_type IN ({','.join('?' * len(_PRIOR_TYPES))}))",
        (source['event_id'], *projects, *_PRIOR_TYPES)).fetchall()
    found = []
    for index, text in wanted.items():
        if any(similarity(text, p) >= PROMOTION_SIMILARITY for p in proposed):
            continue
        if any(r[1] == 'AUTHORITY' and similarity(text, r[0] or '') >= PROMOTION_SIMILARITY for r in rows):
            continue
        if any(r[1] != 'AUTHORITY' and r[2] and r[2] != source['session_id']
               and similarity(text, r[0] or '') >= PROMOTION_SIMILARITY for r in rows):
            found.append(index)
    return frozenset(found[:PROMOTION_WEEKLY_CAP - recent])


def compile_plan(source, candidates, *, source_system=None, repeated=frozenset()):
    result = []
    for index, candidate in enumerate(candidates):
        kind = candidate['kind']
        if kind == 'NO_STRUCTURE':
            action, pending, reason = 'NO_ACTION', False, 'no_structure'
        elif kind in TASK_BINDING_KINDS:
            action, pending, reason = _task_binding(source, candidate)
            if pending and str(source_system or '').endswith(NATIVE_CAPTURE_SUFFIX):
                # Nobody waits on these; queuing them only grew an unread backlog.
                action, pending, reason = 'NO_ACTION', False, 'observed_memory_only'
        else:
            candidate, action, pending, reason = _durable(source, candidate, index in repeated)
        result.append({'candidate_index': index, 'candidate': candidate, 'action': action,
                       'pending': pending, 'reason': reason})
    # A task-null capability alone cannot select or invent a Task.
    if not source['task_id'] and not any(r['action'] == 'CREATE_TASK' for r in result):
        for item in result:
            if item['action'] == 'CREATE_RULE' and item['candidate']['kind'] == 'REQUIRED_CAPABILITY':
                item.update(pending=True, reason='task_binding_required')
    disposition = ('PENDING_REVIEW' if any(r['pending'] for r in result) else
                   'APPLIED' if any(r['action'] != 'NO_ACTION' for r in result) else 'NO_ACTION')
    return {'policy_version': POLICY_VERSION, 'disposition': disposition, 'items': result}
