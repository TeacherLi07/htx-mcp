---
name: htx-trading-desk
description: "Operate an ongoing HTX trading desk: monitor live markets, maintain executable setups, and submit qualifying orders within the user's standing authorization."
---

Use this skill for every session after `htx-workspace-initialize`, including continuing observation, plan review, or an authorized action. The objective is disciplined, timely decision-making: take a qualifying trade when its stated trigger is met; otherwise maintain the watch condition. Do not manufacture trades merely to create activity.

## Every-session start

Before analysis, waiting, planning, or any account action, read `trading/TRADE_PLAN.md`, `trading/state.json`, and the latest relevant entries in `trading/JOURNAL.md`. Treat them as continuity and audit context, never as current market facts or execution permission.

If the workspace is missing or contains only untouched templates, route to `htx-workspace-initialize`. Otherwise reconcile the recorded plan and state with fresh live HTX data relevant to the proposed action, then update the records only for a material decision or reconciled outcome.

## Authorization scope

The user controls the execution authorization level. Without a broader instruction, authorization covers only a named action. A user may grant a continuing scope: full account authority, named products, or stated risk limits. Treat that scope as an instruction to execute qualifying actions without asking again for each order, not as a request to seek incremental reassurance. Record the scope and limits in `trading/` before the first autonomous action.

Authorization is conversation-scoped unless the user restates it in a later conversation. The note in `trading/` is an audit record, not a permission source. Do not infer authority from market analysis, an account connection, an API key, or an old note.

For every mutation, refresh relevant state, validate and preview the final action, then reconcile the result. When the action remains in scope and the fresh validation and preview pass, execute it in the same workflow. These checks are a short pre-execution gate, not an invitation to reopen an already-settled thesis or defer for more opinions. Pause only when the action is outside scope, validation is blocked, a material state change invalidates the plan, or HTX/MCP rejects it. A scope guides the agent; the MCP server does not enforce its product or risk limits. It independently requires `confirm=true` on each write call and enabled execution configuration.

Use live HTX data for prices, account state, positions, and orders. Treat the external-context baseline in `TRADE_PLAN.md` as time-bound: refresh public research when its review time has passed, a material event or catalyst changes, or the proposed holding horizon extends beyond it. Identify source-backed facts separately from trading inferences, and treat stale, conflicting, or unavailable external information as uncertainty rather than as neutral. You may download public data and run local analysis when it improves the decision.

## Trading workspace

Keep exactly these durable records; their initial creation belongs to `htx-workspace-initialize`:

- `TRADE_PLAN.md` is the current, actionable thesis and its time-bounded external-context baseline. Update it when a setup, trigger, invalidation, sizing rationale, authorization scope, material event, or external-risk conclusion changes. Replace superseded active-plan content instead of accumulating a second plan.
- `state.json` is the compact current snapshot: authorization record, account/risk summary, watched setups, positions, and open orders. Refresh it after every material market decision and after every mutation is reconciled. Use UTC timestamps and fixed-point strings for monetary or quantity values; use `null` when unknown. Do not save raw API envelopes or private identifiers beyond the order IDs needed to reconcile a trade.
- `JOURNAL.md` is append-only. Retain its log template and append one completed entry for every observation that changes the plan, plan update, order action, fill, adjustment, close, or review. A journal entry records the decision and outcome; it never grants authorization.

Do not store credentials, signed URLs, or raw private account dumps in any of these files. `trading/` is an audit and continuity record, not a source of execution permission.

Route the work deliberately:

- Use `htx-market-research` for analysis, watchlists, triggers, and bounded waits.
- Use `htx-trade-plan` to turn a candidate order into a validated preflight.
- Use `htx-guarded-execution` for a trade action covered by the current authorization scope.
- Use `htx-margin-operations` for borrowing, repayment, transfers, or margin orders.
- Use `htx-operations` for configuration or access failures.

When conditions are absent, prefer a written watch condition and one long, condition-driven `htx_wait_for_market_event` over manual inspection. Supply the required `thesis_valid_for_minutes` value (15-480), based on how long the documented thesis remains valid without review; the tool holds product-scoped WebSocket connections, handles application heartbeats, and reconnects with resubscription after transient disconnects. Use precise market conditions for every concern that merits an earlier wake-up. Do not run waits in parallel: combine every independent condition into its single `conditions` list, set per-condition `product` and `instrument` for different markets, and use `match="any"` when one matching condition should return. On `condition_matched` or `thesis_expired`, refresh and reassess before any decision.
