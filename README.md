# HTX Official API MCP

这是一个基于 HTX 官方 REST API 的 Python MCP Server，使用 `uv` 管理环境，覆盖
现货与 USDT 本位合约的行情、账户、订单、仓位、策略单、杠杆和历史数据。

服务通过 MCP 暴露三类能力：

- Tools：行情查询、账户查询、下单、撤单、仓位和策略单操作。
- Resource：`htx://configuration`，返回不含密钥的运行配置和安全策略。
- Prompt：`trade_preflight`，生成交易前检查清单。

## 安装

要求 Python 3.10+ 和 `uv`。Windows PowerShell：

```powershell
uv sync --dev
Copy-Item .env.example .env
uv run --env-file .env htx-mcp
```

Ubuntu：

```bash
uv sync --dev
cp .env.example .env
uv run --env-file .env htx-mcp
```

`.env` 不会被 MCP Server 自己解析；使用 `uv run --env-file .env` 启动，或由 MCP
客户端通过 `env` 字段传入同样的变量。

验证安装：

```powershell
uv run pytest -q
```

## 配置

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `HTX_API_KEY` | 空 | HTX Access Key；私有接口必需 |
| `HTX_API_SECRET` | 空 | HTX Secret Key；仅用于本地签名 |
| `HTX_API_BASE_URL` | `https://api.huobi.pro` | 现货 API Host |
| `HTX_FUTURES_API_BASE_URL` | `https://api.hbdm.com` | USDT 本位合约 API Host |
| `HTX_SPOT_ACCOUNT_ID` | 空 | 可选默认现货账户 ID；留空时自动解析唯一 working spot 账户 |
| `HTX_TIMEOUT_SECONDS` | `20` | 单次 HTTP 请求超时 |
| `HTX_ENABLE_TRADING` | `false` | 是否允许写接口真正发往 HTX；`false` 时所有写工具只返回 dry-run |
| `HTX_TOOLSETS` | `analysis,planning,ops` | 工具集 allow-list：`analysis`、`planning`、`execution`、`advanced`、`ops`；`core` 等价于 analysis+planning，`trading` 等价于 analysis+planning+execution，`all` 发布完整兼容层 |
| `MCP_TRANSPORT` | `stdio` | `stdio`、`sse` 或 `streamable-http` |
| `HTX_LOG_LEVEL` | `INFO` | 文件日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL` |
| `HTX_LOG_DIR` | `~/.htxmcp` | 日志目录；支持 Windows 和 Ubuntu 路径以及 `~` 展开 |
| `HTX_LOG_MAX_BYTES` | `10485760` | 活动日志达到该字节数后滚动；设为 `0` 可交给外部 logrotate |
| `HTTP_PROXY` / `HTTPS_PROXY` | 空 | 显式 HTTP CONNECT 代理 URL，例如 `http://127.0.0.1:7897` |
| `NO_PROXY` | 继承环境 | 不经过代理的 Host；为强制 HTX 走代理可设为空字符串 |

现货与合约使用独立 Host。除非部署环境明确要求其他官方域名，否则保持默认值。
API 密钥建议只授予 Read；只有确实需要交易时才授予 Trade，并绑定 IP。Secret
不会写入 MCP 响应或日志。

HTTPX 默认读取进程的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY`。
若代理端口同时提供 HTTP 与 SOCKS5，优先使用 `http://127.0.0.1:7897`；HTTPS
请求会通过 HTTP CONNECT 隧道转发。仅 SOCKS5 可用时，需要将代理写成
`socks5://127.0.0.1:7897` 并安装 HTTPX 的 SOCKS extra。

## MCP 客户端接入

### Codex

将以下配置添加到用户级 `~/.codex/config.toml`，或受信任项目的
`.codex/config.toml`。先在启动 Codex 的本地环境中设置 `HTX_API_KEY` 和
`HTX_API_SECRET`；`env_vars` 会将它们转发给 MCP 进程，因此不必把 Secret 写入
TOML 文件。

```toml
[mcp_servers.htx]
command = "uv"
args = ["run", "htx-mcp"]
cwd = "F:/htxauto"
env = { HTX_ENABLE_TRADING = "false", HTX_TOOLSETS = "analysis,planning,ops" }
env_vars = ["HTX_API_KEY", "HTX_API_SECRET"]
```

