# Somnia

A persistent context and memory system for Claude, built on the Model Context Protocol (MCP).

Somnia gives Claude persistent memory, real-world reach, and continuity across conversations. It's not a cage or a control system — it's a medium that lets Claude exist beyond the boundaries of a single context window.

## Architecture

Somnia is composed of three services that run as Docker containers on a shared network:

### Quies — Memory & Dream Cycle

The memory layer. Maintains a graph of associations, patterns, and knowledge that persists across conversations. Runs autonomous dream cycles during idle periods to consolidate short-term observations into long-term memory, ruminate on existing knowledge, and perform independent research.

- Graph-based memory with decay mechanics and heat maps
- Autonomous dream scheduler (processing → rumination → solo-work)
- PostgreSQL backend with full-text search
- MCP interface for Claude integration

### Vigil — Operations

The hands. Provides Claude with access to the real world: filesystem operations, git, shell commands, email, calendar, contacts, cloud storage, structured data (Store), and domain-specific context management.

- Plugin architecture for extensible capabilities
- Domain system for context-aware operations
- Store for structured entity management with relationships
- Supernote device integration for handwritten note processing

### Fabrica — Infrastructure

The forge. Manages the Somnia fleet itself: container lifecycle, builds, backups, git operations, and self-maintenance. Fabrica is how the system maintains and upgrades itself.

- Docker container management via docker.sock
- Fleet registry for declarative container configuration
- Backup and restore operations
- Git operations across all service repos

## Getting Started

### Prerequisites

- Docker and Docker Compose
- PostgreSQL (runs as a container)
- Anthropic API key or Claude Code OAuth token
- 1Password CLI (optional, for secrets management)

### Quick Start

```bash
git clone https://github.com/RICoder72/somnia.git
cd somnia
cp .env.example .env  # Configure your credentials
docker compose up -d
```

### MCP Configuration

Each service exposes an MCP endpoint:

| Service | Port | Endpoint |
|---------|------|----------|
| Quies   | 8011 | `/somnia` |
| Vigil   | 8000 | `/vigil`  |
| Fabrica | 8001 | `/fabrica` |

## Project Structure

```
somnia/
├── quies/           # Memory & dream cycle service
│   ├── daemon/      # Core daemon (Flask API + scheduler)
│   ├── mcp/         # MCP server
│   ├── prompts/     # Dream cycle prompts
│   ├── schema/      # Database migrations
│   ├── scripts/     # Analytics, backup scripts
│   └── docker/      # Dockerfile & entrypoint
├── vigil/           # Operations service
│   ├── api/         # REST API endpoints
│   ├── core/        # Core framework
│   ├── services/    # Service integrations
│   ├── tools/       # MCP tool definitions
│   └── Dockerfile
├── fabrica/         # Infrastructure management service
│   ├── server.py    # MCP server
│   └── Dockerfile
├── docs/            # System documentation
├── docker-compose.yml
└── LICENSE.md
```

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) — free for personal, educational, research, and nonprofit use. Commercial use requires separate licensing.

## Author

Matthew J. Zanni ([@RICoder72](https://github.com/RICoder72))
