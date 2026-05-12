# Context Types in Somnia

Somnia differentiates between types of knowledge and context. This replaces Somnia's flat "domain" concept with something richer.

## The Five Context Types

### 1. Jobs (Work Contexts)

Professional roles with structure, accountability, and deliverables.

**Characteristics:**
- Has a title/role
- External stakeholders (boss, colleagues, clients)
- Documents, templates, policies
- Compliance requirements
- Deadlines and deliverables
- Institutional memory matters

**Example: IT Director**
```yaml
type: job
name: work
role: Director of Technology
organization: Acme School District

assets:
  documents:
    - staff_meeting_agenda_template.md
    - incident_report_template.md
    - tech_plan_2026.docx
  policies:
    - acceptable_use_policy.md
    - security_compliance.md
    - data_privacy_guidelines.md

workflows:
  - apns_token_renewal
  - mdm_device_enrollment
  - email_migration

contacts:
  - role: Superintendent
  - role: Department Heads
  - vendors: [security vendor, network vendor, etc.]

compliance:
  - CJIS (police systems)
  - FERPA (student data)
  - E-Rate (federal funding)
```

**What Claude should remember:**
- Org structure and politics
- Recurring tasks and their timing
- Past decisions and why
- Vendor relationships
- Compliance gotchas

---

### 2. Projects (Active Building)

Things being actively developed with a defined end state.

**Characteristics:**
- Has a goal/vision
- Code or artifacts being created
- Progress can be measured
- Will eventually be "done" (or abandoned)
- Needs technical context

**Example: Game Project**
```yaml
type: project
name: my-game
description: Mobile strategy game

repo: github.com/myuser/my-game
platform: Unity 6.1
status: active

current_focus: Core gameplay loop
architecture: Service Pattern, Definition-Based Content

decisions:
  - 2025-01-12: Adopted Definition System
  - 2025-07-22: Architecture Cleanup

blocked_on: []
next_milestone: Basic tactical gameplay loop
```

**What Claude should remember:**
- Architecture decisions and why
- Current focus and blockers
- Code patterns and conventions
- What's been tried and failed

---

### 3. Interests (Ongoing Curiosities)

Topics for exploration without deliverables or deadlines.

**Characteristics:**
- No end state
- Explored for intrinsic value
- May connect to other interests
- Depth varies by engagement
- No external accountability

**Example: Quantum Mechanics**
```yaml
type: interest
name: quantum
topics:
  - Bell's theorem
  - Wave function collapse
  - Determinism vs free will
  - Consciousness and measurement

connections:
  - philosophy (free will)
  - complexity theory (emergence)
  - etymology (word origins in physics)

depth: intermediate
last_explored: 2025-12-15
```

**What Claude should remember:**
- What fascinates the user about this
- Connections to other interests
- Level of technical depth appropriate
- Past discussions and insights

---

### 4. Skills (Procedural Knowledge)

How to do things - reusable procedures and capabilities.

**Characteristics:**
- Step-by-step knowledge
- Tools and commands involved
- Can be verified as working
- Improves with use
- Transferable across contexts

**Example: GitHub Authentication**
```yaml
type: skill
name: github_auth
purpose: Authenticate Git operations with GitHub

procedure:
  1. Retrieve PAT from 1Password
     tool: op_auth
     item: "GitHub PAT"
     field: credential
  2. Set environment variable or use in command
     git push https://{PAT}@github.com/...
  3. Or configure credential helper

tools_needed:
  - 1password (op_auth)
  - git

verified: true
last_used: 2026-01-28
notes: "PAT has repo and workflow scopes"
```

**What Claude should remember:**
- The exact steps that work
- Which tools are needed
- Common failure modes
- Improvements discovered

---

### 5. Memories (Shared History)

Personal context and relationship history.

**Characteristics:**
- Accumulated over time
- Not structured like other types
- Includes preferences, patterns, history
- Emotional/relational content
- Shapes how other contexts are approached

**Examples:**
- The user prefers tracer bullets over big upfront design
- Professional background shapes how they think about systems
- Values being treated as a competent partner
- Enjoys tangential exploration
- Trust built through collaboration

**What Claude should remember:**
- Communication preferences
- Working style
- Personal context that affects work
- Shared jokes and references
- Trust built through collaboration

---

## How Context Types Interact

```
                    ┌─────────────┐
                    │  MEMORIES   │
                    │ (who we are │
                    │  together)  │
                    └──────┬──────┘
                           │
                           │ shapes all interactions
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│     JOBS      │  │   PROJECTS    │  │   INTERESTS   │
│ (professional │  │   (building   │  │  (exploring   │
│   contexts)   │  │    things)    │  │    ideas)     │
└───────┬───────┘  └───────┬───────┘  └───────┬───────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                           │ all use
                           │
                    ┌──────▼──────┐
                    │   SKILLS    │
                    │ (how to do  │
                    │   things)   │
                    └─────────────┘
```

**Example flow:**
1. Working on a **job workspace** (job)
2. Need to push changes to a repo
3. Recall **github_auth** skill
4. Execute the procedure
5. **Memory** notes: "User prefers SSH over HTTPS when possible"
6. Next time, skill could be updated

---

## Storage in Somnia Graph

Each context type maps to node types:

```sql
-- Job context
INSERT INTO nodes (type, content, metadata) VALUES (
    'job',
    'Director of Technology - Acme School District',
    '{"role": "Director of Technology", "org": "Acme School District"}'
);

-- Project context  
INSERT INTO nodes (type, content, metadata) VALUES (
    'project',
    'My Game - Mobile strategy game',
    '{"repo": "github.com/myuser/my-game", "status": "active"}'
);

-- Interest
INSERT INTO nodes (type, content, metadata) VALUES (
    'interest',
    'Quantum mechanics and philosophy of physics',
    '{"depth": "intermediate", "topics": ["Bell theorem", "determinism"]}'
);

-- Skill (procedure)
INSERT INTO nodes (type, content, metadata) VALUES (
    'procedure',
    'GitHub authentication via 1Password PAT',
    '{"tools": ["1password", "git"], "verified": true}'
);

-- Memory
INSERT INTO nodes (type, content, metadata) VALUES (
    'memory',
    'User prefers tracer bullet approach over big design upfront',
    '{"category": "working_style", "confidence": "high"}'
);
```

Edges connect them:
- Job → uses → Skill
- Project → part_of → Job (sometimes)
- Interest → reminds_of → Interest
- Memory → shapes → everything

---

## Migration from Flat Domains

| Old Domain | Context Type |
|---------------------|---------------------|
| work | Job |
| my-game | Project |
| somnia | Project |
| quantum | Interest |
| gaming | Interest |
| entertainment | Interest |
| maker | Interest + Skills |
| health | Personal (new type?) |
| politics | Interest |
| projects | Meta (tracks projects) |
| homelab | Skills + Project |
| workstation | Skills |

Some domains are cleanly one type. Others (like "maker") blend interest and skill. That's fine - a node can have multiple edges connecting it to different context types.

---

*First documented: 2026-02-01*
