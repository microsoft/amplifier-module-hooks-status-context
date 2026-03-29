"""
Token safety tests for git status truncation.

These tests verify that the smart truncation feature prevents token DoS attacks
from large numbers of untracked files while preserving important tracked changes.
"""

import pytest
from unittest.mock import Mock, patch
from amplifier_module_hooks_status_context import StatusContextHook


class TestTokenSafety:
    """Test suite for token-safe git status truncation."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator for testing."""
        coordinator = Mock()
        coordinator.session_id = "test-session-id"
        coordinator.parent_id = None
        return coordinator

    @pytest.fixture
    def default_hook(self, mock_coordinator):
        """Create a hook with default configuration."""
        config = {
            "working_dir": ".",
            "include_git": True,
            "git_include_status": True,
            "git_include_branch": False,
            "git_include_commits": 0,
            "git_include_main_branch": False,
            "git_status_include_untracked": True,
            "git_status_max_untracked": 20,
            "git_status_max_lines": 100,
            "git_status_enable_path_filtering": True,
            "git_status_max_tracked": 50,
            "git_status_show_filter_summary": True,
        }
        return StatusContextHook(mock_coordinator, config)

    def test_empty_git_status(self, default_hook):
        """Empty status returns 'Working directory clean'."""
        # Mock _run_git to return empty/None for status
        with patch.object(default_hook, "_run_git", return_value=None):
            status = default_hook._gather_git_status()
            assert status == "Working directory clean"

    def test_only_tracked_changes(self, default_hook):
        """All tracked changes shown, no truncation."""
        # Simulate 10 tracked changes, no untracked
        tracked_files = [
            " M file1.py",
            "M  file2.py",
            "MM file3.py",
            "A  file4.py",
            "D  file5.py",
            "R  file6.py -> file7.py",
            "C  file8.py -> file9.py",
            "UU file10.py",
            "AA file11.py",
            "DD file12.py",
        ]
        git_output = "\n".join(tracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # All tracked files should be present
            assert len(status_lines) == len(tracked_files)
            for tracked_file in tracked_files:
                assert tracked_file in status_lines

    def test_only_untracked_files_under_limit(self, default_hook):
        """<20 untracked files, all shown."""
        # Simulate 15 untracked files (under default limit of 20)
        untracked_files = [f"?? untracked{i}.txt" for i in range(15)]
        git_output = "\n".join(untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # All untracked files should be present
            assert len(status_lines) == 15
            for untracked_file in untracked_files:
                assert untracked_file in status_lines

    def test_many_untracked_files_truncated(self, default_hook):
        """>20 untracked files, shows first 20 + summary."""
        # Simulate 100 untracked files (well over limit)
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(100)]
        git_output = "\n".join(untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # Should have 20 untracked files + 1 summary line
            assert len(status_lines) == 21

            # First 20 untracked files should be present
            for i in range(20):
                assert untracked_files[i] in status_lines

            # Summary line should indicate 80 more files omitted
            assert "... (80 more untracked files omitted)" in status_lines

    def test_mixed_tracked_and_untracked(self, default_hook):
        """All tracked shown, untracked limited."""
        # Simulate 5 tracked changes and 50 untracked files
        tracked_files = [
            " M tracked1.py",
            "M  tracked2.py",
            "A  tracked3.py",
            "D  tracked4.py",
            "MM tracked5.py",
        ]
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(50)]
        git_output = "\n".join(tracked_files + untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # Should have: 5 tracked + 20 untracked + 1 summary = 26 lines
            assert len(status_lines) == 26

            # All tracked files should be present
            for tracked_file in tracked_files:
                assert tracked_file in status_lines

            # First 20 untracked files should be present
            for i in range(20):
                assert untracked_files[i] in status_lines

            # Summary line should indicate 30 more files omitted
            assert "... (30 more untracked files omitted)" in status_lines

    def test_include_untracked_false(self, mock_coordinator):
        """Skip all untracked files when disabled."""
        # Create hook with include_untracked disabled
        config = {
            "working_dir": ".",
            "git_status_include_untracked": False,
            "git_status_max_untracked": 20,
            "git_status_max_lines": 100,
            "git_status_enable_path_filtering": True,
            "git_status_show_filter_summary": True,
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate 5 tracked changes and 100 untracked files
        tracked_files = [
            " M tracked1.py",
            "M  tracked2.py",
            "A  tracked3.py",
            "D  tracked4.py",
            "MM tracked5.py",
        ]
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(100)]
        git_output = "\n".join(tracked_files + untracked_files)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # Should have: 5 tracked files (no summary when untracked disabled)
            assert len(status_lines) == 5

            # All tracked files should be present
            for tracked_file in tracked_files:
                assert tracked_file in status_lines

            # No untracked files should be present
            for untracked_file in untracked_files:
                assert untracked_file not in status_lines

    def test_max_untracked_zero_unlimited(self, mock_coordinator):
        """max_untracked=0 shows 0 files with summary."""
        # Create hook with max_untracked=0
        config = {
            "working_dir": ".",
            "git_status_include_untracked": True,
            "git_status_max_untracked": 0,
            "git_status_max_lines": 100,
            "git_status_enable_path_filtering": True,
            "git_status_show_filter_summary": True,
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate 50 untracked files
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(50)]
        git_output = "\n".join(untracked_files)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # Should only have summary (0 untracked files shown, 50 omitted)
            assert len(status_lines) == 1
            assert "... (50 more untracked files omitted)" in status_lines

    def test_max_untracked_custom_limit(self, mock_coordinator):
        """Custom limit like 50 works correctly."""
        # Create hook with custom limit of 50
        config = {
            "working_dir": ".",
            "git_status_include_untracked": True,
            "git_status_max_untracked": 50,
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate 75 untracked files
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(75)]
        git_output = "\n".join(untracked_files)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # Should have: 50 untracked + 1 summary = 51 lines
            assert len(status_lines) == 51

            # First 50 untracked files should be present
            for i in range(50):
                assert untracked_files[i] in status_lines

            # Summary line should indicate 25 more files omitted
            assert "... (25 more untracked files omitted)" in status_lines

    def test_hard_limit_max_lines(self, mock_coordinator):
        """Hard limit truncates total output."""
        # Create hook with hard limit of 30 lines
        config = {
            "working_dir": ".",
            "git_status_include_untracked": True,
            "git_status_max_untracked": 50,  # This should be overridden by max_lines
            "git_status_max_lines": 30,
            "git_status_enable_path_filtering": True,
            "git_status_show_filter_summary": True,
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate 25 tracked changes and 100 untracked files
        tracked_files = [f" M tracked{i:03d}.py" for i in range(25)]
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(100)]
        git_output = "\n".join(tracked_files + untracked_files)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # Should have: 30 files + 1 hard limit message = 31 lines
            assert len(status_lines) == 31

            # Hard limit message should be present
            assert "[Hard limit reached: output truncated to 30 lines]" in status_lines

    def test_unmerged_paths_treated_as_tracked(self, default_hook):
        """U, DD, AU status codes not truncated."""
        # Simulate various unmerged states + 50 untracked files
        unmerged_files = [
            "DD file1.txt",  # both deleted
            "AU file2.txt",  # added by us
            "UD file3.txt",  # deleted by them
            "UA file4.txt",  # added by them
            "DU file5.txt",  # deleted by us
            "AA file6.txt",  # both added
            "UU file7.txt",  # both modified
        ]
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(50)]
        git_output = "\n".join(unmerged_files + untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # Should have: 7 unmerged + 20 untracked + 1 summary = 28 lines
            assert len(status_lines) == 28

            # All unmerged files should be present (not truncated)
            for unmerged_file in unmerged_files:
                assert unmerged_file in status_lines

            # First 20 untracked files should be present
            for i in range(20):
                assert untracked_files[i] in status_lines

            # Summary line should indicate 30 more files omitted
            assert "... (30 more untracked files omitted)" in status_lines

    def test_git_status_failure(self, default_hook):
        """Gracefully handles git command failures."""
        # Mock _run_git to return None (simulating git command failure)
        with patch.object(default_hook, "_run_git", return_value=None):
            status = default_hook._gather_git_status()
            assert status == "Working directory clean"

    def test_truncation_message_format(self, default_hook):
        """Verify truncation message format."""
        # Simulate 30 untracked files
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(30)]
        git_output = "\n".join(untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # Should have: 20 untracked + 1 summary = 21 lines
            assert len(status_lines) == 21

            # Last line should be properly formatted
            last_line = status_lines[-1]
            assert last_line == "... (10 more untracked files omitted)"

    def test_pathological_case_token_consumption(self, default_hook):
        """Verify pathological cases (10k files) result in <100 lines."""
        # Simulate pathological case: 10,000 untracked files in node_modules
        untracked_files = [f"?? node_modules/file{i:05d}.js" for i in range(10000)]
        git_output = "\n".join(untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # With tier-based filtering, all node_modules files are filtered (tier1)
            # Should have just 1 filtered message (well under 100)
            assert len(status_lines) == 1
            assert len(status_lines) < 100

            # Verify filtered message
            assert "[Filtered: 10000 untracked files in ignored paths]" in status_lines

    def test_tracked_only_no_summary_line(self, default_hook):
        """No summary line when only tracked files are present."""
        # Simulate only tracked changes
        tracked_files = [
            " M file1.py",
            "M  file2.py",
            "A  file3.py",
        ]
        git_output = "\n".join(tracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # Should only have tracked files, no summary
            assert len(status_lines) == 3
            assert not any("omitted" in line for line in status_lines)

    def test_exact_limit_boundary(self, default_hook):
        """Exactly 20 untracked files, no truncation needed."""
        # Simulate exactly 20 untracked files (at the limit)
        untracked_files = [f"?? untracked{i:02d}.txt" for i in range(20)]
        git_output = "\n".join(untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # All 20 files should be present, no summary line
            assert len(status_lines) == 20
            assert not any("omitted" in line for line in status_lines)

    def test_one_over_limit(self, default_hook):
        """21 untracked files triggers truncation."""
        # Simulate 21 untracked files (1 over limit)
        untracked_files = [f"?? untracked{i:02d}.txt" for i in range(21)]
        git_output = "\n".join(untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # Should have: 20 untracked + 1 summary = 21 lines
            assert len(status_lines) == 21

            # First 20 files should be present
            for i in range(20):
                assert untracked_files[i] in status_lines

            # Summary line should indicate 1 more file omitted
            assert "... (1 more untracked files omitted)" in status_lines

    def test_hard_limit_with_only_tracked(self, mock_coordinator):
        """Hard limit applies even to tracked files only."""
        # Create hook with hard limit of 10
        config = {
            "working_dir": ".",
            "git_status_max_lines": 10,
            "git_status_enable_path_filtering": True,
            "git_status_show_filter_summary": True,
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate 15 tracked changes
        tracked_files = [f" M tracked{i:02d}.py" for i in range(15)]
        git_output = "\n".join(tracked_files)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # Should have: 10 files + 1 hard limit message = 11 lines
            assert len(status_lines) == 11

            # First 10 tracked files should be present
            for i in range(10):
                assert tracked_files[i] in status_lines

            # Hard limit message should be present
            assert "[Hard limit reached: output truncated to 10 lines]" in status_lines

    def test_empty_output_returns_clean(self, default_hook):
        """Empty string output returns 'Working directory clean'."""
        # Mock _run_git to return empty string
        with patch.object(default_hook, "_run_git", return_value=""):
            status = default_hook._gather_git_status()
            assert status == "Working directory clean"

    def test_whitespace_only_output(self, default_hook):
        """Whitespace-only output returns 'Working directory clean'."""
        # In reality, _run_git strips whitespace, so whitespace-only becomes empty string
        # which is falsy, treated same as None
        with patch.object(default_hook, "_run_git", return_value=""):
            status = default_hook._gather_git_status()
            assert status == "Working directory clean"


class TestTierBasedFiltering:
    """Test suite for tier-based path filtering."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator for testing."""
        coordinator = Mock()
        coordinator.session_id = "test-session-id"
        coordinator.parent_id = None
        return coordinator

    @pytest.fixture
    def hook_with_filtering(self, mock_coordinator):
        """Hook with path filtering enabled (default)."""
        config = {
            "working_dir": ".",
            "git_status_enable_path_filtering": True,
            "git_status_max_tracked": 50,
            "git_status_max_untracked": 20,
            "git_status_max_lines": 100,
            "git_status_show_filter_summary": True,
            "git_status_tier2_limit": 10,
            "git_status_include_untracked": True,
        }
        return StatusContextHook(mock_coordinator, config)

    @pytest.fixture
    def hook_without_filtering(self, mock_coordinator):
        """Hook with path filtering disabled."""
        config = {
            "working_dir": ".",
            "git_status_enable_path_filtering": False,
            "git_status_max_tracked": 50,
            "git_status_max_untracked": 20,
            "git_status_max_lines": 100,
            "git_status_show_filter_summary": True,
        }
        return StatusContextHook(mock_coordinator, config)

    def test_tier1_tracked_files_filtered(self, hook_with_filtering):
        """Tracked files in node_modules filtered with WARNING."""
        # Simulate tracked files in tier1 paths (node_modules, .venv)
        tier1_tracked = [
            "M  node_modules/package/index.js",
            "M  node_modules/another/file.js",
            "A  .venv/lib/python3.9/site.py",
            "MM __pycache__/module.cpython-39.pyc",
        ]
        tier3_tracked = [
            "M  src/main.py",
            "A  src/utils.py",
        ]
        git_output = "\n".join(tier1_tracked + tier3_tracked)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Tier 3 tracked files should be shown
            assert "M  src/main.py" in status_lines
            assert "A  src/utils.py" in status_lines

            # Tier 1 tracked files should NOT be in main output
            assert "M  node_modules/package/index.js" not in status_lines[:2]

            # Should have WARNING message for tier1 tracked files
            warning_found = False
            for line in status_lines:
                if "[WARNING:" in line and "tracked files in ignored paths]" in line:
                    warning_found = True
                    assert "4" in line  # 4 tracked tier1 files
                    break
            assert warning_found, "WARNING message not found for tier1 tracked files"

            # Should show examples
            assert any("node_modules/package/index.js" in line for line in status_lines)

    def test_tier1_untracked_files_filtered(self, hook_with_filtering):
        """Untracked files in node_modules filtered silently."""
        # Simulate untracked files in tier1 paths
        tier1_untracked = [
            "?? node_modules/package/file.js",
            "?? .venv/lib/python3.9/site.py",
            "?? build/output.js",
        ]
        tier3_tracked = [
            "M  src/main.py",
        ]
        git_output = "\n".join(tier1_untracked + tier3_tracked)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Tier 3 tracked file should be shown
            assert "M  src/main.py" in status_lines

            # Tier 1 untracked files should NOT be in output
            assert "?? node_modules/package/file.js" not in status_lines
            assert "?? .venv/lib/python3.9/site.py" not in status_lines
            assert "?? build/output.js" not in status_lines

            # Should have filtered message for tier1 untracked files
            filtered_found = False
            for line in status_lines:
                if "[Filtered:" in line and "untracked files in ignored paths]" in line:
                    filtered_found = True
                    assert "3" in line  # 3 untracked tier1 files
                    break
            assert filtered_found, (
                "Filtered message not found for tier1 untracked files"
            )

    def test_tier2_limited_display(self, hook_with_filtering):
        """Lockfiles and IDE configs limited to 10."""
        # Simulate many tier2 files (lockfiles, IDE configs)
        tier2_files = [
            "M  package-lock.json",
            "M  yarn.lock",
            "M  Gemfile.lock",
            "?? .vscode/settings.json",
            "?? .vscode/launch.json",
            "?? .idea/workspace.xml",
            "?? .idea/modules.xml",
            "M  file1.log",
            "M  file2.log",
            "M  file3.log",
            "M  file4.log",  # 11th tier2 file
            "M  file5.log",
            "M  file6.log",
        ]
        tier3_tracked = [
            "M  src/main.py",
        ]
        git_output = "\n".join(tier2_files + tier3_tracked)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Tier 3 tracked file should be shown
            assert "M  src/main.py" in status_lines

            # First 10 tier2 files should be present
            assert "M  package-lock.json" in status_lines
            assert "M  yarn.lock" in status_lines

            # Should have summary for omitted tier2 files
            summary_found = False
            for line in status_lines:
                if "more support files omitted" in line:
                    summary_found = True
                    assert "3" in line  # 3 tier2 files omitted (13 - 10)
                    break
            assert summary_found, "Support files omitted message not found"

    def test_tier3_tracked_limit(self, hook_with_filtering):
        """More than 50 tracked source files triggers limit."""
        # Simulate 60 tracked tier3 files
        tier3_tracked = [f"M  src/file{i:03d}.py" for i in range(60)]
        git_output = "\n".join(tier3_tracked)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Should show first 50 tracked files
            assert "M  src/file000.py" in status_lines
            assert "M  src/file049.py" in status_lines

            # 51st file should NOT be shown
            assert "M  src/file050.py" not in status_lines

            # Should have summary for omitted tracked files
            summary_found = False
            for line in status_lines:
                if "more tracked files omitted" in line:
                    summary_found = True
                    assert "10" in line  # 10 tracked files omitted (60 - 50)
                    break
            assert summary_found, "Tracked files omitted message not found"

    def test_pattern_matching_directory_patterns(self, hook_with_filtering):
        """Test /** patterns work correctly."""
        # Simulate files in directories with /** patterns
        files = [
            "?? node_modules/deep/nested/file.js",
            "?? .venv/lib/python3.9/site-packages/pkg/module.py",
            "M  __pycache__/module.cpython-39.pyc",
            "M  src/main.py",
        ]
        git_output = "\n".join(files)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Only tier3 file should be in main output
            assert "M  src/main.py" in status_lines

            # Tier1 files should be filtered
            assert "?? node_modules/deep/nested/file.js" not in status_lines
            assert (
                "?? .venv/lib/python3.9/site-packages/pkg/module.py" not in status_lines
            )

            # Should have filtered/warning messages
            assert any(
                "[Filtered:" in line or "[WARNING:" in line for line in status_lines
            )

    def test_pattern_matching_glob_patterns(self, hook_with_filtering):
        """Test *.pyc style patterns."""
        # Simulate files matching glob patterns
        files = [
            "?? module.pyc",
            "?? another.pyo",
            "M  tracked.pyc",  # Tier1 tracked (should trigger WARNING)
            "M  src/main.py",
        ]
        git_output = "\n".join(files)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Tier3 file should be shown
            assert "M  src/main.py" in status_lines

            # .pyc and .pyo files should be filtered
            assert "?? module.pyc" not in status_lines
            assert "?? another.pyo" not in status_lines

            # Tracked .pyc should trigger WARNING
            warning_found = False
            for line in status_lines:
                if "[WARNING:" in line and "tracked files in ignored paths]" in line:
                    warning_found = True
                    break
            assert warning_found, "WARNING not found for tracked .pyc file"

    def test_mixed_tiers_all_shown(self, hook_with_filtering):
        """Files from all tiers shown appropriately."""
        # Simulate files from all tiers
        tier1_untracked = [
            "?? node_modules/pkg/file.js",
        ]
        tier1_tracked = [
            "M  build/output.js",
        ]
        tier2_files = [
            "M  package-lock.json",
            "?? .vscode/settings.json",
        ]
        tier3_tracked = [
            "M  src/main.py",
            "A  src/utils.py",
        ]
        tier3_untracked = [
            "?? test.txt",
        ]
        git_output = "\n".join(
            tier1_untracked
            + tier1_tracked
            + tier2_files
            + tier3_tracked
            + tier3_untracked
        )

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Tier 3 files should be shown
            assert "M  src/main.py" in status_lines
            assert "A  src/utils.py" in status_lines
            assert "?? test.txt" in status_lines

            # Tier 2 files should be shown
            assert "M  package-lock.json" in status_lines
            assert "?? .vscode/settings.json" in status_lines

            # Tier 1 files should have messages
            assert any("[WARNING:" in line for line in status_lines)  # For tracked
            assert any("[Filtered:" in line for line in status_lines)  # For untracked

    def test_warning_message_format(self, hook_with_filtering):
        """Verify WARNING format for tracked tier1 files."""
        # Simulate tracked files in tier1 paths
        tier1_tracked = [
            "M  node_modules/pkg1/file.js",
            "M  node_modules/pkg2/file.js",
            "A  .venv/lib/module.py",
            "MM build/output.js",
            "D  dist/bundle.js",
        ]
        git_output = "\n".join(tier1_tracked)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Find WARNING line
            warning_line_idx = None
            for i, line in enumerate(status_lines):
                if "[WARNING:" in line:
                    warning_line_idx = i
                    assert "5 tracked files in ignored paths]" in line
                    break
            assert warning_line_idx is not None, "WARNING line not found"

            # Should show examples (up to 3)
            assert any("node_modules/pkg1/file.js" in line for line in status_lines)
            assert any("node_modules/pkg2/file.js" in line for line in status_lines)
            assert any(".venv/lib/module.py" in line for line in status_lines)

            # Should have "and X more" message
            assert any("and 2 more" in line for line in status_lines)

            # Should have suggestion
            assert any(
                "[Suggestion: These directories should not be tracked]" in line
                for line in status_lines
            )

    def test_filtering_disabled(self, hook_without_filtering):
        """When path filtering disabled, all shown (subject to hard limit)."""
        # Simulate files that would be filtered if filtering was enabled
        files = [
            "?? node_modules/pkg/file.js",
            "M  .venv/lib/module.py",
            "M  src/main.py",
        ]
        git_output = "\n".join(files)

        with patch.object(hook_without_filtering, "_run_git", return_value=git_output):
            status = hook_without_filtering._gather_git_status()
            status_lines = status.splitlines()

            # All files should be shown (no filtering)
            assert "?? node_modules/pkg/file.js" in status_lines
            assert "M  .venv/lib/module.py" in status_lines
            assert "M  src/main.py" in status_lines

            # No WARNING or Filtered messages
            assert not any("[WARNING:" in line for line in status_lines)
            assert not any("[Filtered:" in line for line in status_lines)

    def test_custom_tier1_patterns(self, mock_coordinator):
        """User can extend tier1 patterns."""
        # Create hook with custom tier1 patterns
        config = {
            "working_dir": ".",
            "git_status_enable_path_filtering": True,
            "git_status_tier1_patterns_extend": ["custom_ignore/**", "*.ignore"],
            "git_status_show_filter_summary": True,
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate files matching custom patterns
        files = [
            "?? custom_ignore/file.txt",
            "M  test.ignore",
            "M  src/main.py",
        ]
        git_output = "\n".join(files)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # Tier3 file should be shown
            assert "M  src/main.py" in status_lines

            # Custom tier1 patterns should be filtered
            assert "?? custom_ignore/file.txt" not in status_lines

            # Tracked custom tier1 pattern should trigger WARNING
            warning_found = False
            for line in status_lines:
                if "[WARNING:" in line:
                    warning_found = True
                    break
            assert warning_found, "WARNING not found for custom tier1 tracked file"

    def test_hard_limit_with_filtering(self, mock_coordinator):
        """Hard limit still applies even with tier filtering."""
        # Create hook with low hard limit
        config = {
            "working_dir": ".",
            "git_status_enable_path_filtering": True,
            "git_status_max_lines": 15,
            "git_status_show_filter_summary": True,
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate many tier3 files
        tier3_tracked = [f"M  src/file{i:03d}.py" for i in range(30)]
        git_output = "\n".join(tier3_tracked)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # Should be truncated to hard limit + 1 for message
            assert len(status_lines) <= 16  # 15 + 1 for hard limit message

            # Should have hard limit message
            hard_limit_found = False
            for line in status_lines:
                if "[Hard limit reached:" in line:
                    hard_limit_found = True
                    assert "15 lines]" in line
                    break
            assert hard_limit_found, "Hard limit message not found"

    def test_all_files_in_tier1(self, hook_with_filtering):
        """All files in ignored paths shows appropriate message."""
        # Simulate only tier1 files
        tier1_untracked = [
            "?? node_modules/pkg1/file.js",
            "?? node_modules/pkg2/file.js",
            "?? .venv/lib/module.py",
        ]
        tier1_tracked = [
            "M  build/output.js",
        ]
        git_output = "\n".join(tier1_untracked + tier1_tracked)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Should have WARNING for tracked
            assert any(
                "[WARNING:" in line and "1 tracked" in line for line in status_lines
            )

            # Should have Filtered message for untracked
            assert any(
                "[Filtered:" in line and "3 untracked" in line for line in status_lines
            )

            # Should show example of tracked tier1 file
            assert any("build/output.js" in line for line in status_lines)

    def test_tier2_untracked_and_tracked_mixed(self, hook_with_filtering):
        """Tier2 files include both tracked and untracked."""
        # Simulate mixed tier2 files
        tier2_files = [
            "M  package-lock.json",  # tracked
            "?? yarn.lock",  # untracked
            "M  .vscode/settings.json",  # tracked
            "?? .idea/workspace.xml",  # untracked
        ]
        tier3_tracked = [
            "M  src/main.py",
        ]
        git_output = "\n".join(tier2_files + tier3_tracked)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # All tier2 files should be shown (under limit)
            assert "M  package-lock.json" in status_lines
            assert "?? yarn.lock" in status_lines
            assert "M  .vscode/settings.json" in status_lines
            assert "?? .idea/workspace.xml" in status_lines

            # Tier3 file should be shown
            assert "M  src/main.py" in status_lines

    def test_tier3_untracked_respects_max_untracked(self, hook_with_filtering):
        """Tier3 untracked files respect max_untracked limit."""
        # Simulate many tier3 untracked files
        tier3_untracked = [f"?? file{i:03d}.txt" for i in range(30)]
        tier3_tracked = [
            "M  src/main.py",
        ]
        git_output = "\n".join(tier3_tracked + tier3_untracked)

        with patch.object(hook_with_filtering, "_run_git", return_value=git_output):
            status = hook_with_filtering._gather_git_status()
            status_lines = status.splitlines()

            # Tracked file should be shown
            assert "M  src/main.py" in status_lines

            # Should show first 20 untracked files (max_untracked=20)
            assert "?? file000.txt" in status_lines
            assert "?? file019.txt" in status_lines

            # 21st file should NOT be shown
            assert "?? file020.txt" not in status_lines

            # Should have summary for omitted untracked files
            summary_found = False
            for line in status_lines:
                if "more untracked files omitted" in line:
                    summary_found = True
                    assert "10" in line  # 10 untracked files omitted (30 - 20)
                    break
            assert summary_found, "Untracked files omitted message not found"


class TestToolContinuationSkip:
    """Test that status context is NOT injected on tool-continuation turns.

    The hook fires on every provider:request event, but tool-continuation turns
    (iteration > 1) don't need fresh status context. Re-injecting it creates
    phantom user-role messages that can cause non-sequitur model responses.

    See: https://github.com/microsoft/amplifier-module-hooks-status-context/pull/XX
    """

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator for testing."""
        coordinator = Mock()
        coordinator.session_id = "test-session-id"
        coordinator.parent_id = None
        return coordinator

    @pytest.fixture
    def hook(self, mock_coordinator):
        """Create a hook with default configuration."""
        config = {
            "working_dir": ".",
            "include_git": True,
            "include_datetime": True,
            "include_session": True,
        }
        return StatusContextHook(mock_coordinator, config)

    @pytest.mark.asyncio
    async def test_first_iteration_injects_context(self, hook):
        """Iteration 1 (user-prompt turn) should inject status context."""
        with patch.object(
            hook,
            "_gather_env_info",
            return_value={
                "is_git_repo": False,
                "formatted": "mock env info",
            },
        ):
            result = await hook.on_provider_request(
                "provider:request", {"provider": "anthropic", "iteration": 1}
            )
            assert result.action == "inject_context"
            assert result.context_injection is not None
            assert "system-reminder" in result.context_injection

    @pytest.mark.asyncio
    async def test_second_iteration_skips_injection(self, hook):
        """Iteration 2 (tool-continuation) should return no-op HookResult."""
        result = await hook.on_provider_request(
            "provider:request", {"provider": "anthropic", "iteration": 2}
        )
        # Default HookResult has action='continue' (no-op) and no injection
        assert result.action == "continue"
        assert result.context_injection is None

    @pytest.mark.asyncio
    async def test_high_iteration_skips_injection(self, hook):
        """Any iteration > 1 should skip injection."""
        for iteration in [3, 5, 10, 50]:
            result = await hook.on_provider_request(
                "provider:request", {"provider": "anthropic", "iteration": iteration}
            )
            assert result.context_injection is None, (
                f"Iteration {iteration} should not inject context"
            )

    @pytest.mark.asyncio
    async def test_missing_iteration_defaults_to_inject(self, hook):
        """If iteration is missing from event data, default to injecting (safe fallback)."""
        with patch.object(
            hook,
            "_gather_env_info",
            return_value={
                "is_git_repo": False,
                "formatted": "mock env info",
            },
        ):
            result = await hook.on_provider_request(
                "provider:request", {"provider": "anthropic"}
            )
            assert result.action == "inject_context"
