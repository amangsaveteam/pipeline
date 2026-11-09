# 轻量研发协作与可选工作流

默认由主 agent 完成实现、必要回归测试和文档同步，运行相关检查后简短交付。只有独立验证能带来收益时才调用一个合适的角色，不强制五阶段交接。

- [协作规则](AGENTS.md)：默认工作方式与边界。
- [提示词](.workflow/PROMPTS.md)：日常任务与按需审查。
- [完整工作流工具](.workflow/README.md)：仅明确选择时读取和启用。

## 安装

日常项目只需合并适用的协作规则；原生角色可按需复制 .codex/agents，保留目标项目既有 MCP 配置。不要默认安装 .workflow/scripts、templates、CI 示例或历史任务，更不要复制工具自测。

完整工具保留在本模板中，需要时再安装 scripts/templates 并按手册配置。旧工具仍按原有状态机运行；本次只取消默认强制使用，不将旧 CLI 改称轻量模式。

## 工具维护验证

仅修改工具运行行为时，在 pipeline 根运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s codex_agents_workflow/.workflow/tests -v
```

测试在临时仓库运行，保留的是工具自身的回归覆盖，不是业务项目的任务产物。提示词和说明调整只核对 TOML、引用及 git diff --check。
