# Changelog

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
