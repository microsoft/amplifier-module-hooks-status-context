# amplifier-module-hooks-status-context

Status context injection hook module for Amplifier. Injects fresh git status and current date/time into the agent's context before each prompt submission.

## Purpose

Provides the agent with up-to-date contextual information about:

- **Current date and time** (local timezone)
- **Git repository status** (branch, changes, commits)

This ensures the agent always has fresh status information when making decisions or providing responses.

## Usage

Add to your profile's hooks section:

```yaml
hooks:
  - module: hooks-status-context
    source: git+https://github.com/microsoft/amplifier-module-hooks-status-context@main
    config:
      # Git options (all default to true)
      include_git: true
      git_include_status: true
      git_include_commits: 5
      git_include_branch: true
      git_include_main_branch: true

      # Datetime options
      include_datetime: true
      datetime_include_timezone: false # Set to true to include timezone name
```

## Configuration Options

### Git Status Options

- `include_git` (default: `true`) - Enable/disable git context injection
- `git_include_status` (default: `true`) - Include working directory status (modified, added, deleted files)
- `git_include_commits` (default: `5`) - Number of recent commits to show (set to 0 to disable)
- `git_include_branch` (default: `true`) - Show current branch name
- `git_include_main_branch` (default: `true`) - Detect and show main/master branch

### Datetime Options

- `include_datetime` (default: `true`) - Enable/disable datetime injection
- `datetime_include_timezone` (default: `false`) - Include timezone name (e.g., "PST", "UTC")

### Hook Options

- `priority` (default: `0`) - Hook execution priority (lower = earlier)

## Example Outputs

### Datetime Injection

```
Current date and time: 2025-11-07 11:45:23
```

With timezone:

```
Current date and time: 2025-11-07 11:45:23 PST
```

### Git Status Injection

```
Git Status: This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.

Current branch: feature/status-hooks

Main branch (you will usually use this for PRs): main

Status:
M amplifier-core/amplifier_core/coordinator.py
?? test_hooks_demo.py

Recent commits:
c964339 chore: Update loop-basic submodule with context injection fix
3d94a63 fix: Add coordinator.process_hook_result() calls for context injection
a1b2c3d docs: Update hooks documentation
```

## How It Works

1. **Event Registration**: Hooks into `prompt:submit` event (once per turn)
2. **Context Gathering**: Collects enabled status information (datetime, git)
3. **Context Injection**: Returns `HookResult` with `action="inject_context"` and `ephemeral=True`
4. **Agent Awareness**: Agent sees injected context for current turn (not stored in conversation history)

## When to Use

Use this hook when you want the agent to have awareness of:

- Current time for scheduling or time-sensitive tasks
- Git repository state for code changes and PR workflows
- Branch information for suggesting where to commit

## Disabling Sections

To disable datetime only:

```yaml
config:
  include_datetime: false
```

To disable git only:

```yaml
config:
  include_git: false
```

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
