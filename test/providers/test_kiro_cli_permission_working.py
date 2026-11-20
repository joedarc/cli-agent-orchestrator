"""Kiro TUI permission prompt shown while the agent is mid-turn (#186).

The Yes / Trust / No menu replaces Kiro's input box, so no idle placeholder sits
below it. Before #186, get_status returned PROCESSING from the init-spinner,
"Kiro is working" and no-idle-prompt checks before it reached the permission
check. Kiro also emits an OSC 7501 state sequence
(ESC ] 7501;state=blocked:kind=permission:app=kiro-cli BEL) that CAO now reads.

The fixture stores ESC and BEL as the literal text ``\\x1b`` and ``\\x07`` so it
stays readable and diffable; load_fixture() turns them into the real bytes.
"""

from pathlib import Path

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.kiro_cli import KIRO_OSC_STATE_PATTERN, KiroCliProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE = "kiro_cli_tui_permission_while_working.txt"

SEP = "─" * 60
OSC_WORKING = "\x1b]7501;state=working:app=kiro-cli\x07"
OSC_DONE = "\x1b]7501;state=done:app=kiro-cli\x07"
OSC_BLOCKED = "\x1b]7501;state=blocked:kind=permission:app=kiro-cli\x07"

# What Kiro prints after the user picks "Yes, single permission": the tool runs,
# the input box is redrawn, the response ends with Credits, 7501 state=done,
# and the final frame shows the idle placeholder.
AFTER_APPROVAL_DONE = (
    f"{OSC_WORKING} Shell git merge gt/main\n"
    " Merge made by the 'ort' strategy.\n"
    f"{SEP}\n"
    " developer · claude-opus-4 · ◔ 4%\n"
    " Ask a question or describe a task ↵\n"
    "  Merged gt/main into kiro-permission-status with no conflicts.\n"
    "\n"
    " ▸ Credits: 0.42 • Time: 6s\n"
    f"{OSC_DONE}{SEP}\n"
    " developer · claude-opus-4 · ◔ 4%\n"
    " Ask a question or describe a task ↵"
)


def load_fixture(name: str) -> str:
    raw = (FIXTURES_DIR / name).read_text(encoding="utf-8")
    return raw.replace("\\x1b", "\x1b").replace("\\x07", "\x07")


def load_plain_fixture(name: str) -> str:
    with open(FIXTURES_DIR / name, "r") as f:
        return f.read()


def without_osc(text: str) -> str:
    return KIRO_OSC_STATE_PATTERN.sub("", text)


def make_provider(agent_profile: str = "developer") -> KiroCliProvider:
    # Not initialized, so get_status never calls the backend.
    return KiroCliProvider("test1234", "test-session", "window-0", agent_profile)


class TestTuiPermissionWhileWorking:
    def test_fixture_is_decoded(self):
        output = load_fixture(FIXTURE)
        assert OSC_BLOCKED in output
        assert "\\x1b" not in output

    def test_menu_with_osc_blocked_is_waiting(self):
        """Fixture as Kiro emits it: working footer, Shell block, menu, 7501 blocked."""
        output = load_fixture(FIXTURE)
        assert make_provider().get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    def test_menu_without_osc_is_waiting(self):
        """Text check alone: menu in the bottom lines, no idle prompt after it."""
        output = without_osc(load_fixture(FIXTURE))
        assert "\x1b]7501" not in output
        assert make_provider().get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    def test_menu_after_init_spinner_is_waiting(self):
        """The init-spinner PROCESSING return no longer wins over an active menu."""
        output = "● Initializing...\n" + without_osc(load_fixture(FIXTURE))
        assert make_provider().get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    def test_approved_then_done_and_idle_is_completed(self):
        """Second case: menu, then 7501 state=done and the idle prompt."""
        output = load_fixture(FIXTURE) + AFTER_APPROVAL_DONE
        status = make_provider().get_status(output)
        assert status in (TerminalStatus.IDLE, TerminalStatus.COMPLETED)
        assert status == TerminalStatus.COMPLETED

    def test_later_working_state_clears_waiting(self):
        """Approved and the tool is running: a later 7501 state clears the menu."""
        output = (
            load_fixture(FIXTURE)
            + f"{OSC_WORKING} Shell git merge gt/main\n Kiro is working · esc to cancel\n"
        )
        assert make_provider().get_status(output) == TerminalStatus.PROCESSING

    def test_menu_in_scrollback_is_not_waiting(self):
        """A menu more than 20 lines above the bottom does not fire."""
        tool_output = "".join(f"  merged file {i}.py\n" for i in range(25))
        output = (
            without_osc(load_fixture(FIXTURE))
            + "\n ✓ Yes, single permission\n"
            + tool_output
            + " Kiro is working · esc to cancel\n"
        )
        assert make_provider().get_status(output) == TerminalStatus.PROCESSING


class TestKiroOsc7501:
    def test_blocked_permission_is_waiting(self):
        output = f" Kiro is working · esc to cancel\n{OSC_BLOCKED}"
        assert make_provider().get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    def test_blocked_with_st_terminator_is_waiting(self):
        output = (
            " Kiro is working · esc to cancel\n"
            "\x1b]7501;state=blocked:kind=permission:app=kiro-cli\x1b\\"
        )
        assert make_provider().get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    def test_blocked_inside_sgr_noise_is_waiting(self):
        output = f"\x1b[38;5;244m Kiro is working\x1b[0m\n\x1b[2K{OSC_BLOCKED}\x1b[0m"
        assert make_provider().get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    @pytest.mark.parametrize("later", [OSC_WORKING, OSC_DONE])
    def test_later_state_clears_blocked(self, later):
        output = f" Kiro is working · esc to cancel\n{OSC_BLOCKED}{later} Kiro is working\n"
        assert make_provider().get_status(output) == TerminalStatus.PROCESSING

    def test_blocked_other_kind_is_not_forced(self):
        output = " Kiro is working · esc to cancel\n\x1b]7501;state=blocked:kind=other:app=kiro-cli\x07"
        assert make_provider().get_status(output) == TerminalStatus.PROCESSING

    def test_done_then_idle_is_unchanged(self):
        output = (
            f"{SEP}\n  hi\n\n  Hello.\n\n ▸ Credits: 0.01 • Time: 1s\n"
            f"{OSC_DONE}{SEP}\n developer · claude-opus-4 · ◔ 1%\n"
            " Ask a question or describe a task ↵"
        )
        assert make_provider().get_status(output) == TerminalStatus.COMPLETED


class TestExistingPermissionFixturesUnchanged:
    @pytest.mark.parametrize(
        "fixture,profile",
        [
            ("kiro_cli_permission_active_empty.txt", "cao-internal-docs-expert"),
            ("kiro_cli_permission_active_trailing_text.txt", "cao-jira-expert"),
            ("kiro_cli_permission_active_injection.txt", "cao-code-explorer-expert"),
            ("kiro_cli_permission_active_partial_typing.txt", "cao-internal-docs-expert"),
        ],
    )
    def test_active_still_waiting(self, fixture, profile):
        output = load_plain_fixture(fixture)
        assert make_provider(profile).get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    @pytest.mark.parametrize(
        "fixture,profile",
        [
            ("kiro_cli_permission_stale_answered.txt", "cao-workspace-expert"),
            ("kiro_cli_permission_stale_long_response.txt", "cao-query-decomposer-supervisor"),
        ],
    )
    def test_stale_still_not_waiting(self, fixture, profile):
        output = load_plain_fixture(fixture)
        assert make_provider(profile).get_status(output) != TerminalStatus.WAITING_USER_ANSWER
