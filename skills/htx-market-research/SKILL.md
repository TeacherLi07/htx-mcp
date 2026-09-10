---
name: htx-market-research
description: Analyze HTX spot or USDT swaps, define exact entry or no-trade conditions, and maintain bounded market monitoring.
---

Use this skill to establish a current, actionable market view and maintain a bounded watch. It does not submit or change trades; hand a qualified setup to `htx-trade-plan` or, when a standing authorization already covers it, `htx-guarded-execution`.

Start with `htx_get_market_snapshot` and request only the context needed to answer. Use `htx_get_technical_indicators` for a stated timeframe and indicators; do not infer an indicator from a single ticker. When macro news affects the conclusion, distinguish a sourced fact from an inference.

Return a compact decision record: instrument, timeframe, observed condition, invalidation level, and next action. Prices, quantities, and thresholds must remain exact fixed-point strings.

When no immediate entry is justified, update `trading/TRADE_PLAN.md` with the watched instrument, exact trigger, invalidation, timeframe, rationale, and a stated validity window; append `trading/JOURNAL.md` when that changes the plan. Then use `htx_wait_for_market_event` only with explicit price or indicator conditions and the required `thesis_valid_for_minutes` value (15-480 minutes, up to eight hours). This value records how long the stated thesis can stand without another agent review. The tool keeps a persistent public WebSocket per product, automatically responds to HTX application heartbeats, reconnects with bounded exponential backoff, and resubscribes all channels after a transient disconnect. Put every concern that could invalidate or qualify the thesis into explicit conditions; do not use short validity windows for periodic market scans. When a condition fires, refresh the snapshot and either advance the stated setup or retain the no-trade condition. When `wake_reason="thesis_expired"`, refresh the snapshot and reassess the written thesis before choosing the next validity window.

To make the wait tool the only wake-up source, set the outer `yield_time_ms` to more than `thesis_valid_for_minutes * 60 * 1000`, with response margin, and do not yield while the tool is pending. When the MCP host deadline is eight hours, choose a validity window below that ceiling to leave response margin. Never run multiple `htx_wait_for_market_event` calls in parallel: they do not independently wake the agent. Put every watched market/indicator condition in one call, set each condition's `product` and `instrument` when markets differ, and use `match="any"` when any one condition should wake the agent. After it returns, retrieve a fresh snapshot before making a new conclusion. Do not emit an order or call an execution tool while waiting.

Do not claim a live market condition after the thesis validity window expires without refreshing data.
