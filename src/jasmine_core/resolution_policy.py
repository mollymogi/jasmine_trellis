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

Task binding (TASK_CREATE_OR_ATTACH, REQUIRED_CAPABILITY) keeps the original
review policy, because the p2/p3 hooks wait on those reviews.
"""
import re

POLICY_VERSION = 'jasmine.resolution.v1'

DURABLE_KINDS = ('RULE', 'DECISION', 'CORRECTION')
_DURABLE_RE = re.compile(
    r'以后|今后|往后|从现在起|从今以后|永远|一律|始终|每次都|一直都|记住|'
    r'\balways\b|\bnever\b|from now on|\bremember\b|going forward', re.I)


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
                and _DURABLE_RE.search(quote))


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


def _durable(source, candidate):
    """Tiers 1-3 for Rule/Decision/Correction candidates."""
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
    return candidate, 'NO_ACTION', False, 'observed_memory_only'


def compile_plan(source, candidates):
    result = []
    for index, candidate in enumerate(candidates):
        kind = candidate['kind']
        if kind == 'NO_STRUCTURE':
            action, pending, reason = 'NO_ACTION', False, 'no_structure'
        elif kind in DURABLE_KINDS:
            candidate, action, pending, reason = _durable(source, candidate)
        else:
            action, pending, reason = _task_binding(source, candidate)
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
