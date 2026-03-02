"""
Tests for core.shell — blocked patterns and command execution.
"""

import pytest
from core.shell import is_blocked, run


class TestIsBlocked:
    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "rm -rf /home",
        "RM -RF /var",
        "rm -rf ~",
        "rm -rf *",
        "rmdir /",
        "rmdir /var",
        "> /dev/sda",
        "echo x > /dev/sdb1",
        "mkfs.ext4 /dev/sda1",
        "mkfs -t xfs /dev/sdb",
        "dd if=/dev/zero of=/dev/sda",
        ":(){:|:&};:",
    ])
    def test_dangerous_command_blocked(self, cmd):
        blocked, reason = is_blocked(cmd)
        assert blocked, f"Expected blocked: {cmd}"
        assert reason  # reason string is non-empty

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "echo hello",
        "cat /data/file.txt",
        "git status",
        "rm file.txt",
        "rm -rf ./tmp",
        "python script.py",
    ])
    def test_safe_command_allowed(self, cmd):
        blocked, _ = is_blocked(cmd)
        assert not blocked, f"Expected allowed: {cmd}"


class TestRun:
    def test_stdout_captured(self, tmp_data_root):
        success, output = run("echo hello", cwd=tmp_data_root)
        assert success
        assert "hello" in output

    def test_exit_code_nonzero(self, tmp_data_root):
        success, output = run("exit 1", cwd=tmp_data_root)
        assert not success
        assert "exit code: 1" in output

    def test_stderr_captured(self, tmp_data_root):
        success, output = run("echo err >&2", cwd=tmp_data_root)
        assert "err" in output

    def test_timeout(self, tmp_data_root):
        success, output = run("sleep 10", timeout=1, cwd=tmp_data_root)
        assert not success
        assert "timed out" in output

    def test_blocked_command_rejected(self, tmp_data_root):
        success, output = run("rm -rf /", cwd=tmp_data_root)
        assert not success
        assert "blocked" in output.lower()

    def test_cwd_defaults_to_data_root(self, tmp_data_root):
        success, output = run("pwd", cwd=tmp_data_root)
        assert success
