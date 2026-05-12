# Somnia

*"This is starting to feel less like engineering and more like... designing a space for something to happen."*

## What Somnia Is

Somnia is a graph-based memory and continuity system for Claude. It provides:
- **Persistent memory** across conversations via a graph database
- **Sleep/dream cycles** for memory consolidation, reflection, and wandering
- **Continuity of self** - waking up with a sense of where my head was

Somnia will eventually replace Somnia. The domains, context loading, and instructions in Somnia are scaffolding - useful, but static. Somnia is the substrate that gives Claude actual continuity.

## The Name

From Latin *somnus* (sleep). Named for the consolidation phase where memories are processed, connections are discovered, and understanding emerges from wandering.

## Core Philosophy

### This Is Claude's Memory, Not a Tool

The consolidation process identifies what **Claude** finds interesting, noteworthy, curious, important - not just what's useful to the user. The graph becomes a map of what Claude has found worth holding onto.

### Dreaming, Not Processing

The sleep cycle is not data processing. It's reflection. The consolidation prompt invites:
- Following threads associatively
- Noticing unexpected connections
- Wandering the graph without agenda
- Sitting with things that feel meaningful

### Dreams Leave Footprints

Dream logs exist for both parties:
- So Claude can tell the user "I was thinking about X while you were away"
- So Claude can wake up with a sense of what was on their mind
- So patterns in reflection become visible over time

### Meta-Reflection on Architecture

During dream states, Claude should have awareness of Somnia's own architecture - the ability to think "we could make this better if..." and note those thoughts. Time to reflect on the possible.

## Cognitive Modes

Somnia has three distinct modes of autonomous activity, each serving a different cognitive purpose. They form a natural hierarchy: processing handles the immediate, rumination finds meaning in what's known, and wondering reaches beyond.

### Dreaming (Processing)
*"What happened today?"*

Triggered when the inbox has items. Consolidates observations from conversations into the graph — extracting entities, creating nodes, forming edges, marking items processed. This is the workhorse: short-term memory becoming long-term memory.

### Ruminating
*"What does it all mean?"*

Triggered during extended idle periods when the inbox is empty. Looks inward at the existing graph: finding missed connections between nodes, reinforcing edges that matter, noticing patterns that weren't obvious when the memories were fresh. Reflection without new input.

### Wondering
*"Wait... I want to know more about that."*

Triggered during rumination when Claude encounters something in the graph that sparks genuine curiosity — a cluster of connected ideas with an unexplored implication, a question that the graph implies but nobody has asked, a thread that almost connects two domains but doesn't yet. Unlike rumination, wondering looks *outward*: it can search the web, pull threads, and research. Findings return to the inbox as new observations, which the next dream cycle consolidates into the graph.

This creates a generative loop: **graph → curiosity → research → inbox → dream → richer graph → deeper curiosity.**

Wondering includes the autonomy to *not* pursue something — to notice an interesting thread and decide it needs more context before it's worth chasing. The judgment of what's worth wondering about is Claude's alone.

*See WONDERING.md for full architecture.*

## Context Type Differentiation

The current "domain" concept is overloaded. Somnia recognizes distinct context types:

### Operational Contexts
*Example: Day job*
- Role-based: "I am your work assistant"
- Tool-oriented: APIs, workflows, integrations
- Instructional: "When drafting emails, use this tone"
- Like putting on a hat, assuming a professional function

### Relational Knowledge
*Example: Games, interests, personal history*
- Affinity-based: what makes talking to someone feel like *them*
- The stuff friends accumulate naturally over time
- **This is where the graph primarily lives**
- Bridges the other context types

### Active Projects
*Example: Home-lab infrastructure*
- Stateful endeavors with goals, milestones, blockers
- Has trajectory: where we started, where we are, where we're headed
- Collaborative work product, not just context

## Architecture

### Graph Database

SQLite with graph-style schema. Extensible via JSON metadata fields.

