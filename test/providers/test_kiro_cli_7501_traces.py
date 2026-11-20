"""Replay real Kiro CLI 2.x OSC 7501 traces against KiroCliProvider.get_status (#1418, PR #1426).

Each fixture test/providers/fixtures/kiro_cli_7501_<end>.txt holds cuts of one real
``GET /terminals/{id}/output?mode=full`` capture taken while a Kiro permission prompt was open and after
it ended. The fixture header names the source and the verdict-neutral normalisations (logo art lines
replaced, rules shortened, long space runs collapsed). Sections are concatenated in file order to rebuild
the buffer at each cut point.

Ends covered: approve (Yes), deny (No), esc, ctrlc, exit (No, then /quit). Plus the full-screen redraw
case: Kiro clears and redraws the screen on tmux attach/resize, drawing the dialog again with no new
report, so the blocked report can leave the 32 KB buffer while the dialog is still open.
"""

from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.kiro_cli import KiroCliProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# The traces were captured with this profile; its name is in the TUI header line.
PROFILE = "developer"

# A turn that has ended may read as either; both let the inbox deliver.
TURN_ENDED = {TerminalStatus.COMPLETED, TerminalStatus.IDLE}


def _decode(text: str) -> str:
    return text.replace("\\x1b", "\x1b").replace("\\x07", "\x07").replace("\\r", "\r")


