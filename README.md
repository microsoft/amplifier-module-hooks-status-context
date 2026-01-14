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
      git_status_include_untracked: true # Include untracked files (default: true)
      git_status_max_untracked: 20       # Max untracked files (default: 20, 0=unlimited)
      git_status_max_tracked: 50         # Max tracked files (default: 50)
      git_status_max_lines: 100          # Hard output cap (default: 100)
      include_datetime: true           # Show date/time (default: true)
      datetime_include_timezone: false # Include TZ name (default: false)
      include_session: true            # Show session ID info (default: true)
      
      # Token safety (tier-based filtering)
      git_status_enable_path_filtering: true  # Enable smart filtering (default: true)
      git_status_tier1_patterns_extend: []    # Extend tier1 ignore patterns
      git_status_tier2_patterns_extend: []    # Extend tier2 limit patterns
      git_status_tier2_limit: 10              # Max tier2 files shown (default: 10)
      git_status_show_filter_summary: true    # Show filter messages (default: true)
```

## Token Safety (Enhanced)

**This module uses smart tier-based filtering to prevent token bloat from large repositories.**

### Why This Matters

Without proper filtering, git status can cause massive token consumption:
- `node_modules/`: 50,000+ files → 200k+ tokens ($3+ per conversation)
- `.venv/`: 10,000+ files → 100k+ tokens
- `build/dist/`: 5,000+ files → 50k+ tokens

**Even if these directories are tracked** (accidentally `git add`ed), they're filtered by default.

### How It Works: Three-Tier System

Files are automatically classified into three tiers:

#### Tier 1: Always Ignore (DoS Prevention)
**Never shown individually, regardless of tracked/untracked status.**

Default patterns:
- **Node/JS**: `node_modules/`, `.npm/`, `.yarn/`, `.pnpm-store/`
- **Python**: `.venv/`, `venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`
- **Build outputs**: `build/`, `dist/`, `out/`, `target/`, `bin/`, `obj/`
- **Git internals**: `.git/`

Treatment:
- Counted but not shown individually
- **WARNING** displayed if tracked files exist in these paths
- Suggestion provided to remove from tracking

#### Tier 2: Limited Display (Support Files)
**Show some, summarize the rest.**

Default patterns:
- Lock files: `*.lock`, `yarn.lock`, `package-lock.json`, `Gemfile.lock`
- IDE configs: `.idea/`, `.vscode/`, `*.swp`
- Logs: `*.log`, `logs/`, `coverage/`
- Minified: `*.min.js`, `*.min.css`, `*.map`

Treatment:
- First 10 shown
- Remainder summarized with count

#### Tier 3: Important Files (Always Show)
**Everything else - your source code.**

Treatment:
- All shown (up to hard limits)
- Tracked files: max 50 (default)
- Untracked files: max 20 (default)
- Hard cap: 100 lines total

### Example Output

**Normal project:**
```
Status:
M  src/api.py
A  src/new_feature.py
?? test.py
?? debug.log

[Filtered: 12,847 untracked files in ignored paths (node_modules: 12,790, .venv: 57)]
```

**Tracked files in ignored paths (problem detected):**
```
Status:
M  src/api.py

[WARNING: 150 tracked files in ignored paths]
  M  node_modules/pkg/index.js
  M  node_modules/pkg/lib.js
  A  .venv/lib/python3.11/site.py
  ... and 147 more
[Suggestion: These directories should not be tracked]
[Filtered: 5,000 untracked files in ignored paths]
```

**Many files changed:**
```
Status:
M  src/api.py
M  src/auth.py
... (48 more tracked files omitted)

[Hard limit reached: output truncated to 100 lines]
[Filtered: 27 support files (lockfiles: 3, IDE configs: 15, logs: 9)]
[Filtered: 8,543 untracked files in ignored paths]
```

### Configuration Examples

**Default (zero config - safe):**
```yaml
hooks:
  - module: hooks-status-context
    # No config needed - filtering enabled by default
```

**Extend ignore patterns:**
```yaml
config:
  git_status_tier1_patterns_extend:
    - "generated/**"
    - "third_party/**"
    - "*.pb.go"  # Protocol buffer generated files
```

**Adjust limits:**
```yaml
config:
  git_status_max_tracked: 100      # Show more tracked files
  git_status_max_untracked: 50     # Show more untracked files
  git_status_tier2_limit: 25       # Show more support files
  git_status_max_lines: 200        # Higher hard limit
```

**Disable path filtering (use with caution):**
```yaml
config:
  git_status_enable_path_filtering: false  # Disable tier filtering
  git_status_max_lines: 500                # Still keep safety cap
```

**Hide filter summaries:**
```yaml
config:
  git_status_show_filter_summary: false  # Cleaner output, less context
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
A src/new_feature.py
?? tests/test_api.py
?? debug.log
... (1,847 more untracked files omitted)

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