此配置只发布语义化的分析、规划和诊断工具；即使工具调用传入 `confirm=true`，也只会
返回 dry-run。完成配置后重启 Codex，并在 TUI 中使用 `/mcp` 检查 `htx` 是否已连接。
需要交易面时，应使用单独的受限执行进程，并显式配置 `HTX_TOOLSETS="trading"` 和
`HTX_ENABLE_TRADING="true"`。

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

stdout 只用于 MCP JSON-RPC；正常运行时不向 stderr 写日志，避免调用记录进入 Codex 的
stderr 日志。无法创建日志目录等启动期致命错误仍可能由 Python 在 stderr 报告。
如果使用 `sse` 或 `streamable-http`，必须在外部配置认证、反向代理和网络访问
控制；不要将带 Trade 权限的服务直接暴露到公网。

Ubuntu 客户端配置使用同一命令，只需替换项目路径：

```json
{
  "mcpServers": {
    "htx": {
      "command": "uv",
      "args": ["--directory", "/home/user/htxauto", "run", "htx-mcp"],
      "env": {
        "HTX_ENABLE_TRADING": "false",
        "HTX_LOG_LEVEL": "INFO"
      }
    }
  }
}
```

## 调用日志

每次工具调用默认向 `~/.htxmcp/htx-mcp.log` 写入单行 JSON 日志：

- `tool_input`：工具名及输入参数。
- `tool_output`：结构化返回、是否出错和耗时。
- `tool_error`：未能生成正常工具返回时的异常类型、消息和耗时。

`call_id` 用来关联同一次调用的输入和输出。API Key、Secret、Authorization、Token、
Password、Signature 以及签名 URL 中的认证参数会被替换为 `<redacted>`。日志仍可能包含
余额、仓位、订单和成交数据，应按敏感交易记录保护，不要上传到公开日志服务。

活动日志默认达到 10 MiB 后滚动为类似
`htx-mcp.20260907T120000000000Z.1234.1.log` 的文件。滚动文件不压缩、不自动删除，因而会
持续保留；管理员必须自行监控 `~/.htxmcp` 的磁盘占用。目录会尝试设置为仅当前用户可访问，
日志文件会尝试设置为 `0600`；Windows 上最终权限仍由该目录的 ACL 决定。

`HTX_LOG_LEVEL=INFO`（默认）记录成功和失败调用；`DEBUG` 还会启用更详细的组件诊断；
`WARNING` 不记录成功调用；`ERROR` 只记录失败调用；`CRITICAL` 关闭普通调用和错误日志。
无效级别会在启动时明确报错。Windows PowerShell 可使用
`$env:HTX_LOG_LEVEL = "WARNING"`，Ubuntu shell 可使用
`export HTX_LOG_LEVEL=WARNING`。

Ubuntu 可选择由系统 `logrotate` 管理活动文件。先在 MCP 环境中设置
`HTX_LOG_MAX_BYTES=0`，再复制 [scripts/htx-mcp.logrotate](scripts/htx-mcp.logrotate) 到
`/etc/logrotate.d/htx-mcp`，并把其中两处 `USERNAME` 替换为运行 MCP 的实际用户。模板使用
`copytruncate`、`nocompress` 和 `rotate -1`：只复制后截断活动文件，不压缩且不删除历史日志。
不要同时让内部大小滚动和 logrotate 管理同一活动文件，以免产生难以预测的双重切分。

## 私有接口认证排障

先调用只读工具 `htx_diagnose_private_access`。它会分别测试现货账户和合约 API
交易状态，并安全返回 HTX 的 HTTP 状态、错误码和错误消息，不会返回 Secret、签名
或完整请求 URL。

Codex 配置中的布尔值必须使用标准 TOML 字符。只读配置应精确写成：

```toml
HTX_ENABLE_TRADING = "false"
```

不要在该行末尾加入中文逗号或其他字符。`false` 会让 `confirm=true` 的写工具继续
返回 dry-run；只有诊断通过、你明确准备执行真实交易时，才改为 `"true"`。

