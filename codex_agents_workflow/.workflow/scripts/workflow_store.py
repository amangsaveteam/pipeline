"""POSIX cooperative locks and recoverable file journals, private to one Git worktree.

These guards do not sandbox agents or lock arbitrary editor writes.
"""
import base64
from contextlib import contextmanager
import fcntl
import os
import json
from pathlib import Path
import uuid

from workflow_core import WorkflowError, WRITE_OBSERVER, git, now, read_json, require, write_bytes, write_json


def encoded(data):
    return None if data is None else base64.b64encode(data).decode('ascii')


def decoded(data):
    return None if data is None else base64.b64decode(data, validate=True)


class Store:
    def __init__(self, root, task):
        self.root, self.task = root, task
        git_dir = Path(os.fsdecode(git(root, 'rev-parse', '--absolute-git-dir')).strip())
        self.directory = git_dir / 'workflow-state'
        self.journal_path = self.directory / (task.name + '.pending.json')
        self.owner_path = self.directory / 'owner.json'

    @contextmanager
    def lock(self, name='state', read=False):
        if read and not self.directory.exists():
            yield None
            return
        require(not self.directory.is_symlink(), 'unsafe workflow runtime directory')
        self.directory.mkdir(mode=0o700, exist_ok=True)
        path = self.directory / (name + '.lock')
        require(not path.is_symlink(), 'unsafe runtime lock')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if read else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkflowError('Workflow operation is busy; read context and retry later.', 'BUSY')
            yield fd
        finally:
            os.close(fd)

    def optional(self, path):
        return read_json(path) if path.exists() else None

    def pending(self):
        return self.optional(self.journal_path)

    def owner(self):
        return self.optional(self.owner_path)

    def claim(self):
        owner = self.owner()
        if owner and owner.get('task_id') != self.task.name:
            raise WorkflowError('Worktree owned by ' + str(owner.get('task_id')) + '; use its release command or a separate worktree.', 'WORKSPACE_BUSY')
        if not owner:
            write_json(self.owner_path, {'task_id': self.task.name, 'claimed_at': now()})

    def release(self):
        owner = self.owner()
        if owner and owner.get('task_id') == self.task.name:
            self.owner_path.unlink()

    @contextmanager
    def transaction(self, action, revision, resume_pending=False):
        pending = self.pending()
        if pending and not resume_pending:
            raise WorkflowError('Unfinished file operation; inspect context, then recover with a reason.', 'OPERATION_INCOMPLETE')
        require(not resume_pending or action == 'recover', 'only recovery may resume a pending journal')
        journal = pending or {'operation_id': uuid.uuid4().hex, 'task_id': self.task.name,
                              'action': action, 'revision': revision, 'started_at': now(), 'files': []}
        if not pending:
            write_json(self.journal_path, journal)

        def observe(path, data):
            path = Path(path)
            require(path.is_relative_to(self.task), 'journal write outside task')
            require(path.parent.resolve().is_relative_to(self.task.resolve()), 'unsafe journal parent')
            relative = path.relative_to(self.task).as_posix()
            item = next((f for f in journal['files'] if f['path'] == relative), None)
            if item is None:
                item = {'path': relative, 'before': encoded(path.read_bytes()) if path.exists() else None,
                        'after': []}
                journal['files'].append(item)
            item['after'].append(encoded(data))
            token = WRITE_OBSERVER.set(None)
            try:
                write_json(self.journal_path, journal)
            finally:
                WRITE_OBSERVER.reset(token)

        token = WRITE_OBSERVER.set(observe)
        try:
            yield journal
        except BaseException:
            # Validation/snapshot preparation errors with no writes are safely retryable.
            if not journal['files'] and not resume_pending:
                self.journal_path.unlink(missing_ok=True)
            raise
        else:
            if action == 'recover':
                self.complete_recovery(journal)
            else:
                self.journal_path.unlink(missing_ok=True)
        finally:
            WRITE_OBSERVER.reset(token)

    def rollback(self):
        journal = self.pending()
        if not journal:
            return None
        require(journal.get('task_id') == self.task.name, 'journal task mismatch')
        entries = journal.get('files')
        require(isinstance(entries, list), 'invalid journal files')
        resolved = []
        # Validate every path and value before changing any file. A human edit since
        # interruption must never be overwritten by recovery.
        for entry in entries:
            relative = Path(entry['path'])
            require(not relative.is_absolute() and '..' not in relative.parts, 'unsafe journal path')
            path = self.task / relative
            require(not path.is_symlink() and path.parent.resolve().is_relative_to(self.task.resolve()), 'unsafe recovery path')
            current = encoded(path.read_bytes()) if path.exists() else None
            if current not in [entry['before'], *entry['after']]:
                raise WorkflowError('Recovery would overwrite subsequent edits: ' + str(relative), 'RECOVERY_CONFLICT')
            resolved.append((path, decoded(entry['before'])))
        # Never republish an earlier manifest revision during rollback, even if
        # recovery itself crashes before its caller records the recovery event.
        manifest_path = self.task / 'manifest.json'
        current_revision = 0
        if manifest_path.exists():
            current_revision = read_json(manifest_path).get('revision', 0)
        for index, (path, before) in enumerate(resolved):
            if path == manifest_path and before is not None:
                restored = json.loads(before)
                restored['revision'] = max(current_revision, restored.get('revision', 0)) + 1
                before = (json.dumps(restored, ensure_ascii=False, indent=2) + '\n').encode()
                resolved[index] = (path, before)
                entry = next(f for f in entries if f['path'] == 'manifest.json')
                entry['after'].append(encoded(before))
                write_json(self.journal_path, journal)
        for path, before in reversed(resolved):
            write_bytes(path, before)
        # Keep the marker through the caller's evidence invalidation and recovery
        # event commit. Restoring an old PASS must never temporarily open the gate.
        return journal

    def complete_recovery(self, journal):
        """Called only after rollback AND invalidation/cleanup have completed."""
        archive = self.directory / (journal['operation_id'] + '.recovered.json')
        token = WRITE_OBSERVER.set(None)
        try:
            write_json(archive, {**journal, 'recovered_at': now()})
            self.journal_path.unlink(missing_ok=True)
        finally:
            WRITE_OBSERVER.reset(token)
