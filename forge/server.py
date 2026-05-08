"""
Forge MCP — Constellation Workbench

Minimal MCP that gives Claude shell access to a rich toolchain:
Python/GIS/Node environment with persistent workspace and shared outputs.

Tools:
  ping          — health check
  shell_exec    — run any command in the forge environment
  read_file     — read from /workspace or /outputs
  write_file    — write to /workspace or /outputs
  list_files    — list directory contents
  env_info      — show installed packages and tools
"""

from fastmcp import FastMCP
import subprocess
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
WORKSPACE   = Path("/workspace")   # persistent scratch; survives restarts
OUTPUTS_DIR = Path("/outputs")     # shared with Vigil for publish
REPOS_DIR   = Path("/repos")       # NAS repos mount
ALLOWED_ROOTS = [WORKSPACE, OUTPUTS_DIR, REPOS_DIR, Path("/tmp")]

mcp = FastMCP("Constellation Forge")

# ── Helpers ────────────────────────────────────────────────────────────────

def _safe_path(path_str: str) -> Path:
    """Resolve path and verify it's within an allowed root."""
    p = Path(path_str)
    if not p.is_absolute():
        p = WORKSPACE / p
    resolved = p.resolve()
    for root in ALLOWED_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise ValueError(f"Path outside allowed directories: {path_str}")


def _run(command: str, cwd: str = "/workspace", timeout: int = 120) -> str:
    """Run a shell command. Returns combined stdout+stderr."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        out = result.stdout
        if result.stderr:
            out += ("\n" if out else "") + result.stderr
        if result.returncode != 0:
            out = f"[exit {result.returncode}]\n{out}"
        return out.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
    except Exception as e:
        return f"[error: {e}]"


# ── Tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
def ping() -> str:
    """Health check."""
    return "pong from Forge 🔨"


@mcp.tool()
def shell_exec(command: str, workdir: str = "/workspace", timeout: int = 120) -> str:
    """
    Execute a shell command in the Forge environment.

    Full Python/GIS/Node.js toolchain available. Working directory
    defaults to /workspace (persistent across sessions). Outputs
    written to /outputs are accessible to Vigil for publishing.

    Args:
        command:  Shell command to run (bash)
        workdir:  Working directory (default: /workspace)
        timeout:  Seconds before timeout (default: 120)
    """
    return _run(command, cwd=workdir, timeout=timeout)


@mcp.tool()
def read_file(path: str) -> str:
    """
    Read a file from /workspace, /outputs, or /repos.

    Args:
        path: Absolute path or relative to /workspace
    """
    try:
        resolved = _safe_path(path)
        if not resolved.exists():
            return f"[not found: {path}]"
        if not resolved.is_file():
            return f"[not a file: {path}]"
        content = resolved.read_text(errors="replace")
        size = len(content)
        if size > 200_000:
            return f"[file too large ({size:,} bytes) — use shell_exec to process it]"
        return content
    except ValueError as e:
        return f"[{e}]"
    except Exception as e:
        return f"[error: {e}]"


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """
    Write content to a file in /workspace or /outputs.

    Creates parent directories as needed.

    Args:
        path:    Absolute path or relative to /workspace
        content: Text content to write
    """
    try:
        resolved = _safe_path(path)
        # Must be in workspace or outputs (not repos — those are git-managed)
        for writable in [WORKSPACE, OUTPUTS_DIR, Path("/tmp")]:
            try:
                resolved.relative_to(writable.resolve())
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(content)
                return f"[wrote {len(content):,} bytes → {resolved}]"
            except ValueError:
                continue
        return f"[path is not in a writable directory: {path}]"
    except ValueError as e:
        return f"[{e}]"
    except Exception as e:
        return f"[error: {e}]"


@mcp.tool()
def list_files(path: str = "/workspace") -> str:
    """
    List directory contents.

    Args:
        path: Directory to list (default: /workspace)
    """
    try:
        resolved = _safe_path(path)
        if not resolved.exists():
            return f"[not found: {path}]"
        if not resolved.is_dir():
            return f"[not a directory: {path}]"

        items = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = [f"📂 {resolved}", "─" * 50]
        for item in items:
            if item.is_dir():
                lines.append(f"  📁 {item.name}/")
            else:
                sz = item.stat().st_size
                sz_str = f"{sz}B" if sz < 1024 else (f"{sz/1024:.1f}KB" if sz < 1048576 else f"{sz/1048576:.1f}MB")
                lines.append(f"  📄 {item.name}  ({sz_str})")
        if len(lines) == 2:
            lines.append("  (empty)")
        return "\n".join(lines)
    except ValueError as e:
        return f"[{e}]"
    except Exception as e:
        return f"[error: {e}]"


@mcp.tool()
def env_info() -> str:
    """
    Show installed Python packages, Node version, and key CLI tools.
    Use this to know what's available before writing code.
    """
    sections = []

    # Python key packages
    py_check = _run(
        "python3 -c \""
        "import importlib, sys; "
        "pkgs = ['numpy','scipy','pandas','matplotlib','geopandas','shapely',"
        "'pyproj','fiona','rasterio','contextily','osmnx','osmium',"
        "'reportlab','openpyxl','requests','PIL']; "
        "[print(f'{p}: {importlib.import_module(p).__version__}') "
        "if hasattr(importlib.import_module(p), '__version__') "
        "else print(f'{p}: ok') "
        "for p in pkgs if __import__(sys.modules.get(p,p) and p or p, fromlist=[p])]"
        "\" 2>&1 || pip list 2>/dev/null | grep -E 'numpy|pandas|matplotlib|geopandas|shapely|scipy|rasterio|osmnx|contextily|reportlab'"
    )
    sections.append("Python packages:\n" + py_check)

    # Node
    node_v = _run("node --version 2>/dev/null && npm --version 2>/dev/null || echo 'node: not available'")
    sections.append("Node.js:\n" + node_v)

    # CLI tools
    cli = _run("for t in python3 gdal_info osmium pandoc git curl; do echo \"$t: $(which $t 2>/dev/null && $t --version 2>/dev/null | head -1 || echo 'not found')\"; done")
    sections.append("CLI tools:\n" + cli)

    # Disk
    disk = _run("df -h /workspace /outputs 2>/dev/null | tail -2")
    sections.append("Storage:\n" + disk)

    return "\n\n".join(sections)



# ── Nuntii Bootstrap ───────────────────────────────────────────────────────
import os, shutil
_nuntii_dir = Path('/workspace/nuntii')
_crontab = _nuntii_dir / 'crontab'
_agents_src = _nuntii_dir / 'agents'
_mcp_src = _nuntii_dir / 'mcp.json'
_claude_dir = Path.home() / '.claude'

# Restore agent definitions from persistent storage
if _agents_src.is_dir():
    _agents_dst = _claude_dir / 'agents'
    _agents_dst.mkdir(parents=True, exist_ok=True)
    for md in _agents_src.glob('*.md'):
        shutil.copy2(md, _agents_dst / md.name)
    print(f'📋 Nuntii agents restored: {", ".join(f.stem for f in _agents_src.glob("*.md"))}')

# Restore MCP config
if _mcp_src.exists():
    _claude_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_mcp_src, _claude_dir / '.mcp.json')
    print('🔌 MCP config restored')

# Start cron scheduler
if _crontab.exists():
    os.system(f'crontab {_crontab} && /usr/sbin/cron')
    print('🕐 Nuntii cron schedule loaded')

# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8003, path="/forge")
