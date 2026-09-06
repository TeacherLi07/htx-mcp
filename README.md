# HTX automation

本仓库现在包含两部分：

- `src/htx_mcp/`：基于 HTX 官方 REST API 的 Python MCP Server，使用 `uv` 管理环境。
- `scripts/`：原有的网页登录态 Playwright 辅助脚本，保持不变。

MCP Server 的安装、配置、工具清单和交易安全说明见
[MCP_README.md](MCP_README.md)。快速验证：

```powershell
uv sync --dev
uv run pytest -q
uv run htx-mcp
```
