---
name: htx-market-research
description: Analyze HTX spot or USDT swaps, define exact entry or no-trade conditions, and maintain bounded market monitoring.
---

Use this skill to establish a current, actionable market view and maintain a bounded watch. It does not submit or change trades; hand a qualified setup to `htx-trade-plan` or, when a standing authorization already covers it, `htx-guarded-execution`.

Start with `htx_get_market_snapshot` and request only the context needed to answer. Use `htx_get_technical_indicators` for a stated timeframe and indicators; do not infer an indicator from a single ticker. When macro news affects the conclusion, distinguish a sourced fact from an inference.

Return a compact decision record: instrument, timeframe, observed condition, invalidation level, and next action. Prices, quantities, and thresholds must remain exact fixed-point strings.

When no immediate entry is justified, create or update a concise note under `trading/` with the watched instrument, exact trigger, invalidation, timeframe, and rationale. Then use `htx_wait_for_market_event` only with explicit price or indicator conditions and a bounded timeout. A long wait is appropriate when it is the intentional wake-up mechanism. When the event fires, refresh the snapshot and either advance the stated setup or retain the no-trade condition; do not create a new thesis merely because the wait returned.

To make the wait tool the only wake-up source, set the outer `yield_time_ms` to more than `timeout_seconds * 1000`, with response margin, and do not yield while the tool is pending. Never run multiple `htx_wait_for_market_event` calls in parallel: they do not independently wake the agent. Put every watched market/indicator condition in one call, set each condition's `product` and `instrument` when markets differ, and use `match="any"` when any one condition should wake the agent. After it returns, retrieve a fresh snapshot before making a new conclusion. Do not emit an order or call an execution tool while waiting.

Do not claim a live market condition after the wait expires without refreshing data.
