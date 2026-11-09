#!/usr/bin/env python3
"""Compatibility CLI for freezing approved contract bytes through guarded service."""
import argparse
import json
import sys
sys.dont_write_bytecode = True
from workflow_core import WorkflowError, legacy_task, git, require
from workflow_service import execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, help='task directory')
    parser.add_argument('--approved-by', required=True)
    parser.add_argument('--expect-revision', type=int)
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    args = parser.parse_args()
    try:
        task = legacy_task(args.task)
        root = git(task, 'rev-parse', '--show-toplevel').decode().strip()
        from pathlib import Path
        require(task == Path(root) / '.workflow' / 'tasks' / task.name, 'freeze task must be a canonical .workflow/tasks/<ID> directory')
        result = execute('freeze', root=root, task_id=task.name,
                         expected_revision=args.expect_revision, approved_by=args.approved_by)
    except (WorkflowError, OSError, ValueError) as exc:
        result = {'ok': False, 'code': getattr(exc, 'code', 'IO_OR_DATA_ERROR'), 'message': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == 'json' else result['message'],
          file=sys.stdout if args.format == 'json' or result['ok'] else sys.stderr)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
