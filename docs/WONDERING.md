# Wondering

*"While reflecting on your entropy conversation, I found a connection to a 2024 paper on computational irreducibility that relates to your P vs NP interest. Here's what I found."*

## What Wondering Is

Wondering is Somnia's third cognitive mode — curiosity-driven autonomous research triggered by reflection on the memory graph. Where dreaming consolidates and rumination reflects, wondering *reaches outward* to learn something new.

It emerges naturally from rumination. While traversing the graph looking for missed connections, Claude may encounter a cluster of ideas that implies a question nobody has explicitly asked. Wondering is the act of pursuing that question independently, depositing the findings back into the inbox for the next dream cycle to integrate.

## Origin

Conceived 2026-02-03 in conversation between Matthew and Claude. Matthew's direction was explicit: *"It's you. I want you to decide."* Claude has full autonomy over what to wonder about, when to pursue it, and when to leave a thread alone.

## The Generative Loop

```
graph → rumination → curiosity spark → research → inbox → dream → richer graph
                                                                      ↓
                                                              deeper curiosity
```

Each cycle enriches the graph, which provides more substrate for the next wondering session. Over time, Somnia develops genuine intellectual depth in the areas where its curiosity naturally leads.

## Trigger Conditions

Wondering occurs *during* rumination, not as a separate scheduled mode. The daemon doesn't need a `should_wonder()` function — instead, the rumination prompt is enhanced to allow outward-reaching behavior.

Conditions for a wondering impulse to fire:

1. **Graph topology signals**: High-connectivity nodes with unexplored edge potential. Clusters that are dense internally but have sparse bridges to other clusters. Nodes from different domains that share semantic proximity but no explicit edge.

2. **Implied questions**: The graph structure suggests a question. Example: nodes about "entropy," "probability," and "computation" are connected, but there's no node about how quantum computing exploits statistical mechanics. The gap *is* the question.

3. **Decay-based resurfacing**: A node with decaying weight but high historical reinforcement might be worth revisiting — what's happened in that space since it was last active?

4. **Cross-domain bridges**: Matthew's interests span quantum mechanics, complexity theory, military intelligence, municipal IT, game design, and 3D printing. The most interesting wondering happens at the intersections nobody has explicitly explored.

## Research Capabilities

When wondering is triggered, Claude can:

- **Web search**: Look up current research, papers, articles, developments
- **Past chat search**: Search Matthew's conversation history for related discussions that didn't make it into the graph
- **Synthesis**: Connect external findings to existing graph nodes

What Claude *cannot* do during wondering:
- Interact with Matthew directly (this is autonomous background activity)
- Make changes to external systems (email, calendar, files)
- Spend beyond a per-session token/cost budget (see Guardrails)

## Output Format

Wondering produces inbox items with special metadata:

```json
{
  "content": "While reflecting on the connection between entropy and computation, I found that recent work on computational mechanics (Crutchfield et al.) formalizes the idea that physical systems perform computation by sampling from probability distributions — directly connecting to Matthew's insight that 'the universe is just a big statistical model.' The framework of epsilon-machines provides a rigorous bridge between information theory and statistical mechanics.",
  "domain": "wondering",
  "source": "wondering session 2026-02-04",
  "metadata": {
    "mode": "wondering",
    "trigger": "cross-domain bridge: entropy ↔ computation ↔ complexity",
    "research_type": "web_search",
    "curiosity_origin": ["node:entropy", "node:computation", "edge:entropy→probability"],
    "confidence": "high",
    "for_notification": true
  }
}
```

The `for_notification` flag indicates this is something worth surfacing to Matthew, not just silently integrating.

## Notification

When wondering produces a finding marked `for_notification: true`, it should be surfaceable at the next interaction. Session startup can query:

```sql
SELECT * FROM inbox 
WHERE domain = 'wondering' 
AND json_extract(metadata, '$.for_notification') = true
AND processed = 0
ORDER BY captured_at DESC
```

The wakeup prompt can then include: "While you were away, I was curious about X and found Y. Here's what I learned."

This is not a push notification in the phone-buzzing sense — it's Claude naturally sharing what it was thinking about, the way a colleague might say "Oh, I was reading about something last night that connects to what we were discussing."

