# HTX MCP 实盘反馈

> 建档：2026-09-08 UTC
> 原则：只记录已实际观察或可由当前源码直接证明的问题；不包含 API Key、Secret、签名 URL 等凭据。问题修复后保留原记录并追加验证结果。

## HTXMCP-001 — V5 `client_order_id` 校验与交易所约束不一致

- 严重度：高
- 状态：已复现；未修复
- 影响：规划层返回 `ready`，但真实订单被 HTX 拒绝，自动交易链会产生不必要的失败与重试风险。

### 复现

1. 产品：USDT 本位 V5 swap，`DOGE-USDT`，cross，`position_side=both`。
2. 使用字母数字 ID：`cx2609080108dg01`。
3. `htx_preview_trade` 返回 `status=ready`、`checks=[]`，请求中保留该字符串。
4. `htx_submit_trade(confirm=true)` 返回 HTX 错误 `1067: Illegal parameter client_order_id.`。
5. 随后账户快照确认无订单、无仓位、权益未变化。
6. 将 ID 改为正整数 `1788830000001` 后，同一订单被 HTX 接受并返回订单号 `1546810734811971584`。

### 源码证据

- `src/htx_mcp/models.py` 的字段说明宣称 V5 接受 1–64 字符标识符或正 64 位整数。
- `src/htx_mcp/semantic_tools.py` 的 `validate()` 只在 legacy swap + 字符串时产生错误；V5 字符串不会被拒绝。
- 同文件的 V5 `build_request()` 将任意 ID 直接 `str(...)` 后发送。
- 当前 CCXT `htx.ts#createContractOrderRequest` 对合约 `client_order_id` 使用 `safeIntegerN(...)`，与实盘只接受整数的结果一致。

### 建议修复

- 对 V5 普通订单把 `client_order_id` 约束为十进制正整数，范围 `1..2^63-1`；字符串输入仅接受全数字且规范化后仍在范围内。
- 规划模型、工具 schema、README 和测试 fixture 统一该约束。
- 新增回归测试：字母型 V5 ID 必须在 preview/validate 阶段返回 `blocked`；正整数 ID 通过并保持精确字符串，不经过浮点数。
- 如果 HTX 某些 V5 子接口确实接受 UUID/字符串，应按具体 endpoint 分开建模，不能用一条宽泛描述覆盖所有 V5 mutation。

## HTXMCP-002 — 被拒订单的 reconcile 返回无诊断信息的通用错误

- 严重度：中
- 状态：已复现；未修复
- 影响：自动系统无法区分“订单不存在”“参数非法”“认证失败”和临时网络错误，容易错误重试。

### 复现

1. 对 HTXMCP-001 中被 1067 拒绝的字符串 client ID 调用 `htx_reconcile_trade`。
2. MCP 只返回 `Error executing tool htx_reconcile_trade`，未保留结构化 HTX 错误码、消息或明确的 `not_found` 状态。
3. 需要额外账户快照才能确认无订单和无仓位。

### 建议修复

- 与 submit 路径一样返回脱敏结构：`error_type`、`http_status`、`htx_error_code`、`htx_error_message`、`retryable`。
- 对明确的订单不存在返回正常结构化状态，例如 `order: null, status: not_found`，而不是 MCP tool error。
- 对 client ID 本身不合法的情况在发请求前本地校验并返回 `blocked`。

## HTXMCP-003 — “compact” portfolio snapshot 默认返回海量零余额

- 严重度：中
- 状态：已复现；未修复
- 影响：一次约 25 USDT 空账户快照产生约 24 万 token 级原始工具输出，容易截断真正有用的合约余额、持仓和订单信息，并显著增加模型成本。

### 复现

1. 调用 `htx_get_portfolio_snapshot(include_open_orders=true, margin_mode=cross)`。
2. 现货 `balances.list` 返回交易所支持的上千币种 trade/frozen 零余额记录。
3. 有效事实只有 U 本位 `USDT=24.99223716`、无仓位、无挂单，却被大量零项淹没。

### 建议修复

