# Codex native multi-agent workflow — repository policy

## Scope and precedence
- This file applies to this repository only. Do not import sibling projects' AGENTS.md or project-specific prompts.
- Keep existing repository conventions and nested AGENTS.md when merging this template; do not overwrite them blindly.
- For multi-stage development work in this template, delegate to the specialist agents below. The main Codex thread is the Orchestrator, not a sixth coding worker.
- Applicable system/developer safety instructions and explicit user approvals take precedence over this repository workflow.

## Orchestrator: the only workflow coordinator
- For non-trivial work, run `python3 .workflow/scripts/workflow.py init --task <TASK_ID>` at the target Git root before delegation. Configure the manifest's permissions, required acceptance IDs and real check commands before advancing.
- Own stage transitions, assign work, check files and outputs, route blockers, stop at applicable approval gates. Reuse explicit authorization already provided in the session; never manufacture an approval record or ask again for an approved action.
- Do not write business code or tests on behalf of specialists. You may archive the reviewer's returned report verbatim without changing its conclusions.
- Use the native custom subagent names: `requirement`, `coding`, `testing`, `review`, `documentation`.
- Delegate narrow, evidence-seeking tasks. Supply task ID, approved contract version/hash, stage, allowed paths, exact outputs and stop conditions.
- Do not spawn more than two simultaneous children. Do not allow children to spawn further agents; report requests to Orchestrator. In a shared worktree, run coding and testing writes sequentially; read-only planning/review can overlap independent work.
- Agents share filesystem and root permissions. Treat prompts as behavioral policy, not filesystem security.

## Canonical workflow
1. `INTAKE` -> `CONTRACT_DRAFT`: requirement writes requirements.md, interface.yaml, acceptance.yaml, open_questions.md.
2. `CONTRACT_REVIEW`: human or designated owner approves public contracts. Only then run freeze_contract.py and commit/record the locked contract.
3. `IMPLEMENTING`: coding writes approved business files only; no changing tests or contracts.
4. `TESTING`: testing independently validates feature functionality and applicable performance criteria, reusing existing tests before adding cases; fix test defects in testing, product defects in coding.
5. `VERIFYING`: actual project-required build and tests, scope check, contract hash check must pass. Configure commands in the manifest and record them through `workflow.py run-checks`; missing prerequisites remain failures or blockers.
6. `REVIEWING`: review is read-only, evidence-based, and returns findings. Maximum two automatic repair loops; then escalate to human.
7. `DOCUMENTING`: documentation writes docs after review approval.
8. `FINAL_GATE`: rerun required checks, review final doc-only diff if material; `advance` generates the summary and enters `READY_FOR_MR`. This is local readiness, not publication. Prepare the reviewable MR content; perform external actions only within actual user authorization.

## Hard stop conditions
- Missing required public-contract approval, unauthorized paths, checksum drift, unclear published interface, or an unapproved expansion to dependencies, another repository, deployment, production access or hardware control. Stop on secret exposure or dangerous deletion.
- No silent changes to approved requirements, acceptance criteria, QoS or public API. Escalate with change_request instead.
- No `git reset --hard`, destructive cleanup, silent commit/push, or overwrite of other agents' changes.
- No tools executing physical robot motion without separate explicit authorization and safety preflight.
- Never report build/test success without command and exit-code evidence.
- Do not use the playwright-cli skill.

## Efficient context
- Read only changed/adjacent modules and contract artifacts; avoid full repository dump, unnecessary logs and repeated RAG reads.
- Record concise results and references, not all terminal output. Do not assume earlier chat memory is repository truth.
- For tiny typo-only tasks, the Orchestrator may classify the task as lightweight and complete a scoped edit with an appropriate check. Do not require five-agent overhead or a new public-contract approval for a change with no public-contract impact.
- Maintenance of this workflow template itself is internal tooling work when requested by the user. Record the scope and validation, but do not create fictional public-interface approvals to bootstrap its own scripts.

## Lean feature testing
- Retain tests tied to a feature's functional acceptance or measurable performance requirements. Boundary/error/compatibility cases belong in that feature's functional tests only when relevant; do not generate a fixed checklist of suites for every task.
- Reuse the project's test runner, fixtures and feature/module test files. A new task or AC does not require a new script. Prefer parameterized cases over copied scripts; split files only for a clear responsibility or maintainability reason.
- Performance acceptance specifies metric, workload, environment, measurement method and threshold before implementation. Extend an existing benchmark where possible. If performance is not applicable, record the reason in requirements; do not invent a benchmark or claim performance passed.
- Add regression assertions to existing feature tests when they prevent a concrete recurrence. Do not retain implementation-mirroring tests, duplicated acceptance checks, temporary debugging scripts or an extra test framework without a demonstrated need. Preserve necessary distinct assertions when consolidating tests.
- No new tests for documentation, prompt or other low-impact changes without behavior impact. Use a relevant existing check. Do not set test-count or coverage-percentage quotas, or require one file per function/task.
- Use the task's existing acceptance material and test_report.json for planning/results; no separate test_plan.md or per-case reports by default. Keep raw benchmark data and large logs outside source control, with retrievable evidence references; do not remove artifacts required by active gates or CI.
- `.workflow/tests/` tests this workflow tool itself. Keep it in the template's maintenance repository; do not copy it into consuming projects by default or add it to every feature's checks.

## Native-vs-enforced boundaries
- Custom role instructions do NOT establish a separate read-only sandbox; subagents inherit root approval/sandbox settings.
- Use `workflow.py check` and `workflow.py advance` for stage gates; they validate stage snapshots, reports and frozen contracts. Do not edit stage/history/snapshots to bypass a failure. The original `check_scope.py` remains available for explicit Git ranges; it is not a replacement for dirty-worktree stage snapshots.
- Run only reviewed, authorized commands from `manifest.checks`. The runner records argv, exit codes and logs; it is not a command sandbox and does not infer whether a command controls hardware or deploys.
- Required acceptance IDs must all map to successful checks. Optional hardware tests remain explicitly unrun. A successful process does not prove assertions are adequate; the testing and review agents must assess coverage.
- Follow `.workflow/ORCHESTRATION.md` for bounded rework, blocking/resumption and replacement tasks for public-contract changes. Do not overwrite a frozen lock.
- For preventive permission isolation, use separately launched sessions/containers with per-role mounts outside this native shared-workspace workflow.
