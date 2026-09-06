# Repository Guidelines

## Project Structure & Module Organization

- Keep production code in the repository’s existing source directories; do not introduce a second source root without documenting it.
- Keep automated tests alongside the project’s established test directory (for example, `tests/` or `__tests__/`).
- Store static fixtures, sample data, and other non-code assets in the existing asset or fixture directories, and use stable relative paths.
- Keep the semantic trading facade in `src/htx_mcp/semantic_tools.py`, shared trade models in `models.py`, and exact decimal helpers in `precision.py`; keep HTX endpoint compatibility code in `server.py`.
- The production tool surface defaults to semantic `analysis`, `planning`, and `ops` tools. Low-level endpoint mappings belong to the opt-in `advanced` toolset and must remain usable when `HTX_TOOLSETS=all`.

## Build, Test, and Development Commands

- Inspect the project’s package/build configuration before choosing commands (for example, `package.json`, `pyproject.toml`, `Cargo.toml`, or `Makefile`).
- Run the repository’s formatter and linter before submitting changes.
- Run the full test command locally; when iterating, run the smallest relevant test target first, then the full suite.
- Use `uv run pytest -q` for the full suite. `tests/test_semantic_tools.py` must cross-check semantic aggregate results and normalized order requests against the corresponding low-level API tools.
- Run `uv run ruff check src tests`, `uv run ruff format --check` on changed Python files, and `uv run python -m compileall -q src tests` before committing.

## Coding Style & Naming Conventions

- Follow the formatter and linter configuration already checked into the repository; avoid manual style exceptions.
- Use four spaces for Python and the language’s standard formatter for other languages. Use `PascalCase` for types, `camelCase` for functions and variables where idiomatic, and descriptive `snake_case` names for Python modules and tests.
- Keep modules focused, prefer explicit names over abbreviations, and add comments only when the reasoning is not obvious from the code.
- Treat prices, quantities, volumes, notional values, and TP/SL levels as `Decimal` values internally and fixed-point strings at the HTX boundary. Do not introduce binary `float` arithmetic into order construction or precision validation.
- Semantic tools should return compact normalized data; raw HTX envelopes belong to the advanced compatibility surface or an explicitly requested raw field.

## Testing Guidelines

- Add or update tests for every behavior change, including regression coverage for fixed defects.
- Name tests after the behavior they verify (for example, `test_rejects_expired_token`). Keep fixtures deterministic and avoid network or machine-specific dependencies.
- Treat formatter, linter, and test failures as blocking before review.
- Add deterministic mock coverage for every semantic-to-low-level mapping. Integration checks using `.env` credentials must never be part of the default suite and must not print credentials or signed URLs.

## Commit & Pull Request Guidelines

- Write imperative, concise commit subjects that describe one logical change (for example, `Fix retry backoff calculation`).
- Keep commits focused and explain non-obvious design choices in the body.
- Pull requests should summarize the change, explain how it was tested, link the relevant issue, and include screenshots or logs when user-visible behavior changes. Call out configuration, migration, or security impacts explicitly.

## Security & Configuration Tips

- Never commit secrets, tokens, private keys, generated credentials, or local environment files. Use documented environment variables and provide safe example values.
- Review dependency and configuration changes carefully, and avoid logging sensitive input or response data.
- Keep `HTX_ENABLE_TRADING=false` for ordinary development. If a read-only key is used to verify the trading path, enable trading only in the temporary process, use `confirm=true`, expect a structured HTX permission error, and restore the flag afterward.
- Configure tool exposure explicitly in deployments with `HTX_TOOLSETS`; use separate read-only analysis and trading processes when possible.
