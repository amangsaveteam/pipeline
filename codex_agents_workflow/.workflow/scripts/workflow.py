#!/usr/bin/env python3
"""Local workflow CLI. Business operations are exposed by workflow_service.execute."""
import argparse
import json
import sys

sys.dont_write_bytecode = True
from workflow_core import STAGES
from workflow_service import execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    commands = ('init', 'status', 'context', 'advance', 'check', 'run-checks', 'rework', 'block', 'resume', 'freeze', 'recover', 'release')
    helps = {'context': 'Read-only evidence and next-step handoff.',
             'recover': 'After checking stopped processes, roll back interrupted workflow records; reject subsequent edits and invalidate interrupted verification. Never repeats external commands.',
             'release': 'Release worktree ownership in INTAKE, BLOCKED or READY_FOR_MR; does not reset snapshots or erase changes.'}
    for command in commands:
        cmd = sub.add_parser(command, help=helps.get(command))
        cmd.add_argument('--task', required=True, help='task ID, not a path')
        cmd.add_argument('--root', default='.', help='target Git root')
        cmd.add_argument('--format', choices=('text', 'json'), default='text')
        if command not in ('init', 'status', 'context', 'check'):
            cmd.add_argument('--expect-revision', type=int, help='optimistic concurrency token; recommended for all automation')
        if command == 'advance':
            cmd.add_argument('--to', choices=STAGES)
        if command == 'check':
            cmd.add_argument('--final', action='store_true')
        if command == 'rework':
            cmd.add_argument('--role', required=True, choices=('coding', 'testing'))
        if command in ('rework', 'block', 'resume', 'recover', 'release'):
            cmd.add_argument('--reason', required=True)
        if command == 'freeze':
            cmd.add_argument('--approved-by', required=True, help='real approval record reference; not identity authentication')
    args = parser.parse_args()
    result = execute(args.command, root=args.root, task_id=args.task,
                     expected_revision=getattr(args, 'expect_revision', None), target=getattr(args, 'to', None),
                     final=getattr(args, 'final', False), role=getattr(args, 'role', None),
                     reason=getattr(args, 'reason', None), approved_by=getattr(args, 'approved_by', None))
    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command in ('status', 'context') and result['ok']:
        print(json.dumps(result.get('context', result), ensure_ascii=False, indent=2))
        print(result['message'])
    else:
        print(result['message'], file=sys.stdout if result['ok'] else sys.stderr)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
