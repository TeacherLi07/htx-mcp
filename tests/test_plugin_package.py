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
    assert htx["tool_timeout_sec"] == 28800
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
        "htx-workspace-initialize",
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


def test_market_wait_skill_omits_codex_host_scheduling_instructions():
    research = (PLUGIN_ROOT / "skills" / "htx-market-research" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    desk = (PLUGIN_ROOT / "skills" / "htx-trading-desk" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "yield_time_ms" not in research
    assert "host deadline" not in research
    assert (
        "Never run multiple `htx_wait_for_market_event` calls in parallel" in research
    )
    assert "Do not run waits in parallel" in desk
    assert "only wake-up source" not in desk
    assert "up to eight hours" in research
    assert "do not use short validity windows for periodic market scans" in research


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
    assert "## Every-session start" in desk
    assert "Before analysis, waiting, planning, or any account action" in desk
    assert "route to `htx-workspace-initialize`" in desk
    plan_template = (templates / "TRADE_PLAN.md").read_text(encoding="utf-8")
    journal_template = (templates / "JOURNAL.md").read_text(encoding="utf-8")
    assert "## 盘外信息基线" in plan_template
    assert "盘外事实 / 来源 / 发布时间 / 生效或事件时间" in journal_template


def test_workspace_initialization_skill_creates_a_read_only_baseline():
    initializer = (
        PLUGIN_ROOT / "skills" / "htx-workspace-initialize" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "`TRADE_PLAN.md`" in initializer
    assert "`state.json`" in initializer
    assert "`JOURNAL.md`" in initializer
    assert "htx_get_portfolio_snapshot" in initializer
    assert "htx_get_market_snapshot" in initializer
    assert "external context" in initializer
    assert "bounded external-context baseline" in initializer
    assert "Prefer primary sources" in initializer
    assert "Absence of evidence is not a neutral external conclusion" in initializer
    assert (
        "does not authorize, submit, cancel, close, transfer, borrow, or repay"
        in initializer
    )
    assert "Use this skill exactly once" in initializer
    assert "The next step is always `htx-trading-desk`" in initializer


def test_specialist_skills_write_only_to_the_standard_trading_records():
    research = (PLUGIN_ROOT / "skills" / "htx-market-research" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    planning = (PLUGIN_ROOT / "skills" / "htx-trade-plan" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    execution = (
        PLUGIN_ROOT / "skills" / "htx-guarded-execution" / "SKILL.md"
    ).read_text(encoding="utf-8")
    margin = (PLUGIN_ROOT / "skills" / "htx-margin-operations" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`trading/TRADE_PLAN.md`" in research
    assert "`trading/JOURNAL.md`" in research
    assert "`trading/TRADE_PLAN.md`" in planning
    assert "`trading/JOURNAL.md`" in planning
    assert "`trading/state.json`" in execution
    assert "`trading/JOURNAL.md`" in execution
    assert "`trading/state.json`" in margin
    assert "`trading/JOURNAL.md`" in margin
