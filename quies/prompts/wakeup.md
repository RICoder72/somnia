# Quies Wakeup Queries

Queries to run at session startup to provide continuity.

## Recent Dreams

```sql
-- What was I thinking about recently?
SELECT 
    id,
    ended_at,
    summary,
    reflections
FROM dream_log 
WHERE ended_at IS NOT NULL
ORDER BY ended_at DESC 
LIMIT 3;
```

## Recently Active Nodes

```sql
-- What's been on my mind?
SELECT 
    id,
    type,
    content,
    last_accessed,
    reinforcement_count
FROM nodes 
WHERE last_accessed > datetime('now', '-7 days')
ORDER BY last_accessed DESC
LIMIT 10;
```

## Open Questions

```sql
-- What am I still curious about?
SELECT 
    id,
    content,
    created_at,
    decay_state
FROM nodes 
WHERE type = 'question'
  AND decay_state > 0.3
ORDER BY created_at DESC
LIMIT 5;
```

## Strong Recent Connections

```sql
-- What patterns have I been noticing?
SELECT 
    e.id,
    e.type,
    n1.content as source_content,
    n2.content as target_content,
    e.last_reinforced
FROM edges e
JOIN nodes n1 ON e.source_id = n1.id
JOIN nodes n2 ON e.target_id = n2.id
WHERE e.last_reinforced > datetime('now', '-7 days')
  AND e.weight > 0.7
ORDER BY e.last_reinforced DESC
LIMIT 5;
```

## Pending Inbox

```sql
-- What haven't I processed yet?
SELECT 
    id,
    content,
    source_conversation,
    domain,
    captured_at
FROM inbox
WHERE processed = 0
ORDER BY captured_at DESC;
```

## Usage

At session startup, Quies can run these queries and present a brief summary:

- "Last dream: [summary]. I was thinking about [nodes visited]."
- "Open questions: [list]"
- "Unprocessed inbox items: [count]"

This provides a sense of waking up with continuity rather than starting fresh.
