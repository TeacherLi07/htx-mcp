"""Static checks for the distributable HTX Trader plugin package."""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parents[1]


def test_plugin_manifest_connects_skills_and_mcp_server():
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "htx-trader"
    assert manifest["version"].startswith("0.8.1+codex.")
    assert manifest["description"].startswith("For every HTX operation")
    assert (
        "Load the relevant HTX workflow skill"
        in manifest["interface"]["longDescription"]
    )
    assert manifest["skills"] == "./skills/"
    htx = manifest["mcpServers"]["htx"]
    assert htx["command"] == "bash"
    assert htx["args"] == ["./scripts/run-htx-mcp-from-env.sh"]
    assert htx["cwd"] == "."
    assert htx["tool_timeout_sec"] == 7200
    assert manifest["interface"]["capabilities"] == ["Interactive", "Write"]
    assert len(manifest["interface"]["defaultPrompt"]) == 3


def test_plugin_mcp_configuration_is_not_a_separate_artifact():
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    htx = manifest["mcpServers"]["htx"]
    assert "env" not in htx
    assert "env_vars" not in htx
    assert not (PLUGIN_ROOT / ".mcp.json").exists()


def test_marketplace_installs_the_repository_root_plugin():
    marketplace = json.loads(
        (PLUGIN_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )

    assert marketplace["name"] == "htx-mcp-local"
    entry = marketplace["plugins"][0]
    assert entry["name"] == "htx-trader"
    assert entry["source"] == {"source": "local", "path": "."}
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"] == "Productivity"


def test_plugin_env_launcher_is_relative_to_the_plugin_root():
    launcher = (PLUGIN_ROOT / "scripts" / "run-htx-mcp-from-env.sh").read_text(
        encoding="utf-8"
    )

    assert 'uv run --env-file "$env_file" python -m htx_mcp' in launcher
    assert "python -m htx_mcp.server" not in launcher
    package_entry_point = (PLUGIN_ROOT / "src" / "htx_mcp" / "__main__.py").read_text(
        encoding="utf-8"
    )
    assert "from .server import main" in package_entry_point


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
    assert "not as a request to seek incremental reassurance" in desk
    assert "not an invitation to reopen an already-settled thesis" in desk
    assert "conversation-scoped" in desk
    assert "not a permission source" in desk
    assert "not a new request for user approval" in execution
    assert "If it matches and validation passes, submit immediately" in execution


def test_market_wait_skill_requires_the_tool_to_be_the_only_wake_up_source():
    research = (PLUGIN_ROOT / "skills" / "htx-market-research" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    desk = (PLUGIN_ROOT / "skills" / "htx-trading-desk" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`yield_time_ms`" in research
    assert "`timeout_seconds * 1000`" in research
    assert "do not yield while the tool is pending" in research
    assert (
        "Never run multiple `htx_wait_for_market_event` calls in parallel" in research
    )
    assert "Do not run waits in parallel" in desk
    assert "only wake-up source" in desk


def test_trading_desk_includes_a_standardized_trading_workspace():
    desk_root = PLUGIN_ROOT / "skills" / "htx-trading-desk"
    desk = (desk_root / "SKILL.md").read_text(encoding="utf-8")
    templates = desk_root / "assets" / "trading"

    assert "`TRADE_PLAN.md`" in desk
    assert "`state.json`" in desk
    assert "`JOURNAL.md`" in desk
    assert (templates / "TRADE_PLAN.md").is_file()
    assert (templates / "state.json").is_file()
    assert (templates / "JOURNAL.md").is_file()
    assert (
        json.loads((templates / "state.json").read_text(encoding="utf-8"))[
            "schema_version"
        ]
        == 1
    )
    assert "日志模板" in (templates / "JOURNAL.md").read_text(encoding="utf-8")