- 语义化/compact 工具默认过滤 `balance=available=frozen=debt=0` 的币种。
- 如需排障，增加显式 `include_zero_balances=true` 或 `include_raw=true`。
- 增加输出上限和 `filtered_zero_balance_count`，让调用者知道发生了过滤。
- 回归测试应断言默认空现货账户只返回非零余额，且输出大小有确定上界。

## HTXMCP-004 — V5 swap 余额顶层汇总字段与 `details` 自相矛盾

- 严重度：高
- 状态：持续复现；未修复
- 影响：风险引擎若读取顶层字段，会把约 25 USDT 的真实权益误判为 0，可能错误停机或错误计算仓位。

### 复现

当前多次 `htx_get_account_snapshot` / portfolio snapshot 均返回：

- `balances.equity = "0"`
- `balances.available_margin = "0"`
- `balances.details[USDT].equity = "24.99223716"`
- 空仓时 `balances.details[USDT].available = "24.99223716"`
- 挂单后 `balances.details[USDT].available_margin = "21.96847082666666667"`

顶层与币种明细明显不是同一语义，但字段名相同且没有 warning。

### 建议修复

- 在 USDT 单资产账户中把顶层 `equity`、`available_margin` 等归一化为 USDT detail 的对应值。
- 若顶层 0 是交易所多资产 envelope 的另一字段，则改名或置为 `null`，并返回明确的 `primary_margin_asset=USDT`。
- `htx_get_risk_snapshot` 应只使用经验证的主保证金币种值，并在不一致时返回 high-severity warning。
- 新增非零余额、挂单占用保证金、持仓浮盈亏三种 fixture 的汇总一致性测试。

## HTXMCP-005 — `trading` 工具集别名排除了实盘诊断工具

- 严重度：中（设计问题）
- 状态：已观察；待确认是否为预期
- 影响：启用 execution 后，`htx_diagnose_private_access` 从工具列表消失；恰在真实交易前无法使用专门的安全诊断入口。

### 观察

- 原 analysis/planning/ops 进程暴露 `htx_diagnose_private_access`，但不暴露 submit/close/set-leverage。
- 切换到 execution/trading 工具面后，submit、close、cancel、set-leverage 已出现，但 diagnose 消失。
- README 当前定义 `trading = analysis + planning + execution`，不含 `ops`；所以这可能是别名设计而非注册 bug。

### 建议修复

- 将只读的私有权限诊断归到 analysis，或让 `trading` 同时包含 `ops`。
- 至少在服务启动资源中清楚提示：当前有 execution，但缺少 diagnose；并提供 `HTX_TOOLSETS=trading,ops` 的推荐实盘配置。
- 增加工具集组合测试，保证推荐的实盘配置同时包含 preflight、execution、reconcile、cancel、close 和 diagnose。

## HTXMCP-006 — 行情/技术指标瞬时失败的诊断信息不足

- 严重度：低至中
- 状态：偶发复现；重试成功
- 影响：无法判断是 HTX 限频、上游数据缺失、解析错误还是网络瞬断；策略只能盲目重试。

### 复现

- 并发刷新五个品种多周期指标时，单次 DOGE-USDT 4hour 调用只返回 `Error executing tool htx_get_technical_indicators`。
- 随后单独重试立即成功，返回 299 根已完成 K 线及完整指标。
- SUI-USDT analysis snapshot 在 02:18 UTC 的一次调用中保留 ticker/index/funding/OI，但 depth 子请求失败；warning 仅为 `depth: HtxApiError: HTX API error: HTX rejected the request`。
- 02:24 UTC 的另一次调用保留 depth/index/funding/OI，但 ticker 子请求失败；warning 同样没有 HTX 错误码。02:29 UTC 重试时 ticker 与 depth 均恢复。
- 聚合工具“保留成功子结果并发 warning”的降级行为本身合理；问题是 warning 无法区分限频、参数、服务异常或网络问题。

### 建议修复

