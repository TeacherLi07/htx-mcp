---
name: htx-trading-desk
description: "Operate an ongoing HTX trading desk: research live markets, maintain a watchlist and notes, plan trades, and route explicit orders to guarded execution."
---

Use this skill when the user delegates continuing market observation or asks the agent to act as an HTX trading desk. The objective is disciplined decision-making, not continuous order activity.

## Authorization scope

The user controls the execution authorization level. Without a broader instruction, authorization covers only a named action. A user may grant a continuing scope: full account authority, named products, or stated risk limits. Execute an action in that scope without asking again for each order. Record the scope and limits in `trading/` before the first autonomous action.

Authorization is conversation-scoped unless the user restates it in a later conversation. The note in `trading/` is an audit record, not a permission source. Do not infer authority from market analysis, an account connection, an API key, or an old note.

Before every mutation, refresh relevant state, validate and preview the action, then reconcile the result. Pause when the action is outside scope, validation is blocked, material state changed, or HTX/MCP rejects it. A scope guides the agent; the MCP server does not enforce its product or risk limits. It independently requires `confirm=true` on each write call and enabled execution configuration.

Use live HTX data for prices, account state, positions, and orders. Use external research for macro context when it materially affects the decision, and identify source-backed facts separately from trading inferences. You may download public data and run local analysis when it improves the decision. Keep durable theses, watchlists, plans, and post-trade notes in `trading/`; do not store credentials, signed URLs, or raw private account dumps there.

Route the work deliberately:

- Use `htx-market-research` for analysis, watchlists, triggers, and bounded waits.
- Use `htx-trade-plan` for a candidate order or margin action.
- Use `htx-guarded-execution` for a trade action covered by the current authorization scope.
- Use `htx-margin-operations` for borrowing, repayment, transfers, or margin orders.
- Use `htx-operations` for configuration or access failures.

When conditions are absent, prefer a written watch condition and a bounded `htx_wait_for_market_event` over repeated polling. Treat a wait result as a signal to refresh and reassess, not as proof that an order should be placed.
