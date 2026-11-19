"""Ordered, bounded semantic policy. Model impact is never rewritten."""
POLICY_VERSION = 'jasmine.resolution.v1'


def compile_plan(source, candidates):
    result = []
    for index, candidate in enumerate(candidates):
        kind = candidate['kind']
        action = 'NO_ACTION'
        reason = 'no_structure'
        pending = False
        if kind != 'NO_STRUCTURE':
            if candidate['certainty'] != 'EXPLICIT':
                reason = 'not_explicit'
            elif source['source_type'] != 'USER_EXPLICIT':
                reason = 'source_not_user_explicit'
            elif kind == 'TASK_CREATE_OR_ATTACH':
                action = 'ATTACH_TASK' if source['task_id'] else 'CREATE_TASK'
                reason = 'explicit_task_binding'
            elif kind in ('RULE', 'DECISION', 'ACCEPTANCE', 'REQUIRED_CAPABILITY') and candidate['scope']['kind'] in ('GLOBAL', 'PROJECT', 'TASK'):
                action = 'CREATE_RULE'
                reason = 'auto_approved_user_explicit'
            else:
                reason = 'observed_memory_only'
        result.append({'candidate_index': index, 'candidate': candidate, 'action': action,
                       'pending': pending, 'reason': reason})
    # A task-null capability alone cannot select or invent a Task.
    if not source['task_id'] and not any(r['action'] == 'CREATE_TASK' for r in result):
        for item in result:
            if item['action'] == 'CREATE_RULE' and item['candidate']['scope']['kind'] == 'TASK':
                if item['candidate']['kind'] == 'REQUIRED_CAPABILITY' or not source.get('project_id'):
                    item.update(action='NO_ACTION', pending=False, reason='task_binding_required')
    disposition = ('PENDING_REVIEW' if any(r['pending'] for r in result) else
                   'APPLIED' if any(r['action'] != 'NO_ACTION' for r in result) else 'NO_ACTION')
    return {'policy_version': POLICY_VERSION, 'disposition': disposition, 'items': result}