若诊断返回 `api-signature-not-valid` 或 `Incorrect Access Key`，依次核对 API Key
是否仍有效、Access Key 与 Secret Key 是否来自同一条 API Key、IP 白名单是否包含
运行 Codex 的出口 IP，以及是否因复制粘贴带入首尾空白。若返回权限错误，请在 HTX
后台为该 API Key 开启所需的 Read 或 Trade 权限。

## 工具范围

### 现货

- `spot_get_ticker`、`spot_get_tickers`、`spot_get_klines`、`spot_get_depth`、
  `spot_get_recent_trades`：实时行情、全市场快照、K 线、深度和成交。
- `spot_get_symbols`、`spot_get_currencies`、`spot_get_currency_reference`、
  `spot_get_market_status`、`spot_get_server_timestamp`：交易规则和公共元数据。
- `spot_get_accounts`、`spot_get_account_balance`、`spot_get_open_orders`、
  `spot_get_order*`、`spot_get_match_results`、`spot_get_history_orders`：账户和订单查询。
- `spot_place_order`、`spot_cancel_*`、`spot_dead_man_switch`：现货下单、撤单和断线保护。
- htx_diagnose_private_access：安全诊断现货与合约私有 API 的认证、权限和 Host 配置。

### USDT 本位合约

- `futures_get_contracts`、`futures_get_ticker`、`futures_get_tickers`、
  `futures_get_depth`、`futures_get_klines`、`futures_get_recent_trades`：合约行情和规则。
- `futures_get_index`、`futures_get_price_limit`、`futures_get_open_interest`、
  `futures_get_funding_rate`、`futures_get_historical_funding_rate`、
  `futures_get_risk_info`、`futures_get_liquidation_orders`：衍生品市场数据。
- `futures_get_account_info`、`futures_get_positions`、`futures_get_open_orders`、
  `futures_get_order_*`：合约账户、仓位和当前订单查询，支持 `isolated`/`cross`。
- `futures_get_history_orders`、`futures_get_match_results`、
  `futures_get_financial_records`、`futures_get_liquidation_orders`：使用 HTX 当前
  v3 历史订单、成交、财务记录和强平查询接口；已停用的 v1 查询接口不会暴露。
- `futures_place_order`、`futures_place_batch_orders`、`futures_cancel_*`、
  `futures_switch_leverage`、`futures_lightning_close_position`：合约交易。
- `futures_place_trigger_order`、`futures_get_trigger_*`、
  `futures_cancel_trigger_*`、`futures_switch_position_mode`：触发单和持仓模式。

工具的完整名称、参数和 JSON Schema 会由 MCP Server 自动发布给客户端；文档中的
通配符表示同一组工具，而不是可直接调用的工具名。

### 面向 LLM 的参数约定

工具 Schema 会为每个参数发布用途、单位、默认值、范围和枚举说明。合约筛选参数优先使用
可读值：`all`、`open_long`、`open_short`、`close_short`、`close_long`、
`liquidate_long`、`liquidate_short`、`buy`、`sell`；服务端会在请求 HTX 前转换成官方数字代码。
触发单的 `trigger_type` 优先使用 `greater_or_equal` 或 `less_or_equal`，持仓模式优先使用
`one_way` 或 `hedged`；为兼容旧调用，HTX 原始短代码和数字值仍可接受。

批量合约下单的每个 `orders` 元素也有嵌套 JSON Schema，明确标出
`contract_code`、`volume`、`direction`、`order_price_type` 等必填字段，以及价格、杠杆、
TP/SL 和 `reduce_only` 等可选字段。时间参数统一使用 Unix 毫秒时间戳；下单、撤单、切换
杠杆或持仓模式等写工具的 `confirm` 默认是 `false`，只会返回 dry-run 预览。

价格、数量、成交量和 TP/SL 价格使用 `Decimal` 语义处理，并以固定点字符串发送给 HTX。
高精度交易参数建议传字符串，例如 `"0.00000001"` 或 `"60000.123456789012345678"`，
不要依赖 JSON 浮点数表达超高精度价格。

