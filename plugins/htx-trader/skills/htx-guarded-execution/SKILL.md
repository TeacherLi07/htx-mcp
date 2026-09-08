---
name: htx-guarded-execution
description: Submit, cancel, or close an HTX trade covered by the user's current authorization scope and a fresh validated plan.
---

Use this skill for an HTX action covered by the user's current authorization scope. A specific request authorizes that action. A continuing scope established through `htx-trading-desk` authorizes qualifying actions without a user confirmation for each order. Never treat general analysis, monitoring, optimization, a plan, an account connection, or an API key alone as authorization to trade.

Before a mutation, refresh the relevant market, account, risk, position, and open-order state. Validate the final intent again and preview it. Compare the preview with the user's requested instrument, direction, price, size, margin mode, and protective levels. Stop and explain any material change or validation failure.

Call an execution tool when the action is covered by the active authorization scope. Set `confirm=true` on every qualifying write call: it is an MCP execution parameter, not a new request for user approval. The server must also expose the `execution` toolset and set `HTX_ENABLE_TRADING=true`; otherwise report the dry-run result instead of claiming that an order was placed.

After every submission, cancellation, or close request, reconcile the resulting order and position state. Record the request, acknowledgement, and reconciled outcome in `trading/`. An acknowledgement is not a fill. Do not retry after a timeout or uncertain response until the order is reconciled by order ID or client order ID.
