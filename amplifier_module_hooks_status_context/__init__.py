"""
Status context injection hook module.
Injects current git status and datetime into agent context before each prompt.
"""

# Amplifier module metadata
__amplifier_module_type__ = "hook"

import hashlib
import logging
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from amplifier_core import HookResult
from amplifier_core import ModuleCoordinator

logger = logging.getLogger(__name__)

# Placement of the static block (system-reminder redesign, W2):
#   "prefix"  (default) -- wraps the context module's system-prompt factory
#       (the surface amplifier-foundation _prepared.py registers via
#       context.set_system_prompt_factory; context-simple calls it on EVERY
#       get_messages_for_request) so the static portion (env facts minus
#       date, plus the git snapshot) rides the provider-cached system block
#       instead of being re-sent as fresh input tokens every request. Only
#       the genuinely live "Today's date" line still rides the late,
#       per-turn injection. Sessions without a factory surface fall back to
#       "inject" with a one-time WARNING.
#   "inject" -- the full block (static + date), injected on every
#       provider:request -- byte-identical to `e3cbf7b` behavior. Explicit,
#       fully supported rollback lever.
VALID_PLACEMENTS = ("prefix", "inject")

# The source-attributed marker this module's own prefix block always opens
# with (see _render_static_block). Used as a CONTENT signal in
# _ensure_prefix_placement (rr wave 20260831, D1 cache-regression fix) --
# see that method's docstring for why identity alone is not a safe check
# once more than one hook wraps the same system-prompt-factory slot.
_PREFIX_MARKER = '<system-reminder source="hooks-status-context">'


# Tier 1: Always ignore (DoS prevention) - Even if tracked, these should never bloat context
DEFAULT_TIER1_PATTERNS = [
    "node_modules/**",
    ".npm/**",
    ".yarn/**",
    ".pnpm-store/**",
    ".venv/**",
    "venv/**",
    "env/**",
    "ENV/**",
    "__pycache__/**",
    "*.pyc",
    "*.pyo",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    "build/**",
    "dist/**",
    "out/**",
    "target/**",
    "bin/**",
    "obj/**",
    ".git/**",
]

# Tier 2: Limit with context - Show some, summarize rest
DEFAULT_TIER2_PATTERNS = [
    "*.lock",
    "*.sum",
    "yarn.lock",
    "package-lock.json",
    "Gemfile.lock",
    ".idea/**",
    ".vscode/**",
    "*.swp",
    "*.swo",
    "*.log",
    "logs/**",
    "coverage/**",
    ".coverage",
    "*.min.js",
    "*.min.css",
    "*.map",
]


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """
    Mount the status context hook.

    Args:
        coordinator: Module coordinator
        config: Optional configuration
            - working_dir: Working directory for operations (default: ".")
              If not set, falls back to session.working_dir capability.
            - include_git: Enable git status injection (default: True)
            - git_include_status: Include working directory status (default: True)
            - git_include_commits: Number of recent commits (default: 5)
            - git_include_branch: Include current branch (default: True)
            - git_include_main_branch: Detect main branch (default: True)
            - git_status_include_untracked: Include untracked files (default: True)
            - git_status_max_untracked: Max untracked files to show (default: 20, 0=unlimited)
            - git_status_max_lines: Hard limit on total status lines (default: 100)
            - git_status_enable_path_filtering: Enable tier-based path filtering (default: True)
            - git_status_tier1_patterns_extend: Additional Tier 1 patterns to ignore (default: [])
            - git_status_tier2_patterns_extend: Additional Tier 2 patterns to limit (default: [])
            - git_status_tier2_limit: Max Tier 2 files to show (default: 10)
            - git_status_max_tracked: Max tracked files to show (default: 50)
            - git_status_show_filter_summary: Show filtering messages (default: True)
            - include_datetime: Include time-of-day (hh:mm:ss) in the date field, not
              just the calendar date (default: False). Leave this False unless you
              specifically need sub-day precision: every request re-renders this field
              live (it is never cached), so second-resolution timestamps make the
              injected block differ on almost every single LLM turn. That defeats
              provider-side prompt caching (measured -17 to -20 percentage points of
              cache-hit rate on Gemini, whose caching is implicit/content-addressed
              with no server-side lever to compensate). Calendar-day resolution keeps
              the field truthful and live while changing at most once every 24h.
            - datetime_include_timezone: Include timezone name (default: False).
              Only applies when include_datetime is True.
            - include_session: Enable session ID injection (default: True)
            - priority: Hook priority (default: 0)

    Returns:
        Optional cleanup function
    """
    config = config or {}

    # If working_dir not explicitly set in config, use session.working_dir capability
    # This enables server deployments where Path.cwd() returns the wrong directory
    if "working_dir" not in config:
        working_dir = coordinator.get_capability("session.working_dir")
        if working_dir:
            config = {**config, "working_dir": working_dir}

    hook = StatusContextHook(coordinator, config)
    hook.register(coordinator.hooks)
    logger.info("Mounted hooks-status-context")
    return


