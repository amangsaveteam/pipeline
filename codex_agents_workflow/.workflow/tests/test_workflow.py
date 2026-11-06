"""Black-box integration tests. All commits and commands run in temporary Git repos.

Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s .workflow/tests -v
Approval references below are synthetic fixtures, never real user approvals.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1]
TASK = "TASK-TEST"


class Project:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-test-")
        self.root = Path(self.temp.name)
        self.task = self.root / ".workflow/tasks" / TASK
        self.env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        for folder in ("scripts", "templates"):
            shutil.copytree(SOURCE / folder, self.root / ".workflow" / folder)
        (self.root / ".gitignore").write_text("__pycache__/\n*.py[cod]\n")
        (self.root / "README.md").write_text("Baseline documentation\n")
        (self.root / "private.txt").write_text("Do not change\n")
        self.git("init", "-q")
        self.git("config", "user.name", "Workflow Test")
        self.git("config", "user.email", "workflow-test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "Synthetic baseline")

    def close(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args],
                              env=self.env, text=True, capture_output=True, check=True).stdout.strip()

    def run(self, script, *args, ok=True):
        result = subprocess.run([sys.executable, str(self.root / ".workflow/scripts" / script), *args],
                                cwd=self.root, env=self.env, text=True, capture_output=True, timeout=15)
        if ok and result.returncode:
            raise AssertionError(f"{script} {args}: exit {result.returncode}\n{result.stdout}{result.stderr}")
        if not ok and result.returncode == 0:
            raise AssertionError(f"{script} {args}: unexpectedly passed\n{result.stdout}{result.stderr}")
        return result

    def cli(self, command, *args, ok=True):
        return self.run("workflow.py", command, "--task", TASK, "--root", str(self.root), *args, ok=ok)

    def read(self, name):
        return json.loads((self.task / name).read_text())

    def write(self, name, value):
        (self.task / name).write_text(json.dumps(value, indent=2) + "\n")

    def modify_manifest(self, **changes):
        value = self.read("manifest.json")
        value.update(changes)
        self.write("manifest.json", value)

    def check_definition(self, code="import runpy; assert runpy.run_path('src/business.py')['VALUE'] == 1; print('unit passed')", timeout=5):
        return {"id": "unit", "argv": [sys.executable, "-c", code], "cwd": ".", "timeout_seconds": timeout}

    def init(self, checks=None):
        self.cli("init")
        self.modify_manifest(checks=[self.check_definition()] if checks is None else checks)

    def draft(self):
        self.cli("advance", "--to", "CONTRACT_DRAFT")
        (self.task / "requirements.md").write_text("# Requirements\nREQ-001: Return the value one.\n")
        (self.task / "interface.yaml").write_text('version: "1.0"\ninterfaces: []\n')
        (self.task / "acceptance.yaml").write_text("acceptance:\n  - id: AC-001\n    condition: Return one\n")
        (self.task / "open_questions.md").write_text("No open questions.\n")

    def freeze(self, approval="synthetic-test-fixture", ok=True):
        return self.run("freeze_contract.py", "--task", str(self.task), "--approved-by", approval, ok=ok)

    def implementing(self, checks=None):
        self.init(checks)
        self.draft()
        self.cli("advance", "--to", "CONTRACT_REVIEW")
        self.freeze()
        self.cli("advance", "--to", "IMPLEMENTING")

    def complete_implementation(self):
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src/business.py").write_text("VALUE = 1\n")
        self.write("implementation_report.json", {
            "status": "completed", "changed_files": ["src/business.py"],
            "requirements_implemented": ["REQ-001"], "blockers": []})

    def testing(self, checks=None):
        self.implementing(checks)
        self.complete_implementation()
        self.cli("advance", "--to", "TESTING")

    def complete_testing(self):
        (self.root / "tests").mkdir(exist_ok=True)
        (self.root / "tests/test_business.py").write_text("# Synthetic test artifact\n")
        self.write("test_report.json", {
            "status": "passed", "checks": [{"acceptance_id": "AC-001", "result": "PASS", "check_id": "unit"}],
            "hardware_tests": "NOT_RUN_HARDWARE", "known_limitations": ["Synthetic local fixture"]})
        self.cli("run-checks")

    def reviewing(self):
        self.testing()
        self.complete_testing()
        self.cli("advance", "--to", "VERIFYING")
        self.cli("advance", "--to", "REVIEWING")

    def documenting(self):
        self.reviewing()
        self.write("review_report.json", {"decision": "approved", "findings": [], "uncertainties": []})
        self.cli("advance", "--to", "DOCUMENTING")

    def final_gate(self):
        self.documenting()
        (self.root / "README.md").write_text("The fixture now returns one.\n")
        self.write("documentation_report.json", {
            "status": "completed", "changed_files": ["README.md"], "known_limitations": []})
        self.cli("advance", "--to", "FINAL_GATE")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)

    def assert_stage(self, stage):
        self.assertEqual(self.p.read("manifest.json")["stage"], stage)

    def test_complete_delivery_records_commands_scopes_and_limitations(self):
        self.p.final_gate()
        # Earlier successful checks cannot certify the final code and docs.
        self.p.cli("advance", ok=False)
        self.assert_stage("FINAL_GATE")
        self.p.cli("run-checks")
        self.p.cli("advance", "--to", "READY_FOR_MR")
        self.p.cli("check", "--final")
        self.assert_stage("READY_FOR_MR")
        verification = self.p.read("verification.json")
        self.assertEqual(verification["checks"][0]["exit_code"], 0)
        self.assertIn("unit passed", (self.p.task / verification["checks"][0]["log"]).read_text())
        summary = (self.p.task / "summary.md").read_text()
        self.assertIn("READY_FOR_MR", summary)
        self.assertIn("exit 0", summary)
        self.assertIn("NOT_RUN_HARDWARE", summary)
        self.assertEqual(self.p.git("rev-list", "--count", "HEAD"), "1")
        self.assertEqual(self.p.git("remote"), "")

    def test_init_rejects_duplicate_invalid_task_and_nonroot(self):
        self.p.init()
        original = (self.p.task / "manifest.json").read_bytes()
        self.p.cli("init", ok=False)
        self.assertEqual(original, (self.p.task / "manifest.json").read_bytes())
        for name in ("../escape", "", "a/b", ".hidden"):
            with self.subTest(name=name):
                self.p.run("workflow.py", "init", "--root", str(self.p.root), "--task", name, ok=False)
        self.p.run("workflow.py", "init", "--root", str(self.p.root / ".workflow"), "--task", "OTHER", ok=False)

    def test_no_stage_skipping_and_status_is_readonly(self):
        self.p.init()
        before = (self.p.task / "manifest.json").read_bytes()
        self.p.cli("advance", "--to", "IMPLEMENTING", ok=False)
        self.p.cli("status")
        self.assertEqual(before, (self.p.task / "manifest.json").read_bytes())
        self.p.cli("check", "--final", ok=False)

    def test_freeze_rejects_placeholder_blank_approval_repeat_and_drift(self):
        self.p.init()
        self.p.freeze(ok=False)
        self.assertFalse((self.p.task / "contract.lock.json").exists())
        self.p.draft()
        self.p.cli("advance")
        self.p.freeze("   ", ok=False)
        self.p.cli("advance", ok=False)
        self.p.freeze()
        lock = (self.p.task / "contract.lock.json").read_bytes()
        self.p.freeze(ok=False)
        self.assertEqual(lock, (self.p.task / "contract.lock.json").read_bytes())
        self.p.run("verify_contract.py", "--task", str(self.p.task))
        (self.p.task / "requirements.md").write_text("Changed after approval\n")
        self.p.run("verify_contract.py", "--task", str(self.p.task), ok=False)
        self.p.cli("advance", ok=False)
        self.assert_stage("CONTRACT_REVIEW")

    def test_contract_review_rejects_policy_drift(self):
        self.p.init()
        self.p.draft()
        self.p.cli("advance")
        self.p.freeze()
        self.p.modify_manifest(required_acceptance_ids=["AC-001", "AC-002"])
        result = self.p.cli("advance", ok=False)
        self.assertIn("manifest policy changed", result.stderr)

    def test_historical_dirty_files_do_not_block_role(self):
        (self.p.root / "private.txt").write_text("Preexisting user changes\n")
        (self.p.root / "untracked-user.txt").write_text("Preexisting untracked work\n")
        self.p.implementing()
        self.p.complete_implementation()
        self.p.cli("advance")
        self.assert_stage("TESTING")

    def test_scope_rejects_unauthorized_add_modify_delete_and_rename(self):
        self.p.implementing()
        self.p.complete_implementation()
        private = self.p.root / "private.txt"
        original = private.read_text()
        for operation in ("add", "modify", "delete", "rename"):
            with self.subTest(operation=operation):
                other = self.p.root / "outside.txt"
                moved = self.p.root / "src/moved.txt"
                if operation == "add":
                    other.write_text("Unauthorized\n")
                elif operation == "modify":
                    private.write_text("Unauthorized\n")
                elif operation == "delete":
                    private.unlink()
                else:
                    private.rename(moved)
                result = self.p.cli("advance", ok=False)
                self.assertIn("scope violation", result.stderr)
                self.assert_stage("IMPLEMENTING")
                private.write_text(original)
                if other.exists():
                    other.unlink()
                if moved.exists():
                    moved.unlink()
        self.p.cli("advance")

    def test_scope_detects_symlink_change_without_reading_external_target(self):
        link = self.p.root / "private-link"
        link.symlink_to("/definitely/missing/outside-target-one")
        self.p.implementing()
        self.p.complete_implementation()
        link.unlink()
        link.symlink_to("/definitely/missing/outside-target-two")
        self.assertIn("private-link", self.p.cli("advance", ok=False).stderr)

    def test_symlink_task_directory_rejected(self):
        (self.p.root / ".workflow/tasks").symlink_to(self.p.root.parent)
        self.p.cli("init", ok=False)

    def test_review_and_verifying_are_readonly(self):
        self.p.testing()
        self.p.complete_testing()
        self.p.cli("advance")
        source = self.p.root / "src/business.py"
        original = source.read_text()
        source.write_text("VALUE = 2\n")
        self.assertIn("scope violation", self.p.cli("advance", ok=False).stderr)
        source.write_text(original)
        self.p.cli("advance")
        self.p.write("review_report.json", {"decision": "approved", "findings": []})
        source.write_text("VALUE = 3\n")
        self.assertIn("scope violation", self.p.cli("advance", ok=False).stderr)

    def test_command_failure_not_found_timeout_and_log_evidence(self):
        checks = [
            {"id": "fail", "argv": [sys.executable, "-c", "print('failure evidence'); raise SystemExit(7)"], "cwd": ".", "timeout_seconds": 5},
            {"id": "missing", "argv": ["/nonexistent-workflow-executable"], "cwd": ".", "timeout_seconds": 5},
            {"id": "slow", "argv": [sys.executable, "-c", "import time; time.sleep(5)"], "cwd": ".", "timeout_seconds": 0.05},
        ]
        self.p.testing(checks)
        self.p.cli("run-checks", ok=False)
        report = self.p.read("verification.json")
        self.assertEqual([r["result"] for r in report["checks"]], ["FAIL", "ERROR", "TIMEOUT"])
        self.assertEqual(report["checks"][0]["exit_code"], 7)
        self.assertIn("failure evidence", (self.p.task / report["checks"][0]["log"]).read_text())
        self.p.cli("advance", ok=False)

    def test_argv_is_not_implicitly_shell(self):
        check = self.p.check_definition()
        check["argv"] = [sys.executable, "-c", "import sys; print(sys.argv[1])", "$(touch SHELL_MARKER); echo bad"]
        self.p.testing([check])
        self.p.complete_testing()
        self.assertFalse((self.p.root / "SHELL_MARKER").exists())

    def test_empty_checks_fail_before_implementation(self):
        self.p.init(checks=[])
        self.p.draft()
        self.p.cli("advance")
        self.p.freeze()
        self.p.cli("advance", ok=False)
        self.assert_stage("CONTRACT_REVIEW")

    def test_missing_implementation_and_test_reports_fail(self):
        self.p.implementing()
        self.p.cli("advance", ok=False)
        self.p.complete_implementation()
        self.p.cli("advance")
        self.p.cli("run-checks")
        self.p.cli("advance", ok=False)
        self.assert_stage("TESTING")

    def test_missing_or_unrun_required_acceptance_rejected(self):
        self.p.testing()
        self.p.complete_testing()
        for rows in ([], [{"acceptance_id": "AC-001", "result": "NOT_RUN", "check_id": "unit"}],
                     [{"acceptance_id": "AC-001", "result": "PASS", "check_id": "invented"}]):
            with self.subTest(rows=rows):
                self.p.write("test_report.json", {"status": "passed", "checks": rows})
                self.p.cli("advance", ok=False)
                self.assert_stage("TESTING")

    def test_changed_inputs_and_modified_logs_invalidate_verification(self):
        self.p.testing()
        self.p.complete_testing()
        test_file = self.p.root / "tests/test_business.py"
        original = test_file.read_text()
        test_file.write_text("# Changed tests after successful verification\n")
        self.assertIn("stale", self.p.cli("advance", ok=False).stderr)
        test_file.write_text(original)
        report = self.p.read("verification.json")
        (self.p.task / report["checks"][0]["log"]).write_text("Invented log\n")
        self.assertIn("log changed", self.p.cli("advance", ok=False).stderr)

    def test_changed_command_configuration_cannot_reuse_pass(self):
        self.p.testing()
        self.p.complete_testing()
        self.p.modify_manifest(checks=[self.p.check_definition("raise SystemExit(3)")])
        self.p.cli("advance", ok=False)
        self.assert_stage("TESTING")

    def test_check_that_mutates_input_cannot_pass(self):
        check = self.p.check_definition("from pathlib import Path; Path('tests/generated.py').write_text('changed')")
        self.p.testing([check])
        (self.p.root / "tests").mkdir()
        self.p.cli("run-checks", ok=False)
        report = self.p.read("verification.json")
        self.assertEqual(report["checks"][0]["exit_code"], 0)
        self.assertFalse(report["inputs_unchanged"])
        self.assertEqual(report["status"], "failed")

    def test_review_requires_approval_and_no_unresolved_high_findings(self):
        self.p.reviewing()
        for report in ({"decision": "changes_requested", "findings": []},
                       {"decision": "approved", "findings": [{"severity": "high", "resolved": False}]},
                       {"decision": "approved", "findings": [{"severity": "HIGH"}]}):
            with self.subTest(report=report):
                self.p.write("review_report.json", report)
                self.p.cli("advance", ok=False)
                self.assert_stage("REVIEWING")

    def test_documentation_completion_required(self):
        self.p.documenting()
        self.p.cli("advance", ok=False)
        self.assert_stage("DOCUMENTING")

    def test_two_rework_rounds_invalidate_reports_and_third_blocks(self):
        self.p.reviewing()
        self.p.cli("rework", "--role", "coding", "--reason", "Product defect")
        self.assert_stage("IMPLEMENTING")
        self.assertEqual(self.p.read("verification.json")["status"], "invalidated")
        self.assertEqual(self.p.read("implementation_report.json")["status"], "in_progress")
        self.assertEqual(self.p.read("test_report.json")["status"], "not_run")
        self.assertEqual(self.p.read("review_report.json")["decision"], "pending")
        self.p.complete_implementation()
        self.p.cli("advance")
        self.p.cli("rework", "--role", "testing", "--reason", "Test defect")
        self.assert_stage("TESTING")
        self.assertEqual(self.p.read("manifest.json")["rework_rounds"], 2)
        self.assertEqual(self.p.read("implementation_report.json")["status"], "completed")
        self.p.cli("rework", "--role", "testing", "--reason", "Third attempt", ok=False)
        self.assert_stage("BLOCKED")
        self.p.cli("resume", "--reason", "Owner will inspect")
        self.assert_stage("TESTING")
        self.assertEqual(self.p.read("manifest.json")["rework_rounds"], 2)
        self.p.cli("advance", ok=False)

    def test_block_resume_preserves_stage_and_scope(self):
        self.p.implementing()
        self.p.cli("block", "--reason", "Waiting for dependency")
        self.assert_stage("BLOCKED")
        self.p.cli("advance", ok=False)
        self.p.cli("resume", "--reason", "   ", ok=False)
        (self.p.root / "private.txt").write_text("Unauthorized while blocked\n")
        self.p.cli("resume", "--reason", "Dependency ready", ok=False)
        self.assert_stage("BLOCKED")
        (self.p.root / "private.txt").write_text("Do not change\n")
        self.p.cli("resume", "--reason", "Dependency ready")
        self.assert_stage("IMPLEMENTING")

    def test_final_check_fails_closed_missing_task_or_lock(self):
        self.p.cli("check", "--final", ok=False)
        self.p.final_gate()
        self.p.cli("run-checks")
        self.p.cli("advance")
        (self.p.task / "contract.lock.json").unlink()
        self.p.cli("check", "--final", ok=False)

    def test_snapshot_failure_does_not_advance_stage(self):
        nested = self.p.root / "nested"
        nested.mkdir()
        (nested / "child.txt").write_text("Tracked nested file\n")
        self.p.git("add", ".")
        self.p.git("commit", "-qm", "Synthetic nested path")
        self.p.init()
        # A cached nested path whose parent has become an external symlink makes
        # snapshot capture fail after the stage's entry prerequisites were met.
        (nested / "child.txt").unlink()
        nested.rmdir()
        nested.symlink_to(self.p.root.parent, target_is_directory=True)
        self.p.cli("advance", ok=False)
        self.assert_stage("INTAKE")
        nested.unlink()
        nested.mkdir()
        (nested / "child.txt").write_text("Tracked nested file\n")
        self.p.cli("advance")
        self.assert_stage("CONTRACT_DRAFT")

    def test_interrupted_recheck_invalidates_previous_pass(self):
        code = (
            "from pathlib import Path; import os, time; "
            "slow = Path('.git/slow-check').exists(); "
            "Path('.git/check-pid').write_text(str(os.getpid())) if slow else None; "
            "time.sleep(10) if slow else print('quick successful run')"
        )
        self.p.testing([self.p.check_definition(code, timeout=15)])
        self.p.complete_testing()
        self.assertEqual(self.p.read("verification.json")["status"], "passed")
        (self.p.root / ".git/slow-check").touch()
        command = [sys.executable, str(self.p.root / ".workflow/scripts/workflow.py"),
                   "run-checks", "--task", TASK, "--root", str(self.p.root)]
        process = subprocess.Popen(command, cwd=self.p.root, env=self.p.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child_pid = self.p.root / ".git/check-pid"
        try:
            deadline = time.monotonic() + 3
            while not child_pid.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(child_pid.exists(), "second real check did not start")
            process.terminate()
            process.communicate(timeout=3)
            self.assertNotEqual(self.p.read("verification.json")["status"], "passed")
            self.p.cli("advance", ok=False)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
            if child_pid.exists():
                try:
                    os.killpg(int(child_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_committing_deleted_file_preserves_verified_content(self):
        self.p.init()
        manifest = self.p.read("manifest.json")
        manifest["permissions"]["coding"].append("private.txt")
        self.p.write("manifest.json", manifest)
        self.p.draft()
        self.p.cli("advance")
        self.p.freeze()
        self.p.cli("advance")
        (self.p.root / "private.txt").unlink()
        self.p.complete_implementation()
        self.p.cli("advance")
        self.p.complete_testing()
        self.p.cli("advance")
        self.p.git("add", "-A")
        self.p.git("commit", "-qm", "Synthetic validated deletion")
        self.p.cli("advance")
        self.assert_stage("REVIEWING")

    def test_legacy_scope_rejects_rename_source_outside_allowed_paths(self):
        self.p.init()
        # Commit task fixtures to isolate just the following rename in the Git range.
        self.p.git("add", ".")
        self.p.git("commit", "-qm", "Synthetic task")
        base = self.p.git("rev-parse", "HEAD")
        (self.p.root / "src").mkdir()
        (self.p.root / "private.txt").rename(self.p.root / "src/moved.txt")
        self.p.git("add", "-A")
        self.p.git("commit", "-qm", "Synthetic rename")
        result = self.p.run("check_scope.py", "--task", str(self.p.task), "--role", "coding",
                            "--base", base, "--head", "HEAD", ok=False)
        self.assertIn("DENY private.txt", result.stdout)


if __name__ == "__main__":
    unittest.main()
