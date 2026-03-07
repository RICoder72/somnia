"""
Fabrica — Somnia Infrastructure MCP

Container management, config editing, backups, and git operations
for the Somnia fleet. Has docker.sock access.

Trust hierarchy: SSH → Fabrica → everything else.
"""

from fastmcp import FastMCP
import subprocess
from pathlib import Path
from datetime import datetime

# =============================================================================
# Config
# =============================================================================
DATA_ROOT = Path("/data")
REPOS_DIR = DATA_ROOT / "repos"
BACKUPS_DIR = DATA_ROOT / "backups"
OUTPUTS_DIR = DATA_ROOT / "outputs"
DOCKER_NETWORK = "mcp-net"

# Known Somnia services — monorepo with per-service subdirectories
SOMNIA_REPO = REPOS_DIR / "somnia"
SERVICE_PATHS = {
    "quies": SOMNIA_REPO / "quies",
    "vigil": SOMNIA_REPO / "vigil",
    "fabrica": SOMNIA_REPO / "fabrica",
}

# Fleet registry — full launch configs for container_start
FLEET_REGISTRY_PATH = DATA_ROOT / "config" / "fleet_registry.json"

def _load_fleet_registry() -> dict:
    """Load fleet registry from JSON. Returns empty dict on failure."""
    import json
    if not FLEET_REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(FLEET_REGISTRY_PATH.read_text())
    except Exception:
        return {}


mcp = FastMCP("Fabrica")