class StatusContextHook:
    """
    Hook that injects status context (git, datetime, session) before each prompt.
    """

    def __init__(self, coordinator: ModuleCoordinator, config: dict[str, Any]):
        """
        Initialize the status context hook.

        Args:
            coordinator: Module coordinator for accessing session info
            config: Configuration dict with options for git, datetime, and session injection
        """
        # Store coordinator for session info access
        self.coordinator = coordinator

        # Working directory
        self.working_dir = config.get("working_dir", ".")

        # Git context options
        self.include_git = config.get("include_git", True)
        self.git_include_status = config.get("git_include_status", True)
        self.git_include_commits = config.get("git_include_commits", 5)
        self.git_include_branch = config.get("git_include_branch", True)
        self.git_include_main_branch = config.get("git_include_main_branch", True)

        # Git status truncation options
        self.git_status_include_untracked = config.get(
            "git_status_include_untracked", True
        )
        self.git_status_max_untracked = config.get("git_status_max_untracked", 20)
        self.git_status_max_lines = config.get("git_status_max_lines", 100)

        # Tier-based filtering (NEW - safe by default)
        self.git_status_enable_path_filtering = config.get(
            "git_status_enable_path_filtering", True
        )
        self.tier1_patterns = DEFAULT_TIER1_PATTERNS + config.get(
            "git_status_tier1_patterns_extend", []
        )
        self.tier2_patterns = DEFAULT_TIER2_PATTERNS + config.get(
            "git_status_tier2_patterns_extend", []
        )
        self.git_status_tier2_limit = config.get("git_status_tier2_limit", 10)

        # Hard limits (NEW - safe by default)
        self.git_status_max_tracked = config.get("git_status_max_tracked", 50)

        # Filtering messages (NEW)
        self.git_status_show_filter_summary = config.get(
            "git_status_show_filter_summary", True
        )

        # Datetime options
        # Default is False (calendar-date only, no time-of-day): the date field is
        # rendered fresh on every request (never cached -- see _current_date_str), so
        # second-resolution timestamps make the injected block differ on nearly every
        # LLM turn. That silently defeats provider-side prompt caching -- measured
        # -17 to -20 percentage points of cache-hit rate on Gemini in production, with
        # no server-side mitigation available (Gemini's caching is implicit/content-
        # addressed). Set to True only if the agent genuinely needs sub-day precision;
        # doing so knowingly reintroduces that cache cost.
        self.include_datetime = config.get("include_datetime", False)
        self.datetime_include_timezone = config.get("datetime_include_timezone", False)

        # Session options
        self.include_session = config.get("include_session", True)

        # Hook priority
        self.priority = config.get("priority", 0)

        # Placement of the static block (system-reminder redesign, W2).
        self.placement = config.get("placement", "prefix")
        if self.placement not in VALID_PLACEMENTS:
            raise ValueError(
                f"Invalid placement={self.placement!r}. "
                f"Valid values: {', '.join(VALID_PLACEMENTS)}."
            )
        # Prefix-placement state: the wrapped factory we registered (identity
        # check for re-wrap detection), the fingerprint hash of the last
        # static render, the cached rendered static block, and a one-time
        # fallback-warning flag.
        self._prefix_factory: Any = None
        self._prefix_static_hash: str | None = None
        self._prefix_rendered: str = ""
        self._prefix_unavailable_logged = False
        # rr wave 20260831 (D1 cache-regression fix): the last `current`
        # factory object we CONTENT-VERIFIED already carries our own marker
        # (see _ensure_prefix_placement). Avoids re-rendering the whole
        # chain on every request once verified once for a given factory
        # object.
        self._prefix_verified_factory: Any = None

        # --- Per-session snapshot cache ---
        # Everything cached here (working dir, platform, OS, git-repo detection, and
        # the entire git status/branch/log block) is invariant for the lifetime of a
        # session: this hook instance is created fresh per mount() (see amplifier-core's
        # module lifecycle contract -- one mount() per session, including forked child
        # sessions), so caching on `self` is exactly session-scoped. Computing these
        # once instead of on every provider:request also matches the git block's own
        # documented promise to the agent ("a snapshot in time ... will not update
        # during the conversation") -- previously the code silently violated that
        # promise by re-running `git status`/`git log` on every single turn.
        self._cached_static_env: dict[str, Any] | None = None
        self._git_details_computed = False
        self._cached_git_details: str | None = None

    def register(self, hooks):
        """Register this hook for provider:request events (fires right before LLM call)."""
        hooks.register(
            "provider:request",
            self.on_provider_request,
            priority=self.priority,
            name="hooks-status-context",
        )

    async def on_provider_request(self, event: str, data: dict[str, Any]) -> HookResult:
        """
        Inject status context before provider request (right before LLM call).

        Placement (system-reminder redesign, W2):
          - "prefix" (default): the STATIC portion (env facts minus date,
            plus the git snapshot) rides the system prompt via the wrapped
            factory (see _ensure_prefix_placement); only the live "Today's
            date" line still rides this per-request injection.
          - "inject" (rollback): the full block (static + date) rides this
            per-request injection every time, byte-identical to `e3cbf7b`.

        Args:
            event: Event name (provider:request)
            data: Event data

        Returns:
            HookResult with context injection
        """
        if self.placement == "prefix":
            if await self._ensure_prefix_placement():
                return HookResult(
                    action="inject_context",
                    context_injection=self._render_dynamic_block(),
                    context_injection_role="user",
                    ephemeral=True,
                    suppress_output=True,
                )
            # Placement surface unavailable (no context module / no factory
            # support). Warn once, then fall back to injecting the FULL
            # block every request so the agent is never silently blinded to
            # its static environment facts.
            if not self._prefix_unavailable_logged:
                logger.warning(
                    "placement='prefix' (the default) but the context "
                    "module offers no system-prompt factory surface "
                    "(set_system_prompt_factory). Falling back to "
                    "per-request injection of the full block -- the "
                    "static env/git facts will not ride the stable cached "
                    "prefix. Set placement='inject' to silence this "
                    "warning."
                )
                self._prefix_unavailable_logged = True

        return HookResult(
            action="inject_context",
            context_injection=self._render_full_block(),
            context_injection_role="user",  # User role more visible than system
            ephemeral=True,  # Temporary injection, not stored in context
            suppress_output=True,  # Don't show verbose status to user
        )

    def _render_full_block(self) -> str:
        """Render the FULL block (static env/git facts + live date),
        wrapped in the system-reminder envelope -- byte-identical to
        `e3cbf7b`'s single-block behavior. Used for `placement="inject"`
        (the rollback lever) and as the fallback when the prefix-placement
        surface is unavailable."""
        static_env = self._get_static_env()
        formatted_env = self._format_env_block(static_env, self._current_date_str())

        git_details = None
        if self.include_git and static_env.get("is_git_repo"):
            git_details = self._get_git_details()

        context_parts = [formatted_env]
        if git_details:
            context_parts.append(git_details)

        context_content = "\n\n".join(context_parts)
        behavioral_note = "\n\nThis context is for your reference only. DO NOT mention this status information to the user unless directly relevant to their question. Process silently and continue your work."
        return f'<system-reminder source="hooks-status-context">\n{context_content}{behavioral_note}\n</system-reminder>'

    def _render_static_block(self) -> str:
        """Render the STATIC portion only (env facts minus the `Today's
        date` line, plus the git snapshot), wrapped in the system-reminder
        envelope, for placement in the system prompt (system-reminder
        redesign, W2). Both inputs are session-cached and invariant for
        the session, so this render is itself effectively constant --
        safe to place in the stable, provider-cached system prefix. The
        behavioral note lives here (stated once, in the system prompt)
        rather than in the per-turn dynamic block."""
        static_env = self._get_static_env()
        env_block = self._format_env_block(static_env, "", include_date=False)

        git_details = None
        if self.include_git and static_env.get("is_git_repo"):
            git_details = self._get_git_details()

        parts = [env_block]
        if git_details:
            parts.append(git_details)

        content = "\n\n".join(parts)
        behavioral_note = "\n\nThis context is for your reference only. DO NOT mention this status information to the user unless directly relevant to their question. Process silently and continue your work."
        return f'<system-reminder source="hooks-status-context">\n{content}{behavioral_note}\n</system-reminder>'

    def _render_dynamic_block(self) -> str:
        """Render ONLY the live `Today's date` line (system-reminder
        redesign, W2) -- the one genuinely time-varying fact, wrapped in
        the same source-attributed envelope. Rides the per-turn injection
        in `placement="prefix"` mode, ~20 tokens instead of the full
        env+git block."""
        date_line = f"Today's date: {self._current_date_str()}"
        return f'<system-reminder source="hooks-status-context">\n{date_line}\n</system-reminder>'

    async def _ensure_prefix_placement(self) -> bool:
        """Ensure the static block rides the system prompt (stable prefix).

        Originally a near-verbatim port of tool-skills'
        `_ensure_prefix_placement` (amplifier-bundle-skills
        modules/tool-skills/hooks.py:232-290): defensive
        coordinator.get("context") lookup, refuse to replace a static
        system prompt (no factory registered), lazy wrap on first
        provider:request.

        rr wave 20260831 (D1 cache-regression fix): the ORIGINAL re-wrap
        check here was pure object identity -- `current is self._prefix_factory`.
        That is safe ONLY while this hook is the sole wrapper of the
        system-prompt-factory slot (tool-skills' own production history).
        Once a SECOND independent hook (e.g. routing-matrix) ALSO wraps the
        same slot with the same pattern, identity breaks: every hook whose
        own wrap is not the OUTERMOST one sees `current` change out from
        under it on every subsequent request (because a PEER hook's wrap
        moved the slot forward), concludes "not wrapped yet", and wraps
        AGAIN around a chain that already contains its own prior
        contribution. With N such hooks all doing this, the composed
        system prompt gains N NEW copies of every hook's block on every
        single request -- unbounded, per-request growth (confirmed on the
        wire: rr-anth-01's system prompt grew ~21.7K chars/request, every
        hook's block count incrementing 1 -> 2 -> 3 -> ... in lockstep with
        the request index). This is what collapsed anthropic's prompt-cache
        read share from ~90% to ~8% in the 20260831-rr validation wave.

        The fix: keep the identity check as a fast path (nothing has
        touched the slot since we last verified/wrapped it -> cheap,
        no-op). When identity fails, do NOT assume "not yet wrapped" --
        render the CURRENT chain once and check whether our own
        source-attributed marker (_PREFIX_MARKER) is already present
        somewhere in it. If so, our content already rides the system
        prompt (just nested under a peer hook's OUTER wrap) and touching
        the slot again would only duplicate it -- return True without
        calling set_system_prompt_factory. Only wrap when our marker is
        genuinely absent (first request ever, or the base was legitimately
        reset to something with no memory of us). The verified factory
        object is cached (_prefix_verified_factory) so this content check
        runs at most once per distinct factory object, not every request.

        Returns:
            True when the static block is (now) riding the system prompt;
            False when the surface is unavailable and the caller should
            fall back to full-block per-request injection.
        """
        getter = getattr(self.coordinator, "get", None) if self.coordinator else None
        context: Any = getter("context") if callable(getter) else None
        if context is None or not hasattr(context, "set_system_prompt_factory"):
            return False

        current = getattr(context, "_system_prompt_factory", None)
        if current is None:
            # No factory registered (static-system-message session).
            # Wrapping would DROP the static system prompt (factory takes
            # precedence over stored system messages in context-simple),
            # so refuse and let the caller fall back.
            return False
        if current is self._prefix_factory or current is self._prefix_verified_factory:
            return (
                True  # fast path -- nothing has touched the slot since we last checked
            )

        # Slow path: a peer hook has (re-)wrapped the slot since we last
        # looked. Render once and check CONTENT, not identity -- see
        # docstring above for why identity alone cannot tell "already
        # present, just wrapped by someone else afterward" apart from
        # "genuinely never wrapped".
        current_text = await current()
        if _PREFIX_MARKER in current_text:
            self._prefix_verified_factory = current
            return True

        base_factory = current

        async def _status_prefixed_factory() -> str:
            base = await base_factory()
            block = self._render_prefix_block()
            return f"{base}\n\n{block}" if block else base

        await context.set_system_prompt_factory(_status_prefixed_factory)
        self._prefix_factory = _status_prefixed_factory
        logger.info(
            "Status-context static block placement: system-prompt prefix "
            "(wrapped the registered system-prompt factory)"
        )
        return True

    def _render_prefix_block(self) -> str:
        """Render the static block for prefix placement, cached by a
        fingerprint hash of its own inputs.

        Both inputs (`_get_static_env()`, `_get_git_details()`) are
        already session-cached and, in practice, never change mid-session
        -- so this render happens once. Keeping the hash-gate discipline
        anyway costs nothing and matches the "re-render on change, never
        go stale" pattern used elsewhere (e.g. tool-skills' own prefix
        placement).
        """
        static_env = self._get_static_env()
        git_details = (
            self._get_git_details()
            if (self.include_git and static_env.get("is_git_repo"))
            else None
        )
        fingerprint = repr((sorted(static_env.items()), git_details))
        fp_hash = hashlib.sha256(fingerprint.encode()).hexdigest()
        if fp_hash != self._prefix_static_hash:
            self._prefix_static_hash = fp_hash
            self._prefix_rendered = self._render_static_block()
        return self._prefix_rendered

    def _get_static_env(self) -> dict[str, Any]:
        """Return environment facts that cannot change during a session.

        Computed once (lazily, on first request) and cached on `self` for the
        remaining lifetime of this hook instance. Working directory, platform, OS
        version, session identity, and git-repo detection are all fixed at session
        start -- there is no need to re-stat the filesystem, re-invoke `platform`,
        or re-shell out to `git rev-parse` on every single LLM turn just to get the
        same answer back. This also means a transient failure is captured once
        (as a stable fallback) rather than logged and retried on every request.
        """
        if self._cached_static_env is not None:
            return self._cached_static_env

        try:
            # Get working directory (from config or current directory)
            working_dir_path = Path(self.working_dir)
            if not working_dir_path.is_absolute():
                working_dir = str(Path.cwd() / working_dir_path)
            else:
                working_dir = str(working_dir_path)

            # Detect if in git repo
            is_git_repo = self._run_git(["rev-parse", "--git-dir"]) is not None

            # Get platform info
            platform_name = platform.system().lower()

            # Get OS version
            os_version = platform.platform()

            # Get session info (from kernel via coordinator)
            session_id = None
            parent_session_id = None
            is_sub_session = False
            if self.include_session:
                try:
                    session_id = self.coordinator.session_id
                    parent_session_id = self.coordinator.parent_id
                    is_sub_session = parent_session_id is not None
                except Exception as e:
                    logger.debug(f"Could not get session info: {e}")

            result = {
                "working_dir": working_dir,
                "is_git_repo": is_git_repo,
                "platform": platform_name,
                "os_version": os_version,
                "session_id": session_id,
                "parent_session_id": parent_session_id,
                "is_sub_session": is_sub_session,
            }

        except Exception as e:
            logger.warning(f"Failed to gather environment info: {e}")
            # Return minimal info on failure with configured working_dir
            working_dir_path = Path(self.working_dir)
            if not working_dir_path.is_absolute():
                fallback_dir = str(Path.cwd() / working_dir_path)
            else:
                fallback_dir = str(working_dir_path)
            result = {
                "working_dir": fallback_dir,
                "is_git_repo": False,
                "platform": "unknown",
                "os_version": "unknown",
                "session_id": None,
                "parent_session_id": None,
                "is_sub_session": False,
                "error": True,
            }

        self._cached_static_env = result
        return result

    def _current_date_str(self) -> str:
        """Return the current date, freshly computed on every call.

        Deliberately NOT cached, unlike the static env facts and git details above:
        "today's date" is a genuinely live fact that can roll over mid-session, and
        reporting a cached-and-stale date would violate the no-silent-staleness rule.
        Instead the granularity is coarsened (see include_datetime, default False):
        calendar-day resolution stays truthful and live while changing at most once
        per 24h, so repeated calls on the same day still produce a byte-identical
        env block without ever caching (and therefore risking) a stale value.
        """
        now = datetime.now()
        if self.include_datetime:
            if self.datetime_include_timezone:
                timezone_name = now.astimezone().tzname()
                return f"{now.strftime('%Y-%m-%d %H:%M:%S')} {timezone_name}"
            return f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
        return now.strftime("%Y-%m-%d")

    def _format_env_block(
        self,
        static_env: dict[str, Any],
        date_str: str,
        *,
        include_date: bool = True,
    ) -> str:
        """Render the `<env>` block from cached static facts plus the live date string.

        `include_date` (system-reminder redesign, W2): False for the
        static-block render (`_render_static_block`), since the `Today's
        date` line is the one genuinely live fact and rides the separate
        dynamic injection instead. `date_str` is ignored entirely when
        `include_date` is False.
        """
        if static_env.get("error"):
            return (
                "Here is useful information about the environment you are running in:"
                "\n<env>\nEnvironment information unavailable\n</env>"
            )

        env_lines = [
            "Here is useful information about the environment you are running in:",
            "<env>",
            f"Working directory: {static_env['working_dir']}",
        ]

        # Add session info if available
        if self.include_session and static_env.get("session_id"):
            env_lines.append(f"Session ID: {static_env['session_id']}")
            if static_env["is_sub_session"]:
                env_lines.append(
                    f"Parent Session ID: {static_env['parent_session_id']}"
                )
                env_lines.append("Is sub-session: Yes")
            else:
                env_lines.append("Is sub-session: No")

        env_lines.extend(
            [
                f"Is directory a git repo: {'Yes' if static_env['is_git_repo'] else 'No'}",
                f"Platform: {static_env['platform']}",
                f"OS Version: {static_env['os_version']}",
            ]
        )
        if include_date:
            env_lines.append(f"Today's date: {date_str}")
        env_lines.append("</env>")

        return "\n".join(env_lines)

    def _get_git_details(self) -> str | None:
        """Return the git status/branch/commits block, computed once per session.

        This block explicitly tells the agent it is "a snapshot in time" that "will
        not update during the conversation" (see _gather_git_context below). Prior to
        this fix that promise was false: `git status`/`git log`/`git branch` were
        re-run on every single provider:request, so the block silently changed turn
        to turn even when nothing in the repo had changed. Caching here makes the
        implementation match its own documented contract, and incidentally removes
        several subprocess invocations from every LLM turn after the first.
        """
        if not self._git_details_computed:
            self._cached_git_details = self._gather_git_context()
            self._git_details_computed = True
        return self._cached_git_details

    def _gather_git_context(self) -> str | None:
        """Gather current git repository context (assumes already detected as git repo)."""
        try:
            parts = [
                "gitStatus: This is the git status at the start of the conversation. "
                "Note that this status is a snapshot in time, and will not update during the conversation."
            ]

            # Current branch
            if self.git_include_branch:
                branch = self._run_git(["branch", "--show-current"])
                if branch:
                    parts.append(f"Current branch: {branch}")

            # Main branch detection
            if self.git_include_main_branch:
                for main_branch in ["main", "master"]:
                    result = self._run_git(["rev-parse", "--verify", main_branch])
                    if result is not None:
                        parts.append(
                            f"\nMain branch (you will usually use this for PRs): {main_branch}"
                        )
                        break

            # Working directory status
            if self.git_include_status:
                status = self._gather_git_status()
                if status:
                    parts.append(f"\nStatus:\n{status}")

            # Recent commits
            if self.git_include_commits and self.git_include_commits > 0:
                log = self._run_git(
                    ["log", "--oneline", f"-{self.git_include_commits}"]
                )
                if log:
                    parts.append(f"\nRecent commits:\n{log}")

            return "\n".join(parts) if len(parts) > 1 else None

        except Exception as e:
            logger.warning(f"Failed to gather git context: {e}")
            return None

    def _matches_tier(self, filepath: str, patterns: list[str]) -> bool:
        """Check if filepath matches any pattern in the list.

        Args:
            filepath: File path to check
            patterns: List of glob patterns to match against

        Returns:
            True if filepath matches any pattern
        """
        import fnmatch

        for pattern in patterns:
            # Handle directory patterns (ends with /**)
            if pattern.endswith("/**"):
                prefix = pattern[:-3]  # Remove /**
                if filepath.startswith(prefix):
                    return True
            # Handle glob patterns
            elif fnmatch.fnmatch(filepath, pattern):
                return True
        return False

    def _classify_status_line(self, line: str) -> tuple[str, str, str]:
        """Classify git status line into tier.

        Args:
            line: Git status line in --short format (e.g., "M  file.py" or "?? dir/file.js")

        Returns:
            Tuple of (tier, filepath, status_code)
            - tier: "tier1", "tier2", or "tier3"
            - filepath: The file path from the status line
            - status_code: The git status code (e.g., "M", "A", "??")
        """
        # Parse git status --short format: "XY filepath"
        # Status codes are 2 characters, followed by space
        status_code = line[:2].strip()
        filepath = line[3:].strip() if len(line) > 3 else ""

        if not self.git_status_enable_path_filtering:
            return ("tier3", filepath, status_code)

        # Check tier 1 (always ignore)
        if self._matches_tier(filepath, self.tier1_patterns):
            return ("tier1", filepath, status_code)

        # Check tier 2 (limit with context)
        if self._matches_tier(filepath, self.tier2_patterns):
            return ("tier2", filepath, status_code)

        # Everything else is tier 3 (show)
        return ("tier3", filepath, status_code)

    def _gather_git_status(self) -> str | None:
        """
        Get git status with tier-based path filtering.

        Three-tier classification system:
        - Tier 1 (Always Ignore): node_modules/, .venv/, build/, etc. - Even if tracked
        - Tier 2 (Limit with Context): *.lock, .vscode/, *.log, etc. - Show some, summarize rest
        - Tier 3 (Always Show): Source code and important files

        Returns:
            Formatted git status output with tier-based filtering applied
        """
        raw_status = self._run_git(["status", "--short"])
        if not raw_status:
            return "Working directory clean"

        # Classify all lines into tiers
        tier1_tracked = []
        tier1_untracked = []
        tier2_lines = []
        tier3_tracked = []
        tier3_untracked = []

        for line in raw_status.splitlines():
            tier, filepath, status = self._classify_status_line(line)

            if tier == "tier1":
                if status == "??":
                    tier1_untracked.append(line)
                else:
                    tier1_tracked.append(line)
            elif tier == "tier2":
                tier2_lines.append(line)
            else:  # tier3
                if status == "??":
                    tier3_untracked.append(line)
                else:
                    tier3_tracked.append(line)

        # Build output
        result = []

        # Tier 3 tracked: Apply tracked limit
        if len(tier3_tracked) <= self.git_status_max_tracked:
            result.extend(tier3_tracked)
        else:
            result.extend(tier3_tracked[: self.git_status_max_tracked])
            omitted = len(tier3_tracked) - self.git_status_max_tracked
            if self.git_status_show_filter_summary:
                result.append(f"... ({omitted} more tracked files omitted)")

        # Tier 3 untracked: Apply untracked limit (existing logic)
        if self.git_status_include_untracked:
            if len(tier3_untracked) <= self.git_status_max_untracked:
                result.extend(tier3_untracked)
            else:
                result.extend(tier3_untracked[: self.git_status_max_untracked])
                omitted = len(tier3_untracked) - self.git_status_max_untracked
                if self.git_status_show_filter_summary:
                    result.append(f"... ({omitted} more untracked files omitted)")

        # Tier 2: Limited display
        if len(tier2_lines) <= self.git_status_tier2_limit:
            result.extend(tier2_lines)
        else:
            result.extend(tier2_lines[: self.git_status_tier2_limit])
            omitted = len(tier2_lines) - self.git_status_tier2_limit
            if self.git_status_show_filter_summary:
                result.append(f"... ({omitted} more support files omitted)")

        # Add blank line before summaries if we showed files
        if (
            result
            and self.git_status_show_filter_summary
            and (tier1_tracked or tier1_untracked)
        ):
            result.append("")

        # Tier 1 summaries with explicit messages
        if self.git_status_show_filter_summary:
            if tier1_tracked:
                # WARNING: Tracked files in ignored paths
                result.append(
                    f"[WARNING: {len(tier1_tracked)} tracked files in ignored paths]"
                )
                # Show examples
                examples = tier1_tracked[:3]
                for ex in examples:
                    result.append(f"  {ex}")
                if len(tier1_tracked) > 3:
                    result.append(f"  ... and {len(tier1_tracked) - 3} more")
                result.append("[Suggestion: These directories should not be tracked]")

            if tier1_untracked:
                result.append(
                    f"[Filtered: {len(tier1_untracked)} untracked files in ignored paths]"
                )

        # Apply absolute hard limit (safety backstop)
        if len(result) > self.git_status_max_lines:
            result = result[: self.git_status_max_lines]
            result.append(
                f"[Hard limit reached: output truncated to {self.git_status_max_lines} lines]"
            )

        return "\n".join(result) if result else "Working directory clean"

    def _run_git(self, args: list[str], timeout: float = 1.0) -> str | None:
        """Run a git command and return output."""
        try:
            # Resolve working directory (handle relative paths)
            working_dir_path = Path(self.working_dir)
            if not working_dir_path.is_absolute():
                cwd = Path.cwd() / working_dir_path
            else:
                cwd = working_dir_path

            result = subprocess.run(
                ["git"] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return None
