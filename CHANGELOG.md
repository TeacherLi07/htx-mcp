# Changelog

## 0.7.2 - 2026-09-08

- Require the HTX Trader plugin to load all bundled skills before handling an HTX operation.

## 0.7.1 - 2026-09-08

- Add a repository-local Codex marketplace and an Ubuntu launcher that loads HTX credentials and runtime mode from the ignored `.env` file.
- Set the HTX MCP server tool timeout to two hours for all tool calls.

## 0.7.0 - 2026-09-08

- Package the HTX MCP server and trading workflows as the `HTX Trader` Codex plugin.
- Add skills for market research, trade planning, guarded execution, margin operations, and diagnostics.
- Define conversation-scoped user authorization for autonomous trade actions while preserving MCP execution gates and reconciliation.

- Keep V5 swap client order IDs numeric, matching the current CCXT HTX implementation and observed exchange validation.
- Add semantic V5 batch submission plus product-neutral batch and all-open-order cancellation tools.
- Exclude diagnostics from the `trading` toolset and allow hedge-mode position side to be specified when closing a V5 position.

## 0.6.1 - 2026-09-07

Add：

- htx_get_portfolio_snapshot：一次汇总现货与 U 本位合约余额、仓位和挂单；V5 合约支持不传标的时获取全部合约挂单。
- htx_get_market_context：按需返回规范化 K 线、近期成交与合约历史资金费率，避免暴露原始 HTX envelope。

## 0.6.0 - 2026-09-07

- Make HTX USDT-margined semantic account, position, order, reconciliation, and cancellation workflows use the v5 multi-asset API by default.
- Route v5 calls to the derivatives host and apply the independent swap-trading safety gate to every v5 mutation.
- Add v5 position-side support, numeric client-order IDs, explicit v5 leverage configuration, and regression coverage for v5 routing and order construction.
- Retain the legacy v1/v3 swap mapping behind `HTX_SWAP_API_VERSION=legacy` for non-migrated accounts.

## 0.5.6 - 2026-09-07

- Add an independent `HTX_ENABLE_SWAP_TRADING` safety gate so unavailable U-margined contract writes can be disabled without disabling spot trading.

## 0.5.5 - 2026-09-07

- Emit a support-ticket diagnostic from the standalone account-type switch script without exposing signed authentication query parameters.

## 0.5.4 - 2026-09-07

- Add a standalone, separately credentialed and explicitly confirmed script for switching a U-margined account to the non-unified account type.

## 0.5.3 - 2026-09-07

- Detect HTX USDT-swap unified accounts before querying legacy cross-margin account endpoints.
- Add a read-only account-type tool and return an explicit account-snapshot diagnosis when unified accounts prevent legacy API access.

## 0.5.2 - 2026-09-07

- Return partial account snapshots with field-level warnings when an independent private endpoint fails.
- Block semantic trade validation when HTX instrument rules or a current ticker price cannot be retrieved.
- Reject malformed HTX JSON envelopes consistently and avoid exposing transport URLs in API errors.
- Document a read-only Codex MCP configuration that forwards credentials without storing them in TOML.

## 0.5.1 - 2026-09-07

- Enforce market-wait deadlines across in-flight HTX requests.
- Normalize indicator aliases and validate requested indicator components before waiting.
- Preserve the latest successful observation through transient polling failures.

## 0.5.0 - 2026-09-07

- Add Decimal-based semantic technical indicators for completed HTX K-lines.
- Keep raw K-lines in the opt-in advanced toolset and expose compact indicator results in analysis.
- Add a bounded, read-only declarative market wait tool for price and indicator thresholds.

## 0.4.0 - 2026-09-07

- Store process and MCP tool-call logs in `~/.htxmcp/htx-mcp.log` instead of stderr.
- Add cross-platform size-based rotation with uncompressed, unlimited retention.
- Protect the log directory and active file with restrictive permissions where supported.
- Add `HTX_LOG_DIR` and `HTX_LOG_MAX_BYTES` configuration.
- Provide an Ubuntu logrotate template using `copytruncate`, `nocompress`, and `rotate -1`.

## 0.3.0 - 2026-09-07

- Log every MCP tool input and structured result to stderr with correlated call IDs and durations.
- Redact credentials, authorization values, tokens, passwords, signatures, and signed-query values.
- Allow `HTX_LOG_LEVEL` to control successful and failed call logging with validated standard levels.
- Add Windows and Ubuntu CI coverage on Python 3.10 and 3.13.
- Lock Ruff as a development dependency so clean environments run the same lint checks.
- Document installation, client configuration, logging, and security guidance for both operating systems.

## 0.2.0 - 2026-09-06

- Restore the documented spot `client-order-id` request field for placement and cancellation.
- Separate string spot client order IDs from numeric USDT-swap client order IDs.
- Validate spot market and limit orders against product-specific precision and minimum-value rules.
- Publish typed output schemas for semantic tools and stabilize submission result envelopes.
- Distinguish trade validation from request preview behavior.
- Report unsupported product-specific account snapshot fields instead of silently omitting them.
- Improve mutation annotations and align the trade preflight prompt with `TradeIntent`.

## 0.1.0

- Initial HTX spot and USDT-margined swap MCP server.
