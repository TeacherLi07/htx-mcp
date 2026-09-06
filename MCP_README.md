# HTX Official API MCP

这是一个直接调用 HTX 官方 REST API 的 MCP Server，不依赖网页登录态或
Playwright。当前覆盖现货与 USDT 本位合约的行情、账户、订单、仓位、策略单、
杠杆和历史数据。

## 使用 uv

要求 Python 3.10+ 和 `uv`：

```powershell
uv sync --dev
Copy-Item .env.example .env
```

MCP 主机启动时需要把环境变量传给进程。可以直接在 PowerShell 中设置：

```powershell
$env:HTX_API_KEY = "your-access-key"
$env:HTX_API_SECRET = "your-secret-key"
$env:HTX_ENABLE_TRADING = "false"
uv run htx-mcp
```

API 密钥建议只授予 Read；只有确实需要交易时才授予 Trade，并限制 IP。密钥
不会写入 MCP 响应或日志。

现货与 USDT 本位合约使用独立的官方 Host：默认分别是
`api.huobi.pro` 与 `api.hbdm.com`，可通过 `HTX_API_BASE_URL` 和
`HTX_FUTURES_API_BASE_URL` 覆盖。

## MCP 主机配置

stdio 是默认传输方式。Windows 上可以在 Claude Desktop、Cursor 或其他 MCP
客户端中使用绝对路径：

```json
{
  "mcpServers": {
    "htx": {
      "command": "uv",
      "args": ["--directory", "C:/Data/programming/htxauto", "run", "htx-mcp"],
      "env": {
        "HTX_API_KEY": "your-access-key",
        "HTX_API_SECRET": "your-secret-key",
        "HTX_ENABLE_TRADING": "false"
      }
    }
  }
}
```

服务端只向 stderr 写日志，stdout 保留给 MCP JSON-RPC，避免破坏 stdio 协议。
也可以设置 `MCP_TRANSPORT=streamable-http` 或 `sse`，但生产环境应自行配置
认证、反向代理和网络访问控制。

## 工具范围

- `spot_get_*`：现货 ticker、全市场 ticker、K 线、深度、成交、币种/交易对元数据、市场状态、时间。
- `spot_get_accounts`、`spot_get_account_balance`、`spot_get_open_orders`、`spot_get_order*`、`spot_get_match_results`：现货账户和订单查询。
- `spot_place_order`、`spot_cancel_*`、`spot_dead_man_switch`：现货下单、撤单与断线保护。
- `futures_get_*`：USDT 本位合约信息、ticker、批量 ticker、深度、K 线、成交、指数、价格限制、持仓量、资金费率和系统状态。
- `futures_get_account_info`、`futures_get_positions`、`futures_get_open_orders`、`futures_get_order_*`、`futures_get_history_orders`、`futures_get_match_results`：合约账户、仓位和订单查询，支持 isolated/cross。
- `futures_place_order`、`futures_place_batch_orders`、`futures_cancel_*`、`futures_switch_leverage`、`futures_lightning_close_position`：合约交易。
- `futures_place_trigger_order`、`futures_get_trigger_*`、`futures_cancel_trigger_*`、`futures_switch_position_mode`：合约策略单和持仓模式。
- `htx://configuration`：不包含密钥的服务能力/配置资源；`trade_preflight`：交易前检查提示模板。

## 交易安全

所有写工具都有两层保护：

1. 调用参数必须显式传 `confirm=true`；默认 `false` 时只返回完整 dry-run 请求。
2. 进程必须显式设置 `HTX_ENABLE_TRADING=true`，否则即使传了 `confirm=true` 也只返回预览。

下单工具只返回 HTX 的受理结果，不把“已受理”误报成“已成交”。成交、撤单和
平仓后应再次调用相应的订单/仓位查询工具确认最终状态。合约下单支持直接传入
HTX 官方的 TP/SL 字段，可在同一个开仓请求中附加交易所侧保护单。

## 开发与测试

```powershell
uv sync --dev
uv run python -m compileall -q src
uv run pytest -q
```

测试使用 mock HTTP，不会触碰真实账户。默认配置没有 API 密钥和交易权限，首次
接入时建议先只使用公开行情工具。

## 官方文档

- [HTX Open Platform API](https://www.htx.com/en-us/opend/newApiPages/)
- [HTX Spot API Reference](https://huobiapi.github.io/docs/spot/v1/en/)
- [HTX USDT-margined Contracts API Reference](https://huobiapi.github.io/docs/usdt_swap/v1/en/)
- [MCP Build a server](https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server)
