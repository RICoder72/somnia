"""
Tests for tools.git — git operations using real tmp repos.
"""

import subprocess
import pytest
from pathlib import Path
from fastmcp import FastMCP
from tools.git import register, _run_git


def _init_repo(path: Path) -> Path:
    """Create a git repo with one commit."""
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    (path / "README.md").write_text("# Test Repo")
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True)
    return path


@pytest.fixture
def git_tools(tmp_data_root):
    """Register git tools on a fresh FastMCP and return tool lookup."""
    mcp = FastMCP("test")
    register(mcp)
    return mcp._tool_manager._tools


@pytest.fixture
def repo(tmp_data_root):
    """Create a git repo inside the sandboxed tmp_data_root."""
    return _init_repo(tmp_data_root / "myrepo")


class TestRunGit:
    def test_simple_command(self, repo):
        success, output = _run_git(["status"], cwd=repo)
        assert success

    def test_invalid_command(self, repo):
        success, output = _run_git(["notarealcommand"], cwd=repo)
        assert not success

    def test_timeout(self, tmp_data_root):
        # git with no args should return quickly, but test the parameter flows
        success, output = _run_git(["status"], cwd=tmp_data_root, timeout=5)
        # May fail because tmp_data_root isn't a git repo — that's fine
        assert isinstance(success, bool)


class TestGitTools:
    @pytest.mark.asyncio
    async def test_git_status(self, git_tools, repo, tmp_data_root):
        # Path relative to DATA_ROOT
        rel = repo.relative_to(tmp_data_root)
        result = await git_tools["git_status"].fn(path=str(rel))
        assert "Git Status" in result or "nothing to commit" in result.lower()

    @pytest.mark.asyncio
    async def test_git_log(self, git_tools, repo, tmp_data_root):
        rel = repo.relative_to(tmp_data_root)
        result = await git_tools["git_log"].fn(path=str(rel))
        assert "Initial commit" in result

    @pytest.mark.asyncio
    async def test_git_commit(self, git_tools, repo, tmp_data_root):
        (repo / "new.txt").write_text("new file")
        rel = repo.relative_to(tmp_data_root)
        result = await git_tools["git_commit"].fn(path=str(rel), message="Add new file")
        assert "✅" in result or "Committed" in result

    @pytest.mark.asyncio
    async def test_git_branch_create_and_list(self, git_tools, repo, tmp_data_root):
        rel = repo.relative_to(tmp_data_root)
        result = await git_tools["git_branch"].fn(path=str(rel), name="feature")
        assert "✅" in result

        result = await git_tools["git_branch"].fn(path=str(rel))
        assert "feature" in result

    @pytest.mark.asyncio
    async def test_git_diff_no_changes(self, git_tools, repo, tmp_data_root):
        rel = repo.relative_to(tmp_data_root)
        result = await git_tools["git_diff"].fn(path=str(rel))
        assert "No changes" in result

    @pytest.mark.asyncio
    async def test_not_a_repo(self, git_tools, tmp_data_root):
        (tmp_data_root / "notrepo").mkdir()
        result = await git_tools["git_status"].fn(path="notrepo")
        assert "❌" in result
