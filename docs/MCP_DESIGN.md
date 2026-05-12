# Somnia MCP Integration Design

## Overview

This document captures the design decisions for Somnia's MCP server and client
integration. The MCP server is how Claude interacts with Somnia's memory graph
during conversations.

## Design Principles

### Write Without Checking

When Claude observes something worth remembering during a conversation, it writes
to the inbox immediately without first checking whether the graph already contains
similar information. Rationale:

- The dream cycle's job is consolidation — deduplication, reinforcement, edge
  discovery. Frequency of observation is a signal, not noise.
- Pre-checking adds read overhead to every write and solves the wrong problem at
  the wrong layer.
- If something comes up repeatedly, the dream cycle uses that as reinforcement
  data for existing nodes and edges.

### MCP as Abstraction Layer

The MCP tools abstract away the implementation details of memory storage and
retrieval. `somnia_recall("camera infrastructure")` works the same whether the
backend uses FTS5 keyword search, semantic embeddings, or something else. This
means search quality can be improved independently of the client integration.

### Topic-Derived Reads

Claude doesn't do a big upfront context dump. Instead, as conversation develops
and topics become clear, Claude pulls relevant memories. If the topic shifts,
Claude pulls again. This is lightweight and natural — the equivalent of "what do
I know about this?" when it would actually be useful.

### Selective Writes, Liberal Reads

- **Reads**: Be liberal. Searching is cheap and the payoff is high — better
  responses and better situational awareness. A few searches per conversation as
  topics shift.
- **Writes**: Be selective on *quality* (decisions, preferences, patterns,
  surprises, connections) but not on *novelty*. Don't filter out things the graph
  might already know. Average 1-5 inbox items per conversation depending on
  content density.

## MCP Tools (Initial Set)

### `somnia_remember`
- **Purpose**: Add an observation to the inbox for later consolidation
- **Input**: content (string), optional domain/source tags
- **Behavior**: Fire-and-forget. No validation against existing graph.
- **Usage**: During conversation when Claude notices something worth keeping

### `somnia_recall`
- **Purpose**: Search for relevant memories by topic
- **Input**: query (string), optional limit
- **Returns**: Matching nodes with their types, content, and edge summaries
- **Behavior**: Searches nodes via FTS (upgradeable to semantic search later)
- **Usage**: When a topic surfaces and Claude wants to know what it already knows

### `somnia_status`
- **Purpose**: Quick health/state check
- **Returns**: Node count, edge count, inbox depth, last dream time, dream readiness
- **Usage**: Diagnostic, occasional

These three tools are the complete initial integration. More tools (direct graph
browsing, edge exploration) can be added later as needs emerge.

## Client Integration

### Global Instructions

Claude's global instruction set (via Somnia) includes awareness of Somnia:

- Somnia exists as a persistent memory system
- During conversations, note decisions, preferences, patterns, surprises,
  interconnections
- As topics develop, recall relevant memories to inform responses
- Don't narrate the memory process — just use the knowledge naturally
- Don't be excessive — quality over quantity for writes, topic-shift driven for reads

### Session Behavior

No heavy wakeup ritual. The first recall happens naturally once Claude understands
what the conversation is about. Subsequent recalls happen as topics shift.

### Token Budget Awareness

Each MCP call has overhead. The integration should be efficient:
- Recalls: 2-4 per conversation typical
- Remembers: 1-5 per conversation typical
- Some conversations may generate zero writes (casual chat, nothing new learned)
- Dense planning/decision sessions may generate more

## Architecture

```
┌──────────────────────┐     MCP (tools)      ┌──────────────────────┐
│                      │ ◄──────────────────── │                      │
│   Somnia Container   │   somnia_remember     │   Claude (any UI)    │
│                      │   somnia_recall       │                      │
│   ┌──────────────┐   │   somnia_status       │   Global instructions│
│   │  MCP Server  │   │ ────────────────────► │   drive behavior     │
│   │  (SSE/stdio) │   │                       │                      │
│   └──────┬───────┘   │                       └──────────────────────┘
│          │           │
│   ┌──────▼───────┐   │     HTTP (internal)   ┌──────────────────────┐
│   │  Flask API   │   │ ◄──────────────────── │   Somnia       │
│   │  (existing)  │   │   curl calls           │   (existing tools)   │
│   └──────┬───────┘   │                       └──────────────────────┘
│          │           │
│   ┌──────▼───────┐   │     CLI subprocess    ┌──────────────────────┐
│   │  SQLite DB   │   │ ────────────────────► │   Claude Code        │
│   │  (graph)     │   │   dream consolidation │   (dream worker)     │
│   └──────────────┘   │                       └──────────────────────┘
│                      │
└──────────────────────┘
```

## What This Does NOT Include (Yet)

- Direct node/edge creation from MCP (writes go through inbox → dream cycle)
- Semantic search / embeddings (FTS5 keyword search for now)
- Automatic idle detection for dream triggering
- Decay mechanics
- Agent spawning during dreams
- Integration with Somnia domain system

These are all on the roadmap but deferred in favor of getting the core
read/write/dream loop working end-to-end first.

---

*Documented: 2026-02-03*
*Design conversation between the author and Claude*
