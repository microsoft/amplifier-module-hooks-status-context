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
            "git_status_max_lines": None,
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

            # Should have: 5 tracked + 1 summary = 6 lines
            assert len(status_lines) == 6

            # All tracked files should be present
            for tracked_file in tracked_files:
                assert tracked_file in status_lines

            # No untracked files should be present
            for untracked_file in untracked_files:
                assert untracked_file not in status_lines

            # Summary line should indicate untracked files were omitted
            assert "... (100 untracked files omitted)" in status_lines

    def test_max_untracked_zero_unlimited(self, mock_coordinator):
        """max_untracked=0 shows all files (old behavior)."""
        # Create hook with max_untracked=0 (unlimited)
        config = {
            "working_dir": ".",
            "git_status_include_untracked": True,
            "git_status_max_untracked": 0,
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate 50 untracked files
        untracked_files = [f"?? untracked{i:03d}.txt" for i in range(50)]
        git_output = "\n".join(untracked_files)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # All 50 files should be present (no truncation)
            assert len(status_lines) == 50
            for untracked_file in untracked_files:
                assert untracked_file in status_lines

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

            # Should have: 30 files + 1 summary = 31 lines
            assert len(status_lines) == 31

            # Summary line should indicate files were omitted
            assert "... (95 more files omitted)" in status_lines

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
        # Simulate pathological case: 10,000 untracked files
        untracked_files = [f"?? node_modules/file{i:05d}.js" for i in range(10000)]
        git_output = "\n".join(untracked_files)

        with patch.object(default_hook, "_run_git", return_value=git_output):
            status = default_hook._gather_git_status()
            status_lines = status.splitlines()

            # Should have: 20 untracked + 1 summary = 21 lines (well under 100)
            assert len(status_lines) == 21
            assert len(status_lines) < 100

            # Verify truncation message
            assert "... (9980 more untracked files omitted)" in status_lines

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
        }
        hook = StatusContextHook(mock_coordinator, config)

        # Simulate 15 tracked changes
        tracked_files = [f" M tracked{i:02d}.py" for i in range(15)]
        git_output = "\n".join(tracked_files)

        with patch.object(hook, "_run_git", return_value=git_output):
            status = hook._gather_git_status()
            assert status is not None
            status_lines = status.splitlines()

            # Should have: 10 files + 1 summary = 11 lines
            assert len(status_lines) == 11

            # First 10 tracked files should be present
            for i in range(10):
                assert tracked_files[i] in status_lines

            # Summary line should indicate 5 more files omitted
            assert "... (5 more files omitted)" in status_lines

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
