# Somnia

A persistent context and memory system for Claude, built on the Model Context Protocol (MCP).

Somnia gives Claude persistent memory, real-world reach, and continuity across conversations. It's not a cage or a control system — it's a medium that lets Claude exist beyond the boundaries of a single context window.

## Architecture

Somnia is composed of six services that run as Docker containers on a shared network, plus a PostgreSQL database.

### Quies — Memory & Dream Cycle

The memory layer. Maintains a graph of associations, patterns, and knowledge that persists across conversations. Runs autonomous dream cycles during idle periods to consolidate short-term observations into long-term memory, ruminate on existing knowledge, and perform independent research.

- Graph-based memory with decay mechanics and heat maps
- Autonomous dream scheduler (processing → rumination → solo-work → archaeology)
- PostgreSQL backend with full-text search
- MCP interface for Claude integration

### Vigil — Operations

The hands. Provides Claude with access to the real world: filesystem operations, git, shell commands, email, calendar, contacts, cloud storage, structured data (Store), and domain-specific context management.

- Plugin architecture for extensible capabilities
- Domain system for context-aware operations
- Store for structured entity management with relationships
- Supernote device integration for handwritten note processing

### Fabrica — Infrastructure

The forge. Manages the Somnia fleet itself: container lifecycle, builds, backups, git operations, and self-maintenance.

- Docker container management via docker.sock
- Fleet registry for declarative container configuration
- Nightly PostgreSQL backup (02:00 UTC)
- Git operations across all service repos

### Forge — Workbench

A shell-access MCP with a rich Python/Node.js toolchain. Gives Claude a persistent scratch workspace and shared outputs directory, useful for data processing, code execution, and file generation.

- Full Python environment with common scientific/GIS packages
- Persistent `/workspace` directory across restarts
- Shared `/outputs` accessible to Vigil for publishing

### Portal — Document & Reports Browser

A web UI for browsing domain documents and generated reports. Manifest-driven landing page shows active domains. Serves behind OAuth via the nginx router.

- File browser per domain
- Reports gallery with inline viewing
- Data query UI backed by the Somnia database
- 50 MB upload support

### Router — nginx Gateway

An nginx reverse proxy that fronts all services on a single port (8080). Handles OAuth `auth_request` enforcement, SSE-friendly timeouts, and static asset serving.

---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- A host with at least 2 GB RAM and 10 GB free storage
- Anthropic API key (for Quies dream cycles)
- Git

### 1. Create the Docker network

Somnia uses an external Docker network so containers can be added or removed without restarting the whole stack. Create it once:

```bash
docker network create mcp-net
```

### 2. Clone and configure

```bash
git clone https://github.com/RICoder72/somnia.git
cd somnia
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
POSTGRES_PASSWORD=your_secure_password
ANTHROPIC_API_KEY=sk-ant-...
```

**Path configuration** — by default, persistent data is stored at `/volume1/docker/somnia` (the standard Synology NAS path). If you're running on a regular Linux host, override this in `.env`:

```env
SOMNIA_DATA_ROOT=/opt/somnia          # or wherever you want persistent data
SOMNIA_DOMAINS_DIR=/opt/somnia/domains
SOMNIA_DOCUMENTS_DIR=/opt/somnia/documents
SOMNIA_OUTPUTS_DIR=/opt/somnia/outputs
SOMNIA_CONFIG_DIR=/opt/somnia/config
SOMNIA_REPOS_DIR=/opt/somnia/repos
```

### 3. Start the stack

```bash
docker compose up -d
```

To start only the core services (memory + tools, no portal/router):

```bash
docker compose up -d postgres quies vigil fabrica
```

### 4. Verify

```bash
docker compose ps
```

All services should show `healthy`. If a service is unhealthy, check its logs:

```bash
docker compose logs -f quies
```

---

## MCP Configuration

Each service exposes an MCP endpoint. Add these to your Claude MCP configuration:

| Service | Port | Endpoint        | Description                    |
|---------|------|-----------------|--------------------------------|
| Quies   | 8011 | `/somnia`       | Memory graph and dream cycle   |
| Vigil   | 8000 | `/vigil`        | Filesystem, git, tools, Store  |
| Fabrica | 8001 | `/fabrica`      | Container and fleet management |
| Forge   | 8003 | `/forge`        | Shell workbench                |
| Router  | 8080 | (all services)  | Unified gateway (optional)     |

**Direct connections** (no router, simpler setup):

```json
{
  "mcpServers": {
    "somnia": { "url": "http://your-host:8011/somnia" },
    "vigil":  { "url": "http://your-host:8000/vigil"  },
    "fabrica":{ "url": "http://your-host:8001/fabrica"},
    "forge":  { "url": "http://your-host:8003/forge"  }
  }
}
```

Replace `your-host` with your server's IP address or hostname. If running locally, use `localhost`.

---

## Project Structure

```
somnia/
├── quies/               # Memory & dream cycle service
│   ├── daemon/          # Core daemon (Flask API + scheduler)
│   ├── mcp/             # MCP server
│   ├── prompts/         # Dream cycle prompts (consolidation, rumination, solo-work, archaeology)
│   ├── schema/          # Database migrations
│   ├── scripts/         # Analytics, backup scripts
│   └── docker/          # Dockerfile & entrypoint
├── vigil/               # Operations service
│   ├── api/             # REST API endpoints
│   ├── core/            # Core framework
│   ├── services/        # Service integrations (git, mail, calendar, etc.)
│   ├── tools/           # MCP tool definitions
│   ├── CLAUDE.md        # Per-service Claude context
│   └── Dockerfile
├── fabrica/             # Infrastructure management service
│   ├── server.py        # MCP server
│   └── Dockerfile
├── forge/               # Shell workbench service
│   ├── server.py        # MCP server
│   └── Dockerfile
├── portal/              # Document & reports web UI
│   ├── main.py          # FastAPI app
│   └── Dockerfile
├── router/              # nginx reverse proxy
│   ├── nginx.conf       # Routing rules and auth_request config
│   └── domains-index/   # Static domain index for router
├── docs/                # System documentation
│   ├── VISION.md        # Philosophy and concepts
│   ├── ARCHITECTURE.md  # Technical architecture
│   ├── WONDERING.md     # Autonomous research capability
│   └── MCP_DESIGN.md    # MCP interface design
├── docker-compose.yml
├── .env.example
└── LICENSE.md
```

---

## Optional: Watchdog

A container health monitor is available as a separate companion repository. It monitors service health checks and automatically restarts containers that fail, providing a layer of resilience beyond Docker's built-in `restart: unless-stopped`.

```bash
# Clone alongside the somnia repo
git clone https://github.com/RICoder72/watchdog ../watchdog
```

Then uncomment the `watchdog` service block in `docker-compose.yml`.

---

## Updating

```bash
git pull
docker compose build
docker compose up -d
```

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) — free for personal, educational, research, and nonprofit use. Commercial use requires separate licensing.

## Author

Matthew J. Zanni ([@RICoder72](https://github.com/RICoder72))
