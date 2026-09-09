---
name: htx-workspace-initialize
description: Initialize a new HTX trading workspace once with current market, external-context, and account baselines before desk operations begin.
---

Use this skill exactly once when starting HTX trading work in a new workspace. It creates the initial local record and a no-trade or candidate plan; it does not authorize, submit, cancel, close, transfer, borrow, or repay anything. After handoff, use `htx-trading-desk` for every subsequent session, including any refreshed baseline.

## Preserve an existing workspace

This skill is only for a fresh `trading/` workspace. First inspect it. If any of `trading/TRADE_PLAN.md`, `trading/state.json`, or `trading/JOURNAL.md` already contains substantive records, do not overwrite it. Initialization is complete; route continuing work through `htx-trading-desk` instead.

When the workspace is new, copy all three canonical templates from [`../htx-trading-desk/assets/trading/`](../htx-trading-desk/assets/trading/): `TRADE_PLAN.md`, `state.json`, and `JOURNAL.md`. Keep these as the only durable workspace records. Do not create a second plan, state file, or journal.

## Establish the baseline

Collect only read-only evidence, in this order when available:

1. Inspect `htx://configuration` and use `htx_diagnose_private_access` if private access is needed. Record the exposed toolsets and whether account data was available; do not alter configuration or credentials to make a check pass.
2. Establish the account baseline with `htx_get_portfolio_snapshot`, then request a focused `htx_get_account_snapshot` or `htx_get_risk_snapshot` for the contemplated product when necessary. Capture balances or equity, high-water mark and drawdown when available, open positions, open orders, margin mode, and material liabilities. Missing private access is a recorded limitation, not a reason to invent values.
3. Establish the internal market baseline with `htx_get_market_snapshot`, `htx_get_technical_indicators` for stated timeframes, and `htx_get_instrument_rules` for every proposed instrument. Keep prices, quantities, and thresholds as fixed-point strings.
4. Establish a bounded external-context baseline (external context; 盘外) for the intended holding horizon. At minimum screen the relevant scheduled macro, central-bank, policy, regulatory, legal, and market-structure events; cross-asset regime and liquidity; crypto-native funding, leverage, basis, liquidation, stablecoin, ETF, and on-chain flow risks; instrument/project catalysts such as unlocks, upgrades, governance, listings, delistings, or security incidents; and HTX, chain, custody, and operational risks. Record that a category was checked and not material when it does not affect the plan; do not turn the scan into an unfiltered news list. Separate sourced facts, publication time, effective/event time, and source quality from the resulting trading inference. Do not treat a headline, rumor, or sentiment reading as an entry signal without current market confirmation.

Do not use a single ticker as a technical conclusion. Do not start a market wait during initialization; finish the baseline and hand off to `htx-trading-desk`.

## Convert external information into a decision input

Use the holding horizon to decide how far ahead to look: an intraday plan needs the next relevant event windows and any catalyst that can persist beyond the session; a longer plan needs coverage through its intended hold. For every material external item, capture:

- the confirmed fact, source, publication time, and event or effective time in UTC;
- the expected transmission channel: direction, volatility, liquidity, correlation, or execution/venue risk;
- source quality and confidence, including whether the item is confirmed, disputed, or only a lead;
- the expiry or next review time, and the exact plan condition that would change if it arrives, is revised, or remains unresolved.

Prefer primary sources for scheduled events, policy, exchange, project, and security notices; corroborate material claims with independent reliable data. Treat social posts and single-source headlines as unconfirmed leads, never as facts. Do not double-count several reports describing the same event.

Translate the scan into a compact conclusion: supports, contradicts, or does not resolve the internal-market thesis; expected directional bias versus volatility risk; an event-risk or no-trade window; and any size, leverage, liquidity, or revalidation constraint. If external data is unavailable, stale, contradictory, or materially incomplete, record the gap and downgrade the candidate to no-trade or explicit fresh-confirmation status. Absence of evidence is not a neutral external conclusion.

## Write the initial records

Complete the initial values in all three files before handing off. `htx-trading-desk` owns their ongoing update rules:

- In `TRADE_PLAN.md`, write the UTC baseline time and information horizon, a concise cross-timeframe thesis, the external-context facts and conclusion (with source/event times, expected impact, confidence, review time, and gaps), instrument rules relevant to the setup, exact entry trigger or explicit no-trade condition, invalidation, exit logic, sizing/risk constraints, and remaining preconditions. Record authorization as absent unless the user expressly supplied a current scope. A validation or preview is not part of initialization and is not an order.
- In `state.json`, set `updated_at_utc` and populate only observed account/risk, watchlist, position, and order data. Use fixed-point strings for values and quantities, `null` for unknown values, and empty arrays for confirmed absence. Never store raw API envelopes, credentials, signed URLs, or unnecessary account identifiers.
- In `JOURNAL.md`, preserve the supplied template and append an `初始化` entry using its fields. Include which market, external, and account inputs were available, the sourced external facts and their times, the resulting direction/volatility/liquidity assessment, known limitations or conflicts, and the next external review or market watch action.

Return a compact handoff: available versus unavailable account evidence, current market/external conclusion, and the active no-trade or candidate trigger. The next step is always `htx-trading-desk`; it selects research, planning, or authorized execution from the current records.
