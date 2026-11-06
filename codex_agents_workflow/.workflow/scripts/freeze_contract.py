#!/usr/bin/env python3
"""Freeze approved contract bytes. An approval reference is a record, not authentication."""
import argparse
import sys
sys.dont_write_bytecode = True
from workflow_core import WorkflowError, freeze_contract, legacy_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, help='task directory, e.g. .workflow/tasks/TASK-0001')
    parser.add_argument('--approved-by', required=True, help='actual human/owner approval reference')
    args = parser.parse_args()
    try:
        task = legacy_task(args.task)
        freeze_contract(task, args.approved_by)
        print(f'Contract locked: {task / "contract.lock.json"}')
        print('Approval reference recorded; identity is not authenticated by this script.')
    except (WorkflowError, OSError, UnicodeError, ValueError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
