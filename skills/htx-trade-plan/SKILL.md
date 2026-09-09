---
name: htx-trade-plan
description: Turn a proposed HTX spot or USDT swap order into a validated preflight and normalized request without placing it.
---

Use this skill when the user asks whether or how to make a trade, including a strategy that specifies an entry, stop loss, take profit, or position size. This skill creates the order preflight only; use `htx-guarded-execution` to submit an authorized order once the trigger and scope are satisfied.

Establish current state before planning: inspect market conditions, instrument rules, relevant account or portfolio state, risk snapshot, positions, and open orders. For a margin action, inspect the margin snapshot first.

Express the candidate as one normalized intent and call `htx_validate_trade_intent`. If valid, call `htx_preview_trade` to show the exact normalized request, applicable precision, and collected market context. Validation checks rules and last price; use the already-fetched account, risk, position, and open-order state to complete the decision. Treat a failed check, unavailable balance, unclear direction, or missing invalidation as a no-trade outcome rather than filling gaps with assumptions.

Write the resulting thesis, entry trigger, invalidation, target or exit logic, size rationale, and validation status to `trading/`. Do not include API credentials, signed URLs, or unnecessary account identifiers. State plainly that validation and preview are not an order; a ready, authorized order should proceed to guarded execution rather than remain indefinitely in planning.
