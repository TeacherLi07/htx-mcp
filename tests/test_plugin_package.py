"""Static checks for the distributable HTX Trader plugin package."""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parents[1] / "plugins" / "htx-trader"


def test_plugin_manifest_connects_skills_and_mcp_server():
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "htx-trader"
    assert manifest["version"] == "0.7.2"
    assert manifest["description"].startswith("For every HTX operation")
    assert "load all bundled skills" in manifest["interface"]["longDescription"]
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["capabilities"] == ["Interactive", "Write"]
    assert len(manifest["interface"]["defaultPrompt"]) == 3


def test_plugin_mcp_defaults_to_semantic_read_and_plan_tools():
    config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    htx = config["mcpServers"]["htx"]

    assert htx["command"] == "bash"
    assert htx["args"] == ["scripts/run-htx-mcp-from-env.sh"]
    assert htx["cwd"] == "/workspace/htx-mcp"
    assert htx["tool_timeout_sec"] == 7200
    assert "env" not in htx
    assert "env_vars" not in htx


def test_plugin_marketplace_and_env_launcher_are_ready_for_codex_cli():
    marketplace = json.loads(
        (PLUGIN_ROOT.parents[1] / ".agents" / "plugins" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    launcher = (
        PLUGIN_ROOT.parents[1] / "scripts" / "run-htx-mcp-from-env.sh"
    ).read_text(encoding="utf-8")

    assert marketplace["name"] == "htx-mcp-local"
    assert marketplace["plugins"][0]["source"]["path"] == "./plugins/htx-trader"
    assert 'uv run --env-file "$env_file" htx-mcp' in launcher


def test_plugin_skills_cover_the_trading_lifecycle():
    expected = {
        "htx-market-research",
        "htx-trade-plan",
        "htx-guarded-execution",
        "htx-margin-operations",
        "htx-operations",
        "htx-trading-desk",
    }
    discovered = {
        path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")
    }

    assert discovered == expected
    for name in expected:
        text = (PLUGIN_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\nname:")


def test_execution_skill_allows_user_controlled_continuing_authorization():
    desk = (PLUGIN_ROOT / "skills" / "htx-trading-desk" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    execution = (
        PLUGIN_ROOT / "skills" / "htx-guarded-execution" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "full account authority" in desk
    assert "without asking again for each order" in desk
    assert "conversation-scoped" in desk
    assert "not a permission source" in desk
    assert "not a new request for user approval" in execution