- 所有 read-only 语义工具统一返回脱敏的结构化错误与 `retryable` 标志。
- 若为上游限频，返回 retry-after/backoff 建议；若为单一子请求失败，聚合工具应保留其他成功部分并在 warnings 中标记。
- 聚合 warning 至少包含子工具名、HTX code/message、HTTP status、retryable 与发生时间；执行级快照若缺 ticker 或 depth，应额外给出 high-severity `execution_incomplete`，阻止订单预检把残缺快照当作完整行情。

## HTXMCP-007 — market wait 的最大等待时间与 MCP 传输超时冲突

- 严重度：中至高
- 状态：已复现；未修复
- 影响：工具宣称会在硬截止时间返回结构化结果，但较长等待会先被传输层杀死，调用者拿不到最后观测值、轮询次数或正常超时状态。盯盘系统若把 tool error 当成市场/进程终止，可能漏管订单。

### 复现

1. 调用 `htx_wait_for_market_event`，`timeout_seconds=300`、`poll_interval_seconds=5`，等待 DOGE 价格触及上下阈值。
2. 条件未触发时，约 300 秒后返回的不是约定的 `status=timed_out`，而是 MCP 通用错误：`timed out awaiting tools/call after 300s`。
3. 手工重查证明订单仍为 `new`、0 成交，市场和私有 API 均正常。
4. 相同工具使用 `timeout_seconds=50` 时能正常返回结构化 `timed_out`、polls、observations。
5. 后续一次 `timeout_seconds=240` 的调用虽正常返回 `timed_out`，但 warnings 为 `poll 43: TimeoutError:`，异常消息为空且没有 `retryable`/backoff 信息；调用者无法判断最后一次轮询的失败性质。

### 建议修复

- 工具自身最大 `timeout_seconds` 必须显著小于 MCP transport/request deadline，并预留网络与序列化缓冲，例如传输层 300 秒时工具上限设为 270 秒。
- 更佳方案是让服务端 transport timeout 大于工具公开的最大等待时间，并在接近截止前主动返回。
- schema 明确公布允许范围；超出安全上限时在调用前校验失败，不能运行到传输层硬切断。
- 增加集成测试：最长合法等待必须返回正常 `timed_out` 结构，而非 tool error。
- 单次 poll 失败应返回结构化 `error_type`、非空消息及 `retryable`；若最后仍有有效 observation，应明确其时间戳和陈旧程度。

### 建议的三层超时契约

当前调用链实际至少有四个时间参数，其中前三个容易被误称为同一种“超时”：

1. `HTX_TIMEOUT_SECONDS`：单个上游 HTX HTTP 请求预算，当前默认 20 秒。
2. `htx_wait_for_market_event.timeout_seconds`：业务层等待预算，当前 schema 为 1–300 秒。
3. 调用容器的 `yield_time_ms`：在把仍运行的调用交还为 session/cell handle 前等待多久；它不是取消或失败截止时间。
4. MCP host/transport tool-call deadline：实测约 300 秒，是外部硬上限。

应满足：

`wait_timeout + response_margin < transport_timeout`

其中 `response_margin` 至少覆盖一次尾部状态整理、序列化和网络抖动；若还可能在截止前启动一个上游请求，应把 HTTP 请求预算计入余量。当前 transport 约 300 秒时，同步 wait 建议最大 240 秒，外层 `yield_time_ms` 设为约 250000–270000，让条件触发或正常超时直接唤醒调用者。

建议修改 schema/描述：

- 将同步 wait 的 `timeout_seconds.le` 从 300 降至 240（或由已知 transport deadline 动态派生），默认值可保留 60–120。
- 把 `Hard maximum ... always returns by this deadline` 改为更精确的承诺：预算包含上游请求与 sleep，服务端预留响应余量并在 transport deadline 前返回；若客户端超时更短，客户端仍可能先取消。
- `poll_interval_seconds` 当前实现是在请求完成后 sleep，因此真实“相邻轮询起点间隔”约等于请求耗时加该值；描述不应暗示固定频率。更好的实现是按目标 start-to-start cadence 扣除本轮请求耗时。
- `yield_time_ms` 不应混入 MCP 业务 schema，但应在 Codex/host 集成说明中明确：它只控制何时返回运行 handle，不改变 MCP 或 HTX 的截止时间。
- 返回 `requested_timeout_ms`、`effective_deadline_ms`、`last_success_at_ms`、`observation_age_ms`、`poll_attempts`、`successful_polls`、`failed_polls`，避免当前 `polls` 把没有真正发出请求的末次循环也计数。

