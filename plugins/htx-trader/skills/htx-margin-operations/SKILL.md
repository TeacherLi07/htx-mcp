---
name: htx-margin-operations
description: Plan or execute HTX spot-margin transfers, borrowing, repayment, and margin orders with explicit account and risk checks.
---

Use this skill for spot-margin funding, borrowing, repayment, or orders. Do not use it for ordinary spot or swap trading.

Always begin with `htx_get_spot_margin_snapshot`. Use `htx_plan_spot_margin_action` to validate the requested action against the current account, available borrowing capacity, symbol requirements, and normalized request. Treat borrowing, repayment, transfer, and order placement as separate actions; do not imply one from another.

For an explicit execution request, use `htx_execute_spot_margin_action` only after refreshing the snapshot and reviewing the plan. Follow the same confirmation and post-action reconciliation rules as guarded execution. Record liability-affecting actions in `trading/`.
