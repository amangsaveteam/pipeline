#!/usr/bin/env python3
"""Check changed paths in a Git range or working tree against task role allowlist.

Example (clean stage start):
  python3 .workflow/scripts/check_scope.py --task .workflow/tasks/TASK-0001 --role coding --base HEAD --include-worktree
Example (stage commit range):
  python3 .workflow/scripts/check_scope.py --task .workflow/tasks/TASK-0001 --role coding --base abc123 --head def456
"""
import argparse
import fnmatch
import json
import subprocess
from pathlib import Path


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE)


def names(data):
    return [entry.decode('utf-8', 'surrogateescape') for entry in data.split(b'\x00') if entry]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task',required=True)
    p.add_argument('--role',required=True,choices=['requirement','coding','testing','review','documentation'])
    p.add_argument('--base',required=True,help='base ref at beginning of this stage')
    p.add_argument('--head',help='end ref; omit for current worktree')
    p.add_argument('--include-worktree',action='store_true',help='also include untracked files; use with --head omitted')
    a=p.parse_args()
    if a.head and a.include_worktree: p.error('use --head or --include-worktree, not both')
    task=Path(a.task)
    manifest=json.loads((task/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('task_id') != task.name:
        p.error('task_id in manifest does not match directory')
    allowed=manifest['permissions'][a.role]
    # Detect both source deletion and destination addition of a rename.
    changed=set(names(git('diff','--no-renames','--name-only','-z',a.base,*([a.head] if a.head else []),'--')))
    if a.include_worktree:
        changed.update(names(git('ls-files','--others','--exclude-standard','-z')))
    rejected=sorted(f for f in changed if not any(fnmatch.fnmatchcase(f,pattern) for pattern in allowed))
    print(f'ROLE: {a.role}; files changed: {len(changed)}; allowed patterns: {len(allowed)}')
    for path in sorted(changed): print(('DENY ' if path in rejected else 'OK   ') + path)
    if rejected:
        print('Scope gate FAILED; changes outside allowlist:', len(rejected))
        raise SystemExit(1)
    print('Scope gate PASSED')

if __name__ == '__main__': main()
