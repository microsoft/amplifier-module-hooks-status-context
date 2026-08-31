"""Tests for the D1 fix (rr wave 20260831 -- anthropic prompt-cache collapse).

Background: the `20260831-rr` treatment-validation wave found the anthropic
system prompt growing ~22K chars EVERY REQUEST (44,525 -> 66,268 -> 88,011 ->
...), collapsing prompt-cache read share from ~90% to ~8% (~14x cost). Traced
on the wire: EVERY hook that wraps `context.set_system_prompt_factory` (this
module, routing-matrix, and tool-skills) gained one MORE copy of its own
block on every single request -- 1 copy after request 1, 2 after request 2,
3 after request 3, etc. -- in lockstep with the request index.

Root cause: `_ensure_prefix_placement`'s original re-wrap check was pure
object identity (`current is self._prefix_factory`). That check is safe
ONLY while this hook is the SOLE wrapper of the system-prompt-factory slot
(tool-skills' own solo production history). Once a SECOND independent hook
ALSO wraps the SAME slot with the same pattern, the slot's value changes out
from under whichever hook isn't the outermost wrapper on any given request
-- so on the NEXT request, that hook's own identity check fails, it
concludes "not wrapped yet", and wraps AGAIN around a chain that ALREADY
contains its own prior contribution. With N such hooks sharing the slot,
this compounds every single request, regardless of hook ordering.

These tests simulate a PEER hook also wrapping the SAME `FakeContext`'s
system-prompt-factory slot (exactly what routing-matrix / tool-skills also
do in production), and assert this module's own contribution/marker never
duplicates -- the property the wave's own unit suite (151 passing tests, all
single-hook-in-isolation) never exercised, and the property a live
multi-turn DTU run caught the hard way.
"""

from __future__ import annotations

import asyncio
from typing import Any

from amplifier_module_hooks_status_context import StatusContextHook

_STATUS_MARKER = '<system-reminder source="hooks-status-context">'


def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeContext:
    """Same shape as tests/test_prefix_placement.py's FakeContext."""

    def __init__(self, base_prompt: str = "BASE SYSTEM PROMPT") -> None:
        self._system_prompt_factory = self._make_base(base_prompt)

    @staticmethod
    def _make_base(text: str) -> Any:
        async def _base() -> str:
            return text

        return _base

    async def set_system_prompt_factory(self, factory: Any) -> None:
        self._system_prompt_factory = factory


class FakeCoordinator:
    """Coordinator exposing .get('context'), .session_id, .parent_id --
    the surface StatusContextHook uses. Mirrors
    amplifier-bundle-skills modules/tool-skills/tests/test_prefix_placement.py's
    own FakeCoordinator shape (a plain duck-typed class, not a Mock)."""

    def __init__(self, context: Any = None) -> None:
        self._context = context
        self.session_id = "test-session-id"
        self.parent_id = None

    def get(self, name: str) -> Any:
        return self._context if name == "context" else None


def _make_coordinator(*, context: Any = None) -> Any:
    """Typed as Any -- same rationale as the tool-skills exemplar's own
    `fake_coordinator` helper: the duck-typed FakeCoordinator satisfies the
    hook's runtime contract but not its (Rust-binding) type annotation."""
    return FakeCoordinator(context)


def _no_git_hook(coordinator: Any, **config_overrides: Any) -> StatusContextHook:
    config: dict[str, Any] = {"working_dir": ".", "include_git": False}
    config.update(config_overrides)
    return StatusContextHook(coordinator, config)


async def _peer_rewrap_unconditionally(context: FakeContext, marker: str) -> None:
    """Simulates ANY other hook sharing the system-prompt-factory slot with
    NO idempotency guard at all -- the worst realistic case (an unfixed
    peer, or simply another hook's wrap landing after ours every time). It
    always wraps the CURRENT factory, unconditionally, adding one more copy
    of its own marker every call. This module's own fix must hold
    regardless of how badly-behaved a peer is."""
    base_factory = context._system_prompt_factory

    async def _peer_factory() -> str:
        base = await base_factory()
        return f'{base}\n\n<system-reminder source="{marker}">peer content</system-reminder>'

    await context.set_system_prompt_factory(_peer_factory)


# ---------------------------------------------------------------------------
# D1-01 -- our own marker never duplicates under a chaotically-rewrapping peer.
# ---------------------------------------------------------------------------


def test_d1_01_own_marker_never_duplicates_under_a_rewrapping_peer():
    """Six simulated requests, a PEER hook re-wrapping the slot before each
    one (so our own identity check would always mismatch on the old, pure-
    identity implementation). Our OWN block must appear EXACTLY ONCE in the
    composed system prompt regardless.

    FAILS BEFORE the fix: our own marker count grows 1 -> 2 -> ... -> 6.
    """
    context = FakeContext()
    coordinator = _make_coordinator(context=context)
    hook = _no_git_hook(coordinator)

    for _ in range(6):
        _run_async(_peer_rewrap_unconditionally(context, "peer-hook"))
        result = _run_async(hook.on_provider_request("provider:request", {}))
        # Unlike tool-skills (which returns "continue" -- ALL its content
        # moved to the prefix), status-context's dynamic "Today's date"
        # line still rides this per-request injection every time (spec
        # sec W2.2) -- only the STATIC block lives in the system prompt.
        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert "Working directory" not in result.context_injection, (
            "the static block must never ALSO ride the per-request "
            "injection once prefix placement is established"
        )

    rendered = _run_async(context._system_prompt_factory())
    own_marker_count = rendered.count(_STATUS_MARKER)
    assert own_marker_count == 1, (
        "Our own block must appear EXACTLY ONCE in the system prompt no "
        "matter how many times a peer hook also wraps the slot between our "
        f"own checks; found {own_marker_count} copies. Rendered length: "
        f"{len(rendered)} chars."
    )


# ---------------------------------------------------------------------------
# D1-02 -- the composed system prompt is BYTE-STABLE across many requests
# once both this hook and a well-behaved peer have each wrapped once.
# ---------------------------------------------------------------------------


def test_d1_02_system_prompt_byte_stable_across_many_requests():
    """A well-behaved peer wraps the slot exactly once (its own realistic,
    already-idempotent behavior -- e.g. routing-matrix or tool-skills after
    their own first request), then this hook's on_provider_request is
    called repeatedly (simulating N iterations / N get_messages_for_request
    calls across turns). The composed system-prompt TEXT must be
    byte-identical across all of them -- the exact property whose absence
    collapsed anthropic's prompt-cache read share from ~90% to ~8% in the
    20260831-rr wave (req0 sys_len=44,525 -> req5 sys_len=153,240 on the
    unfixed code).

    FAILS BEFORE the fix: length grows every call.
    """
    context = FakeContext()
    coordinator = _make_coordinator(context=context)
    hook = _no_git_hook(coordinator)

    # Peer wraps once, up front -- its own stable, already-established state
    # (this is exactly what a peer module like routing-matrix looks like
    # once past its own first request).
    _run_async(_peer_rewrap_unconditionally(context, "routing-matrix"))

    lengths: list[int] = []
    texts: list[str] = []
    for _ in range(6):
        result = _run_async(hook.on_provider_request("provider:request", {}))
        assert result.action == "inject_context"  # the dynamic date line
        rendered = _run_async(context._system_prompt_factory())
        lengths.append(len(rendered))
        texts.append(rendered)

    assert len(set(lengths)) == 1, (
        f"System prompt length must be BYTE-STABLE across requests once "
        f"established; got growing lengths: {lengths!r}"
    )
    assert len(set(texts)) == 1, (
        "System prompt TEXT must be byte-identical across requests"
    )
