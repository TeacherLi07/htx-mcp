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
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["capabilities"] == ["Interactive", "Write"]
    assert len(manifest["interface"]["defaultPrompt"]) == 3


def test_plugin_mcp_defaults_to_semantic_read_and_plan_tools():
    config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    htx = config["mcpServers"]["htx"]

    assert htx["command"] == "uv"
    assert htx["args"] == ["run", "htx-mcp"]
    assert htx["env"]["HTX_ENABLE_TRADING"] == "false"
    assert htx["env"]["HTX_TOOLSETS"] == "analysis,planning,ops"


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