# =============================================================================
# Helpers
# =============================================================================
def _run(cmd: str, timeout: int = 120, cwd: str | None = None) -> tuple[bool, str]:
    """Run shell command, return (success, output)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd or str(DATA_ROOT),
        )
        out = result.stdout.strip()
        if result.stderr.strip():
            out += f"\n[stderr]\n{result.stderr.strip()}"
        return result.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, f"Timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def _validate_path(path: str) -> Path:
    """Resolve path within /data sandbox."""
    p = Path(path) if path.startswith("/") else DATA_ROOT / path
    resolved = p.resolve()
    if not str(resolved).startswith(str(DATA_ROOT.resolve())):
        raise ValueError(f"Path outside sandbox: {path}")
    return resolved

# =============================================================================
# Health
# =============================================================================
@mcp.tool()
def ping() -> str:
    """Health check."""
    return "pong from Fabrica 🔧"

# =============================================================================
# Docker — Fleet Management
# =============================================================================
@mcp.tool()
def fleet_status() -> str:
    """Status of all Somnia containers on mcp-net, plus any legacy containers."""
    ok, out = _run("docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | sort")
    if not ok:
        return f"❌ {out}"

    mcp_net_containers = set()
    ok2, net_out = _run(f"docker network inspect {DOCKER_NETWORK} --format '{{{{range .Containers}}}}{{{{.Name}}}} {{{{end}}}}'")
    if ok2:
        mcp_net_containers = set(net_out.strip().split())

    lines = ["🛰️  Somnia Fleet", "─" * 60]
    for line in out.strip().split("\n"):
        if not line:
            continue
        name = line.split("\t")[0]
        network_tag = " [mcp-net]" if name in mcp_net_containers else " [legacy]"
        lines.append(f"  {line}{network_tag}")
    return "\n".join(lines)


@mcp.tool()
def container_logs(container: str, lines: int = 50) -> str:
    """Get recent logs from any container."""
    ok, out = _run(f"docker logs --tail {lines} {container}")
    return out if ok else f"❌ {out}"


@mcp.tool()
def container_restart(container: str) -> str:
    """Restart a container without rebuilding."""
    ok, out = _run(f"docker restart {container}", timeout=30)
    return f"✅ Restarted {container}" if ok else f"❌ {out}"


@mcp.tool()
def container_stop(container: str) -> str:
    """Stop and remove a container."""
    _run(f"docker stop {container}", timeout=30)
    ok, out = _run(f"docker rm {container}", timeout=10)
    return f"✅ Stopped {container}" if ok else f"⚠️ {out}"


@mcp.tool()
def container_rebuild(container: str, repo_path: str = "", dockerfile: str = "") -> str:
    """
    Rebuild a container: stop → build image → start.
    Uses the container's known repo path, or specify repo_path manually.
    Checks fleet_registry.json for dockerfile path and build_context.
    Returns build output. Container must be started separately with container_start.
    """
    # Check registry for build config
    registry = _load_fleet_registry()
    reg = registry.get(container, {})

    # Resolve repo path: explicit arg > registry > known service paths
    if repo_path:
        repo = Path(repo_path)
    elif reg.get("repo_path"):
        repo = Path(reg["repo_path"])
    else:
        repo = SERVICE_PATHS.get(container)

    if not repo or not repo.exists():
        return f"❌ Unknown container or missing repo: {container}. Provide repo_path."

    # Build context: subdirectory within repo (for monorepo layout)
    build_ctx = reg.get("build_context", "")
    if build_ctx:
        build_dir = repo / build_ctx
        if not build_dir.exists():
            return f"❌ Build context not found: {build_dir}"
    else:
        build_dir = repo

    df = dockerfile or reg.get("dockerfile", "Dockerfile")

    # Stop existing
    _run(f"docker stop {container}", timeout=30)
    _run(f"docker rm {container}", timeout=10)

    # Build — dockerfile path is relative to build_dir
    image_name = reg.get("image", container)
    ok, out = _run(f"docker build -t {image_name} -f {df} .", timeout=300, cwd=str(build_dir))
    if not ok:
        return f"❌ Build failed:\n{out}"

    return f"✅ Image '{image_name}' built. Use container_start() to launch it."


@mcp.tool()
def container_start(
    container: str,
    image: str = "",
    env_file: str = "",
    volumes: str = "",
    ports: str = "",
    extra_args: str = "",
) -> str:
    """
    Start a container on mcp-net.

    If no args are provided beyond container name, checks fleet_registry.json
    for saved launch config. Explicit args override registry values.

    Args:
        container: Container name
        image: Docker image (default: same as container name)
        env_file: Path to .env file (host path)
        volumes: Comma-separated volume mounts (e.g. "/host/path:/container/path:ro")
        ports: Port mapping (e.g. "8081:8080")
        extra_args: Any additional docker run arguments
    """
    # Check registry for defaults
    registry = _load_fleet_registry()
    reg = registry.get(container, {})

    img = image or reg.get("image", container.replace("constellation-", ""))
    restart_policy = reg.get("restart", "unless-stopped")

    cmd = f"docker run -d --name {container} --restart {restart_policy} --network {DOCKER_NETWORK}"

    # Env file: explicit arg wins, then registry
    ef = env_file or reg.get("env_file", "")
    if ef:
        cmd += f" --env-file {ef}"

    # Environment variables from registry
    if not extra_args or "-e " not in extra_args:
        for k, v in reg.get("environment", {}).items():
            cmd += f" -e {k}={v}"

    # Secrets from env files (read specific vars from files)
    for var_name, source_file in reg.get("env_from_secrets", {}).items():
        source_path = DATA_ROOT / source_file
        if source_path.exists():
            # Extract the specific variable from the file
            try:
                for line in source_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith(f"{var_name}="):
                        val = line.split("=", 1)[1]
                        cmd += f" -e {var_name}={val}"
                        break
            except Exception:
                pass

    # Volumes: explicit arg wins, then registry
    vol_list = []
    if volumes:
        vol_list = [v.strip() for v in volumes.split(",")]
    elif reg.get("volumes"):
        vol_list = reg["volumes"]
    for v in vol_list:
        cmd += f" -v {v}"

    # Ports: explicit arg wins, then registry
    port_list = []
    if ports:
        port_list = [ports]
    elif reg.get("ports"):
        port_list = reg["ports"]
    for p in port_list:
        cmd += f" -p {p}"

    if extra_args:
        cmd += f" {extra_args}"
    cmd += f" {img}"

    ok, out = _run(cmd)
    source = "registry" if reg and not (image or volumes or ports) else "manual"
    return f"✅ Started {container} ({source})" if ok else f"❌ {out}"


# =============================================================================
# File Operations — Emergency repairs when other MCPs are down
# =============================================================================
@mcp.tool()
def fs_read(path: str) -> str:
    """Read a file from /data."""
    try:
        p = _validate_path(path)
        if not p.exists():
            return f"❌ Not found: {path}"
        return f"📄 {path}\n{'─' * 40}\n{p.read_text()}"
    except (ValueError, Exception) as e:
        return f"❌ {e}"


@mcp.tool()
def fs_write(path: str, content: str) -> str:
    """Write a file to /data. Creates parent dirs."""
    try:
        p = _validate_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"✅ Wrote {len(content)} bytes → {path}"
    except (ValueError, Exception) as e:
        return f"❌ {e}"


@mcp.tool()
def fs_list(path: str = ".") -> str:
    """List directory contents in /data."""
    try:
        p = _validate_path(path)
        if not p.is_dir():
            return f"❌ Not a directory: {path}"
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = [f"📂 {path}", "─" * 40]
        for item in items:
            if item.name.startswith(".") and item.name not in (".env", ".gitignore"):
                continue
            if item.is_dir():
                lines.append(f"  📁 {item.name}/")
            else:
                sz = item.stat().st_size
                label = f"{sz} B" if sz < 1024 else f"{sz/1024:.1f} KB" if sz < 1048576 else f"{sz/1048576:.1f} MB"
                lines.append(f"  📄 {item.name} ({label})")
        return "\n".join(lines)
    except (ValueError, Exception) as e:
        return f"❌ {e}"


@mcp.tool()
def fs_replace(path: str, old: str, new: str) -> str:
    """Replace a unique string in a file (must appear exactly once)."""
    try:
        p = _validate_path(path)
        content = p.read_text()
        if content.count(old) != 1:
            n = content.count(old)
            return f"❌ Found {n} occurrences (need exactly 1)"
        p.write_text(content.replace(old, new))
        return f"✅ Replaced in {path}"
    except (ValueError, Exception) as e:
        return f"❌ {e}"


# =============================================================================
# Backup & Restore
# =============================================================================
@mcp.tool()
def backup(name: str = "") -> str:
    """Backup domains, config, and documents to a timestamped archive."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{name}" if name else ""
    fname = f"backup_{ts}{suffix}.tar.gz"
    fpath = BACKUPS_DIR / fname

    ok, out = _run(f"tar -czf {fpath} domains/ config/ documents/ 2>&1")
    if ok and fpath.exists():
        mb = fpath.stat().st_size / 1048576
        return f"✅ {fname} ({mb:.2f} MB)"
    return f"❌ Backup failed:\n{out}"


