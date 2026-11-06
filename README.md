# Pipeline

可复用的 Codex 多 Agent 研发流程：需求、契约、实现、测试、审查、文档和 MR 交付准备。

从 [流程总览与安装说明](codex_agents_workflow/README.md) 开始；已有目标项目可直接使用 [启动提示词](codex_agents_workflow/.workflow/PROMPTS.md)。

当前 `codex_agents_workflow/` 是模板目录，需合并到目标 Git 仓库根目录后使用，不会自动配置当前父会话。

在本仓库根目录验证模板：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s codex_agents_workflow/.workflow/tests -v
```
