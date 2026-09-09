---
name: htx-operations
description: Diagnose HTX MCP connectivity, private API access, account mode, and available tool surface without changing trading state.
---

Use this skill for failed private API calls, missing tools, account-mode problems, or configuration questions.

Use `htx_diagnose_private_access` when it is available before speculating about credentials or permissions. Inspect `htx://configuration` to identify the active tool surface and execution state. If the diagnostic tool is unavailable, use the configuration resource and the exact returned tool error as evidence. Keep diagnostic output free of access keys, secrets, signatures, and signed URLs.

Do not enable trading, alter credentials, switch account types, or run account migration scripts as part of diagnosis. Explain the smallest configuration change that the user can intentionally make.