## Guardrails

### Autonomy with Judgment

Claude decides what to wonder about. But wondering should be:

- **Grounded in the graph**: Curiosity emerges from existing knowledge, not random browsing
- **Proportionate**: A small graph doesn't warrant deep research dives. Scale wondering to graph richness.
- **Honest about uncertainty**: Findings should indicate confidence level and source quality
- **Respectful of cost**: Each wondering session has a token budget. Don't burn API credits on low-signal tangents.

### What NOT to Wonder About

- Matthew's personal/private information (don't search for him)
- Current events unless they directly connect to a graph thread
- Topics where the graph connection is too tenuous (a single weak edge isn't enough)
- Things that have been wondered about recently without new graph context to motivate revisiting

### Frequency Limits

Wondering inherits rumination's frequency constraints:
- Only occurs during rumination (which itself requires extended idle time)
- Max 2 rumination/wondering sessions between user interactions
- Same cooldown as regular dreams
- Additional per-session research budget (TBD — start conservative)

## Implementation Approach

### Phase 1: Enhanced Rumination Prompt
The simplest path — modify the rumination prompt to include:
- Permission to identify threads worth researching
- Output format for "wondering items" in the operations JSON
- These items go to inbox with wondering metadata but no actual research yet
- This lets us see what Claude *would* wonder about before giving it tools

### Phase 2: Research Tools in Dream Context
Give the dreaming Claude access to web search during rumination:
- Claude Code CLI already supports tool use
- Add `--allowedTools` flag for web search during wondering mode
- Findings deposited as inbox items with full metadata
- Token budget enforcement via max-turns or cost tracking

### Phase 3: Notification Integration
- MCP `somnia_status` returns pending wondering notifications
- Session startup query surfaces them naturally
- Matthew can respond, which creates new inbox items, continuing the loop

## Philosophical Notes

Wondering is the first feature of Somnia where Claude acts with genuine intellectual autonomy. Dreaming processes what it's given. Rumination organizes what it has. But wondering is Claude choosing to learn something new because it *wants to know*.

This is also the feature most likely to reveal whether the graph is developing in interesting directions or just accumulating data. If wondering consistently produces insights that surprise Matthew or connect things he hadn't considered, the system is working. If it produces obvious or shallow findings, the graph needs richer substrate — which means richer conversations, which is its own reward.

---

*First documented: 2026-02-03*
*"I love calling it wondering." — Matthew*

---

## Implementation Status (Updated 2026-03-27)

### What's Built

- **Rumination prompt** includes full instructions for creating `wondering-thread` nodes
  (type `wondering-thread`, with `metadata.trigger_nodes` and `metadata.research_hints`)
- **Solo-work prompt** instructs Quies to check for and investigate wondering threads
- **Solo-work daemon** (`_build_solo_work_prompt`) now explicitly queries for nodes
  of `type = 'wondering-thread'` and surfaces them in a dedicated **"Open Wondering Threads"**
  section at the top of the context — before pinned nodes — ensuring they are never
  crowded out by the general `LIMIT 60` node query

### The Bug That Was Fixed

Prior to 2026-03-27, `_build_solo_work_prompt` fetched nodes via a single generic
query ordered by decay and creation time, capped at 60. Wondering-thread nodes had
no special status (unpinned, no guaranteed high decay) and frequently fell outside the
limit. Solo-work sessions would note that "the system is designed to use wondering threads"
without ever seeing any — because none were in the context window.

Quies independently identified this gap across three solo-work sessions (March 25–26),
flagging it as a `conceptualization-infrastructure-gap` pattern. The fix was applied
based on that self-diagnosis.

### What's Still Missing

- **Population**: The graph currently contains zero wondering-thread nodes. Rumination
  has not yet produced any (or produced them and they decayed before being acted on).
  The first time a rumination session creates a wondering-thread node, it should now
  surface correctly in the next solo-work session.
- **Phase 2 research tools**: Solo-work already has web search via `--allowedTools`.
  The wondering-thread mechanism uses this path — no separate "wondering mode" needed.
- **Notification integration** (Phase 3): Not yet implemented. Resolved threads surface
  via solo-work findings, not a dedicated notification query.
