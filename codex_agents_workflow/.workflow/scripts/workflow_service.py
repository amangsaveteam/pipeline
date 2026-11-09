#!/usr/bin/env python3
"""Local task gates for a Codex-led workflow (Python 3.10+, Git, no extra packages).

Configure manifest permissions, required_acceptance_ids and checks before advancing
from INTAKE. This tool never launches an agent, authenticates approval, or publishes.
"""
import json
import math
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
from workflow_core import (CONTRACT_FILES, REPORTS, ROLES, STAGES, STAGE_ROLE, WorkflowError,
                           changed_paths, contract_hashes, denied_paths, digest, git,
                           json_digest, load_manifest, now, read_json, repository,
                           require, safe_directory, snapshot, task_path, verify_contract, write_json, write_bytes, freeze_contract)


def save(task, manifest):
    write_json(task / 'manifest.json', manifest)


def event(manifest, action, **fields):
    manifest.setdefault('history', []).append({'at': now(), 'action': action, **fields})


def check_config(root, manifest):
    ids = manifest.get('required_acceptance_ids')
    require(isinstance(ids, list) and bool(ids) and all(isinstance(x, str) and x.startswith('AC-') for x in ids)
            and len(ids) == len(set(ids)), 'required_acceptance_ids must contain unique AC IDs')
    checks = manifest.get('checks')
    require(isinstance(checks, list) and bool(checks), 'configure at least one real check in manifest.checks')
    seen = set()
    for check in checks:
        require(isinstance(check, dict), 'each check must be an object')
        check_id = check.get('id')
        require(isinstance(check_id, str) and check_id and check_id not in seen, 'check IDs must be nonempty and unique')
        seen.add(check_id)
        argv = check.get('argv')
        require(isinstance(argv, list) and bool(argv) and all(isinstance(a, str) and a and '\0' not in a for a in argv),
                f'check {check_id}: argv must be a nonempty string array')
        safe_directory(root, check.get('cwd', '.'))
        timeout = check.get('timeout_seconds', 60)
        require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
                f'check {check_id}: timeout_seconds must be positive and finite')
    return checks


def capture(root, task, manifest):
    stage = manifest['stage']
    if stage not in STAGE_ROLE and stage not in ('CONTRACT_REVIEW', 'VERIFYING', 'FINAL_GATE'):
        manifest['active_snapshot'] = None
        save(task, manifest)
        return
    name = f'{len(manifest["history"]):04d}-{stage}-{manifest["rework_rounds"]}.json'
    directory = task / 'snapshots'
    require(not directory.is_symlink(), 'unsafe snapshots directory')
    directory.mkdir(exist_ok=True)
    path = directory / name
    require(not path.exists(), 'refuse to overwrite stage snapshot')
    manifest['active_snapshot'] = name
    write_json(path, {'stage': stage, 'role': STAGE_ROLE.get(stage),
                      'files': snapshot(root, task, review=stage == 'REVIEWING')})
    # State fields are normalized out of snapshots, so publish the transition
    # only after its snapshot has been generated successfully.
    save(task, manifest)


def scope(root, task, manifest, stage=None):
    stage = stage or manifest['stage']
    if stage not in STAGE_ROLE and stage not in ('CONTRACT_REVIEW', 'VERIFYING', 'FINAL_GATE', 'READY_FOR_MR'):
        return []
    name = manifest.get('active_snapshot')
    require(isinstance(name, str) and Path(name).name == name, 'missing/unsafe active stage snapshot')
    require(not (task / 'snapshots').is_symlink(), 'unsafe snapshots directory')
    saved = read_json(task / 'snapshots' / name)
    expected_stage = 'FINAL_GATE' if stage == 'READY_FOR_MR' else stage
    require(saved.get('stage') == expected_stage and isinstance(saved.get('files'), dict), 'snapshot stage mismatch')
    current = snapshot(root, task, review=stage == 'REVIEWING')
    changes = changed_paths(saved['files'], current)
    role = STAGE_ROLE.get(stage)
    # Current allowlists cannot authorize their own edits: manifest policy itself
    # is never in a role's writable surface, even if a broad glob was configured.
    policy_path = (task / 'manifest.json').relative_to(root).as_posix()
    require(policy_path not in changes, 'manifest policy changed during stage; restore it or start a new task')
    allowed = manifest['permissions'][role] if role else []
    if stage == 'CONTRACT_REVIEW':
        allowed = [(task / 'contract.lock.json').relative_to(root).as_posix()]
    denied = denied_paths(changes, allowed)
    require(not denied, 'scope violation: ' + ', '.join(denied))
    return changes


