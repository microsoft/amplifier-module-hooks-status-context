"""
Status context injection hook module.
Injects current git status and datetime into agent context before each prompt.
"""

import logging
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from amplifier_core import HookResult
from amplifier_core import ModuleCoordinator

logger = logging.getLogger(__name__)


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """
    Mount the status context hook.

    Args:
        coordinator: Module coordinator
        config: Optional configuration
            - include_git: Enable git status injection (default: True)
            - git_include_status: Include working directory status (default: True)
            - git_include_commits: Number of recent commits (default: 5)
            - git_include_branch: Include current branch (default: True)
            - git_include_main_branch: Detect main branch (default: True)
            - include_datetime: Enable datetime injection (default: True)
            - datetime_include_timezone: Include timezone name (default: False)
            - priority: Hook priority (default: 0)

    Returns:
        Optional cleanup function
    """
    config = config or {}
    hook = StatusContextHook(config)
    hook.register(coordinator.hooks)
    logger.info("Mounted hooks-status-context")
    return


class StatusContextHook:
    """
    Hook that injects status context (git, datetime) before each prompt.
    """

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the status context hook.

        Args:
            config: Configuration dict with options for git and datetime injection
        """
        # Git context options
        self.include_git = config.get("include_git", True)
        self.git_include_status = config.get("git_include_status", True)
        self.git_include_commits = config.get("git_include_commits", 5)
        self.git_include_branch = config.get("git_include_branch", True)
        self.git_include_main_branch = config.get("git_include_main_branch", True)

        # Datetime options
        self.include_datetime = config.get("include_datetime", True)
        self.datetime_include_timezone = config.get("datetime_include_timezone", False)

        # Hook priority
        self.priority = config.get("priority", 0)

    def register(self, hooks):
        """Register this hook for provider:request events (fires right before LLM call)."""
        hooks.register(
            "provider:request", self.on_provider_request, priority=self.priority, name="hooks-status-context"
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

        # Build context injection
        context_parts = [env_info["formatted"]]
        if git_details:
            context_parts.append(git_details)

        context_injection = "\n\n".join(context_parts)

        return HookResult(
            action="inject_context",
            context_injection=context_injection,
            context_injection_role="user",  # User role more visible than system
            ephemeral=True,  # Temporary injection, not stored in context
            suppress_output=True,  # Don't show verbose status to user
        )

    def _gather_env_info(self) -> dict[str, Any]:
        """Gather environment information (working dir, platform, OS, date, git detection)."""
        try:
            # Get current working directory
            working_dir = str(Path.cwd())

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

            # Format the env block
            formatted = (
                "Here is useful information about the environment you are running in:\n"
                "<env>\n"
                f"Working directory: {working_dir}\n"
                f"Is directory a git repo: {'Yes' if is_git_repo else 'No'}\n"
                f"Platform: {platform_name}\n"
                f"OS Version: {os_version}\n"
                f"Today's date: {date_str}\n"
                "</env>"
            )

            return {
                "working_dir": working_dir,
                "is_git_repo": is_git_repo,
                "platform": platform_name,
                "os_version": os_version,
                "date": date_str,
                "formatted": formatted,
            }

        except Exception as e:
            logger.warning(f"Failed to gather environment info: {e}")
            # Return minimal info on failure
            return {
                "working_dir": str(Path.cwd()),
                "is_git_repo": False,
                "platform": "unknown",
                "os_version": "unknown",
                "date": datetime.now().strftime("%Y-%m-%d"),
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
                        parts.append(f"\nMain branch (you will usually use this for PRs): {main_branch}")
                        break

            # Working directory status
            if self.git_include_status:
                status = self._run_git(["status", "--short"])
                if status:
                    parts.append(f"\nStatus:\n{status}")

            # Recent commits
            if self.git_include_commits and self.git_include_commits > 0:
                log = self._run_git(["log", "--oneline", f"-{self.git_include_commits}"])
                if log:
                    parts.append(f"\nRecent commits:\n{log}")

            return "\n".join(parts) if len(parts) > 1 else None

        except Exception as e:
            logger.warning(f"Failed to gather git context: {e}")
            return None

    def _run_git(self, args: list[str], timeout: float = 1.0) -> str | None:
        """Run a git command and return output."""
        try:
            result = subprocess.run(
                ["git"] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=Path.cwd(),
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return None