def load_cuts(end: str) -> dict[str, str]:
    """Return the named sections of kiro_cli_7501_<end>.txt, escapes decoded.

    Format: comment lines until the first line starting with "@@ ", then one "@@ <name>" line per
    section. A section is the text up to the next "@@ " line; the newline right before a marker line
    belongs to the file, not to the section. ESC, BEL and CR are written as \\x1b, \\x07 and \\r;
    every real newline is a \\n byte of the capture.
    """
    text = (FIXTURES_DIR / f"kiro_cli_7501_{end}.txt").read_text(encoding="utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("@@ "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {name: _decode("\n".join(lines)) for name, lines in sections.items()}


def buffer(end: str, *names: str) -> str:
    cuts = load_cuts(end)
    return "".join(cuts[name] for name in names)


REPORT = "\x1b]7501;"


def provider(mock_backend=None, pane_command: Optional[str] = None) -> KiroCliProvider:
    """A provider as the status monitor sees it.

    Without ``mock_backend`` the provider is fresh (``_initialized`` False). With it, the provider is
    marked initialized after a launch from zsh, and the backend reports ``pane_command`` as the pane's
    foreground process (``None`` models a lookup that returned nothing).
    """
    p = KiroCliProvider("trace7501", "cao-trace-7501", "window-0", PROFILE)
    if mock_backend is not None:
        mock_backend.return_value.get_pane_current_command.return_value = pane_command
        p.shell_baseline = "zsh"
        p._initialized = True
    return p


def test_fixtures_hold_the_expected_reports():
    """Guard the fixtures themselves: decoding works and each cut has the expected reports."""
    assert REPORT + "state=blocked:kind=permission:app=kiro-cli\x07" in buffer("approve", "a")
    assert REPORT not in buffer("approve", "b")
    for end in ("esc", "ctrlc"):
        assert REPORT + "state=blocked:kind=permission:app=kiro-cli\x07" in buffer(end, "a")
        assert REPORT not in buffer(end, "r")
        assert REPORT + "state=idle:app=kiro-cli\x07" in buffer(end, "b")
    assert REPORT + "state=blocked:kind=permission:app=kiro-cli\x07" in buffer("deny", "a")
    assert REPORT not in buffer("deny", "r")
    assert REPORT + "state=working:app=kiro-cli\x07" in buffer("deny", "p")
    assert REPORT + "state=done:app=kiro-cli\x07" in buffer("deny", "b")
    assert REPORT not in buffer("exit", "a")
    assert REPORT + "state=done:app=kiro-cli\x07" in buffer("exit", "b")
    assert "Session ended." in buffer("exit", "b")


@patch("cli_agent_orchestrator.providers.kiro_cli.get_backend")
class TestPromptOpen:
    """(a) The permission dialog is on screen and the buffer's last report is state=blocked."""

    @pytest.mark.parametrize("end", ["approve", "deny", "esc", "ctrlc"])
    def test_blocked_report_is_waiting(self, mock_backend, end):
        assert provider().get_status(buffer(end, "a")) == TerminalStatus.WAITING_USER_ANSWER

    @pytest.mark.parametrize("end", ["deny", "esc", "ctrlc"])
    def test_blocked_report_survives_redraws(self, mock_backend, end):
        """Full-screen redraws with no new report, blocked report still in the buffer."""
        assert provider().get_status(buffer(end, "a", "r")) == TerminalStatus.WAITING_USER_ANSWER

    def test_blocked_report_on_a_running_terminal(self, mock_backend):
        """Same buffer, but as a launched terminal: initialized, shell_baseline set, kiro in the pane.

        This is the state of every real CAO terminal, so it exercises the exit-to-shell guard of the
        #1418 block.
        """
        p = provider(mock_backend, pane_command="kiro-cli")
        assert p.get_status(buffer("esc", "a")) == TerminalStatus.WAITING_USER_ANSWER


@patch("cli_agent_orchestrator.providers.kiro_cli.get_backend")
class TestFullScreenRedraw:
    """The dialog is still open but the blocked report has left the buffer."""

    def test_redraw_only_window_is_waiting(self, mock_backend):
        """Buffer starts at Kiro's ESC[3J ESC[2J ESC[H clear: two redraws of the open dialog, no report."""
        output = buffer("esc", "r")
        assert REPORT not in output and "shell requires approval" in output
        assert provider().get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    def test_capped_buffer_with_open_dialog_is_waiting(self, mock_backend):
        """The exit trace as captured: the 32 KB cap had already pushed the blocked report out."""
        assert provider().get_status(buffer("exit", "a")) == TerminalStatus.WAITING_USER_ANSWER


@patch("cli_agent_orchestrator.providers.kiro_cli.get_backend")
class TestAfterTheEnd:
    """(b) The status after each way the prompt can end."""

    def test_approve_working_after_yes(self, mock_backend):
        """Approved prompt, turn running: the blocked report is still last but the spinner and footer follow it."""
        output = buffer("approve", "a", "w")
        assert REPORT + "state=blocked:kind=permission:app=kiro-cli\x07" in output
        assert "Thinking..." in output and "Kiro is working" in output
        assert provider().get_status(output) == TerminalStatus.PROCESSING

    def test_approve_working_on_a_running_terminal(self, mock_backend):
        p = provider(mock_backend, pane_command="kiro-cli")
        assert p.get_status(buffer("approve", "a", "w")) == TerminalStatus.PROCESSING

    def test_dialog_redrawn_after_working_is_waiting(self, mock_backend):
        """A redraw that puts the dialog back after the working footer means the prompt is still open."""
        output = buffer("approve", "a", "w", "a")
        assert provider().get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    def test_approve_turn_end(self, mock_backend):
        """After approve Kiro reprints the transcript; no report of any kind is left in the buffer."""
        assert provider().get_status(buffer("approve", "b")) in TURN_ENDED

    def test_deny_working_after_no(self, mock_backend):
        """The user chose No: state=working report, "Rejected", Kiro is working again."""
        output = buffer("deny", "a", "r", "p")
        assert provider().get_status(output) == TerminalStatus.PROCESSING

    def test_deny_turn_end(self, mock_backend):
        """state=done is the last report and the idle prompt is drawn after it.

        The stale dialog text from the redraws is still in the buffer above. This is the buffer CAO
        reported as waiting_user_answer.
        """
        output = buffer("deny", "a", "r", "p", "b")
        assert provider().get_status(output) in TURN_ENDED

    @pytest.mark.parametrize("end", ["esc", "ctrlc"])
    def test_cancel_turn_end(self, mock_backend, end):
        """state=idle report, "Cancelled", "Cancelled streaming", idle prompt."""
        assert provider().get_status(buffer(end, "a", "r", "b")) in TURN_ENDED

    def test_exit_to_shell(self, mock_backend):
        """state=done, /quit, "Session ended.", zsh in the pane: anything but WAITING_USER_ANSWER."""
        p = provider(mock_backend, pane_command="zsh")
        status = p.get_status(buffer("exit", "a", "b"))
        assert status != TerminalStatus.WAITING_USER_ANSWER
        assert status in TURN_ENDED

    def test_exit_buffer_when_pane_lookup_returns_nothing(self, mock_backend):
        """Same buffer when get_pane_current_command returns None (the dead-process guard is best-effort).

        The "/" command menu footer ("esc to cancel · ↑↓ to navigate · ↵ to select") sits in the last
        20 lines and matches TUI_TRUST_ALL_TOOLS_FOOTER, which is Kiro's generic list-selector chrome.
        """
        p = provider(mock_backend, pane_command=None)
        assert p.get_status(buffer("exit", "a", "b")) != TerminalStatus.WAITING_USER_ANSWER
