# amplifier-module-hooks-status-context

Injects environment info (working directory, platform, OS, date), session context, and optional git status into agent context before each prompt. Ensures agent has fresh contextual information for decisions.

## Usage

```yaml
hooks:
  - module: hooks-status-context
    source: git+https://github.com/microsoft/amplifier-module-hooks-status-context@main
    config:
      working_dir: "."                 # Working directory for operations (default: ".")
      include_git: true                # Enable git status (default: true)
      git_include_status: true         # Show working dir status (default: true)
      git_include_commits: 5           # Recent commits count (default: 5, 0=disable)
      git_include_branch: true         # Show current branch (default: true)
      git_include_main_branch: true    # Detect main branch (default: true)
      include_datetime: true           # Show date/time (default: true)
      datetime_include_timezone: false # Include TZ name (default: false)
      include_session: true            # Show session ID info (default: true)
```

## Output Format

**In git repository (root session):**

```
<system-reminder>
Here is useful information about the environment you are running in:
<env>
Working directory: /home/user/projects/myapp
Session ID: session_abc123
Is sub-session: No
Is directory a git repo: Yes
Platform: linux
OS Version: Linux 6.6.87.2-microsoft-standard-WSL2
Today's date: 2025-11-09 14:23:45
</env>

gitStatus: This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.
Current branch: feature/new-api

Main branch (you will usually use this for PRs): main

Status:
M src/api.py
?? tests/test_api.py

Recent commits:
abc1234 feat: Add new API endpoint
def5678 refactor: Simplify request handling
</system-reminder>
```

**In a sub-session (spawned by task tool):**

```
<system-reminder>
Here is useful information about the environment you are running in:
<env>
Working directory: /home/user/projects/myapp
Session ID: session_abc123-1234567890abcdef_zen-architect
Parent Session ID: session_abc123
Is sub-session: Yes
Is directory a git repo: Yes
Platform: linux
OS Version: Linux 6.6.87.2-microsoft-standard-WSL2
Today's date: 2025-11-09 14:23:45
</env>
...
</system-reminder>
```

**Outside git repository:**

```
<system-reminder>
Here is useful information about the environment you are running in:
<env>
Working directory: /home/user/documents
Session ID: session_def456
Is sub-session: No
Is directory a git repo: No
Platform: linux
OS Version: Linux 6.6.87.2-microsoft-standard-WSL2
Today's date: 2025-11-09 14:23:45
</env>
</system-reminder>
```

Note: Git status only shown when in a git repository and `include_git: true`. Date format includes time when `include_datetime: true`, otherwise date only. Session lineage (parent session ID) only shown for sub-sessions spawned via the task tool.

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