def evidence(root, task, manifest, final=False):
    checks = check_config(root, manifest)
    report = read_json(task / 'verification.json')
    require(report.get('status') == 'passed', 'verification did not pass')
    require(report.get('checks_digest') == json_digest(checks), 'check configuration changed; rerun checks')
    require(report.get('input_fingerprint') == json_digest(snapshot(root, task, evidence=True)),
            'verification evidence is stale; rerun checks')
    if final:
        require(report.get('stage') in ('FINAL_GATE', 'READY_FOR_MR')
                and report.get('started_at', '') >= manifest.get('final_gate_entered_at', '~'),
                'final gate requires a fresh run-checks after entering FINAL_GATE')
    results = report.get('checks')
    require(isinstance(results, list) and len(results) == len(checks), 'incomplete check evidence')
    require({r.get('id') for r in results if isinstance(r, dict)} == {c['id'] for c in checks}, 'check evidence IDs mismatch')
    for check, result in zip(checks, results):
        require(result.get('id') == check['id'] and result.get('argv') == check['argv']
                and result.get('cwd') == check.get('cwd', '.'), 'command evidence mismatch')
        require(result.get('result') == 'PASS' and type(result.get('exit_code')) is int and result['exit_code'] == 0,
                f'check failed: {check["id"]}')
        log = result.get('log')
        require(isinstance(log, str) and Path(log).parts[:1] == ('logs',)
                and '..' not in Path(log).parts and not Path(log).is_absolute(), 'invalid evidence log path')
        path = task / log
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(task.resolve()), 'missing/unsafe evidence log')
        require(digest(path.read_bytes()) == result.get('log_sha256'), 'check log changed or missing')
    return report


def role_report(task, name):
    report = read_json(task / name)
    require(isinstance(report, dict), f'{name} must be an object')
    return report


def implementation(task):
    report = role_report(task, 'implementation_report.json')
    require(report.get('status') == 'completed', 'implementation report is not completed')
    require(isinstance(report.get('changed_files'), list), 'implementation needs changed_files')
    require(isinstance(report.get('requirements_implemented'), list) and bool(report['requirements_implemented']),
            'implementation needs requirement mapping')
    require(not report.get('blockers'), 'implementation has unresolved blockers')


def acceptance_satisfied(rows, acceptance_id, available_checks):
    """Use the same acceptance predicate for enforcement and recovery diagnostics."""
    matches = [row for row in rows if isinstance(row, dict) and row.get('acceptance_id') == acceptance_id]
    return bool(matches) and all(row.get('result') == 'PASS' and row.get('check_id') in available_checks for row in matches)


def testing(task, manifest, verification):
    report = role_report(task, 'test_report.json')
    require(report.get('status') == 'passed', 'test report is not passed')
    rows = report.get('checks')
    require(isinstance(rows, list), 'test report needs checks')
    available = {row['id'] for row in verification['checks'] if row['result'] == 'PASS'}
    for ac in manifest['required_acceptance_ids']:
        require(acceptance_satisfied(rows, ac, available),
                f'missing/failed/unexecuted acceptance evidence: {ac}')


def review(task):
    report = role_report(task, 'review_report.json')
    require(report.get('decision') == 'approved', 'review is not approved')
    findings = report.get('findings')
    require(isinstance(findings, list) and all(isinstance(f, dict) for f in findings), 'review needs findings array')
    require(all(f.get('severity') in ('blocker', 'high', 'medium', 'low') for f in findings), 'invalid review severity')
    require(not any(f.get('severity') in ('blocker', 'high') and f.get('resolved') is not True for f in findings),
            'review has unresolved blocker/high findings')


def documentation(task):
    report = role_report(task, 'documentation_report.json')
    require(report.get('status') == 'completed' and isinstance(report.get('changed_files'), list),
            'documentation report is not completed')


