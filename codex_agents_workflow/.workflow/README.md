# 工作流运行说明

以下命令均在安装完成的目标 Git 根运行。`workflow.py --task` 接受任务 ID；旧版 freeze/verify/check_scope 的 `--task` 接受任务目录路径，二者不要混用。所有新子命令均支持 `--root /absolute/target/repository`，默认当前目录且必须正好是 Git 根。

## 统一接口与结果

CLI 只解析参数和展示结果，业务入口为 `workflow_service.execute(command, root=..., task_id=..., expected_revision=...)`，返回可 JSON 序列化的对象，不输出终端文本。未来 MCP 可复用此入口；当前没有 MCP 服务。

所有子命令支持 `--format text|json`，默认 `text`。`json` 模式的正常执行 stdout 只包含一个结果对象；命令参数错误仍由 argparse 通过 stderr 报告。结果包含 `ok`、`code`、`message`、`task_id`、`stage`、`revision`、`state_changed`，查询还包含 `gate.passed` 与 blockers。业务操作 `ok=false` 时 CLI 退出 1；参数错误退出 2。`status/context` 查询成功即为 `ok=true`，不表示阶段门禁通过；自动化验收使用 `check` 的退出码或 `gate.passed`。

状态修改支持 `--expect-revision`（`init` 除外）。旧调用可省略以兼容，但新自动化和 Agent 分派必须携带刚读取的版本；收到 `REVISION_CONFLICT` 后重新获取上下文，不盲目重试。旧 manifest 缺少 revision 时按 0 读取，下一次成功修改写入版本；只读查询不迁移文件。一次检查的开始和结果提交都会更新版本，因此不要预测下次版本号。

```bash
python3 .workflow/scripts/workflow.py context --task TASK-0001 --format json
# 12 仅示例；使用上一步真实返回的 revision。
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to VERIFYING --expect-revision 12 --format json
```

下面阶段命令为便于人工阅读省略版本参数；自动化执行每次修改前必须重新读取实际版本，并补上 `--expect-revision`。

## 初始化与任务配置

```bash
python3 .workflow/scripts/workflow.py init --task TASK-0001
```

`init` 创建契约和报告模板，记录 `base_commit`，初始阶段为 `INTAKE`，成功操作后返回当前 `revision` 并占用工作区。重复 ID、目录穿越、目录符号链接和没有初始提交的仓库会被拒绝。它不创建分支、不提交文件。

在进入 `CONTRACT_DRAFT` 前编辑 `.workflow/tasks/TASK-0001/manifest.json`。保留生成的状态字段，只调整实际任务配置。例如一个已有 Python 项目的配置片段：

```json
{
  "required_acceptance_ids": ["AC-001", "AC-002"],
  "checks": [
    {
      "id": "functional",
      "argv": ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_parser.py", "-v"],
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

`checks` 必须非空；配置该功能及受影响已有功能的必要测试、适用的性能检查与项目必需构建命令。示例仅选择已有 `test_parser.py`，不要求新建文件或无差别执行全仓测试；共用组件变更则选择相应的已有回归用例。单元/集成是可复用的验证方式，不要求为同一行为各建一套脚本。一个返回 0 的命令不等于足够覆盖，Testing 与 Review 必须核实每项验收真正被测试。`argv` 不隐式经过 shell；`cwd` 必须是仓库内目录，超时值为正数，默认 60 秒。执行环境继承当前进程，依赖安装和环境设置由项目负责。命令先由主线程审阅并确认在已有授权范围内；脚本无法判断任意命令是否会部署或驱动硬件。

性能验收在需求中记录指标、负载、环境、测量方法和阈值，并复用现有基准入口；检查命令应在超过阈值时返回非零，实测值记录在日志、验收映射引用同一 check_id。没有适用的性能要求时说明原因，不强制生成 benchmark。功能测试优先参数化、合并重复场景；不为每个任务新建测试目录、框架或 test_plan.md。

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
python3 .workflow/scripts/workflow.py freeze --task TASK-0001 --approved-by '<真实批准记录引用>'
python3 .workflow/scripts/verify_contract.py --task .workflow/tasks/TASK-0001
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to IMPLEMENTING
```

`freeze` 仅接受 CONTRACT_REVIEW。旧 `freeze_contract.py` 路径入口仍可用，但也经过统一服务与并发保护，任务必须位于 Git 根的 `.workflow/tasks/<ID>`。冻结拒绝空白批准引用、空文件、常见模板占位符和已有锁；锁对三份契约原始字节计算 SHA-256。保存真实批准证据及锁供评审，脚本本身不认证批准者。`CONTRACT_REVIEW` 阶段只允许新增锁，不能顺手改契约或任务策略。

**3. 实现与测试。** Coding 完成允许范围内的业务代码及实现报告；随后 Testing 独立核查验收，复用或补充必要的功能/性能测试，运行配置的检查并填写验收映射。已有测试足够时无需修改测试文件。

```bash
# coding 完成 implementation_report.json
python3 .workflow/scripts/workflow.py advance --task TASK-0001 --to TESTING
# testing 确认已有测试足够，或补齐必要用例
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
    {"acceptance_id": "AC-001", "check_id": "functional", "result": "PASS"},
    {"acceptance_id": "AC-002", "check_id": "functional", "result": "PASS"}
  ],
  "hardware_tests": "NOT_RUN_HARDWARE",
  "known_limitations": []
}
```

源码、命令配置或受检查文件变化会使证据失效；日志缺失或内容变化也会失败。检查前后文件指纹不同不能记为成功；执行期间仍须协调写入，前后指纹不证明中间从未改动。新一轮执行先将证据标为 `running`，中断后必须重跑。超时会终止该检查的进程组。对强制终止的运行，恢复前确认没有遗留检查进程。

