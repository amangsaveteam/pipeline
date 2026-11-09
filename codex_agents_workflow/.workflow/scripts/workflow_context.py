"""Deterministic, read-only task handoff assembled from repository evidence."""
from workflow_core import (CONTRACT_FILES, REPORTS, STAGES, STAGE_ROLE, WorkflowError,
                           changed_paths, load_manifest, read_json, snapshot, verify_contract, require)


def attempt(fn):
    try:
        return {'available': True, 'value': fn()}
    except (WorkflowError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        return {'available': False, 'error': str(exc)}


def build_context(root, task, manifest, store, diagnostic):
    from workflow_service import evidence, acceptance_satisfied
    role = STAGE_ROLE.get(manifest['stage'])
    reports = {name: attempt(lambda name=name: read_json(task / name)) for name in REPORTS}
    verification = attempt(lambda: read_json(task / 'verification.json'))
    verified = attempt(lambda: evidence(root, task, manifest, final=manifest['stage'] in ('FINAL_GATE', 'READY_FOR_MR')))
    contract = attempt(lambda: verify_contract(task))
    pending = store.pending()
    run = verification.get('value', {})
    run_state = run.get('status', 'unknown') if isinstance(run, dict) else 'unknown'
    if run_state == 'running':
        try:
            with store.lock('execution', read=True):
                run_state = 'interrupted'
        except WorkflowError as exc:
            run_state = 'running' if exc.code == 'BUSY' else 'unknown'
    def changed():
        from pathlib import Path
        name = manifest['active_snapshot']
        require(isinstance(name, str) and Path(name).name == name, 'invalid snapshot reference')
        require(not (task / 'snapshots').is_symlink(), 'unsafe snapshots directory')
        return changed_paths(read_json(task / 'snapshots' / name)['files'], snapshot(root, task, review=(manifest.get('blocked_from') if manifest['stage'] == 'BLOCKED' else manifest['stage']) == 'REVIEWING'))
    changes = attempt(changed) if manifest.get('active_snapshot') else {'available': True, 'value': []}
    test_report = reports['test_report.json'].get('value', {})
    rows = test_report.get('checks', []) if isinstance(test_report, dict) else []
    if not isinstance(rows, list):
        rows = []
    valid_checks = {row['id'] for row in verified.get('value', {}).get('checks', []) if isinstance(row, dict) and row.get('result') == 'PASS'}
    missing = [ac for ac in manifest.get('required_acceptance_ids', [])
               if not acceptance_satisfied(rows, ac, valid_checks)]
    if pending or run_state == 'interrupted':
        next_actions = ['Inspect interrupted operation and confirm no check process remains.', 'Run recover with a specific reason; then reload context and rerun invalidated checks.']
    elif run_state == 'running':
        next_actions = ['Wait for the active check run; do not mutate the task or its checked inputs.']
    elif manifest['stage'] == 'BLOCKED':
        next_actions = ['Resolve the recorded blocker.', 'Resume with a reason and expected revision; normal exit gates still apply.']
    elif manifest['stage'] == 'READY_FOR_MR':
        next_actions = ['Review summary.md and prepare delivery within actual authorization; local readiness is not publication.']
    elif diagnostic['passed']:
        next_actions = ['Review current artifacts, then advance exactly one stage using the current revision.']
    else:
        next_actions = ['Resolve the listed gate blockers with the responsible role.', 'Reload context before any mutation; use check to verify readiness.']
    return {'task_id': task.name, 'stage': manifest['stage'], 'revision': manifest.get('revision', 0),
            'base_commit': manifest.get('base_commit'), 'responsible_role': role or 'orchestrator',
            'allowed_paths': manifest['permissions'].get(role, []),
            'rework_rounds': manifest['rework_rounds'], 'blocked_reason': manifest.get('blocked_reason'),
            'contract': contract, 'reports': {name: {'path': str((task / name).relative_to(root)),
                'available': item['available'], 'status': item.get('value', {}).get('status', item.get('value', {}).get('decision')) if isinstance(item.get('value', {}), dict) else None,
                'error': item.get('error')} for name, item in reports.items()},
            'verification': {'available': verification['available'], 'status': run_state,
                             'run_id': run.get('run_id') if isinstance(run, dict) else None,
                             'valid': verified['available'], 'error': verified.get('error'),
                             'checks': run.get('checks', []) if isinstance(run, dict) else []},
            'missing_acceptance_ids': missing, 'changes': changes, 'gate': diagnostic,
            'pending_operation': {k: v for k, v in pending.items() if k != 'files'} if pending else None,
            'workspace_owner': store.owner(), 'next_actions': next_actions,
            'artifact_paths': [str((task / name).relative_to(root)) for name in (*CONTRACT_FILES, 'open_questions.md', 'manifest.json', 'verification.json')],
            'limits': ['Approval references are records, not authenticated identity.',
                       'Cooperative locks do not prevent direct editor/agent file writes.',
                       'Evidence fingerprints cover Git-visible inputs, not all external environment changes.']}
