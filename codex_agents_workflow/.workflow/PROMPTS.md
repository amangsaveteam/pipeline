# 给 Codex 主线程的提示词

在安装好模板的目标 Git 根开启新会话后使用。替换任务 ID 和需求；已有任务先读状态，不再次 init。下列批准示例只能由有权批准的人在看过具体材料后发送。

## 启动完整流程

```text
请担任这个项目的 Orchestrator，遵守 AGENTS.md 和 .workflow/ORCHESTRATION.md。
任务：<具体需求、预期行为和约束>
任务 ID：TASK-0001。

先检查仓库状态与已有授权和工作区占用。用 workflow.py init 创建任务，在 INTAKE 配置精确的角色允许路径、required_acceptance_ids 和项目真实构建/测试命令；不要提交我的全部现有改动。进入 CONTRACT_DRAFT 后只分派 requirement，交付需求、接口、验收标准和未决事项。

向我展示需要批准的具体公开契约；同一版本已有批准直接沿用，否则说明需要批准的依据。冻结后依次分派 coding、testing、review、documentation，每个阶段使用 CLI 门禁，不手改状态。每次修改前读取 context --format json，携带真实 revision 调用 --expect-revision；遇版本冲突重新读取，不盲目重试。共享工作区中代码和测试写入串行，最多同时两个子 Agent，禁止子 Agent 再委派。

测试只长期保留面向开发功能的功能验收和性能测试，先复用现有入口/用例，再补明确缺口；不按任务新建脚本或测试框架。需求中明确适用的性能指标、负载、环境、测量方法和阈值，不适用时说明原因。不要机械铺满测试类别；无行为变更不新增测试，不另建test_plan或逐用例报告，精简不能删必要断言或掩盖失败。

遇产品缺陷交回 coding，测试缺陷交回 testing，最多两轮自动返工。缺环境或未解决决策就记录 BLOCKED。最终重跑检查，生成 summary.md，并给出变更、测试、限制和下一步。外部推送、MR、合并、部署及硬件动作按已有实际授权执行；尚无授权时先准备可评审结果，再请求相应确认。
```

## 批准一个已展示的契约版本

```text
我批准 TASK-0001 当前展示的 requirements.md、interface.yaml 和 acceptance.yaml 这一具体版本。
批准记录引用：<本次批准消息或正式审批记录引用>。
请用真实引用冻结契约并验证哈希，推进 IMPLEMENTING，再按允许路径分派 coding。继续后续测试、审查、文档与最终检查；不得把本次契约批准解释为新增的硬件、部署或外部发布授权。
```

## 从已有任务继续

```text
继续 TASK-0001。先执行 workflow.py context --task TASK-0001 --format json，按其引用读取必要契约、报告和日志。
不要重新 init，不要覆盖锁、快照或历史。确认是否有活动/遗留检查进程、未完成操作以及未归属的文件变化，再根据当前阶段继续。查询成功不是门禁通过，用 check 的真实退出码判断。存在中断操作时先核对原因和进程，使用 recover --reason；不重放外部命令、不覆盖后续编辑。存在其他任务占用时用独立 worktree，或由负责人按 release 条件交接，不手删锁或 owner。每次修改携带当前 --expect-revision，冲突后重新获取 context。
已有批准继续有效；源文件或检查配置变动后的旧成功证据不能复用。BLOCKED 时先说明原因是否已经解决，仅恢复原阶段，不跳阶段或重置返工计数。
```

## 修复测试发现的问题

```text
处理 TASK-0001 的以下失败：<失败命令、日志与复现条件>。
先判断 PRODUCT_DEFECT / TEST_DEFECT / ENVIRONMENT_BLOCKED。产品问题用 rework --role coding，测试自身问题用 --role testing；环境问题先 block。只分派正确角色，不弱化断言、不修改验收标准。修复后重跑受影响检查并继续后继阶段；超过两轮时记录阻塞并提交需要负责人决定的具体方案。
```

## 完成文档与交付准备

```text
对 TASK-0001，确认 Review 已 approved 后分派 documentation，限制在文档允许路径。
核查文档与实现/真实证据一致，进入 FINAL_GATE 后重新 run-checks，执行 check --final，再 advance 到 READY_FOR_MR。展示 summary.md 的重点与未验证项；若下一步外部动作尚无授权，先给出具体 MR/发布内容供审批，不重复询问已有授权。
```

## 修改公开契约

```text
TASK-0001 的公开契约需要变更：<拟修改内容和原因>。
保留旧任务与 contract.lock.json，列出接口兼容、实现、测试和文档影响。创建关联的新任务 TASK-0001-v2，重新确认改变的契约内容并取得实际批准，再走完整后续流程。不要覆写旧锁或将审批引用当作身份认证。
```