def gate(root, task, manifest, final=False):
    stage = manifest['stage']
    require(stage != 'BLOCKED', 'task is BLOCKED; resolve reason and resume')
    if final:
        require(stage in ('FINAL_GATE', 'READY_FOR_MR'), 'final check requires FINAL_GATE or READY_FOR_MR')
    if STAGES.index(stage) >= STAGES.index('IMPLEMENTING') or stage == 'CONTRACT_REVIEW':
        verify_contract(task)
        check_config(root, manifest)
    changes = scope(root, task, manifest)
    if stage == 'CONTRACT_DRAFT':
        contract_hashes(task, placeholders=True)
        questions = task / 'open_questions.md'
        require(questions.is_file() and bool(questions.read_text().strip()), 'missing open_questions.md')
        require('待填写' not in questions.read_text(), 'open questions still contain placeholder')
    if stage == 'CONTRACT_REVIEW':
        acceptance = (task / 'acceptance.yaml').read_text()
        require(all(ac in acceptance for ac in manifest['required_acceptance_ids']), 'required AC IDs absent from acceptance.yaml')
    if stage == 'IMPLEMENTING':
        implementation(task)
    if stage in ('TESTING', 'VERIFYING', 'REVIEWING'):
        implementation(task)
        verification = evidence(root, task, manifest)
        testing(task, manifest, verification)
    if stage == 'REVIEWING':
        review(task)
    if stage == 'DOCUMENTING':
        implementation(task)
        review(task)
        documentation(task)
    if stage in ('FINAL_GATE', 'READY_FOR_MR'):
        implementation(task)
        testing(task, manifest, evidence(root, task, manifest, final=True))
        review(task)
        documentation(task)
        completed_roles = {entry.get('role') for entry in manifest.get('scope_history', []) if entry.get('result') == 'PASS'}
        require(set(ROLES).issubset(completed_roles), 'missing completed role scope evidence')
    return changes


def init(root, task):
    require(not task.exists(), 'task already exists; refusing overwrite')
    templates = Path(__file__).resolve().parents[1] / 'templates'
    manifest = read_json(templates / 'manifest.json')
    # Resolve HEAD before creating anything: an unborn/non-Git project is not a task baseline.
    base = git(root, 'rev-parse', 'HEAD').decode().strip()
    contents = {}
    for name in ('manifest.json', *CONTRACT_FILES, 'open_questions.md', *REPORTS):
        contents[name] = (templates / name).read_text(encoding='utf-8').replace('TASK-0001', task.name)
    manifest = json.loads(contents.pop('manifest.json'))
    manifest.update(task_id=task.name, base_commit=base, stage='INTAKE', history=[],
                    rework_rounds=0, scope_history=[], active_snapshot=None)
    event(manifest, 'init', stage='INTAKE')
    task.mkdir(parents=True)
    for name, content in contents.items():
        write_bytes(task / name, content.encode())
    save(task, manifest)
    return 'Created task in INTAKE; configure permissions, checks and acceptance IDs.'


def advance(root, task, manifest, target=None):
    stage = manifest['stage']
    require(stage in STAGES and stage != 'READY_FOR_MR', 'task cannot advance from this stage')
    next_stage = STAGES[STAGES.index(stage) + 1]
    require(target is None or target == next_stage, f'only next stage is allowed: {next_stage}')
    changes = gate(root, task, manifest)
    if stage in STAGE_ROLE:
        manifest['scope_history'].append({'stage': stage, 'role': STAGE_ROLE[stage], 'at': now(),
                                          'result': 'PASS', 'changed_files': changes,
                                          'snapshot': manifest['active_snapshot']})
    manifest['stage'] = next_stage
    if next_stage == 'FINAL_GATE':
        manifest['final_gate_entered_at'] = now()
    event(manifest, 'advance', previous=stage, stage=next_stage)
    if next_stage == 'READY_FOR_MR':
        summary(task, manifest)
        save(task, manifest)  # Retain the FINAL_GATE snapshot for future CI checks.
    else:
        capture(root, task, manifest)
    return f'{stage} -> {next_stage}'


