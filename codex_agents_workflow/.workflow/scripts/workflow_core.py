"""Shared local workflow primitives. No model, network, or publishing operations."""
import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class WorkflowError(Exception):
    pass


CONTRACT_FILES = ('requirements.md', 'interface.yaml', 'acceptance.yaml')
ROLES = ('requirement', 'coding', 'testing', 'review', 'documentation')
STAGES = ('INTAKE', 'CONTRACT_DRAFT', 'CONTRACT_REVIEW', 'IMPLEMENTING',
          'TESTING', 'VERIFYING', 'REVIEWING', 'DOCUMENTING', 'FINAL_GATE', 'READY_FOR_MR')
STAGE_ROLE = {'CONTRACT_DRAFT': 'requirement', 'IMPLEMENTING': 'coding',
              'TESTING': 'testing', 'REVIEWING': 'review', 'DOCUMENTING': 'documentation'}
REPORTS = ('implementation_report.json', 'test_report.json', 'review_report.json',
           'documentation_report.json')
STATE_KEYS = {'stage', 'history', 'rework_rounds', 'active_snapshot', 'scope_history',
              'blocked_from', 'blocked_reason', 'final_gate_entered_at'}


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def read_json(path):
    require(path.is_file() and not path.is_symlink(), f'missing or unsafe JSON file: {path}')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise WorkflowError(f'invalid JSON in {path}: {exc}') from exc


def write_json(path, data):
    require(not path.is_symlink(), f'refuse symlink output: {path}')
    temporary = path.with_name(path.name + '.tmp')
    require(not temporary.is_symlink(), f'refuse symlink output: {temporary}')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_digest(data):
    return digest(json.dumps(data, ensure_ascii=True, sort_keys=True).encode())


def git(root, *args):
    try:
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise WorkflowError(f'Git command failed: git {" ".join(args)}') from exc


def repository(path):
    root = Path(path).resolve()
    actual = Path(os.fsdecode(git(root, 'rev-parse', '--show-toplevel')).strip()).resolve()
    require(root == actual, f'--root must be the Git root: {actual}; copy the template into a target repository first')
    return root


def task_path(root, task_id, existing=True):
    require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', task_id)), 'invalid task ID')
    target = root / '.workflow' / 'tasks' / task_id
    for parent in (root / '.workflow', root / '.workflow/tasks', target):
        require(not parent.is_symlink(), f'refuse symlink task directory: {parent}')
    require(target.resolve().is_relative_to(root), 'task path escapes repository')
    if existing:
        require(target.is_dir(), f'task does not exist: {task_id}')
    return target


def load_manifest(task):
    m = read_json(task / 'manifest.json')
    require(isinstance(m, dict) and m.get('task_id') == task.name, 'manifest task_id does not match directory')
    require(m.get('stage') in STAGES + ('BLOCKED',), 'invalid or missing manifest stage')
    require(isinstance(m.get('permissions'), dict), 'permissions must be an object')
    for role in ROLES:
        patterns = m['permissions'].get(role)
        require(isinstance(patterns, list) and all(isinstance(p, str) and p and not p.startswith('/')
                and '..' not in p.split('/') for p in patterns), f'invalid permissions.{role}')
    require(m['permissions']['review'] == [], 'review permissions must be empty')
    return m


def contract_hashes(task, placeholders=False):
    result = {}
    for name in CONTRACT_FILES:
        path = task / name
        require(path.is_file() and not path.is_symlink(), f'missing or unsafe contract file: {path}')
        data = path.read_bytes()
        require(bool(data.strip()) and b'\x00' not in data, f'empty or invalid contract file: {name}')
        if placeholders:
            text = data.decode('utf-8')
            require(not re.search(r'待填写|replace[_ ]?me|REPLACE_WITH|<command>|TODO|TBD', text, re.I),
                    f'contract contains template placeholder: {name}')
        result[name] = digest(data)
    return result


