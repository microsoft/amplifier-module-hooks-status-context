"""
Status context injection hook module.
Injects current git status and datetime into agent context before each prompt.
"""

import logging
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
        """Register this hook for prompt:submit events."""
        hooks.register("prompt:submit", self.on_prompt_submit, priority=self.priority, name="hooks-status-context")

    async def on_prompt_submit(self, event: str, data: dict[str, Any]) -> HookResult:
        """
        Inject status context before prompt processing.

        Args:
            event: Event name (prompt:submit)
            data: Event data

        Returns:
            HookResult with context injection
        """
        context_parts = []

        # Gather datetime
        if self.include_datetime:
            datetime_context = self._gather_datetime()
            if datetime_context:
                context_parts.append(datetime_context)

        # Gather git status
        if self.include_git:
            git_context = self._gather_git_context()
            if git_context:
                context_parts.append(git_context)

        # Inject combined context
        if context_parts:
            context_injection = "\n\n".join(context_parts)
            result = HookResult(
                action="inject_context",
                context_injection=context_injection,
                context_injection_role="user",  # User role more visible than system
                ephemeral=True,  # Temporary injection, not stored in context (git status doesn't need persistence)
                suppress_output=True,  # Don't show verbose status to user
            )
            return result

        return HookResult(action="continue")

    def _gather_datetime(self) -> str | None:
        """Gather current date/time in local timezone."""
        try:
            now = datetime.now()

            if self.datetime_include_timezone:
                # Include timezone name if requested
                timezone_name = now.astimezone().tzname()
                return f"Current date and time: {now.strftime('%Y-%m-%d %H:%M:%S')} {timezone_name}"
            # Simple format without timezone
            return f"Current date and time: {now.strftime('%Y-%m-%d %H:%M:%S')}"

        except Exception as e:
            logger.warning(f"Failed to gather datetime: {e}")
            return None

    def _gather_git_context(self) -> str | None:
        """Gather current git repository context."""
        try:
            # Check if in git repo
            result = self._run_git(["rev-parse", "--git-dir"])
            if result is None:
                return None

            parts = [
                "Git Status: This is the git status at the start of the conversation. "
                "Note that this status is a snapshot in time, and will not update during the conversation."
            ]

            # Current branch
            if self.git_include_branch:
                branch = self._run_git(["branch", "--show-current"])
                if branch:
                    parts.append(f"\nCurrent branch: {branch}")

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
                else:
                    parts.append("\nStatus: Clean (no changes)")

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