def run_checks(root, task, manifest, progress=None, run_id=None, pass_fds=(), commit=None, prepared=None):
    checks, started, fingerprint = prepared
    directory = task / 'logs'
    require(not directory.is_symlink(), 'unsafe logs directory')
    directory.mkdir(exist_ok=True)
    results = []
    for index, check in enumerate(checks):
        path = directory / f'{time.time_ns()}-{index}.log'
        entry = {'id': check['id'], 'argv': check['argv'], 'cwd': check.get('cwd', '.'),
                 'started_at': now(), 'exit_code': None, 'result': 'ERROR',
                 'log': path.relative_to(task).as_posix()}
        with path.open('xb') as output:
            process = None
            try:
                process = subprocess.Popen(check['argv'], cwd=safe_directory(root, check.get('cwd', '.')),
                                           stdout=output, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=pass_fds)
                code = process.wait(timeout=check.get('timeout_seconds', 60))
                entry.update(exit_code=code, result='PASS' if code == 0 else 'FAIL')
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                entry.update(exit_code=process.returncode, result='TIMEOUT')
                output.write(b'\nWorkflow timeout: process group terminated.\n')
            except OSError as exc:
                output.write(f'Cannot run check: {exc}\n'.encode())
            finally:
                entry['finished_at'] = now()
        entry['log_sha256'] = digest(path.read_bytes())
        results.append(entry)
        if progress:
            progress(dict(entry))
    unchanged = fingerprint == json_digest(snapshot(root, task, evidence=True))
    passed = unchanged and all(row['result'] == 'PASS' for row in results)
    result = {'status': 'passed' if passed else 'failed', 'stage': manifest['stage'], 'run_id': run_id,
               'started_at': started, 'finished_at': now(), 'checks_digest': json_digest(checks),
               'input_fingerprint': fingerprint, 'inputs_unchanged': unchanged, 'checks': results}
    if commit:
        commit(result)
    else:
        write_json(task / 'verification.json', result)
    require(passed, 'checks failed or changed input files; inspect verification.json and logs')


def reset_report(task, name):
    template = Path(__file__).resolve().parents[1] / 'templates' / name
    write_json(task / name, json.loads(template.read_text().replace('TASK-0001', task.name)))


def rework(root, task, manifest, role, reason):
    stage = manifest['stage']
    require(stage in ('TESTING', 'VERIFYING', 'REVIEWING', 'DOCUMENTING', 'FINAL_GATE'),
            'rework requires a failed downstream stage')
    require(bool(reason.strip()), 'rework reason must not be empty')
    verify_contract(task)
    scope(root, task, manifest)
    limit = manifest.get('max_rework_rounds', 2)
    require(type(limit) is int and 0 <= limit <= 2, 'max_rework_rounds must be between 0 and 2')
    if manifest['rework_rounds'] >= limit:
        block(task, manifest, 'Automatic rework limit reached: ' + reason)
        raise WorkflowError('automatic rework limit reached; task BLOCKED for owner decision')
    manifest['rework_rounds'] += 1
    target = 'IMPLEMENTING' if role == 'coding' else 'TESTING'
    invalid = REPORTS if role == 'coding' else REPORTS[1:]
    # Preserve previous reports for diagnosis before marking them incomplete.
    archive = task / 'snapshots' / f'rework-{manifest["rework_rounds"]}-reports.json'
    require(not archive.parent.is_symlink(), 'unsafe snapshots directory')
    write_json(archive, {name: read_json(task / name) for name in invalid})
    for name in invalid:
        reset_report(task, name)
    verification = task / 'verification.json'
    if verification.exists():
        write_json(verification, {'status': 'invalidated', 'reason': reason, 'at': now()})
    if (task / 'summary.md').exists():
        write_bytes(task / 'summary.md', None)
    first_role = ROLES.index(role)
    manifest['scope_history'] = [e for e in manifest['scope_history'] if e['role'] in ROLES[:first_role]]
    manifest.pop('final_gate_entered_at', None)
    manifest['stage'] = target
    event(manifest, 'rework', previous=stage, stage=target, role=role, reason=reason)
    capture(root, task, manifest)
    return f'{stage} -> {target}; rework round {manifest["rework_rounds"]}/{limit}'


def block(task, manifest, reason):
    require(manifest['stage'] not in ('BLOCKED', 'READY_FOR_MR'), 'cannot block this stage')
    require(bool(reason.strip()), 'block reason must not be empty')
    manifest['blocked_from'] = manifest['stage']
    manifest['blocked_reason'] = reason
    manifest['stage'] = 'BLOCKED'
    event(manifest, 'block', previous=manifest['blocked_from'], reason=reason)
    save(task, manifest)
    return 'BLOCKED: ' + reason


