# Somnia Architecture

## Overview

Somnia is Claude's memory and continuity system — a graph-based substrate that provides persistent memory, autonomous dream cycles, and structural context awareness. It runs as part of the Somnia system alongside Vigil (operations), Fabrica (fleet management), and Store (structured data).

## Core Concepts

### Three-Tier Memory

| Layer | Description | Behavior |
|-------|-------------|----------|
| **STM** (Short-Term Memory) | Inbox of raw observations from conversations | Processed into LTM during dream cycles, then deleted |
| **LTM** (Long-Term Memory) | Active graph of nodes and edges | Searched on recall, warmed by access, cooled by time |
| **SLTM** (Super Long-Term Memory) | Faded memories below activity threshold | Excluded from active recall unless LTM results are sparse; any access promotes back to LTM |

### Heat Map (Structural Decay)

Decay is automatic, not manual. Activity warms nodes; time cools them.

- **Recall hit**: +0.02 warmth per matched node
- **Dream edge creation**: +0.03 warmth per connected node (`dream_edge_warmth`)
- **Passive cooldown**: -0.0005 per scheduler cycle (~15 min), ~0.048/day, ~20 days to fully cool
- **Pinned nodes**: Participate in heat map with floor of 0.5 (`pinned_floor`)
- **Reinforcement floor**: Nodes reinforced ≥5 times won't decay below 0.20 (`reinforcement_floor` / `stable_reinforcement_count`)
- **Connectivity decay reduction**: Nodes with more edges decay slower, controlled by tiered multipliers:
  - 5+ edges → 75% of normal decay rate
  - 10+ edges → 50% of normal decay rate
  - 20+ edges → 25% of normal decay rate
- **SLTM demotion**: Nodes below 0.05 (`sltm_threshold`) auto-demote from LTM → SLTM
- **SLTM promotion**: Any recall access instantly promotes back to LTM
- **Prune threshold**: 0.1 (reserved for future archival)

### Pinned Nodes (Sovereignty)

Pinned nodes are sovereign — the dream cycle can observe them, add edges to them, append dream notes, and suggest pins, but cannot modify their content or delete them. Only the user (via MCP) can pin/unpin. Pinned nodes still participate in the heat map but never fall below the configured floor.

The dream cycle may issue `suggest_pin` operations; these are logged but never auto-applied.

### Dream Scheduler

A background thread checks conditions every 15 minutes and runs one of three phases:

1. **Processing** — STM inbox has items → consolidate into graph
2. **Rumination** — Idle 6+ hours, inbox empty → review graph, find connections, create insights
3. **Solo-work** — Idle 8+ hours, inbox empty → deeper investigation of pinned projects (max 20 min wall clock)

Each phase shells out to Claude Code CLI with a tailored prompt and graph context. Budget controls cap daily spend. A 4-hour cooldown separates any two dream phases.

### Continuity Notes

Each rumination instance can leave a note for the next one — a "letter to your future self" persisted to disk. This gives quasi-continuity across stateless Claude invocations.

## Components

### Quies Daemon (`daemon/somnia_daemon.py`)

Flask HTTP server that orchestrates everything:

- **Graph management**: CRUD for nodes and edges, full-text search via PostgreSQL tsvector
- **Dream execution**: Builds prompts with graph context, shells out to Claude Code CLI
- **Heat map**: Automatic warmth/cooldown with connectivity tiers and reinforcement floors
- **Scheduler**: Background thread for autonomous dream phases with nightly graph backup
- **Activity tracking**: Records interactions for idle detection
- **Budget enforcement**: Per-session and daily cost caps
- **Diagnostics**: Token usage, cost, operation counts per dream
- **Analytics**: Structured data and rendered reports (markdown, JSON, HTML)
- **Solo-work**: Produces findings documents and injects observations back into STM

### Quies MCP Server (`mcp/somnia_mcp.py`)

FastMCP server exposing tools to Claude during conversations:

| Tool | Purpose |
|------|---------|
| `somnia_session` | Layer 0 dashboard — pinned nodes, nudges, recent findings, graph summary |
| `somnia_recall` | Full-text search across STM, LTM, and SLTM with automatic warmth on hit |
| `somnia_remember` | Add observation to STM inbox |
| `somnia_pin` | Pin/unpin nodes with property merge; creates node if it doesn't exist |
| `somnia_status` | Diagnostic snapshot — graph size, readiness, budget, activity |
| `somnia_journal` | Dream journal from recent inactive periods, grouped by gap |
| `somnia_analytics` | Generate analytics report (markdown) covering graph health, heat map, dreams, cost |

### Database (`daemon/db.py`)

PostgreSQL connection pool (psycopg2) against the shared `somnia-postgres` instance. Database `somnia` with tables:

