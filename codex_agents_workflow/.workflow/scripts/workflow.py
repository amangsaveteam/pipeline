#!/usr/bin/env python3
"""Local task gates for a Codex-led workflow (Python 3.10+, Git, no extra packages).

Configure manifest permissions, required_acceptance_ids and checks before advancing
from INTAKE. This tool never launches an agent, authenticates approval, or publishes.
"""
import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
from workflow_core import (CONTRACT_FILES, REPORTS, ROLES, STAGES, STAGE_ROLE, WorkflowError,
                           changed_paths, contract_hashes, denied_paths, digest, git,
                           json_digest, load_manifest, now, read_json, repository,
                           require, safe_directory, snapshot, task_path, verify_contract, write_json)


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


def testing(task, manifest, verification):
    report = role_report(task, 'test_report.json')
    require(report.get('status') == 'passed', 'test report is not passed')
    rows = report.get('checks')
    require(isinstance(rows, list), 'test report needs checks')
    available = {row['id'] for row in verification['checks'] if row['result'] == 'PASS'}
    for ac in manifest['required_acceptance_ids']:
        matches = [r for r in rows if isinstance(r, dict) and r.get('acceptance_id') == ac]
        require(bool(matches) and all(r.get('result') == 'PASS' and r.get('check_id') in available for r in matches),
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
        (task / name).write_text(content, encoding='utf-8')
    save(task, manifest)
    print(f'Created {task.name}: INTAKE. Configure manifest permissions, checks, and required_acceptance_ids; then advance.')


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
    print(f'{stage} -> {next_stage}')


def run_checks(root, task, manifest):
    require(manifest['stage'] in ('TESTING', 'VERIFYING', 'FINAL_GATE', 'READY_FOR_MR'),
            'run-checks requires TESTING, VERIFYING, FINAL_GATE, or READY_FOR_MR')
    verify_contract(task)
    checks = check_config(root, manifest)
    scope(root, task, manifest)
    started = now()
    # A stopped or crashed rerun must never leave the previous success usable.
    write_json(task / 'verification.json', {'status': 'running', 'stage': manifest['stage'],
               'started_at': started, 'checks_digest': json_digest(checks), 'checks': []})
    fingerprint = json_digest(snapshot(root, task, evidence=True))
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
                                           stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
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
        print(f'{check["id"]}: {entry["result"]} (exit={entry["exit_code"]})')
    unchanged = fingerprint == json_digest(snapshot(root, task, evidence=True))
    passed = unchanged and all(row['result'] == 'PASS' for row in results)
    write_json(task / 'verification.json', {'status': 'passed' if passed else 'failed', 'stage': manifest['stage'],
               'started_at': started, 'finished_at': now(), 'checks_digest': json_digest(checks),
               'input_fingerprint': fingerprint, 'inputs_unchanged': unchanged, 'checks': results})
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
        (task / 'summary.md').unlink()
    first_role = ROLES.index(role)
    manifest['scope_history'] = [e for e in manifest['scope_history'] if e['role'] in ROLES[:first_role]]
    manifest.pop('final_gate_entered_at', None)
    manifest['stage'] = target
    event(manifest, 'rework', previous=stage, stage=target, role=role, reason=reason)
    capture(root, task, manifest)
    print(f'{stage} -> {target}; rework round {manifest["rework_rounds"]}/{limit}')


def block(task, manifest, reason):
    require(manifest['stage'] not in ('BLOCKED', 'READY_FOR_MR'), 'cannot block this stage')
    require(bool(reason.strip()), 'block reason must not be empty')
    manifest['blocked_from'] = manifest['stage']
    manifest['blocked_reason'] = reason
    manifest['stage'] = 'BLOCKED'
    event(manifest, 'block', previous=manifest['blocked_from'], reason=reason)
    save(task, manifest)
    print('BLOCKED: ' + reason)


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
    print(f'Resumed {previous}; its normal exit gates still apply.')


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
    output.write_text('\n'.join(text) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'status', 'advance', 'check', 'run-checks', 'rework', 'block', 'resume'):
        cmd = sub.add_parser(command)
        cmd.add_argument('--task', required=True, help='task ID, not a path')
        cmd.add_argument('--root', default='.', help='target Git repository root (default: cwd)')
        if command == 'advance':
            cmd.add_argument('--to', choices=STAGES)
        if command == 'check':
            cmd.add_argument('--final', action='store_true', help='require final delivery gates (CI)')
        if command == 'rework':
            cmd.add_argument('--role', required=True, choices=('coding', 'testing'))
        if command in ('rework', 'block', 'resume'):
            cmd.add_argument('--reason', required=True)
    args = parser.parse_args()
    try:
        root = repository(args.root)
        task = task_path(root, args.task, existing=args.command != 'init')
        if args.command == 'init':
            init(root, task)
            return
        manifest = load_manifest(task)
        if args.command == 'status':
            print(json.dumps({'task_id': task.name, 'stage': manifest['stage'], 'rework_rounds': manifest['rework_rounds'],
                              'blocked_reason': manifest.get('blocked_reason'),
                              'next_stage': STAGES[STAGES.index(manifest['stage']) + 1]
                              if manifest['stage'] in STAGES[:-1] else None}, ensure_ascii=False, indent=2))
            try:
                gate(root, task, manifest)
                print('Current exit gate: PASS')
            except WorkflowError as exc:
                print(f'Current exit gate: NOT READY — {exc}')
        elif args.command == 'advance':
            advance(root, task, manifest, args.to)
        elif args.command == 'check':
            gate(root, task, manifest, final=args.final)
            print('Workflow gate PASSED')
        elif args.command == 'run-checks':
            run_checks(root, task, manifest)
        elif args.command == 'rework':
            rework(root, task, manifest, args.role, args.reason)
        elif args.command == 'block':
            block(task, manifest, args.reason)
        elif args.command == 'resume':
            resume(root, task, manifest, args.reason)
    except (WorkflowError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
