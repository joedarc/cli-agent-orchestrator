"""Fork-specific tests: non-yolo agents use --trust-tools, never --trust-all-tools.

This file is owned by the joedarc/cli-agent-orchestrator fork and tests behavior
that intentionally diverges from upstream. Upstream hardcodes yolo=True for all
non-yolo launches, bypassing permissions.yaml. This fork restores scoped tool
enforcement via --trust-tools derived from each agent's allowedTools.

Keep this file separate from upstream test files so merges don't conflict.
Tool names use real CAO vocabulary (fs_*, shell, grep, glob) matching actual
rl-orchestrator agent profiles.
"""

from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.providers.kiro_cli import KiroCliProvider
from cli_agent_orchestrator.utils.tool_mapping import get_kiro_trust_tools


# ---------------------------------------------------------------------------
# Unit tests for get_kiro_trust_tools() mapping (no provider needed)
# ---------------------------------------------------------------------------

class TestKiroTrustToolsMapping:
    """Verify the CAO vocab → kiro tag mapping produces expected --trust-tools values."""

    def test_fs_star_maps_to_read_write(self):
        tags = get_kiro_trust_tools(["fs_*"])
        assert set(tags.split(",")) == {"read", "write"}

    def test_shell_maps_to_shell(self):
        tags = get_kiro_trust_tools(["shell"])
        assert tags == "shell"

    def test_grep_and_glob_map_to_read(self):
        # Both grep and glob map to "read"; deduped to single entry
        tags = get_kiro_trust_tools(["grep", "glob"])
        assert tags == "read"

    def test_coder_profile_tools(self):
        """Standard coder profile: fs_* + grep + glob + shell + MCP."""
        tags = get_kiro_trust_tools(["fs_*", "grep", "glob", "shell", "@cao-mcp-server"])
        assert set(tags.split(",")) == {"read", "write", "shell"}

    def test_readonly_profile_tools(self):
        """Validator profile: read-only tools + MCP."""
        tags = get_kiro_trust_tools(["fs_*", "grep", "glob", "@cao-mcp-server"])
        assert set(tags.split(",")) == {"read", "write"}

    def test_mcp_only_gives_empty_string(self):
        """Supervisor with only MCP refs: no native kiro tools trusted."""
        tags = get_kiro_trust_tools(["@cao-mcp-server", "@cao-ops-mcp-server"])
        assert tags == ""

    def test_yolo_returns_none(self):
        """Wildcard allowedTools means caller should use --trust-all-tools."""
        assert get_kiro_trust_tools(["*"]) is None

    def test_mixed_star_with_others_returns_none(self):
        assert get_kiro_trust_tools(["*", "shell"]) is None


# ---------------------------------------------------------------------------
# Integration tests: KiroCliProvider launch command enforcement
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_deps():
    with (
        patch("cli_agent_orchestrator.providers.kiro_cli.load_agent_profile") as mock_profile,
        patch("cli_agent_orchestrator.providers.kiro_cli.wait_for_shell") as mock_shell,
        patch("cli_agent_orchestrator.providers.kiro_cli.wait_until_status") as mock_status,
        patch("cli_agent_orchestrator.providers.kiro_cli.get_backend") as mock_backend,
    ):
        mock_profile.side_effect = FileNotFoundError("no profile")
        mock_shell.return_value = True
        mock_status.return_value = True
        yield mock_backend, mock_status


class TestNonYoloLaunchCommands:
    """Non-yolo agents must use --trust-tools, never --trust-all-tools."""

    @pytest.mark.asyncio
    async def test_no_allowed_tools_omits_trust_flags(self, mock_deps):
        """Agent with no allowedTools: no --trust-all-tools, no --trust-tools."""
        mock_backend, _ = mock_deps
        provider = KiroCliProvider("t", "s", "w", "developer")
        await provider.initialize()

        cmd = mock_backend.return_value.send_keys.call_args.args[2]
        assert "--trust-all-tools" not in cmd
        assert "--trust-tools" not in cmd

    @pytest.mark.asyncio
    async def test_coder_profile_gets_read_write_shell(self, mock_deps):
        """Standard coder (fs_* + grep + glob + shell) gets --trust-tools=read,write,shell."""
        mock_backend, _ = mock_deps
        provider = KiroCliProvider(
            "t", "s", "w", "coder-sensor-service",
            allowed_tools=["fs_*", "grep", "glob", "shell", "@cao-mcp-server"]
        )
        await provider.initialize()

        cmd = mock_backend.return_value.send_keys.call_args.args[2]
        assert "--trust-all-tools" not in cmd
        assert "--trust-tools" in cmd
        trust_val = cmd.split("--trust-tools ")[1].split(" ")[0]
        assert set(trust_val.split(",")) == {"read", "write", "shell"}

    @pytest.mark.asyncio
    async def test_readonly_profile_gets_read_write(self, mock_deps):
        """Read-only agent (fs_* + grep + glob, no shell) gets --trust-tools=read,write."""
        mock_backend, _ = mock_deps
        provider = KiroCliProvider(
            "t", "s", "w", "validator",
            allowed_tools=["fs_*", "grep", "glob", "@cao-mcp-server"]
        )
        await provider.initialize()

        cmd = mock_backend.return_value.send_keys.call_args.args[2]
        assert "--trust-all-tools" not in cmd
        trust_val = cmd.split("--trust-tools ")[1].split(" ")[0]
        assert set(trust_val.split(",")) == {"read", "write"}

    @pytest.mark.asyncio
    async def test_mcp_only_supervisor_gets_empty_trust_tools(self, mock_deps):
        """Supervisor with only MCP refs: --trust-tools="" (hard-deny all native tools)."""
        mock_backend, _ = mock_deps
        provider = KiroCliProvider(
            "t", "s", "w", "supervisor",
            allowed_tools=["@cao-mcp-server", "@cao-ops-mcp-server"]
        )
        await provider.initialize()

        cmd = mock_backend.return_value.send_keys.call_args.args[2]
        assert "--trust-all-tools" not in cmd
        assert "--trust-tools" in cmd

    @pytest.mark.asyncio
    async def test_yolo_still_uses_trust_all_tools_and_legacy_ui(self, mock_deps):
        """Yolo (allowedTools=['*']) still gets --trust-all-tools + --legacy-ui."""
        mock_backend, _ = mock_deps
        provider = KiroCliProvider("t", "s", "w", "developer", allowed_tools=["*"])
        await provider.initialize()

        cmd = mock_backend.return_value.send_keys.call_args.args[2]
        assert "--trust-all-tools" in cmd
        assert "--legacy-ui" in cmd

    @pytest.mark.asyncio
    async def test_legacy_ui_fallback_preserves_trust_tools(self, mock_deps):
        """TUI timeout → --legacy-ui fallback must carry --trust-tools on the retry."""
        mock_backend, mock_status = mock_deps
        mock_status.side_effect = [False, True]

        provider = KiroCliProvider(
            "t", "s", "w", "coder",
            allowed_tools=["fs_*", "shell", "@cao-mcp-server"]
        )
        await provider.initialize()

        calls = mock_backend.return_value.send_keys.call_args_list
        assert len(calls) == 3  # TUI, /exit, --legacy-ui
        tui_cmd = calls[0].args[2]
        legacy_cmd = calls[2].args[2]
        assert "--trust-all-tools" not in tui_cmd
        assert "--trust-tools" in tui_cmd
        assert "--trust-all-tools" not in legacy_cmd
        assert "--trust-tools" in legacy_cmd
        assert "--legacy-ui" in legacy_cmd
