"""Tests for per-session snapshot caching in StatusContextHook.

These tests guard the regression this module exists to fix: the hook is
`ephemeral=True`, so a fresh copy of its injected text is appended to every
single provider:request. If that text is not byte-identical across
consecutive calls when nothing in the environment has actually changed, it
poisons provider-side prompt caching (measured -17 to -20 percentage points
of Gemini cache-hit rate in production -- see PR description / commit
message for the underlying experiment).

The fix has two parts:
  1. Git status/branch/commits are computed once per session and cached
     (matching the hook's own documented promise to the agent that this
     block is "a snapshot in time ... will not update during the
     conversation").
  2. The date field defaults to calendar-day resolution (no time-of-day),
     since it is rendered live (never cached) on every call and
     second-resolution timestamps guarantee a unique blob per request.
"""

import asyncio
import datetime as dt
from unittest.mock import Mock, patch

import pytest
from amplifier_module_hooks_status_context import StatusContextHook


@pytest.fixture
def mock_coordinator():
    coordinator = Mock()
    coordinator.session_id = "test-session-id"
    coordinator.parent_id = None
    return coordinator


@pytest.fixture
def hook(mock_coordinator):
    """Hook with default configuration (no explicit overrides)."""
    return StatusContextHook(mock_coordinator, {"working_dir": "."})


def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _date_line_value(context_injection: str | None) -> str:
    """Extract the value after "Today's date:" from an injected block."""
    assert context_injection is not None
    line = next(
        line
        for line in context_injection.splitlines()
        if line.startswith("Today's date:")
    )
    return line.split("Today's date:", 1)[1].strip()


class TestByteIdenticalInjection:
    """The regression test that matters: unchanged env -> unchanged bytes."""

    def test_two_consecutive_calls_are_byte_identical(self, hook):
        """Two back-to-back provider:request events with nothing changed in
        the environment must produce byte-identical `context_injection` text.
        """
        git_output = "M  src/main.py\n?? new_file.py"

        with patch.object(hook, "_run_git") as mock_run_git:
            # rev-parse (is_git_repo check), branch, main-branch check, status, log
            mock_run_git.side_effect = [
                ".git",  # rev-parse --git-dir
                "main",  # branch --show-current
                "abc123",  # rev-parse --verify main
                git_output,  # status --short
                "abc1234 initial commit",  # log --oneline
            ]

            result1 = _run_async(hook.on_provider_request("provider:request", {}))
            result2 = _run_async(hook.on_provider_request("provider:request", {}))

        assert result1.context_injection == result2.context_injection
        assert result1.context_injection is not None
        # Sanity: the injected text actually contains what we expect (not an
        # accidental empty-string false positive).
        assert "src/main.py" in result1.context_injection
        assert "Today's date:" in result1.context_injection

    def test_git_subprocess_only_invoked_once_across_multiple_calls(self, hook):
        """The whole point of caching: git must not be re-shelled-out to on
        every single provider:request.
        """
        with patch.object(hook, "_run_git") as mock_run_git:
            mock_run_git.side_effect = [
                ".git",
                "main",
                "abc123",
                "M  src/main.py",
                "abc1234 initial commit",
            ]

            _run_async(hook.on_provider_request("provider:request", {}))
            call_count_after_first = mock_run_git.call_count

            # Second (and third) call must NOT invoke git again.
            _run_async(hook.on_provider_request("provider:request", {}))
            _run_async(hook.on_provider_request("provider:request", {}))

        assert call_count_after_first == 5  # rev-parse, branch, main, status, log
        assert mock_run_git.call_count == call_count_after_first, (
            "git was re-invoked on a later call -- caching is not working"
        )

    def test_cached_git_snapshot_ignores_later_repo_changes(self, hook):
        """If the repo genuinely changes mid-session (the agent edits files),
        the injected git block must still reflect the FIRST snapshot -- this
        is what the hook's own text promises the agent ("will not update
        during the conversation"). This test would fail against the old
        (pre-fix) implementation, which silently re-ran `git status` every
        call and would show the new, different status.
        """
        with patch.object(hook, "_run_git") as mock_run_git:
            mock_run_git.side_effect = [
                ".git",
                "main",
                "abc123",
                "M  src/main.py",
                "abc1234 initial commit",
            ]
            result1 = _run_async(hook.on_provider_request("provider:request", {}))

        # Simulate the repo changing after the first snapshot: if caching were
        # NOT in effect, a second call would re-invoke git and pick this up.
        with patch.object(hook, "_run_git") as mock_run_git_2:
            mock_run_git_2.side_effect = [
                ".git",
                "feature/other-branch",
                "abc999",
                "M  completely_different_file.py",
                "def5678 a totally different commit",
            ]
            result2 = _run_async(hook.on_provider_request("provider:request", {}))
            # The cache must mean _run_git is never called again.
            mock_run_git_2.assert_not_called()

        assert result1.context_injection == result2.context_injection
        assert result2.context_injection is not None
        assert "src/main.py" in result2.context_injection
        assert "completely_different_file.py" not in result2.context_injection


