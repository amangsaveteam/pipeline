# 工作流运行说明

以下命令均在安装完成的目标 Git 根运行。`workflow.py --task` 接受任务 ID；旧版 freeze/verify/check_scope 的 `--task` 接受任务目录路径，二者不要混用。所有新子命令均支持 `--root /absolute/target/repository`，默认当前目录且必须正好是 Git 根。

## 初始化与任务配置

```bash
python3 .workflow/scripts/workflow.py init --task TASK-0001
```

`init` 创建契约和报告模板，记录 `base_commit`，初始阶段为 `INTAKE`。重复 ID、目录穿越、目录符号链接和没有初始提交的仓库会被拒绝。它不创建分支、不提交文件。

在进入 `CONTRACT_DRAFT` 前编辑 `.workflow/tasks/TASK-0001/manifest.json`。保留生成的状态字段，只调整实际任务配置。例如一个已有 Python 项目的配置片段：

```json
{
  "required_acceptance_ids": ["AC-001", "AC-002"],
  "checks": [
    {
      "id": "unit",
      "argv": ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
      "cwd": ".",
      "timeout_seconds": 120
    }
  ],
  "permissions": {
    "coding": ["src/parser.py", ".workflow/tasks/TASK-0001/implementation_report.json"],
    "testing": ["tests/test_parser.py", ".workflow/tasks/TASK-0001/test_report.json"],
    "review": []
  }
}
```

这是**局部示例**，合并时保留 `requirement`、`documentation` 的允许路径及其余 manifest 字段。原模板带有 ROS2/C++ 路径示例，需按目标项目缩小或替换。报告文件也要在所属角色允许路径中。允许模式采用 Python `fnmatch`，其中 `*` 可能跨目录；优先列精确文件，避免把宽泛模式当成目录隔离。

`checks` 必须非空；用实际项目的构建、单元/集成测试替换示例。一个返回 0 的命令不等于足够覆盖，Testing 与 Review 必须核实每项验收真正被测试。`argv` 不隐式经过 shell；`cwd` 必须是仓库内目录，超时值为正数，默认 60 秒。执行环境继承当前进程，依赖安装和环境设置由项目负责。命令先由主线程审阅并确认在已有授权范围内；脚本无法判断任意命令是否会部署或驱动硬件。

进入阶段后不要修改权限、检查命令或必需验收列表以绕过门禁；策略变化会被检测。需要改变范围时使用新任务，保留原因与旧任务关联。

## 按阶段执行

这些命令是主线程的操作顺序，不是可以跳过 Agent 工作一次粘贴执行的脚本。每次 `advance` 只前进一阶段，`--to` 用来校验预期目标；进入阶段时自动保存快照，没有单独的 `snapshot` 命令。

**1. 需求草案。** 配置完成后进入需求阶段，让 requirement 填写四份材料，消除模板占位符并明确未决事项。

```bash
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to CONTRACT_DRAFT
# requirement 完成 requirements.md、interface.yaml、acceptance.yaml、open_questions.md
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to CONTRACT_REVIEW
```

**2. 批准与冻结。** 展示三份契约的具体内容，沿用已针对该版本给出的批准。仅当缺少所需批准时请求确认，并说明来自 `AGENTS.md` 的契约要求。`--approved-by` 必须填写真实可追溯的批准记录引用，不能把示例字符串当成实际批准。

```bash
python3 .workflow/scripts/freeze_contract.py --task .workflow/tasks/TASK-0001 --approved-by '<真实批准记录引用>'
python3 .workflow/scripts/verify_contract.py --task .workflow/tasks/TASK-0001
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to IMPLEMENTING
```

冻结拒绝空白批准引用、空文件、常见模板占位符和已有锁；锁对三份契约原始字节计算 SHA-256。保存真实批准证据及锁供评审，脚本本身不认证批准者。`CONTRACT_REVIEW` 阶段只允许新增锁，不能顺手改契约或任务策略。

**3. 实现与测试。** Coding 完成允许范围内的业务代码及实现报告；随后 Testing 独立编写测试，运行配置的检查并填写验收映射。

```bash
# coding 完成 implementation_report.json
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to TESTING
# testing 完成测试文件
python3 .workflow/scripts/workflow.py run-checks --task TASK-0001
# testing 根据真实结果完成 test_report.json
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to VERIFYING
```

**4. 验证与审查。** VERIFYING 不允许修改业务代码；新发现的缺陷通过 `rework` 路由。主线程核查证据后进入审查，将 Review 的返回结果原样存为 `review_report.json`。

```bash
python3 .workflow/scripts/workflow.py check --task TASK-0001
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to REVIEWING
# review 只读审查，主线程归档报告
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to DOCUMENTING
```

**5. 文档与最终交付。** Documentation 完成文档和报告后进入 FINAL_GATE；必须在进入该阶段后重新运行检查。此前的成功记录不能代替最终执行。

```bash
# documentation 完成文档和 documentation_report.json
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to FINAL_GATE
python3 .workflow/scripts/workflow.py run-checks --task TASK-0001
python3 .workflow/scripts/workflow.py check --task TASK-0001 --final
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to READY_FOR_MR
```

最后生成 `.workflow/tasks/TASK-0001/summary.md`。主线程检查最终文档差异；摘要可用于准备 MR，外部推送、MR、合并与部署只按实际授权执行。

## 报告与真实证据

