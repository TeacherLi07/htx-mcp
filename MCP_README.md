# HTX Official API MCP

这是一个直接调用 HTX 官方 REST API 的 Python MCP Server。它不依赖网页登录态、
Cookie 或浏览器自动化，覆盖现货与 USDT 本位合约的行情、账户、订单、仓位、
策略单、杠杆和历史数据。

服务通过 MCP 暴露三类能力：

- Tools：行情查询、账户查询、下单、撤单、仓位和策略单操作。
- Resource：`htx://configuration`，返回不含密钥的运行配置和安全策略。
- Prompt：`trade_preflight`，生成交易前检查清单。

## 安装

要求 Python 3.10+ 和 `uv`：

```powershell
uv sync --dev
Copy-Item .env.example .env
```

`.env` 不会被 MCP Server 自己解析；使用 `uv run --env-file .env` 启动，或由
MCP 客户端通过 `env` 字段传入同样的变量：

```powershell
uv run --env-file .env htx-mcp
```

## 配置

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `HTX_API_KEY` | 空 | HTX Access Key；私有接口必需 |
| `HTX_API_SECRET` | 空 | HTX Secret Key；仅用于本地签名 |
| `HTX_API_BASE_URL` | `https://api.huobi.pro` | 现货 API Host |
| `HTX_FUTURES_API_BASE_URL` | `https://api.hbdm.com` | USDT 本位合约 API Host |
| `HTX_SPOT_ACCOUNT_ID` | 空 | 默认现货账户 ID；可先调用 `spot_get_accounts` 获取 |
| `HTX_TIMEOUT_SECONDS` | `20` | 单次 HTTP 请求超时 |
| `HTX_ENABLE_TRADING` | `false` | 是否允许写接口真正发往 HTX |
| `MCP_TRANSPORT` | `stdio` | `stdio`、`sse` 或 `streamable-http` |
| `HTX_LOG_LEVEL` | `INFO` | stderr 日志级别 |

现货与合约使用独立 Host。除非部署环境明确要求其他官方域名，否则保持默认值。
API 密钥建议只授予 Read；只有确实需要交易时才授予 Trade，并绑定 IP。Secret
不会写入 MCP 响应或日志。

## MCP 客户端接入

stdio 是桌面 MCP 客户端最简单的传输方式。Windows 上可使用绝对路径：

```json
{
  "mcpServers": {
    "htx": {
      "command": "uv",
      "args": [
        "--directory",
        "F:/htxauto",
        "run",
        "htx-mcp"
      ],
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
如果使用 `sse` 或 `streamable-http`，必须在外部配置认证、反向代理和网络访问
控制；不要将带 Trade 权限的服务直接暴露到公网。

## 工具范围

### 现货

- `spot_get_ticker`、`spot_get_tickers`、`spot_get_klines`、`spot_get_depth`、
  `spot_get_recent_trades`：实时行情、全市场快照、K 线、深度和成交。
- `spot_get_symbols`、`spot_get_currencies`、`spot_get_currency_reference`、
  `spot_get_market_status`、`spot_get_server_timestamp`：交易规则和公共元数据。
- `spot_get_accounts`、`spot_get_account_balance`、`spot_get_open_orders`、
  `spot_get_order*`、`spot_get_match_results`、`spot_get_history_orders`：账户和订单查询。
- `spot_place_order`、`spot_cancel_*`、`spot_dead_man_switch`：现货下单、撤单和断线保护。

### USDT 本位合约

- `futures_get_contracts`、`futures_get_ticker`、`futures_get_tickers`、
  `futures_get_depth`、`futures_get_klines`、`futures_get_recent_trades`：合约行情和规则。
- `futures_get_index`、`futures_get_price_limit`、`futures_get_open_interest`、
  `futures_get_funding_rate`、`futures_get_historical_funding_rate`、
  `futures_get_risk_info`、`futures_get_liquidation_orders`：衍生品市场数据。
- `futures_get_account_info`、`futures_get_positions`、`futures_get_open_orders`、
  `futures_get_order_*`、`futures_get_history_orders`、`futures_get_match_results`：
  合约账户、仓位、订单和成交查询，支持 `isolated`/`cross`。
- `futures_place_order`、`futures_place_batch_orders`、`futures_cancel_*`、
  `futures_switch_leverage`、`futures_lightning_close_position`：合约交易。
- `futures_place_trigger_order`、`futures_get_trigger_*`、
  `futures_cancel_trigger_*`、`futures_switch_position_mode`：触发单和持仓模式。

工具的完整名称、参数和 JSON Schema 会由 MCP Server 自动发布给客户端；文档中的
通配符表示同一组工具，而不是可直接调用的工具名。

## 交易安全

所有写工具都有两层保护：

1. 调用参数必须显式传 `confirm=true`；默认 `false` 时只返回完整 dry-run 请求。
2. 进程必须显式设置 `HTX_ENABLE_TRADING=true`，否则即使传了 `confirm=true` 也只返回预览。

只读模式建议保持：

```powershell
$env:HTX_ENABLE_TRADING = "false"
```

启用真实交易前，先查询合约规则、账户余额、仓位和未成交订单，再进行本地风险检查。
合约下单支持在开仓请求中传入 HTX 官方 TP/SL 字段，保护单由交易所侧维护。

下单工具返回的是 HTX 的“受理”结果，不代表已经成交。下单、撤单或平仓后，必须
再次调用对应的订单/仓位查询工具确认最终状态。不要因为网络超时就盲目重试，先用
`client_order_id` 或订单 ID 查询结果。

## 开发与测试

```powershell
uv sync --dev
uv run python -m compileall -q src
uv run pytest -q
```

测试使用 mock HTTP，不会触碰真实账户。启动 MCP Server 的 smoke test：

```powershell
uv run --env-file .env htx-mcp
```

若客户端无法发现工具，先确认 `uv`、项目绝对路径和环境变量均可用，并检查 stderr
日志；不要向 stdout 写入调试信息。

## 官方文档

- [HTX Open Platform API](https://www.htx.com/en-us/opend/newApiPages/)
- [HTX Spot API Reference](https://huobiapi.github.io/docs/spot/v1/en/)
- [HTX USDT-margined Contracts API Reference](https://huobiapi.github.io/docs/usdt_swap/v1/en/)
- [MCP Build a server](https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server)