class TestDatetimeGranularityDefault:
    """The timestamp is the other churn source: verify the new default and
    the preserved opt-in for finer resolution.
    """

    def test_default_is_date_only_no_time(self, hook):
        with patch.object(hook, "_run_git", return_value=None):
            result = _run_async(hook.on_provider_request("provider:request", {}))

        assert hook.include_datetime is False
        date_value = _date_line_value(result.context_injection)
        assert ":" not in date_value  # no HH:MM:SS
        # Must still be a valid ISO calendar date (YYYY-MM-DD).
        parsed = dt.datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=dt.UTC)
        assert parsed.year >= 2025

    def test_explicit_include_datetime_true_preserves_seconds(self, mock_coordinator):
        hook = StatusContextHook(
            mock_coordinator, {"working_dir": ".", "include_datetime": True}
        )
        with patch.object(hook, "_run_git", return_value=None):
            result = _run_async(hook.on_provider_request("provider:request", {}))

        date_value = _date_line_value(result.context_injection)
        assert ":" in date_value  # HH:MM:SS present
        parsed = dt.datetime.strptime(date_value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=dt.UTC
        )
        assert parsed.year >= 2025

    def test_explicit_include_datetime_true_still_churns_between_calls(
        self, mock_coordinator
    ):
        """Documents (does not "fix") the trade-off: opting into full
        timestamps knowingly reintroduces per-second churn. This is expected,
        not a bug -- it's an explicit, documented opt-in.
        """
        hook = StatusContextHook(
            mock_coordinator, {"working_dir": ".", "include_datetime": True}
        )
        with (
            patch.object(hook, "_run_git", return_value=None),
            patch("amplifier_module_hooks_status_context.datetime") as mock_datetime,
        ):
            mock_datetime.now.side_effect = [
                dt.datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt.UTC),
                dt.datetime(2025, 1, 1, 12, 0, 1, tzinfo=dt.UTC),
            ]
            result1 = _run_async(hook.on_provider_request("provider:request", {}))
            result2 = _run_async(hook.on_provider_request("provider:request", {}))

        assert result1.context_injection != result2.context_injection

    def test_default_produces_identical_output_across_a_simulated_minute_boundary(
        self, hook
    ):
        """With the new default (date-only), advancing the clock by a minute
        within the same calendar day must NOT change the injected text --
        proving the coarsening actually eliminates the dominant churn source.
        """
        with (
            patch.object(hook, "_run_git", return_value=None),
            patch("amplifier_module_hooks_status_context.datetime") as mock_datetime,
        ):
            mock_datetime.now.side_effect = [
                dt.datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt.UTC),
                dt.datetime(2025, 1, 1, 12, 5, 47, tzinfo=dt.UTC),
            ]
            result1 = _run_async(hook.on_provider_request("provider:request", {}))
            result2 = _run_async(hook.on_provider_request("provider:request", {}))

        assert result1.context_injection == result2.context_injection


class TestStaticEnvCaching:
    """Non-git static facts (working dir, platform, OS, git-repo detection,
    session id) must also be computed once, not on every request.
    """

    def test_platform_only_queried_once(self, hook):
        with (
            patch.object(hook, "_run_git", return_value=None),
            patch(
                "amplifier_module_hooks_status_context.platform.system"
            ) as mock_system,
        ):
            mock_system.return_value = "linux"
            _run_async(hook.on_provider_request("provider:request", {}))
            _run_async(hook.on_provider_request("provider:request", {}))

        assert mock_system.call_count == 1

    def test_is_git_repo_detection_cached(self, hook):
        """The rev-parse check that determines is_git_repo must only run
        once, even across many calls.
        """
        with patch.object(hook, "_run_git") as mock_run_git:
            mock_run_git.return_value = None  # not a git repo
            _run_async(hook.on_provider_request("provider:request", {}))
            _run_async(hook.on_provider_request("provider:request", {}))
            _run_async(hook.on_provider_request("provider:request", {}))

        # Only the single rev-parse call (is_git_repo check) should have
        # happened, once, since git details are skipped entirely when not a
        # git repo.
        assert mock_run_git.call_count == 1

    def test_static_env_cache_is_per_instance_not_global(self, mock_coordinator):
        """Two separate hook instances (e.g. two different sessions) must
        not share cached state.
        """
        hook_a = StatusContextHook(mock_coordinator, {"working_dir": "."})
        hook_b = StatusContextHook(mock_coordinator, {"working_dir": "."})

        with patch.object(hook_a, "_run_git", return_value=None):
            _run_async(hook_a.on_provider_request("provider:request", {}))

        assert hook_a._cached_static_env is not None
        assert hook_b._cached_static_env is None