def resume(root, task, manifest, reason):
    require(manifest['stage'] == 'BLOCKED', 'task is not BLOCKED')
    require(bool(reason.strip()), 'resume reason must not be empty')
    previous = manifest.get('blocked_from')
    require(previous in STAGES and previous != 'READY_FOR_MR', 'invalid resume stage')
    if STAGES.index(previous) >= STAGES.index('IMPLEMENTING'):
        verify_contract(task)
    scope(root, task, manifest, stage=previous)
    manifest['stage'] = previous
    manifest.pop('blocked_from', None)
    manifest.pop('blocked_reason', None)
    event(manifest, 'resume', stage=previous, reason=reason)
    save(task, manifest)
    return f'Resumed {previous}; its normal exit gates still apply.'


def summary(task, manifest):
    verification = read_json(task / 'verification.json')
    lock = verify_contract(task)
    text = [f'# {task.name} — READY_FOR_MR', '', f'Base commit: `{manifest["base_commit"]}`',
            f'Contract approval reference: {lock["approved_by"]}',
            f'Rework rounds: {manifest["rework_rounds"]}', '', '## Verified commands', '']
    for check in verification['checks']:
        text.append(f'- `{json.dumps(check["argv"], ensure_ascii=False)}` — exit {check["exit_code"]}; log: `{check["log"]}`')
    text.extend(['', '## Changes', ''])
    for entry in manifest['scope_history']:
        text.append(f'- {entry["role"]}: ' + (', '.join(f'`{p}`' for p in entry['changed_files']) or 'no file changes'))
    text.extend(['', '## Limitations', '', '- READY_FOR_MR means local gates passed. Nothing was pushed, merged, or deployed.',
                 '- Approval references and reports are records, not identity authentication or filesystem isolation.'])
    for name in REPORTS:
        report = read_json(task / name)
        for item in report.get('known_limitations', []) + report.get('uncertainties', []):
            text.append(f'- {name}: {item}')
    report = read_json(task / 'test_report.json')
    if report.get('hardware_tests'):
        text.append(f'- Hardware: {report["hardware_tests"]}')
    output = task / 'summary.md'
    require(not output.is_symlink(), 'unsafe summary path')
    write_bytes(output, ('\n'.join(text) + '\n').encode())



# Public entry point. All CLI/MCP adapters call this function; none parse text.
from workflow_store import Store


def diagnose(root, task, manifest, final=False):
    try:
        changes = gate(root, task, manifest, final=final)
        return {'passed': True, 'blockers': [], 'changed_files': changes}
    except (WorkflowError, OSError, ValueError, KeyError, TypeError) as exc:
        return {'passed': False, 'blockers': [{'code': getattr(exc, 'code', 'INVALID_ARTIFACT'),
                                              'message': str(exc)}]}


def revision_check(manifest, expected):
    revision = manifest.get('revision', 0)
    if expected is not None and (type(expected) is not int or expected != revision):
        raise WorkflowError(f'Expected revision {expected}, actual revision {revision}; reload context.', 'REVISION_CONFLICT')
    return revision


def bump(task, manifest, action):
    manifest['revision'] = manifest.get('revision', 0) + 1
    # Revision is runtime state, excluded from stage/evidence fingerprints.
    save(task, manifest)