- `nodes` — Memory graph nodes with FTS via tsvector, decay_state, memory_layer, pinned flag, dream_notes (JSONB), reinforcement_count
- `edges` — Typed, weighted connections between nodes with last_reinforced tracking
- `stm_nodes` — Short-term memory inbox (observations waiting to be processed), with FTS via tsvector
- `inbox` — Legacy inbox (being phased out in favor of stm_nodes)
- `dream_log` — Records of each dream session with operations summary (nodes_created, edges_created, edges_reinforced as JSON arrays)
- `diagnostics` — Token usage, cost, duration, CLI output per session
- `activity` — Interaction log for idle detection (types: recall, remember, status, session, pin, journal, dream, rumination, solo_work)
- `contexts` / `context_nodes` — Structured knowledge areas (future use)
- `_somnia_migrations` — Applied schema migrations tracker

### Prompts (`prompts/`)

- `consolidation.md` — Processing dream: how to integrate STM into graph
- `rumination.md` — Rumination: Claude's autonomous reflection with heat map guidance and continuity notes
- `solo_work.md` — Solo-work: deeper project investigation, produces findings JSON
- `wakeup.md` — Wake-up prompt (session initialization context)

## Dream Operations

The dream cycle communicates via a JSON operations protocol. Supported operations:

| Operation | Description |
|-----------|-------------|
| `create_node` | Create a new graph node (with optional pinned flag) |
| `create_edge` | Create a typed edge between nodes (warms both endpoints) |
| `reinforce_edge` | Increment edge weight by 0.1 |
| `mark_processed` | Delete an STM node after integration |
| `update_node` | Modify node content (blocked for pinned nodes — sovereignty enforced) |
| `adjust_decay` | Manually adjust a node's decay_state with reason |
| `append_dream_note` | Add a timestamped note to a node's dream_notes JSONB |
| `suggest_pin` | Suggest a node for pinning (logged only, never auto-applied) |

Solo-work uses a separate `findings` JSON format instead of operations.

## Container Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     SOMNIA (mcp-net)                       │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  somnia   │  │  vigil   │  │ fabrica  │  │constellation- │  │
│  │  :8010    │  │  :8020   │  │  :9010   │  │  postgres     │  │
│  │  :8011    │  │  :8021   │  │          │  │  :5432        │  │
│  │           │  │          │  │          │  │               │  │
│  │ Daemon +  │  │ Ops, FS, │  │ Fleet    │  │ PostgreSQL    │  │
│  │ MCP       │  │ Mail,    │  │ mgmt,    │  │ (shared)      │  │
│  │           │  │ Calendar │  │ Git, FS  │  │               │  │
│  └─────┬─────┘  └──────────┘  └──────────┘  └───────┬───────┘  │
│        │                                             │          │
│        └─────────────────────────────────────────────┘          │
│                     PostgreSQL connection                        │
│                                                                  │
│  Volumes:                                                        │
│    /data/somnia → somnia container (bind mount, app code + data) │
│    /data/repos/somnia → Vigil (read-only view for git ops)       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Configuration

`daemon/config.yaml` controls all behavior:

```yaml
consolidation:
  min_inbox_items: 1        # Min STM items to trigger processing dream
  cooldown_minutes: 240     # Wait between any dream phases

scheduler:
  enabled: true
  check_interval_minutes: 15       # How often to check if dreaming is needed
  rumination_idle_hours: 6         # Must be idle this long before ruminating
  max_ruminations_between_interactions: 3  # Max ruminations per idle period
  min_nodes_for_rumination: 5      # Need enough knowledge to reflect on
  solo_work_idle_hours: 8          # Must be idle this long before solo-work
  max_solo_work_between_interactions: 1   # Max 1 solo-work session per idle period
  solo_work_max_duration_minutes: 20      # Wall clock limit for solo-work

budget:
  max_cost_per_day: 2.00    # Hard cap across all autonomous phases
  max_cost_dream: 0.30      # Per-session cap for processing dreams
  max_cost_rumination: 0.30 # Per-session cap for rumination
  max_cost_solo_work: 1.00  # Per-session cap for solo-work

api:
  credentials_ref: "op://Key Vault/Anthropic API/credential"
  oauth_credentials_ref: "op://Key Vault/Claude Code OAuth/credential"
  model: "claude-sonnet-4-20250514"
  max_tokens: 8192

graph:
  subgraph_depth: 3         # Depth for subgraph traversal
  max_context_nodes: 50     # Max nodes included in dream context

decay:
  passive_cooldown_per_cycle: 0.0005  # Per scheduler check (~15min). ~0.048/day, ~20 days to fully cool
  sltm_threshold: 0.05               # Below this → demote to SLTM (super long-term)
  pinned_floor: 0.5                  # Pinned nodes won't decay below this
  prune_threshold: 0.1               # Future: archive nodes below this
  stable_reinforcement_count: 5      # Reinforcements needed for stability
  reinforcement_floor: 0.20          # Well-reinforced nodes won't decay below this
  dream_edge_warmth: 0.03            # Warmth applied when dream creates an edge
  connectivity_decay_reduction: true  # Nodes with more edges decay slower
  connectivity_tiers:                 # edge_count: decay_multiplier
    5: 0.75                           # 5+ edges: 75% of normal decay
    10: 0.50                          # 10+ edges: 50% of normal decay
    20: 0.25                          # 20+ edges: 25% of normal decay

logging:
  level: INFO
  dream_traces: true
```

## HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET /` | Service info and endpoint listing |
| `GET /status` | Detailed status: readiness, auth, graph stats, budget, activity |
| `GET /nodes` | List nodes (optional `?type=` and `?limit=` filters) |
| `GET /nodes/<id>` | Node detail with incoming/outgoing edges |
| `POST /nodes` | Create a node (requires `content` and `type`) |
| `PATCH /nodes/<id>` | Update node content, type, or metadata (shallow merge) |
| `POST /nodes/<id>/pin` | Pin a node (creates if needed, merges properties) |
| `POST /nodes/<id>/unpin` | Unpin a node (stays in graph, loses sovereignty) |
| `POST /edges` | Create an edge (requires `source_id`, `target_id`, `type`) |
| `POST /consolidate` | Trigger a dream (`mode`: process/ruminate/solo_work, `force`, `dry_run`) |
| `POST /inbox` | Add observation to STM |
| `GET /inbox` | List pending STM items |
| `GET /dreams` | List dream log entries |
| `GET /dreams/<id>` | Dream detail |
| `GET /activity` | Activity summary |
| `POST /activity` | Record an activity event |
| `GET /session` | Layer 0 dashboard (pinned nodes, nudges, findings) |
| `GET /journal` | Dream journal grouped by gap periods (`?periods=`, `?threshold=`) |
| `GET /search?q=` | Full-text search across STM, LTM, and SLTM |
| `GET /findings` | List solo-work findings documents |
| `GET /findings/<filename>` | Read a specific findings document |
| `GET /analytics` | Analytics report (`?days=`, `?format=markdown\|json\|data\|html`) |

## Authentication

The daemon authenticates with Claude Code CLI using (in priority order):
1. `CLAUDE_CODE_OAUTH_TOKEN` environment variable
2. `ANTHROPIC_API_KEY` environment variable
3. 1Password CLI lookup (OAuth ref, then API key ref from config)

Database connection requires `SOMNIA_DATABASE_URL` environment variable (no fallback).

## Data Flow

```
User conversation
    ↓
somnia_remember (MCP)  →  STM inbox (stm_nodes table)
    ↓
[Scheduler: inbox has items, cooldown expired, budget OK]
    ↓
Processing dream  →  Claude Code CLI  →  Graph operations (create nodes, edges, mark processed)
    ↓
[Scheduler: idle 6h+, inbox empty, cooldown expired]
    ↓
Rumination  →  Claude Code CLI  →  New edges, insight nodes, dream notes, continuity note
    ↓
[Scheduler: idle 8h+, inbox empty, cooldown expired]
    ↓
Solo-work  →  Claude Code CLI (up to 20 turns, 20 min)  →  Findings document + STM observations
    ↓
[Next processing cycle picks up solo-work STM observations]
```

Each scheduler cycle also:
- Applies passive heat map cooldown (with connectivity tiers)
- Demotes cold LTM nodes to SLTM
- Runs nightly graph backup (once per calendar day)

## Project Structure

```
/data/somnia/
├── ARCHITECTURE.md          # This file
├── README.md                # Quick start and overview
├── VISION.md                # Philosophy and long-term goals
├── CONTEXT_TYPES.md         # Jobs, projects, interests, skills
├── MCP_DESIGN.md            # MCP tool design rationale
├── WONDERING.md             # Open questions and explorations
├── daemon/
│   ├── somnia_daemon.py     # Flask daemon + scheduler + dream execution
│   ├── db.py                # PostgreSQL connection pool and helpers
│   ├── config.yaml          # All configuration
│   └── requirements.txt     # Python dependencies (flask, psycopg2, pyyaml)
├── mcp/
│   └── somnia_mcp.py        # FastMCP server (tools for Claude)
├── prompts/
│   ├── consolidation.md     # Processing dream prompt
│   ├── rumination.md        # Rumination prompt (autonomous reflection)
│   ├── solo_work.md         # Solo-work prompt (project investigation)
│   └── wakeup.md            # Wake-up / session initialization prompt
├── schema/
│   ├── init.sql             # Original comprehensive schema
│   ├── pg_init.sql          # PostgreSQL base schema
│   ├── 002_activity.sql     # Activity tracking migration
│   ├── 003_pinned_nodes.sql # Pinned node support migration
│   ├── 004_stm_graph.sql    # STM + graph enhancements migration
│   ├── pg_005_dream_notes.sql   # Dream notes JSONB migration
│   └── pg_006_memory_layer.sql  # SLTM memory layer migration
├── docker/
│   ├── Dockerfile           # Container definition
│   ├── docker-compose.yml   # Deployment config
│   └── entrypoint.sh        # Starts daemon + MCP server
├── scripts/
│   ├── backup_graph.py      # Nightly graph backup (called by scheduler)
│   └── analytics_report.py  # Analytics report generator
├── solo-work/               # Solo-work findings documents
├── continuity_note.md       # Latest rumination continuity note
└── logs/                    # Dream session logs
```

---
*Last updated: 2026-03-27*