`status`、`context`、`check` 和实际推进复用门禁逻辑。当前门禁诊断返回首先遇到的失败，不承诺一次列出所有缺口；修复后重新查询。

报告引用已有用例与日志，不复制源码或生成逐用例报告。大体积日志、性能原始数据放在可追溯的 CI artifacts 等证据存储中，按项目保留期管理；活动任务和当前 MR 所需的本地日志、锁、快照及报告在门禁/CI完成前必须保留，不为瘦身清掉检查依赖。

## 并发与中断恢复

状态锁、执行锁、工作区 owner 和操作日志位于当前 worktree 的 Git 私有目录 `workflow-state/`，通过 `git rev-parse --absolute-git-dir` 定位，不放入源码快照或部署产物。不要手工删除运行记录来解锁。每个 worktree 同时只允许一个修改型任务占用；同一任务的修改串行，锁冲突立即返回 `BUSY`，其他任务返回 `WORKSPACE_BUSY`。任务锁保护读取、版本校验和写入，但不能阻止编辑器或 agent 直接改源码。

`run-checks` 登记运行 ID、输入指纹并失效旧证据后，释放短状态锁执行命令，查询仍可运行；提交结果时再次核对版本和运行 ID。执行锁贯穿检查并传给检查子进程，因此父进程死亡但子进程仍在时，冲突修改/恢复仍被拒绝。锁短暂被写入占用时查询也可能返回 BUSY，稍后重新读取。

多文件修改先记录原内容和拟写入内容，随后逐个原子替换。未完成操作返回 `OPERATION_INCOMPLETE`，后续修改失败关闭。`context` 汇总 pending 操作、运行状态、契约、报告、有效证据、验收缺口、变更路径和下一步建议；相关材料缺失/损坏显示不可用，不生成通过结论。它不推进阶段、执行检查或改写任务材料，也不生成可能过期的摘要文件。

```bash
python3 .workflow/scripts/workflow.py context --task TASK-0001 --format json
# 已核对中断操作且确认检查进程结束后执行；12 仅示例。
python3 .workflow/scripts/workflow.py recover --task TASK-0001 --reason '记录中断原因和已核对的进程状态' --expect-revision 12 --format json
```

`recover` 仅回滚日志记录的工作流文件，不撤销业务源码、Git 操作或已执行的外部命令，也不重放检查。回滚前逐项比较当前内容与记录，发现后续编辑返回 `RECOVERY_CONFLICT`，由负责人核对而不覆盖。pending 标记覆盖整个回滚、证据失效和恢复提交过程，即使恢复再次中断也不放行旧成功证据。恢复保留单调版本、归档操作记录，将中断验证标为失效；随后重读 context，按正常门禁继续并重跑检查。部分初始化在 manifest 尚未创建时可直接调用 recover（此时没有可用版本），清理其已记录文件后重新 init；若仍有未记录的临时文件或其他残留，则保留 pending 并返回 RECOVERY_CONFLICT 和 remaining_paths，先核对残留归属、只处理已确认的临时产物，再重试 recover，不能直接重新 init。`resume` 只恢复 BLOCKED 阶段，与修复文件操作中断的 `recover` 不同。

工作区占用跨命令保留，进入 READY_FOR_MR 时自动释放。必要的显式交接仅允许在 INTAKE、BLOCKED 或 READY_FOR_MR，且不能有活动检查或未完成操作：

```bash
python3 .workflow/scripts/workflow.py release --task TASK-0001 --reason '说明交接安排' --expect-revision 12
```

释放不会重置快照、抹去改动或使多个任务的源码变化互不影响。不同任务需要并行开发时使用独立 worktree；后续恢复旧任务仍需通过原范围检查。owner 不承担身份认证，运行元数据不跨克隆/机器同步。

## 范围、返工与恢复

每个阶段与其起点快照比较，因此已有未提交/未跟踪文件只要没有在本阶段变化，就不会被误算为当前角色修改。新增、删除、重命名两端、内容、执行位及符号链接变化会参与检测。范围检查是事后检测，发现越权时先保留证据、恢复越权改动，再按正确角色重新分派；不要改快照来消除失败。

```bash
python3 .workflow/scripts/workflow.py context --task TASK-0001 --format json
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
- 当前自动化验证在 Linux 完成；锁使用 POSIX flock，超时使用 POSIX 进程组，Windows 和网络文件系统语义未验证。
- 文件通过同目录唯一临时文件、文件 fsync 与原子替换保存；目录未做完整持久化同步。这是受控进程并发和可检测中断恢复，不是数据库 ACID、断电耐久性保证或抵抗恶意修改的事务系统。
- 快照检查 Git 可见文件：未跟踪且被忽略的文件不会纳入，已跟踪文件仍纳入。外部依赖、环境变量与仓库外测试输入不在文件指纹保证范围内。Git 子模块目录会被拒绝，需要单独设计验证方案。
- 契约 YAML 按原文锁定，不做完整 YAML 语义或接口正确性验证。required_acceptance_ids 由主线程显式维护，需求与测试是否真正对应仍需审查。
- 审批引用、报告、快照和历史是协作记录，不是签名认证或抵抗恶意修改的证据系统。不要手工伪造状态、成功报告或审批身份。
- 可选硬件测试可保留 NOT_RUN_HARDWARE 并列明限制；列入 required_acceptance_ids 的必需硬件验收未执行就不能放行。ROS2、伺服或 EtherCAT 的实机动作仍需实际授权与相应现场条件。
