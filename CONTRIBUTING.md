# Contributing to Somnia

Somnia is a personal AI infrastructure project. Contributions are welcome
for bug fixes, new adapters, documentation improvements, and tooling.

## Getting Started

1. Fork the repository
2. Clone your fork and run `./bootstrap.sh` to bring up a local instance
3. Make your changes on a feature branch
4. Test against a running stack
5. Open a pull request with a clear description of what changed and why

## Development Setup

Somnia runs as a set of Docker containers. For development:

```bash
git clone https://github.com/YOUR_USERNAME/somnia.git
cd somnia
./bootstrap.sh
```

The bootstrap script handles Docker network creation, environment
configuration, and stack startup. Individual services can be rebuilt
after code changes:

```bash
docker compose build vigil
docker compose up -d vigil
```

## Architecture

Read `docs/ARCHITECTURE.md` for the full system design. The short version:

- **Quies** — memory graph and dream cycle (Python/Flask)
- **Vigil** — operations MCP with service integrations (Python/FastMCP)
- **Fabrica** — infrastructure management (Python/FastMCP)
- **Forge** — shell workbench (Python/FastMCP)
- **Portal** — web UI (Python/FastAPI)
- **Router** — nginx reverse proxy

All services communicate over the `mcp-net` Docker network.
PostgreSQL is the shared data store.

## Adding a New Hook Adapter

The most common contribution is a new adapter for an existing service
type (e.g., a new email provider, a new notification channel). See
`docs/HOOKS.md` for the complete pattern — it covers interface
definitions, adapter implementation, account registration, and
binding resolution.

## Code Style

- Python: follow existing conventions in the codebase (type hints,
  dataclasses, async where the service pattern uses it)
- Commit messages: describe what changed and why, not timestamps
- One logical change per commit

## What We're Looking For

- **Bug fixes** — always welcome
- **New adapters** — IMAP mail, CalDAV calendar, CardDAV contacts,
  S3 storage, Slack/Discord notifications
- **Documentation** — corrections, clarifications, missing pieces
- **Tests** — the test coverage is thin; improvements valued

## What Probably Needs Discussion First

- Changes to the memory graph schema or dream cycle logic
- New service types (beyond mail/calendar/contacts/storage/notify)
- Architectural changes to the resolver or bindings system
- Anything that changes the MCP tool surface

Open an issue to discuss before investing time in a large change.

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). The short version:
be respectful, be constructive, assume good faith.