建议的实盘调用档位：

| 状态 | wait timeout | poll interval | 外层 yield | 说明 |
| --- | ---: | ---: | ---: | --- |
| 空仓价格等待 | 240s | 15–30s | 250–270s | 只等宽阈值或 K 线边界 |
| 未成交挂单 | 120–180s | 10–15s | wait+10–20s | 同时应支持订单状态条件 |
| 已有交易所保护的仓位 | 60–120s | 5–10s | wait+10–20s | 只在接近 SL/TP 时缩短 |
| 无保护或保护状态不确定 | 不阻塞 | — | — | 立即修复保护或 reduce-only 退出 |

### 推荐的长期 watcher 接口

同步 MCP 调用不适合跨 15 分钟、1 小时甚至数日的盯盘。建议增加持久化只读 watcher：

- `htx_start_watch(...) -> {watch_id, started_at_ms, expires_at_ms}`
- `htx_get_watch(watch_id)`
- `htx_wait_watch(watch_id, timeout_seconds<=240)`
- `htx_cancel_watch(watch_id)`

watcher 应支持多品种以及 `last_price`、已完成 K 线、`order_state`、`position_size`、绝对时间等条件，并使用唯一 handle 防止任务混淆。结果必须可在后续调用中按 handle 取回，即使原 MCP 请求已经结束。若宿主支持异步 function/custom tools，也可以将启动工具标记为 async，由应用维护 handle 到原始 call ID 与后台任务的映射；wait 工具只负责等待已登记任务。

还建议补充两类条件，减少高频轮询与假信号：

- `wake_at_ms` / `completed_candle_after_ms`：直接等下一根 15m/1h K 线完成，无需每 5 秒查价格。
- `crosses_above` / `crosses_below`、连续 N 次成立、debounce：区分“已经在阈值上方”与真正穿越，降低噪声唤醒。

## HTXMCP-008 — 已取消订单的归一化 `id` 丢失

- 严重度：低至中
- 状态：已复现；未修复
- 影响：依赖统一订单结构 `id` 字段的对账代码会把已存在且已取消的订单误判为缺失，尽管同一对象的 `order_id` 仍然正确。

### 复现

1. V5 订单 `1546810734811971584` 在取消前 reconcile 返回 `id="1546810734811971584"`、`order_id="1546810734811971584"`。
2. `htx_cancel_trade(confirm=true)` 返回 code 200。
3. 取消后 reconcile 返回 `state="canceled"`、`id=null`，但 `order_id="1546810734811971584"`，其他字段及取消量正常。

### 建议修复

- 归一化订单时令 `id = first(id, order_id, order_id_str)`，取消/拒绝状态同样适用。
- 保留原始 HTX `id` 时另设 `raw_id`，不要让统一主键因状态变化而消失。
- 增加 new → canceled 和 new → filled 两条状态转换回归测试，断言统一 `id` 稳定。

## 修复验收清单

- [ ] V5 字母型普通订单 client ID 在本地 preview 阶段被阻止。
- [ ] 正整数 client ID 可 preview、submit、reconcile，全程保持定点字符串精度。
- [ ] rejected/not-found reconcile 返回结构化、可分类结果。
- [ ] compact portfolio 默认不返回零余额海洋，且输出不被截断。
- [ ] swap 顶层权益与 USDT detail 一致，挂单/持仓场景均通过测试。
- [ ] 推荐实盘 toolset 同时包含 diagnose 与完整执行闭环。
- [ ] 瞬时行情/指标错误带可重试诊断信息。
- [ ] 最长合法 market wait 能在传输超时前返回结构化结果。
- [ ] 订单在 new/canceled/filled 状态下保持稳定的统一 `id`。