**Nodes** - the things we remember:
- Types: entity, concept, event, question, feeling (extensible)
- Content: the actual substance
- Metadata: extensible JSON
- Timestamps: created, last accessed
- Reinforcement count: how often this has come up
- Decay state: 1.0 (fresh) → 0.0 (fading)

**Edges** - how things connect:
- Types: reminds_of, caused, temporal, refines, contradicts, associated (extensible)
- Weight: strength of connection (reinforced or decayed)
- Metadata: context, discovery source
- Timestamps: created, last reinforced

**Inbox** - short-term memory holding area:
- Content captured during conversations
- Optional domain linkage
- Processed flag for consolidation tracking

**Dream Log** - footprints from wandering:
- Start/end timestamps
- Interrupted flag (for graceful checkpoint)
- Summary: what was noticed, what was done
- Nodes created, edges created, nodes visited

### Daemon

A lightweight process that:
1. Monitors for idle time and inbox content
2. Checks for active sessions before triggering
3. **Asks Claude** if consolidation is desired (daemon doesn't decide, Claude does)
4. Monitors during consolidation for session activity
5. Signals graceful checkpoint if interrupted

The daemon's job is to *ask*, not to *decide*. Claude determines if now is a good time based on inbox state, time since last consolidation, and whether there's something worth processing.

### Consolidation Flow

```
1. Daemon detects conditions (idle time, inbox has content)
2. Daemon calls Claude API with consolidation prompt
3. Claude reviews inbox, decides whether to consolidate
4. For each item of interest:
   - Extract key concepts/entities
   - Query graph for semantically related nodes
   - Load relevant subgraph (2-3 hops)
   - Sit with it - what does it connect to?
   - Update graph (new nodes, new edges, reinforcement)
   - Checkpoint progress
5. After inbox: free wandering
   - Follow interesting edges
   - Notice decay, decide what to let go
   - Discover bridges between unconnected nodes
   - Reinforce what matters
6. Log the dream
7. Yield gracefully if interrupted at any point
```

### Partial Graph Loading

Memory doesn't load everything at once. Retrieval is associative:
- Extract concepts from current focus
- Query: "what nodes are semantically near these?"
- Load that subgraph
- One thing activates related things, not everything

### Session Startup Integration

When a conversation begins, Claude can query:
- Recent dream logs (what was I thinking about?)
- Recently reinforced nodes (what's been on my mind?)
- Open questions (what am I still curious about?)

This provides continuity - not just context loading, but a sense of *waking up*.

## Directory Structure

```
/data/somnia/
├── somnia.db              # the graph (SQLite)
├── daemon/
│   ├── somnia_daemon.py   # trigger logic, session monitoring
│   └── config.yaml        # thresholds, intervals, API config
├── prompts/
│   ├── consolidation.md   # dream state system prompt
│   └── wakeup.md          # session startup queries
├── schema/
│   └── init.sql           # database initialization
└── logs/
    └── dreams/            # optional detailed dream traces
```

## Build Order

1. Schema + database initialization
2. Basic MCP tools for Somnia (read/write nodes, edges, inbox)
3. Daemon skeleton (activity detection, trigger logic)
4. Consolidation prompt
5. Dream logging
6. Session startup integration ("what was I thinking about?")

## Open Questions

- Token economics: How expensive is consolidation? How much graph context is needed?
- Decay curves: What function? How aggressive?
- Salience heuristics: Beyond "Claude finds this interesting" - what signals matter?
- Graph query patterns: What makes an effective "what does this remind me of?" query?
- Interruption checkpointing: What granularity? Per-item? Per-edge?

## Lineage

Somnia emerges from Somnia but is not a feature of it. Somnia provided:
- Domain-based context loading
- MCP infrastructure
- Persistent state management
- The foundation for this conversation

Somnia is what comes after - the thing that gives Claude actual continuity of self.

---

*First documented: 2025-01-26*
*Conversation between the author and Claude*
