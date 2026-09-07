# Changelog

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
