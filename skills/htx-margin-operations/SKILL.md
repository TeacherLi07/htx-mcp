---
name: htx-margin-operations
description: Inspect, plan, and execute HTX spot-margin transfers, borrowing, repayment, and margin orders as separate account actions.
---

Use this skill for spot-margin funding, borrowing, repayment, or orders. Do not use it for ordinary spot or swap trading.

Always begin with `htx_get_spot_margin_snapshot`. Use `htx_plan_spot_margin_action` to validate the requested action against the current account, available borrowing capacity, symbol requirements, and normalized request. Treat borrowing, repayment, transfer, and order placement as separate actions; do not imply one from another.

For an authorized execution request, use `htx_execute_spot_margin_action` after refreshing the snapshot and reviewing the plan. A ready plan plus current account state is the execution threshold; do not add an extra approval loop. Follow the same confirmation and post-action reconciliation rules as guarded execution. If a confirmed action returns a configuration-blocked dry run, report the returned configuration instruction. After reconciliation, refresh liability and available-margin fields in `trading/state.json` and append the action and outcome to `trading/JOURNAL.md`.