| 文件 | 放行条件 | 责任方 |
|---|---|---|
| `implementation_report.json` | `status: completed`，`changed_files` 数组，非空 `requirements_implemented`，无 blockers | coding |
| `test_report.json` | `status: passed`，每个必需 AC 都是 PASS，且 check_id 对应真实成功检查 | testing |
| `review_report.json` | `decision: approved`，合法 findings 数组，无未解决 blocker/high | review 返回，主线程归档 |
| `documentation_report.json` | `status: completed`，`changed_files` 数组；填写引用证据与限制 | documentation |
| `verification.json`、`logs/` | runner 生成命令、工作目录、时刻、退出码、日志哈希和文件指纹 | run-checks |

测试报告的验收映射示例，**只有实际运行成功后才能填写 PASS**：

```json
{
  "status": "passed",
  "contract_version": "0.1",
  "checks": [
    {"acceptance_id": "AC-001", "check_id": "unit", "result": "PASS"},
    {"acceptance_id": "AC-002", "check_id": "unit", "result": "PASS"}
  ],
  "hardware_tests": "NOT_RUN_HARDWARE",
  "known_limitations": []
}
```

源码、命令配置或受检查文件变化会使证据失效；日志缺失或内容变化也会失败。命令执行期间改动输入不能记为成功；新一轮执行先将证据标为 `running`，中断后必须重跑。超时会终止该检查的进程组。对强制终止的运行，恢复前确认没有遗留检查进程。

`status` 只读展示阶段和未满足条件，即使显示 NOT READY 也可能退出 0；自动化应使用 `check` 的非零退出码，而非解析 `status`。

## 范围、返工与恢复

每个阶段与其起点快照比较，因此已有未提交/未跟踪文件只要没有在本阶段变化，就不会被误算为当前角色修改。新增、删除、重命名两端、内容、执行位及符号链接变化会参与检测。范围检查是事后检测，发现越权时先保留证据、恢复越权改动，再按正确角色重新分派；不要改快照来消除失败。

```bash
python3 .workflow/scripts/workflow.py status --task TASK-0001
python3 .workflow/scripts/workflow.py rework --task TASK-0001 --role coding --reason '说明产品缺陷与证据'
# 测试本身有缺陷时用 --role testing
python3 .workflow/scripts/workflow.py block --task TASK-0001 --reason '说明缺失环境或待决事项'
python3 .workflow/scripts/workflow.py resume --task TASK-0001 --reason '说明阻塞如何解决'
```

`rework` 可从 TESTING、VERIFYING、REVIEWING、DOCUMENTING、FINAL_GATE 退回 IMPLEMENTING 或 TESTING，归档并失效受影响的报告和检查证据。累计最多两轮，第三次请求会进入 BLOCKED；`resume` 不清零计数、不跳阶段，恢复后仍需正常门禁。完整路由见 [ORCHESTRATION.md](ORCHESTRATION.md)。

保留的旧入口适合明确的 Git 范围；工作区模式会计入所有未提交/未跟踪文件，不能代替新阶段快照：

```bash
python3 .workflow/scripts/check_scope.py --task .workflow/tasks/TASK-0001 --role coding --base '<阶段开始SHA>' --head '<阶段结束SHA>'
python3 .workflow/scripts/check_scope.py --task .workflow/tasks/TASK-0001 --role coding --base HEAD --include-worktree
```

## GitLab CI

合并 [gitlab-ci.example.yml](gitlab-ci.example.yml) 中的 job，不替换项目已有 `stages`。示例使用默认 `test` 阶段，可单独运行；若项目自定义阶段没有 `test`，将该 job 改到已有验证阶段。参见 [GitLab 官方阶段规则](https://docs.gitlab.com/ci/yaml/#stages)。显式设置 `WORKFLOW_TASK_ID` 为本次业务任务；提交任务 manifest、契约、快照、锁和角色报告供 CI 检查；日志和 verification 由 CI 本轮重新生成。缺少 ID、任务、锁或有效阶段都会失败，不能静默跳过。任务应处于 FINAL_GATE 或 READY_FOR_MR，示例先重跑项目真实检查，再执行最终门禁。

Runner 需提供 Git、Python 和所有项目依赖；示例镜像只作 Python 项目的起点。CI 接入、镜像与依赖配置应在任务初始化前完成。示例仅提供本地可审阅的配置，没有在远端 GitLab 执行，也不会调用 Codex 或监听 Issue Webhook。

## 检测边界

- 主会话和原生子 Agent 共享文件系统及权限；角色提示词和 allowlist 不提供预防性隔离。需要硬隔离时采用单独受控会话/容器。
- 当前自动化验证在 Linux 完成；超时使用 POSIX 进程组，Windows 未验证。
- 快照检查 Git 可见文件：未跟踪且被忽略的文件不会纳入，已跟踪文件仍纳入。外部依赖、环境变量与仓库外测试输入不在文件指纹保证范围内。Git 子模块目录会被拒绝，需要单独设计验证方案。
- 契约 YAML 按原文锁定，不做完整 YAML 语义或接口正确性验证。required_acceptance_ids 由主线程显式维护，需求与测试是否真正对应仍需审查。
- 审批引用、报告、快照和历史是协作记录，不是签名认证或抵抗恶意修改的证据系统。不要手工伪造状态、成功报告或审批身份。
- 可选硬件测试可保留 NOT_RUN_HARDWARE 并列明限制；列入 required_acceptance_ids 的必需硬件验收未执行就不能放行。ROS2、伺服或 EtherCAT 的实机动作仍需实际授权与相应现场条件。