@mcp.tool()
def backup_list() -> str:
    """List available backups."""
    if not BACKUPS_DIR.exists():
        return "📁 No backups yet"
    backups = sorted(BACKUPS_DIR.glob("backup_*.tar.gz"), reverse=True)
    if not backups:
        return "📁 No backups found"
    lines = ["📁 Backups", "─" * 40]
    for b in backups[:20]:
        mb = b.stat().st_size / 1048576
        lines.append(f"  {b.name} ({mb:.2f} MB)")
    return "\n".join(lines)


@mcp.tool()
def backup_restore(backup_name: str) -> str:
    """Restore from a backup. WARNING: overwrites current data."""
    bp = BACKUPS_DIR / backup_name
    if not bp.exists():
        return f"❌ Not found: {backup_name}"
    ok, out = _run(f"tar -xzf {bp}")
    return f"✅ Restored {backup_name}" if ok else f"❌ {out}"


# =============================================================================
# Git — for any repo in /data
# =============================================================================
@mcp.tool()
def git_status(repo: str) -> str:
    """Show git status for a repo (path relative to /data or absolute)."""
    p = _validate_path(repo)
    ok, out = _run("git status", cwd=str(p))
    return out if ok else f"❌ {out}"


@mcp.tool()
def git_log(repo: str, n: int = 10) -> str:
    """Show recent git commits."""
    p = _validate_path(repo)
    ok, out = _run(f"git log --oneline -n {n}", cwd=str(p))
    return out if ok else f"❌ {out}"


@mcp.tool()
def git_commit(repo: str, message: str) -> str:
    """Stage all changes and commit."""
    p = _validate_path(repo)
    _run("git add -A", cwd=str(p))
    ok, out = _run(f'git commit -m "{message}"', cwd=str(p))
    return out if ok else f"❌ {out}"


@mcp.tool()
def git_pull(repo: str) -> str:
    """Pull latest from origin."""
    p = _validate_path(repo)
    ok, out = _run("git pull", cwd=str(p))
    return out if ok else f"❌ {out}"


@mcp.tool()
def git_push(repo: str) -> str:
    """Push to origin."""
    p = _validate_path(repo)
    ok, out = _run("git push", cwd=str(p))
    return out if ok else f"❌ {out}"



# =============================================================================
# Forge — workbench lifecycle
# =============================================================================

@mcp.tool()
def forge_start() -> str:
    """
    Start the Forge workbench container.

    Forge provides a full Python/GIS/Node.js environment with persistent
    /workspace and shared /outputs. Use when you need to run map rendering,
    GIS processing, data analysis, or any heavy workbench task.
    """
    ok, out = _run(
        "docker run -d "
        "--name forge "
        "--network mcp-net "
        "-p 8003:8003 "
        "-v /volume1/docker/super-claude/forge/workspace:/workspace "
        "-v /volume1/docker/super-claude/outputs:/outputs "
        "-v /volume1/docker/super-claude/repos:/repos "
        "--restart unless-stopped "
        "forge"
    )
    if ok:
        return "✅ Forge started — connect via https://zanni.synology.me/forge"
    # May already be running
    if "already in use" in out:
        return "ℹ️  Forge is already running"
    return f"❌ Failed to start Forge:\n{out}"


@mcp.tool()
def forge_stop() -> str:
    """
    Stop the Forge workbench container.

    /workspace data persists on disk. Safe to stop when not in use.
    """
    _run("docker stop forge")
    ok, out = _run("docker rm forge")
    if ok or "No such container" in out:
        return "✅ Forge stopped and removed"
    return f"⚠️  {out}"


@mcp.tool()
def forge_status() -> str:
    """Check whether Forge is running and healthy."""
    ok, out = _run("docker inspect --format '{{.State.Status}} | {{.State.Health.Status}}' forge 2>/dev/null")
    if not ok or not out.strip():
        return "🔴 Forge is not running"
    return f"🔨 Forge: {out.strip()}"


# =============================================================================
# Main
# =============================================================================
if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001, path="/fabrica")
