---
name: htx-market-research
description: Analyze HTX spot or USDT swap markets, technical conditions, and macro context when a user asks for a trading view, watchlist, or monitored price setup.
---

Use this skill for research and monitoring, not for submitting or changing trades.

Start with `htx_get_market_snapshot` and request only the context needed to answer. Use `htx_get_technical_indicators` for a stated timeframe and indicators; do not infer an indicator from a single ticker. When macro news affects the conclusion, distinguish a sourced fact from an inference.

Return a compact decision record: instrument, timeframe, observed condition, invalidation level, and next action. Prices, quantities, and thresholds must remain exact fixed-point strings.

When no current trade is justified, create or update a concise note under `trading/` with the watched instrument, conditions, levels, timeframe, and rationale. Then use `htx_wait_for_market_event` only with explicit price or indicator conditions and a bounded timeout. A long wait is appropriate when it is the intentional wake-up mechanism.

To make the wait tool the only wake-up source, set the outer `yield_time_ms` to more than `timeout_seconds * 1000`, with response margin, and do not yield while the tool is pending. After it returns, retrieve a fresh snapshot before making a new conclusion. Do not emit an order or call an execution tool while waiting.

Do not claim a live market condition after the wait expires without refreshing data.
