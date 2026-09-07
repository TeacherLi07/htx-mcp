# Changelog

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
