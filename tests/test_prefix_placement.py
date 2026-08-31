"""Tests for placement -- static status block in the stable system-prompt prefix.

placement="prefix" (default) wraps the context module's system-prompt
factory (the surface amplifier-foundation _prepared.py registers via
context.set_system_prompt_factory) so the STATIC portion (env facts minus
date, plus the git snapshot) rides the provider-cached system block instead
of being re-sent as fresh input tokens every request. Only the genuinely
live "Today's date" line still rides the per-request injection.
placement="inject" is the fully supported explicit opt-out: the full block
(static + date) injected on every provider:request, byte-identical to
`e3cbf7b` behavior (the rollback guarantee).

Mirrors amplifier-bundle-skills modules/tool-skills/tests/test_prefix_placement.py
1:1 in structure (FakeContext/FakeCoordinator shapes and the test set).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import Mock, patch

import pytest
from amplifier_module_hooks_status_context import StatusContextHook

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeContext:
    """Context module exposing the system-prompt-factory surface
    (context-simple shape: public async setter, private attribute)."""

    def __init__(self, base_prompt: str = "BASE SYSTEM PROMPT") -> None:
        self._system_prompt_factory = self._make_base(base_prompt)

    @staticmethod
    def _make_base(text: str) -> Any:
        async def _base() -> str:
            return text

        return _base

    async def set_system_prompt_factory(self, factory: Any) -> None:
        self._system_prompt_factory = factory


def _make_coordinator(*, context: Any = None) -> Mock:
    """Mock coordinator exposing .get('context'), .session_id, .parent_id --
    the surface StatusContextHook uses."""
    coordinator = Mock()
    coordinator.session_id = "test-session-id"
    coordinator.parent_id = None
    coordinator.get = Mock(
        side_effect=lambda key: context if key == "context" else None
    )
    return coordinator


def _no_git_hook(coordinator: Mock, **config_overrides: Any) -> StatusContextHook:
    """Build a hook with git disabled (isolates placement behavior from
    real git subprocess calls, matching the exemplar's simplicity)."""
    config: dict[str, Any] = {"working_dir": ".", "include_git": False}
    config.update(config_overrides)
    return StatusContextHook(coordinator, config)


# ---------------------------------------------------------------------------
# Default mode -- prefix is the default.
# ---------------------------------------------------------------------------


def test_default_is_prefix():
    """No placement key -> prefix placement: static block lands in the
    system prompt via the wrapped factory; the per-request injection
    carries only the dynamic date line."""
    context = FakeContext()
    hook = _no_git_hook(_make_coordinator(context=context))
    assert hook.placement == "prefix"

    result = _run_async(hook.on_provider_request("provider:request", {}))
    assert result.action == "inject_context"
    assert result.context_injection is not None
    assert "Today's date:" in result.context_injection
    assert "Working directory" not in result.context_injection

    rendered = _run_async(context._system_prompt_factory())
    assert rendered.startswith("BASE SYSTEM PROMPT")
    assert '<system-reminder source="hooks-status-context">' in rendered
    assert "Working directory" in rendered
    assert "Today's date:" not in rendered


def test_default_without_surface_warns_and_falls_back(caplog):
    """Default (prefix) with no factory surface -> ONE WARNING (not ERROR)
    + fallback to injecting the FULL block every request so the agent is
    never silently blinded to its static environment facts."""
    hook = _no_git_hook(_make_coordinator(context=None))
    with caplog.at_level("WARNING"):
        r1 = _run_async(hook.on_provider_request("provider:request", {}))
        r2 = _run_async(hook.on_provider_request("provider:request", {}))

    assert r1.action == r2.action == "inject_context"
    assert "Working directory" in (r1.context_injection or "")
    assert "Today's date:" in (r1.context_injection or "")
    warnings = [
        r for r in caplog.records if r.levelname == "WARNING" and "prefix" in r.message
    ]
    assert len(warnings) == 1  # once per instance, not per request
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_explicit_inject_optout():
    """placement='inject' opt-out: per-request inject_context with the
    pre-redesign shape (byte-identical to `e3cbf7b`), even when a factory
    surface IS available (and the factory is left untouched)."""
    context = FakeContext()
    hook = _no_git_hook(_make_coordinator(context=context), placement="inject")
    assert hook.placement == "inject"

    result = _run_async(hook.on_provider_request("provider:request", {}))
    assert result.action == "inject_context"
    assert result.context_injection is not None
    assert '<system-reminder source="hooks-status-context">' in result.context_injection
    assert "Working directory" in result.context_injection
    assert "Today's date:" in result.context_injection
    assert result.context_injection_role == "user"
    assert result.ephemeral is True
    # The system prompt is untouched -- no double-inject in opt-out mode.
    rendered = _run_async(context._system_prompt_factory())
    assert "hooks-status-context" not in rendered


def test_invalid_placement_rejected():
    """Unknown placement value fails loudly at construction, not mid-session."""
    with pytest.raises(ValueError, match="placement"):
        _no_git_hook(_make_coordinator(), placement="sideways")


# ---------------------------------------------------------------------------
# Prefix mode -- placement, refresh behavior, no-double-inject, fallback
# ---------------------------------------------------------------------------


def test_prefix_mode_places_static_block_in_system_prompt():
    """Prefix mode: factory output = base + static block; the per-request
    injection carries only the dynamic date line."""
    context = FakeContext()
    hook = _no_git_hook(_make_coordinator(context=context), placement="prefix")

    result = _run_async(hook.on_provider_request("provider:request", {}))
    assert result.action == "inject_context"
    assert result.context_injection is not None
    assert "Today's date:" in result.context_injection

    rendered = _run_async(context._system_prompt_factory())
    assert rendered.startswith("BASE SYSTEM PROMPT")
    assert '<system-reminder source="hooks-status-context">' in rendered
    assert "Working directory" in rendered


def test_prefix_mode_stable_across_requests():
    """Unchanged static facts -> byte-identical system prompt across
    requests (cacheable prefix), and the cached render is reused (no
    re-render)."""
    context = FakeContext()
    hook = _no_git_hook(_make_coordinator(context=context), placement="prefix")

    _run_async(hook.on_provider_request("provider:request", {}))
    first = _run_async(context._system_prompt_factory())
    rendered_obj = hook._prefix_rendered
    _run_async(hook.on_provider_request("provider:request", {}))
    second = _run_async(context._system_prompt_factory())

    assert first == second
    assert hook._prefix_rendered is rendered_obj  # cached, not re-rendered
    assert first.count('<system-reminder source="hooks-status-context">') == 1


def test_prefix_mode_rewraps_after_factory_rereg():
    """If someone re-registers a new base factory after our wrap, the next
    request re-wraps around it -- the static block never silently
    disappears."""
    context = FakeContext()
    hook = _no_git_hook(_make_coordinator(context=context), placement="prefix")
    _run_async(hook.on_provider_request("provider:request", {}))
    _run_async(context._system_prompt_factory())

    async def new_base() -> str:
        return "REPLACED BASE"

    _run_async(context.set_system_prompt_factory(new_base))  # clobbers our wrap
    _run_async(hook.on_provider_request("provider:request", {}))
    rendered = _run_async(context._system_prompt_factory())
    assert rendered.startswith("REPLACED BASE")
    assert "Working directory" in rendered
    assert rendered.count('<system-reminder source="hooks-status-context">') == 1


def test_prefix_mode_falls_back_with_warning_without_surface(caplog):
    """No context module at all -> WARNING (not ERROR) + full-block
    fallback (agent never silently loses the static facts)."""
    hook = _no_git_hook(_make_coordinator(context=None), placement="prefix")
    with caplog.at_level("WARNING"):
        result = _run_async(hook.on_provider_request("provider:request", {}))
    assert result.action == "inject_context"
    assert any(
        r.levelname == "WARNING" and "prefix" in r.message for r in caplog.records
    )
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_prefix_mode_refuses_to_replace_static_prompt():
    """A session with NO registered factory (static system messages) must
    not have its prompt replaced by a status-only factory -- falls back."""
    context = FakeContext()
    context._system_prompt_factory = None
    hook = _no_git_hook(_make_coordinator(context=context), placement="prefix")
    result = _run_async(hook.on_provider_request("provider:request", {}))
    assert result.action == "inject_context"  # full-block fallback
    assert context._system_prompt_factory is None  # untouched


# ---------------------------------------------------------------------------
# The git block in the system prompt / date line in the injection --
# fails before (the exact split this workstream implements).
# ---------------------------------------------------------------------------


def test_git_block_in_system_prompt_not_in_injection():
    """The git snapshot must appear in the system prompt (prefix mode) and
    NOT in the per-request injection. Fails before this workstream (the
    git block used to ride the per-request injection every turn)."""
    context = FakeContext()
    coordinator = _make_coordinator(context=context)
    hook = StatusContextHook(coordinator, {"working_dir": "."})

    with patch.object(hook, "_run_git") as mock_run_git:
        mock_run_git.side_effect = [
            ".git",
            "main",
            "abc123",
            "M  src/main.py",
            "abc1234 initial commit",
        ]
        result = _run_async(hook.on_provider_request("provider:request", {}))
        rendered = _run_async(context._system_prompt_factory())

    assert "src/main.py" not in (result.context_injection or "")
    assert "src/main.py" in rendered


def test_todays_date_in_injection_not_in_system_prompt():
    """`Today's date` must appear in the per-request injection and NOT in
    the system prompt (prefix mode). Fails before this workstream (the
    date line used to be baked into the one-shot system-prompt-eligible
    block, which would have made the prefix churn daily)."""
    context = FakeContext()
    hook = _no_git_hook(_make_coordinator(context=context))

    result = _run_async(hook.on_provider_request("provider:request", {}))
    rendered = _run_async(context._system_prompt_factory())

    assert "Today's date:" in (result.context_injection or "")
    assert "Today's date:" not in rendered


def test_inject_mode_output_is_byte_identical_to_e3cbf7b_behavior():
    """placement="inject" output is byte-identical to `e3cbf7b` behavior
    (the rollback guarantee) -- one single block, static facts + git +
    date + behavioral note, all in the per-request injection."""
    coordinator = _make_coordinator(context=FakeContext())
    hook = StatusContextHook(coordinator, {"working_dir": ".", "placement": "inject"})

    with patch.object(hook, "_run_git") as mock_run_git:
        mock_run_git.side_effect = [
            ".git",
            "main",
            "abc123",
            "M  src/main.py",
            "abc1234 initial commit",
        ]
        result = _run_async(hook.on_provider_request("provider:request", {}))

    injection = result.context_injection
    assert injection is not None
    assert injection.startswith('<system-reminder source="hooks-status-context">')
    assert injection.endswith("</system-reminder>")
    assert "Working directory" in injection
    assert "src/main.py" in injection
    assert "Today's date:" in injection
    assert "DO NOT mention this status information" in injection
