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
from typing import Any
from unittest.mock import Mock, patch

import pytest
from amplifier_module_hooks_status_context import StatusContextHook


class FakeContext:
    """Context module exposing the system-prompt-factory surface
    (context-simple shape: public async setter, private attribute) --
    same shape amplifier-bundle-skills' tool-skills tests use. Needed
    because `placement="prefix"` is now the default (system-reminder
    redesign, W2): the STATIC portion (git/env facts) rides this surface
    instead of `on_provider_request`'s own return value."""

    def __init__(self, base_prompt: str = "BASE SYSTEM PROMPT") -> None:
        self._system_prompt_factory = self._make_base(base_prompt)

    @staticmethod
    def _make_base(text: str) -> Any:
        async def _base() -> str:
            return text

        return _base

    async def set_system_prompt_factory(self, factory: Any) -> None:
        self._system_prompt_factory = factory


@pytest.fixture
def fake_context():
    return FakeContext()


@pytest.fixture
def mock_coordinator(fake_context):
    coordinator = Mock()
    coordinator.session_id = "test-session-id"
    coordinator.parent_id = None
    coordinator.get = Mock(
        side_effect=lambda key: fake_context if key == "context" else None
    )
    return coordinator


@pytest.fixture
def hook(mock_coordinator):
    """Hook with default configuration (no explicit overrides). Default
    placement is "prefix" -- the STATIC portion (git/env facts) rides
    `fake_context._system_prompt_factory`; `on_provider_request`'s return
    value carries only the dynamic `Today's date` line."""
    return StatusContextHook(mock_coordinator, {"working_dir": "."})


def _rendered_system_prompt(fake_context: FakeContext) -> str:
    """Fetch the current system prompt (base + wrapped static block) from
    a FakeContext, for tests asserting on the STATIC (prefix-placed)
    content."""
    return _run_async(fake_context._system_prompt_factory())


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

    def test_two_consecutive_calls_are_byte_identical(self, hook, fake_context):
        """Two back-to-back provider:request events with nothing changed in
        the environment must produce byte-identical `context_injection`
        text. Under the default `placement="prefix"` (system-reminder
        redesign, W2), `context_injection` is now the DYNAMIC (date-only)
        block; the static git/env facts ride the wrapped system prompt
        instead -- checked separately below.
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
        assert "Today's date:" in result1.context_injection

        # Sanity: the static content actually made it into the system
        # prompt (not an accidental empty-string false positive), and is
        # itself stable across both calls. Rendering the factory is what
        # actually triggers the (lazy) static computation, exactly like
        # context-simple calling it on every get_messages_for_request --
        # must happen while the git mock is still active.
        with patch.object(hook, "_run_git") as mock_run_git:
            mock_run_git.side_effect = [
                ".git",
                "main",
                "abc123",
                git_output,
                "abc1234 initial commit",
            ]
            rendered = _rendered_system_prompt(fake_context)
        assert "src/main.py" in rendered
        assert "Today's date:" not in rendered  # date stays in the dynamic block only

    def test_git_subprocess_only_invoked_once_across_multiple_calls(
        self, hook, fake_context
    ):
        """The whole point of caching: git must not be re-shelled-out to on
        every single provider:request (nor on every system-prompt-factory
        render, which is when the static block actually gets computed
        under `placement="prefix"`, per W2).
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
            _rendered_system_prompt(fake_context)  # triggers the lazy static render
            call_count_after_first = mock_run_git.call_count

            # Second (and third) turn's hook call + factory render must NOT
            # invoke git again.
            _run_async(hook.on_provider_request("provider:request", {}))
            _rendered_system_prompt(fake_context)
            _run_async(hook.on_provider_request("provider:request", {}))
            _rendered_system_prompt(fake_context)

        assert call_count_after_first == 5  # rev-parse, branch, main, status, log
        assert mock_run_git.call_count == call_count_after_first, (
            "git was re-invoked on a later call -- caching is not working"
        )

    def test_cached_git_snapshot_ignores_later_repo_changes(self, hook, fake_context):
        """If the repo genuinely changes mid-session (the agent edits files),
        the STATIC block (now riding the system prompt, per W2) must still
        reflect the FIRST snapshot -- this is what the hook's own text
        promises the agent ("will not update during the conversation").
        This test would fail against the old (pre-fix) implementation,
        which silently re-ran `git status` every call and would show the
        new, different status.
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
            rendered1 = _rendered_system_prompt(fake_context)  # first snapshot taken

        # Simulate the repo changing after the first snapshot: if caching were
        # NOT in effect, a second render would re-invoke git and pick this up.
        with patch.object(hook, "_run_git") as mock_run_git_2:
            mock_run_git_2.side_effect = [
                ".git",
                "feature/other-branch",
                "abc999",
                "M  completely_different_file.py",
                "def5678 a totally different commit",
            ]
            result2 = _run_async(hook.on_provider_request("provider:request", {}))
            rendered2 = _rendered_system_prompt(fake_context)
            # The cache must mean _run_git is never called again.
            mock_run_git_2.assert_not_called()

        assert result1.context_injection == result2.context_injection
        assert result2.context_injection is not None

        assert "src/main.py" in rendered1
        assert rendered1 == rendered2
        assert "completely_different_file.py" not in rendered2


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

    def test_platform_only_queried_once(self, hook, fake_context):
        with (
            patch.object(hook, "_run_git", return_value=None),
            patch(
                "amplifier_module_hooks_status_context.platform.system"
            ) as mock_system,
        ):
            mock_system.return_value = "linux"
            _run_async(hook.on_provider_request("provider:request", {}))
            _rendered_system_prompt(fake_context)  # triggers the lazy static render
            _run_async(hook.on_provider_request("provider:request", {}))
            _rendered_system_prompt(fake_context)

        assert mock_system.call_count == 1

    def test_is_git_repo_detection_cached(self, hook, fake_context):
        """The rev-parse check that determines is_git_repo must only run
        once, even across many calls.
        """
        with patch.object(hook, "_run_git") as mock_run_git:
            mock_run_git.return_value = None  # not a git repo
            _run_async(hook.on_provider_request("provider:request", {}))
            _rendered_system_prompt(fake_context)
            _run_async(hook.on_provider_request("provider:request", {}))
            _rendered_system_prompt(fake_context)
            _run_async(hook.on_provider_request("provider:request", {}))
            _rendered_system_prompt(fake_context)

        # Only the single rev-parse call (is_git_repo check) should have
        # happened, once, since git details are skipped entirely when not a
        # git repo.
        assert mock_run_git.call_count == 1

    def test_static_env_cache_is_per_instance_not_global(
        self, mock_coordinator, fake_context
    ):
        """Two separate hook instances (e.g. two different sessions) must
        not share cached state.
        """
        hook_a = StatusContextHook(mock_coordinator, {"working_dir": "."})
        hook_b = StatusContextHook(mock_coordinator, {"working_dir": "."})

        with patch.object(hook_a, "_run_git", return_value=None):
            _run_async(hook_a.on_provider_request("provider:request", {}))
            _rendered_system_prompt(fake_context)  # triggers hook_a's lazy render

        assert hook_a._cached_static_env is not None
        assert hook_b._cached_static_env is None
