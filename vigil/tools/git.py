"""Git tools — clone, status, pull, push, commit, log, diff, branch, checkout."""

import subprocess
from pathlib import Path
from fastmcp import FastMCP

from config import DATA_ROOT
from core.paths import validate


def _run_git(args: list, cwd: Path = None, timeout: int = 60) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd or DATA_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            output += "\n" + result.stderr.strip() if output else result.stderr.strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def register(mcp: FastMCP):

    @mcp.tool()
    async def git_clone(
        url: str, path: str = None, branch: str = None, depth: int = None
    ) -> str:
        """Clone a git repository.

        Args:
            url: Repository URL (HTTPS or SSH)
            path: Local path to clone to (default: repo name in /data/repos/)
            branch: Branch to clone (default: default branch)
            depth: Shallow clone depth (default: full clone)"""
        if path is None:
            repo_name = url.rstrip("/").split("/")[-1]
            if repo_name.endswith(".git"):
                repo_name = repo_name[:-4]
            path = f"repos/{repo_name}"

        target = validate(path)
        if target.exists():
            return f"❌ Path already exists: {path}"

        args = ["clone"]
        if branch:
            args.extend(["--branch", branch])
        if depth:
            args.extend(["--depth", str(depth)])
        args.extend([url, str(target)])

        success, output = _run_git(args, timeout=120)
        if success:
            return f"✅ Cloned to: {path}\n\n{output}"
        return f"❌ Clone failed: {output}"

    @mcp.tool()
    async def git_status(path: str) -> str:
        """Show git status for a repository."""
        repo = validate(path)
        if not (repo / ".git").exists():
            return f"❌ Not a git repository: {path}"
        success, output = _run_git(["status"], cwd=repo)
        if success:
            return f"📊 Git Status: {path}\n{'─' * 40}\n{output}"
        return f"❌ Status failed: {output}"

    @mcp.tool()
    async def git_pull(path: str, remote: str = "origin", branch: str = None) -> str:
        """Pull latest changes from remote."""
        repo = validate(path)
        if not (repo / ".git").exists():
            return f"❌ Not a git repository: {path}"
        args = ["pull", remote]
        if branch:
            args.append(branch)
        success, output = _run_git(args, cwd=repo)
        if success:
            return f"✅ Pull complete\n\n{output}"
        return f"❌ Pull failed: {output}"

    @mcp.tool()
    async def git_push(
        path: str,
        remote: str = "origin",
        branch: str = None,
        auth_item: str = None,
    ) -> str:
        """Push commits to remote.

        Args:
            path: Path to repository
            remote: Remote name (default: origin)
            branch: Branch to push (default: current branch)
            auth_item: 1Password item name for GitHub PAT (for HTTPS remotes)"""
        repo = validate(path)
        if not (repo / ".git").exists():
            return f"❌ Not a git repository: {path}"

        if auth_item:
            try:
                import re
                from core.credentials import get_credential
                token = get_credential(auth_item)
                success, remote_url = _run_git(["remote", "get-url", remote], cwd=repo)
                if not success:
                    return f"❌ Could not get remote URL: {remote_url}"
                if "github.com" in remote_url and remote_url.startswith("https://"):
                    # Strip any existing userinfo (e.g. "user:@") before injecting token
                    clean_url = re.sub(r"https://[^@]+@", "https://", remote_url)
                    auth_url = clean_url.replace("https://", f"https://x-access-token:{token}@")
                    args = ["push", auth_url]
                    if branch:
                        args.append(branch)
                    success, output = _run_git(args, cwd=repo, timeout=120)
                    if success:
                        return f"✅ Push complete\n\n{output}"
                    return f"❌ Push failed: {output}"
            except Exception as e:
                return f"❌ Auth failed: {e}"

        args = ["push", remote]
        if branch:
            args.append(branch)
        success, output = _run_git(args, cwd=repo, timeout=120)
        if success:
            return f"✅ Push complete\n\n{output}"
        return f"❌ Push failed: {output}"

    @mcp.tool()
    async def git_commit(path: str, message: str, add_all: bool = True) -> str:
        """Commit changes to repository."""
        repo = validate(path)
        if not (repo / ".git").exists():
            return f"❌ Not a git repository: {path}"
        if add_all:
            success, output = _run_git(["add", "-A"], cwd=repo)
            if not success:
                return f"❌ Add failed: {output}"
        success, output = _run_git(["commit", "-m", message], cwd=repo)
        if success:
            return f"✅ Committed\n\n{output}"
        if "nothing to commit" in output:
            return f"ℹ️ Nothing to commit\n\n{output}"
        return f"❌ Commit failed: {output}"

    @mcp.tool()
    async def git_log(path: str, count: int = 10, oneline: bool = True) -> str:
        """Show commit history."""
        repo = validate(path)
        if not (repo / ".git").exists():
            return f"❌ Not a git repository: {path}"
        args = ["log", f"-{count}"]
        if oneline:
            args.append("--oneline")
        success, output = _run_git(args, cwd=repo)
        if success:
            return f"📜 Git Log: {path}\n{'─' * 40}\n{output}"
        return f"❌ Log failed: {output}"

    @mcp.tool()
    async def git_diff(path: str, staged: bool = False) -> str:
        """Show changes in repository."""
        repo = validate(path)
        if not (repo / ".git").exists():
            return f"❌ Not a git repository: {path}"
        args = ["diff"]
        if staged:
            args.append("--staged")
        success, output = _run_git(args, cwd=repo)
        if success:
            return output if output else "ℹ️ No changes"
        return f"❌ Diff failed: {output}"

    @mcp.tool()
    async def git_branch(path: str, name: str = None, delete: bool = False) -> str:
        """List, create, or delete branches."""
        repo = validate(path)
        if not (repo / ".git").exists():
            return f"❌ Not a git repository: {path}"
        if name is None:
            success, output = _run_git(["branch", "-a"], cwd=repo)
            if success:
                return f"🌿 Branches:\n{'─' * 40}\n{output}"
            return f"❌ Failed: {output}"
        if delete:
            success, output = _run_git(["branch", "-d", name], cwd=repo)
            if success:
                return f"✅ Deleted branch: {name}"
            return f"❌ Delete failed: {output}"
        success, output = _run_git(["branch", name], cwd=repo)
        if success:
            return f"✅ Created branch: {name}"
        return f"❌ Create failed: {output}"

    @mcp.tool()
    async def git_checkout(path: str, target: str, create: bool = False) -> str:
        """Switch branches or restore files."""
        repo = validate(path)
        if not (repo / ".git").exists():
            return f"❌ Not a git repository: {path}"
        args = ["checkout"]
        if create:
            args.append("-b")
        args.append(target)
        success, output = _run_git(args, cwd=repo)
        if success:
            return f"✅ Switched to: {target}\n\n{output}"
        return f"❌ Checkout failed: {output}"
