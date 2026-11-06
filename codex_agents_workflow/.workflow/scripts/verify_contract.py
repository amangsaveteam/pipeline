#!/usr/bin/env python3
"""Verify frozen contract bytes and the presence of its approval reference."""
import argparse
import sys
sys.dont_write_bytecode = True
from workflow_core import WorkflowError, legacy_task, verify_contract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, help='task directory')
    args = parser.parse_args()
    try:
        task = legacy_task(args.task)
        verify_contract(task)
        print('Contract gate PASSED')
    except (WorkflowError, OSError, UnicodeError, ValueError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
