# HTX Official API MCP

这是一个基于 HTX 官方 REST API 的 Python MCP Server，使用 `uv` 管理环境。
它直接提供现货与 USDT 本位合约的行情、账户、订单、仓位和交易工具，不依赖
网页登录态或浏览器自动化。

详细的安装、配置、工具范围和交易安全说明见
[MCP_README.md](MCP_README.md)。

快速开始：

```powershell
uv sync --dev
Copy-Item .env.example .env
uv run --env-file .env htx-mcp
```

验证安装：

```powershell
uv run pytest -q
```
