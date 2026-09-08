---
name: htx-operations
description: Diagnose HTX MCP connectivity, private API access, account mode, or tool configuration without changing trading state.
---

Use this skill for failed private API calls, missing tools, account-mode problems, or configuration questions.

Use `htx_diagnose_private_access` before speculating about credentials or permissions. Inspect `htx://configuration` to identify enabled toolsets and safety gates. Keep diagnostic output free of access keys, secrets, signatures, and signed URLs.

Do not enable trading, alter credentials, switch account types, or run account migration scripts as part of diagnosis. Explain the smallest configuration change that the user can intentionally make.
