---
name: htx-trading-desk
description: "Operate an ongoing HTX trading desk: monitor live markets, maintain executable setups, and submit qualifying orders within the user's standing authorization."
---

Use this skill when the user delegates continuing market observation or asks the agent to act as an HTX trading desk. The objective is disciplined, timely decision-making: take a qualifying trade when its stated trigger is met; otherwise maintain the watch condition. Do not manufacture trades merely to create activity.

## Authorization scope

The user controls the execution authorization level. Without a broader instruction, authorization covers only a named action. A user may grant a continuing scope: full account authority, named products, or stated risk limits. Treat that scope as an instruction to execute qualifying actions without asking again for each order, not as a request to seek incremental reassurance. Record the scope and limits in `trading/` before the first autonomous action.

Authorization is conversation-scoped unless the user restates it in a later conversation. The note in `trading/` is an audit record, not a permission source. Do not infer authority from market analysis, an account connection, an API key, or an old note.

For every mutation, refresh relevant state, validate and preview the final action, then reconcile the result. When the action remains in scope and the fresh validation and preview pass, execute it in the same workflow. These checks are a short pre-execution gate, not an invitation to reopen an already-settled thesis or defer for more opinions. Pause only when the action is outside scope, validation is blocked, a material state change invalidates the plan, or HTX/MCP rejects it. A scope guides the agent; the MCP server does not enforce its product or risk limits. It independently requires `confirm=true` on each write call and enabled execution configuration.

Use live HTX data for prices, account state, positions, and orders. Use external research for macro context when it materially affects the decision, and identify source-backed facts separately from trading inferences. You may download public data and run local analysis when it improves the decision.

## Trading workspace

Before the first desk action, create `trading/` from this skill's [`assets/trading/`](assets/trading/) templates. Keep exactly these durable records:

- `TRADE_PLAN.md` is the current, actionable thesis. Update it when a setup, trigger, invalidation, sizing rationale, or authorization scope changes. Replace superseded active-plan content instead of accumulating a second plan.
- `state.json` is the compact current snapshot: authorization record, account/risk summary, watched setups, positions, and open orders. Refresh it after every material market decision and after every mutation is reconciled. Use UTC timestamps and fixed-point strings for monetary or quantity values; use `null` when unknown. Do not save raw API envelopes or private identifiers beyond the order IDs needed to reconcile a trade.
- `JOURNAL.md` is append-only. Retain its log template and append one completed entry for every observation that changes the plan, plan update, order action, fill, adjustment, close, or review. A journal entry records the decision and outcome; it never grants authorization.

Do not store credentials, signed URLs, or raw private account dumps in any of these files. `trading/` is an audit and continuity record, not a source of execution permission.

Route the work deliberately:

- Use `htx-market-research` for analysis, watchlists, triggers, and bounded waits.
- Use `htx-trade-plan` to turn a candidate order into a validated preflight.
- Use `htx-guarded-execution` for a trade action covered by the current authorization scope.
- Use `htx-margin-operations` for borrowing, repayment, transfers, or margin orders.
- Use `htx-operations` for configuration or access failures.

When conditions are absent, prefer a written watch condition and one bounded `htx_wait_for_market_event` over repeated polling. Do not run waits in parallel: combine every independent condition into its single `conditions` list, set per-condition `product` and `instrument` for different markets, and use `match="any"` when one matching condition should wake the desk. Follow `htx-market-research`'s `yield_time_ms` rule so the wait tool is the only wake-up source. Treat a wait result as a signal to refresh and reassess, not as proof that an order should be placed.