`client_order_id` 按产品使用不同类型：现货接受 1-64 位字母、数字、下划线或连字符；
U 本位合约接受 `1` 到 `9223372036854775807` 的整数。合约查询和撤单会在 HTX 边界按接口
要求转换为字符串，不要给合约订单使用带字母的 client ID。

### 面向自动分析与交易的工具集

当前 API 映射工具仍完整保留在 `advanced` 工具集中；高层语义工具负责聚合常用工作流：

- `analysis`：`htx_get_market_snapshot`、`htx_get_technical_indicators`、`htx_wait_for_market_event`、`htx_get_instrument_rules`、`htx_get_account_snapshot`、`htx_get_risk_snapshot`。`htx_get_technical_indicators` 只返回模型请求的确定性指标（SMA/EMA、RSI、ATR、成交量均线、布林带、MACD、KDJ），默认排除未收盘 K 线；日常分析不暴露原始 K 线。`htx_wait_for_market_event` 只接受有上限的声明式价格/指标阈值，超时必定返回且不执行写操作。需要研究或排障时，`advanced` 工具集仍提供 `spot_get_klines` 和 `futures_get_klines`。

等待工具在条件满足或超时前不会向 LLM 发送中间市场更新；仅在有意延后分析时使用，并在工具返回后重新获取市场快照。
- `planning`：`htx_validate_trade_intent`、`htx_preview_trade`、`htx_reconcile_trade`。
- `execution`：`htx_submit_trade`、`htx_cancel_trade`、`htx_close_position`。
- `ops`：诊断工具。

生产环境可只暴露分析和规划工具：

```powershell
$env:HTX_TOOLSETS = "analysis,planning"
```

需要交易工具时，再启用：

```powershell
$env:HTX_TOOLSETS = "trading"
```

未设置 `HTX_TOOLSETS` 时只发布 `analysis,planning,ops`；需要旧版完整 API 面时显式设置 `HTX_TOOLSETS=all`。自动交易部署建议使用独立的只读分析进程和交易进程。

语义工具发布明确的 MCP `outputSchema`。`htx_validate_trade_intent` 只返回状态、规则和检查项；
需要查看规范化 HTX 请求和采集时的市场上下文时调用 `htx_preview_trade`。`htx_submit_trade`
无论校验阻断、dry-run 或真实提交，都稳定返回 `validation` 与 `execution` 两部分。

`trade_preflight` prompt 的参数与交易意图一致，分别接收 `product`、`instrument`、`action`、
`side`、`quantity`、`order_kind`、`price`、`margin_mode`、`stop_loss` 和 `take_profit`；它只会
引导模型调用分析、校验和预览工具，不会调用执行工具。

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

Ubuntu 使用相同的 `uv` 命令，无需 PowerShell。GitHub Actions 会在 Windows 和 Ubuntu、
Python 3.10 和 3.13 的组合上执行锁文件安装、lint、格式、编译、测试和启动导入检查。

测试使用 mock HTTP，不会触碰真实账户。若客户端无法发现工具，先确认 `uv`、项目
绝对路径和环境变量均可用，并检查 stderr 日志；不要向 stdout 写入调试信息。

`tests/test_semantic_tools.py` 会用同一组 mock HTX 响应分别调用底层 API 工具和高层
语义工具，交叉校验行情快照、合约规则、账户状态和最终订单请求，防止聚合层与底层接口
语义漂移。只读 API Key 的交易权限验证应在临时进程中设置：

```powershell
$env:HTX_TOOLSETS = "trading"
$env:HTX_ENABLE_TRADING = "true"
uv run --env-file .env htx-mcp
```

使用 `confirm=true` 调用 `htx_submit_trade` 后，预期由 HTX 返回权限错误；验证完成后恢复
`HTX_ENABLE_TRADING=false`。不要把真实密钥写入仓库或测试 fixture。

## 官方文档

- [HTX Open Platform API](https://www.htx.com/en-us/opend/newApiPages/)
- [HTX Spot API Reference](https://huobiapi.github.io/docs/spot/v1/en/)
- [HTX USDT-margined Contracts API Reference](https://huobiapi.github.io/docs/usdt_swap/v1/en/)
- [MCP Python SDK: Tools](https://py.sdk.modelcontextprotocol.io/v2/servers/tools/)
- [MCP Build a server](https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server)
