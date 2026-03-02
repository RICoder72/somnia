# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Vigil

Vigil is an MCP (Model Context Protocol) server built with FastMCP that provides filesystem, git, shell, context/domain management, publishing, and external service integrations (mail, calendar, contacts, storage, supernote). It runs as a Docker container in the "Project Constellation" architecture, exposing tools over HTTP on port 8000 at `/vigil`.

## Build & Run

```bash
# Build container
docker build -t vigil:latest .

# Run (requires mcp-net Docker network and /data volume mount)
docker run -d --name vigil --network mcp-net --expose 8000 \
  -v /volume1/docker/super-claude:/data \
  -e CREDENTIALS_API_KEY=<key> vigil:latest
```

Tests use pytest (`pip install -e ".[test]"` then `pytest tests/ -v`). CI runs via GitHub Actions (test, lint, docker-build). The project uses `pyproject.toml` with setuptools and installs via `uv pip install --system -e .` inside the container.

## Architecture

**Entrypoint:** `server.py` creates a `FastMCP("Vigil")` instance and registers all tool modules. Core tools are imported directly; services are imported in try/except blocks so failures don't block startup.

**Tool registration pattern:** Every tool module (in `tools/` and `services/*/tools.py`) exports a `register(mcp: FastMCP)` function. Inside `register()`, tools are defined as inner functions decorated with `@mcp.tool()`. To add a new tool module:
1. Create the module with a `register(mcp)` function
2. Import and call it in `server.py`

**Core layer (`core/`):**
- `paths.py` — `validate(path)` resolves paths and enforces sandbox to `DATA_ROOT` (`/data`). All filesystem tools must use this.
- `shell.py` — `run(command)` / `run_simple(command)` with blocked-pattern safety checks (prevents `rm -rf /`, fork bombs, etc.)
- `credentials.py` — HTTP client to the Credentials Service on `mcp-net` for infrastructure secrets (1Password-backed)

**Services (`services/`) — adapter pattern:**
Each service (mail, calendar, contacts, storage) follows the same structure:
- `interface.py` — ABC base class + dataclasses defining the contract
- `manager.py` — Account CRUD, adapter registry, routing by account name. Config stored in `/data/config/<service>_accounts.json`
- `adapters/<provider>.py` — Platform-specific implementation (currently Google: Gmail, GCal, GContacts, GDrive)
- `tools.py` — `register(mcp)` wiring; instantiates the manager and registers adapter types

The `supernote` service is standalone (no adapter pattern) — it handles Supernote device sync and .note/.mark file conversion.

## Key Conventions

- **Python 3.12**, type hints used throughout
- **Async:** Git tools and service adapter methods are `async`; filesystem and shell tools are sync
- All file paths in tools are relative to `/data` and validated through `core.paths.validate()`
- Config lives in `/data/config/`, domain contexts in `/data/domains/`
- Credentials split: user-facing passwords go through `tools/secrets.py` (1Password `op` CLI directly); infrastructure secrets go through `core/credentials.py` (Credentials Service HTTP API)
- Tools return emoji-prefixed status strings (e.g., `"✅ Done"`, `"❌ Error: ..."`)
