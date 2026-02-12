"""
Status context injection hook module.
Injects current git status and datetime into agent context before each prompt.
"""

# Amplifier module metadata
__amplifier_module_type__ = "hook"

import logging
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from amplifier_core import HookResult
from amplifier_core import ModuleCoordinator

logger = logging.getLogger(__name__)


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
            - include_datetime: Enable datetime injection (default: True)
            - datetime_include_timezone: Include timezone name (default: False)
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
        self.include_datetime = config.get("include_datetime", True)
        self.datetime_include_timezone = config.get("datetime_include_timezone", False)

        # Session options
        self.include_session = config.get("include_session", True)

        # Hook priority
        self.priority = config.get("priority", 0)

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

        Args:
            event: Event name (provider:request)
            data: Event data

        Returns:
            HookResult with context injection
        """
        # Gather environment info (always shown)
        env_info = self._gather_env_info()

        # Gather git status details (only if repo detected and enabled)
        git_details = None
        if self.include_git and env_info.get("is_git_repo"):
            git_details = self._gather_git_context()

        # Build context injection wrapped in system-reminder tags
        context_parts = [env_info["formatted"]]
        if git_details:
            context_parts.append(git_details)

        context_content = "\n\n".join(context_parts)
        behavioral_note = "\n\nThis context is for your reference only. DO NOT mention this status information to the user unless directly relevant to their question. Process silently and continue your work."
        context_injection = f'<system-reminder source="hooks-status-context">\n{context_content}{behavioral_note}\n</system-reminder>'

        return HookResult(
            action="inject_context",
            context_injection=context_injection,
            context_injection_role="user",  # User role ensures visibility across all providers
            ephemeral=True,  # Temporary injection, not stored in context
            suppress_output=True,  # Don't show verbose status to user
            append_to_last_tool_result=True,  # Merge into last message to prevent consecutive user messages
        )

    def _gather_env_info(self) -> dict[str, Any]:
        """Gather environment information (working dir, platform, OS, date, session, git detection)."""
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

            # Get current date (with optional time)
            now = datetime.now()
            if self.include_datetime:
                if self.datetime_include_timezone:
                    timezone_name = now.astimezone().tzname()
                    date_str = f"{now.strftime('%Y-%m-%d %H:%M:%S')} {timezone_name}"
                else:
                    date_str = f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                date_str = now.strftime("%Y-%m-%d")

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

            # Format the env block
            env_lines = [
                "Here is useful information about the environment you are running in:",
                "<env>",
                f"Working directory: {working_dir}",
            ]

            # Add session info if available
            if self.include_session and session_id:
                env_lines.append(f"Session ID: {session_id}")
                if is_sub_session:
                    env_lines.append(f"Parent Session ID: {parent_session_id}")
                    env_lines.append("Is sub-session: Yes")
                else:
                    env_lines.append("Is sub-session: No")

            env_lines.extend(
                [
                    f"Is directory a git repo: {'Yes' if is_git_repo else 'No'}",
                    f"Platform: {platform_name}",
                    f"OS Version: {os_version}",
                    f"Today's date: {date_str}",
                    "</env>",
                ]
            )

            formatted = "\n".join(env_lines)

            return {
                "working_dir": working_dir,
                "is_git_repo": is_git_repo,
                "platform": platform_name,
                "os_version": os_version,
                "date": date_str,
                "session_id": session_id,
                "parent_session_id": parent_session_id,
                "is_sub_session": is_sub_session,
                "formatted": formatted,
            }

        except Exception as e:
            logger.warning(f"Failed to gather environment info: {e}")
            # Return minimal info on failure with configured working_dir
            working_dir_path = Path(self.working_dir)
            if not working_dir_path.is_absolute():
                fallback_dir = str(Path.cwd() / working_dir_path)
            else:
                fallback_dir = str(working_dir_path)
            return {
                "working_dir": fallback_dir,
                "is_git_repo": False,
                "platform": "unknown",
                "os_version": "unknown",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "session_id": None,
                "parent_session_id": None,
                "is_sub_session": False,
                "formatted": "Here is useful information about the environment you are running in:\n<env>\nEnvironment information unavailable\n</env>",
            }

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