def legacy_task(path):
    """Legacy commands accept a task path, but never traverse a directory symlink."""
    task = Path(os.path.abspath(path))
    require(task.is_dir(), f'task directory missing: {task}')
    for part in (task, *task.parents):
        require(not part.is_symlink(), f'refuse symlink task path: {part}')
    return task


def verify_contract(task):
    lock = read_json(task / 'contract.lock.json')
    require(isinstance(lock, dict) and isinstance(lock.get('approved_by'), str)
            and bool(lock['approved_by'].strip()), 'lock needs nonempty approval reference')
    require(isinstance(lock.get('approved_at_utc'), str) and bool(lock['approved_at_utc']), 'lock needs approval timestamp')
    require(lock.get('sha256') == contract_hashes(task), 'contract hash mismatch or incomplete lock')
    return lock


def freeze_contract(task, approved_by):
    require(bool(approved_by.strip()), 'approval reference must not be empty')
    lock = task / 'contract.lock.json'
    require(not lock.exists() and not lock.is_symlink(), 'contract lock already exists; use a new version/task and approval')
    result = {'approved_by': approved_by.strip(), 'approved_at_utc': now(),
              'sha256': contract_hashes(task, placeholders=True)}
    # Exclusive create also protects against an accidental second freeze.
    with lock.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return result


def generated_path(relative, task_rel):
    prefix = task_rel + '/'
    if not relative.startswith(prefix):
        return False
    tail = relative[len(prefix):]
    return tail.startswith(('logs/', 'snapshots/')) or tail in {
        'verification.json', 'summary.md', 'manifest.json.tmp'}


def snapshot(root, task, evidence=False, review=False):
    """Hash Git-visible files without following links; ignore only our generated artifacts.

    Git-ignored build/cache output is omitted, but tracked ignored files remain visible.
    Runtime state is normalized out of manifest content; policy/config stays in scope.
    """
    paths = set(os.fsdecode(p) for p in git(root, 'ls-files', '--cached', '--others',
                                          '--exclude-standard', '-z').split(b'\0') if p)
    task_rel = task.relative_to(root).as_posix()
    result = {}
    for relative in sorted(paths):
        if generated_path(relative, task_rel):
            continue
        tail = relative.removeprefix(task_rel + '/')
        if relative.startswith(task_rel + '/') and ((evidence and tail in REPORTS) or
                                                    (review and tail == 'review_report.json')):
            continue
        path = root / relative
        # Parent symlinks would let a listed path read outside root.
        require(path.parent.resolve().is_relative_to(root), f'file parent escapes repository: {relative}')
        try:
            info = path.lstat()
        except FileNotFoundError:
            # A deletion has the same content before and after git add/commit.
            # The prior snapshot still contains the file and detects its removal.
            continue
        if stat.S_ISLNK(info.st_mode):
            result[relative] = 'link:' + digest(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            if relative == task_rel + '/manifest.json':
                value = read_json(path)
                content = json_digest({key: val for key, val in value.items() if key not in STATE_KEYS})
            else:
                content = digest(path.read_bytes())
            result[relative] = f'file:{info.st_mode & 0o111}:{content}'
        elif stat.S_ISDIR(info.st_mode):
            # Submodule worktrees cannot be comprehensively checked by this tool.
            raise WorkflowError(f'directory/submodule is not supported in file snapshot: {relative}')
        else:
            raise WorkflowError(f'unsupported file type: {relative}')
    return result


def changed_paths(before, after):
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def denied_paths(changed, patterns):
    return [path for path in changed if not any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)]


def safe_directory(root, relative):
    require(isinstance(relative, str) and not Path(relative).is_absolute() and '..' not in Path(relative).parts,
            'check cwd must be repository-relative')
    directory = (root / relative).resolve()
    require(directory.is_relative_to(root) and directory.is_dir(), 'check cwd missing or outside repository')
    return directory
