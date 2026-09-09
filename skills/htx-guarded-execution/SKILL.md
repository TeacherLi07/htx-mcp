---
name: htx-guarded-execution
description: Submit, cancel, or close an HTX order when it is covered by the user's authorization and a fresh preflight.
---

Use this skill for an HTX action covered by the user's current authorization scope. A specific request authorizes that action. A continuing scope established through `htx-trading-desk` authorizes qualifying actions without a user confirmation for each order. Never treat general analysis, monitoring, optimization, a plan, an account connection, or an API key alone as authorization to trade.

Before a mutation, refresh the relevant market, account, risk, position, and open-order state. Validate the final intent again and preview it. Compare the preview with the authorized instrument, direction, price, size, margin mode, and protective levels. If it matches and validation passes, submit immediately in the same workflow; do not ask again, repeat analysis, or wait for a better-looking setup. Stop and explain only a material change, a validation failure, or an action outside the authorization scope.

Call an execution tool as soon as the action is covered by the active authorization scope and the final checks pass. Set `confirm=true` on every qualifying write call: it is an MCP execution parameter, not a new request for user approval. If a confirmed execution returns a configuration-blocked dry run, report the returned configuration instruction and do not claim that an order was placed.

After every submission, cancellation, or close request, reconcile the resulting order and position state. Record the request, acknowledgement, and reconciled outcome in `trading/`. An acknowledgement is not a fill. Do not retry after a timeout or uncertain response until the order is reconciled by order ID or client order ID.
