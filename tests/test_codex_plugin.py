"""Regression checks for the repository-contained Codex plugin."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "codex-plugin" / "pickflow-1688"


def test_plugin_manifest_points_to_versioned_resources() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == PLUGIN_ROOT.name
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert (PLUGIN_ROOT / manifest["skills"]).is_dir()
    assert (PLUGIN_ROOT / manifest["mcpServers"]).is_file()


def test_plugin_mcp_command_resolves_inside_repository() -> None:
    config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    pickflow = config["mcpServers"]["pickflow"]
    server_cwd = (PLUGIN_ROOT / pickflow["cwd"]).resolve()
    command = (server_cwd / pickflow["command"]).resolve()

    assert server_cwd == REPO_ROOT
    assert command == REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    assert command.is_file()
    assert pickflow["args"] == ["-m", "pickflow_mcp_server"]
    assert pickflow["env_vars"] == ["SORFTIME_MCP_URL"]


def test_plugin_sources_contain_no_placeholders_or_embedded_credentials() -> None:
    sources = [
        path
        for path in PLUGIN_ROOT.rglob("*")
        if path.is_file() and path.suffix in {".json", ".md", ".yaml", ".yml"}
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)

    assert "[TODO:" not in combined
    assert "mcp.sorftime.com?key=" not in combined
    assert "C:\\Users\\" not in combined
    assert "SORFTIME_MCP_URL" in combined


def test_skill_prompt_and_evidence_guards_are_present() -> None:
    skill_root = PLUGIN_ROOT / "skills" / "pickflow-1688-sourcing"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    interface = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
    evidence_policy = (skill_root / "references" / "evidence-policy.md").read_text(
        encoding="utf-8"
    )

    assert "$pickflow-1688-sourcing" in interface
    assert "supplier_compare_prepare" in skill
    assert "Never feed an ambiguous displayed price into `fba_profit`" in skill
    assert "Never inspect cookies, storage, passwords, or session internals" in skill
    assert "security slider" in skill
    assert "verification_required" in evidence_policy
    assert "Stop automated retries immediately" in evidence_policy
    assert "same sidebar browser" in evidence_policy