def execute(command, *, root='.', task_id, expected_revision=None, target=None,
            role=None, reason=None, final=False, approved_by=None, progress=None):
    """Return JSON-compatible results; no stdout, no implicit command retries.

    Mutations accept optional optimistic concurrency tokens for legacy CLI
    compatibility. New automation should always supply expected_revision.
    """
    result = {'ok': False, 'code': 'ERROR', 'message': '', 'task_id': task_id,
              'stage': None, 'revision': None, 'state_changed': False}
    store = task = None
    before_revision = None
    before_digest = None
    try:
        root = repository(root)
        task = task_path(root, task_id, existing=command not in ('init', 'recover'))
        store = Store(root, task)
        if command in ('status', 'context', 'check'):
            with store.lock(read=True):
                manifest = load_manifest(task)
                result.update(stage=manifest['stage'], revision=manifest.get('revision', 0))
                diagnostic = diagnose(root, task, manifest, final=final)
                pending = store.pending()
                try:
                    verification = store.optional(task / 'verification.json')
                    require(verification is None or isinstance(verification, dict), 'verification must be an object')
                except (WorkflowError, OSError, ValueError, TypeError) as exc:
                    verification = None
                    diagnostic = {'passed': False, 'blockers': [{'code': 'INVALID_ARTIFACT', 'message': str(exc)}]}
                if pending:
                    diagnostic = {'passed': False, 'blockers': [{'code': 'OPERATION_INCOMPLETE', 'message': 'Interrupted file operation requires recovery.'}]}
                if verification and verification.get('status') == 'running':
                    diagnostic = {'passed': False, 'blockers': [{'code': 'RUN_UNFINISHED', 'message': 'Check execution is running or interrupted; inspect context.'}]}
                result.update(gate=diagnostic, rework_rounds=manifest['rework_rounds'],
                              blocked_reason=manifest.get('blocked_reason'),
                              next_stage=STAGES[STAGES.index(manifest['stage']) + 1] if manifest['stage'] in STAGES[:-1] else None)
                if command == 'context':
                    from workflow_context import build_context
                    result['context'] = build_context(root, task, manifest, store, diagnostic)
                result.update(ok=command != 'check' or diagnostic['passed'],
                              code='OK' if command != 'check' or diagnostic['passed'] else 'GATE_NOT_SATISFIED',
                              message='Current exit gate: PASS' if diagnostic['passed'] else 'Current exit gate: NOT READY')
                return result
        allowed = ('init', 'advance', 'run-checks', 'rework', 'block', 'resume', 'freeze', 'recover', 'release')
        require(command in allowed, 'unknown workflow command')
        # Shared execution lock prevents mutations while a check subprocess still
        # runs, even if its original parent died (the subprocess inherits the FD).
        with store.lock('execution') as run_fd:
            with store.lock():
                if command == 'recover':
                    require(isinstance(reason, str) and bool(reason.strip()), 'recovery reason is required')
                    observed_revision = 0
                    if (task / 'manifest.json').exists():
                        before_manifest = load_manifest(task)
                        observed_revision = revision_check(before_manifest, expected_revision)
                    store.claim()
                    recovered = store.rollback()
                    result['state_changed'] = bool(recovered and recovered.get('files'))
                    if not (task / 'manifest.json').exists():
                        # Interrupted initialization: remove only its now-empty directories.
                        if task.exists():
                            remaining = sorted(path.name for path in task.iterdir())
                            if remaining:
                                result['remaining_paths'] = remaining
                                raise WorkflowError('Initialization rollback left files; inspect and remove only verified temporary artifacts before retrying recover.', 'RECOVERY_CONFLICT')
                            task.rmdir()
                        if recovered:
                            store.complete_recovery(recovered)
                        store.release()
                        result.update(ok=True, code='RECOVERED', message='Initialization rolled back; init can be retried.', state_changed=bool(recovered))
                        return result
                    manifest = load_manifest(task)
                    manifest['revision'] = max(observed_revision, manifest.get('revision', 0))
                    report = store.optional(task / 'verification.json')
                    with store.transaction('recover', manifest.get('revision', 0), resume_pending=bool(recovered)):
                        if report and (report.get('status') == 'running' or recovered):
                            write_json(task / 'verification.json', {'status': 'invalidated', 'reason': reason, 'at': now()})
                        event(manifest, 'recover', reason=reason, recovered_operation=recovered.get('operation_id') if recovered else None)
                        bump(task, manifest, 'recover')
                    result.update(ok=True, code='RECOVERED', message='Workflow records recovered; normal gates still apply. Rerun interrupted checks.',
                                  stage=manifest['stage'], revision=manifest['revision'], state_changed=True)
                    return result
                if store.pending():
                    raise WorkflowError('Unfinished operation; inspect context and recover with a reason.', 'OPERATION_INCOMPLETE')
                manifest = None if command == 'init' else load_manifest(task)
                if manifest:
                    before_revision = revision_check(manifest, expected_revision)
                    before_digest = digest((task / 'manifest.json').read_bytes())
                    result.update(stage=manifest['stage'], revision=before_revision)
                    verification = store.optional(task / 'verification.json')
                    if verification and verification.get('status') == 'running':
                        raise WorkflowError('Previous check run did not finish; recover before retrying.', 'RUN_UNFINISHED')
                if command == 'release':
                    require(isinstance(reason, str) and bool(reason.strip()), 'release reason is required')
                    require(manifest['stage'] in ('INTAKE', 'BLOCKED', 'READY_FOR_MR'), 'release requires INTAKE, BLOCKED or READY_FOR_MR')
                    owner = store.owner()
                    if owner and owner.get('task_id') != task_id:
                        raise WorkflowError('Another task owns this worktree.', 'WORKSPACE_BUSY')
                    with store.transaction(command, before_revision):
                        event(manifest, 'release', reason=reason)
                        bump(task, manifest, command)
                    store.release()
                    result.update(ok=True, code='OK', message='Workspace released; future scope checks remain unchanged.', stage=manifest['stage'], revision=manifest['revision'], state_changed=True)
                    return result
                if command == 'init':
                    require(not task.exists(), 'task already exists; refusing overwrite')
                    git(root, 'rev-parse', 'HEAD')
                store.claim()
                if command == 'run-checks':
                    require(manifest['stage'] in ('TESTING', 'VERIFYING', 'FINAL_GATE', 'READY_FOR_MR'), 'run-checks requires TESTING, VERIFYING, FINAL_GATE, or READY_FOR_MR')
                    verify_contract(task)
                    checks = check_config(root, manifest)
                    scope(root, task, manifest)
                    fingerprint = json_digest(snapshot(root, task, evidence=True))
                    started, run_id = now(), uuid.uuid4().hex
                    with store.transaction('start-checks', before_revision):
                        write_json(task / 'verification.json', {'status': 'running', 'stage': manifest['stage'], 'run_id': run_id,
                                   'started_at': started, 'checks_digest': json_digest(checks), 'checks': []})
                        bump(task, manifest, command)
                    run_revision = manifest['revision']
                else:
                    with store.transaction(command, before_revision):
                        if command == 'init':
                            message = init(root, task)
                            manifest = load_manifest(task)
                        elif command == 'advance':
                            message = advance(root, task, manifest, target)
                        elif command == 'rework':
                            require(role in ('coding', 'testing'), 'invalid rework role')
                            try:
                                message = rework(root, task, manifest, role, reason or '')
                            except WorkflowError:
                                if manifest['stage'] != 'BLOCKED':
                                    raise
                                message = manifest['blocked_reason']
                                result.update(code='REWORK_LIMIT', message=message)
                        elif command == 'block':
                            message = block(task, manifest, reason or '')
                        elif command == 'resume':
                            message = resume(root, task, manifest, reason or '')
                        elif command == 'freeze':
                            require(manifest['stage'] == 'CONTRACT_REVIEW', 'freeze requires CONTRACT_REVIEW')
                            freeze_contract(task, approved_by or '')
                            message = 'Contract locked; approval reference recorded, not authenticated.'
                        bump(task, manifest, command)
                    if manifest['stage'] == 'READY_FOR_MR':
                        store.release()
                    result.update(ok=result['code'] != 'REWORK_LIMIT', code=result['code'] if result['code'] == 'REWORK_LIMIT' else 'OK',
                                  message=message, stage=manifest['stage'], revision=manifest['revision'], state_changed=True)
                    return result
            # No state lock is held while external checks run.
            def commit(report):
                with store.lock():
                    current = load_manifest(task)
                    revision_check(current, run_revision)
                    saved = read_json(task / 'verification.json')
                    require(saved.get('run_id') == run_id and saved.get('status') == 'running', 'run ownership changed')
                    with store.transaction('finish-checks', run_revision):
                        write_json(task / 'verification.json', report)
                        bump(task, current, 'finish-checks')
            run_checks(root, task, manifest, progress=progress, run_id=run_id, pass_fds=(run_fd,), commit=commit,
                       prepared=(checks, started, fingerprint))
            current = load_manifest(task)
            result.update(ok=True, code='OK', message='Checks passed.', stage=current['stage'], revision=current['revision'], state_changed=True,
                          verification=read_json(task / 'verification.json'))
            return result
    except (WorkflowError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        result.update(code=getattr(exc, 'code', 'IO_OR_DATA_ERROR'), message=str(exc))
        if task and (task / 'manifest.json').is_file():
            try:
                current = load_manifest(task)
                result.update(stage=current['stage'], revision=current.get('revision', 0))
                result['state_changed'] = result['state_changed'] or (before_digest is not None and before_digest != digest((task / 'manifest.json').read_bytes()))
            except (WorkflowError, OSError, ValueError, KeyError, TypeError):
                pass
        if store:
            try:
                if store.pending():
                    result.update(pending_operation=True,
                                  recovery='Inspect context and run recover --reason after checking the interrupted operation.')
                    if result['code'] != 'RECOVERY_CONFLICT':
                        result.update(code='OPERATION_INCOMPLETE', state_changed=True)
            except (WorkflowError, OSError, ValueError, KeyError, TypeError):
                pass
        return result
