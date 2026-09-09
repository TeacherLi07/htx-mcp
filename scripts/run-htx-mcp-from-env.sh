#!/usr/bin/env bash
# Start the local MCP server with the repository's intentionally untracked .env.
set -euo pipefail

repository_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
env_file="$repository_root/.env"

if [[ ! -f "$env_file" ]]; then
    echo "Missing $env_file. Copy .env.example to .env and set HTX credentials and mode." >&2
    exit 1
fi

cd "$repository_root"
exec uv run --env-file "$env_file" python -m htx_mcp.server
