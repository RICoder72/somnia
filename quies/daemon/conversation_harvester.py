"""
conversation_harvester.py — Claude.ai conversation harvester for Quies

Fetches recent conversations from the Claude.ai internal API, mines them
for observations worth remembering, and drops them into the Somnia STM inbox.

Two-layer state tracking:
  harvest_state.json  — machine enforcement (UUID set, authoritative, never re-mine)
  sticky-notes.md     — human-readable ledger, one entry per conversation, written
                        incrementally as each conversation completes so partial runs
                        are fully recoverable.

Architecture:
  - Runs as Phase 5 in the dream_scheduler loop (~daily)
  - Session key stored in 1Password as "Claude AI Session Key"
  - Uses Haiku for mining (cheap, fast)
  - Fails gracefully on 401 with a nudge in sticky notes
"""

import json
import logging
import os
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
APP_DIR = Path(os.environ.get("SOMNIA_APP_DIR", "/app"))
DATA_DIR = Path(os.environ.get("SOMNIA_DATA_DIR", "/data/somnia"))
STATE_FILE = DATA_DIR / "harvest_state.json"

# ── Claude.ai API ──────────────────────────────────────────────────────────
CLAUDE_AI_BASE = "https://claude.ai"
CLAUDE_ORG_UUID = "81643bb2-a15a-4306-bb99-0dd8847b4e83"

# ── Config ─────────────────────────────────────────────────────────────────
MAX_CONVERSATIONS_PER_RUN = 20
MAX_MESSAGES_TO_MINE = 80
HARVESTER_COOLDOWN_HOURS = 20


# ── State management ───────────────────────────────────────────────────────

def load_harvest_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_harvest_at": None,
        "processed_uuids": [],
        "last_error": None,
        "total_observations": 0,
        "total_conversations_processed": 0
    }


def save_harvest_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def mark_conversation_done(state, conv_uuid):
    """
    Mark a single conversation as processed in the machine state.
    Called immediately after mining completes (or fails) for that conversation.
    Saves immediately so partial run crashes don't lose progress.
    """
    if conv_uuid not in state["processed_uuids"]:
        state["processed_uuids"].append(conv_uuid)
    # Keep bounded
    state["processed_uuids"] = state["processed_uuids"][-500:]
    save_harvest_state(state)


# ── Auth ───────────────────────────────────────────────────────────────────

def get_session_key():
    """Returns (key, None) on success, (None, error_msg) on failure."""
    env_key = os.environ.get("CLAUDE_AI_SESSION_KEY")
    if env_key:
        return env_key, None

    try:
        result = subprocess.run(
            ["op", "item", "get", "Claude AI Session Key",
             "--vault", "Key Vault",
             "--fields", "password", "--format", "json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            key = json.loads(result.stdout).get("value", "").strip()
            if key:
                return key, None
            return None, "1Password returned empty sessionKey"
        return None, f"1Password lookup failed: {result.stderr.strip()[:200]}"
    except subprocess.TimeoutExpired:
        return None, "1Password lookup timed out"
    except Exception as e:
        return None, f"Auth error: {e}"


# ── Claude.ai API calls ────────────────────────────────────────────────────

def _headers(session_key):
    # Headers matched to actual Claude.ai browser requests (2026-04-06).
    # anthropic-* headers required for conversation endpoints.
    return {
        "Cookie": f"sessionKey={session_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
        "Accept": "*/*",
        "content-type": "application/json",
        "Referer": "https://claude.ai/",
        "Origin": "https://claude.ai",
        "anthropic-anonymous-id": "claudeai.v1.a68e81fa-108d-4b3f-b062-6fcf4ba8a9f5",
        "anthropic-client-platform": "web_claude_ai",
        "anthropic-client-sha": "c7c35a812db05f5e0dc02c7d83a6ceea05b2fdc1",
        "anthropic-client-version": "1.0.0",
        "anthropic-device-id": "d7ddc069-74a6-4308-858e-494bc3b46804",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }


def fetch_conversations(session_key, limit=50):
    r = requests.get(
        f"{CLAUDE_AI_BASE}/api/organizations/{CLAUDE_ORG_UUID}/chat_conversations",
        headers=_headers(session_key),
        params={"limit": limit},
        timeout=30
    )
    r.raise_for_status()
    return r.json()


def fetch_conversation_messages(session_key, conv_uuid):
    r = requests.get(
        f"{CLAUDE_AI_BASE}/api/organizations/{CLAUDE_ORG_UUID}/chat_conversations/{conv_uuid}",
        headers=_headers(session_key),
        timeout=30
    )
    r.raise_for_status()
    return r.json()


# ── Text extraction ────────────────────────────────────────────────────────

def extract_conversation_text(conv_data, max_messages=MAX_MESSAGES_TO_MINE):
    import re
    messages = conv_data.get("chat_messages", [])
    name = conv_data.get("name", "Untitled")
    created = conv_data.get("created_at", "")[:10]

    lines = [f"# Conversation: {name} ({created})\n"]
    messages = sorted(messages, key=lambda m: m.get("index", 0))

    if len(messages) > max_messages:
        messages = messages[:max_messages]
        lines.append(f"[Truncated to first {max_messages} messages]\n")

    for msg in messages:
        sender = msg.get("sender", "unknown")
        text = msg.get("text", "") or ""
        if not isinstance(text, str):
            text = str(text)
        if sender == "assistant":
            text = re.sub(r'```\n.*?```', '[tool call]', text, flags=re.DOTALL)
            text = re.sub(r'(\[tool call\]\s*)+', '[tool calls] ', text)
        if text.strip():
            prefix = "Human" if sender == "human" else "Claude"
            lines.append(f"**{prefix}:** {text.strip()[:2000]}\n")

    return "\n".join(lines)


# ── Mining via Claude API ──────────────────────────────────────────────────

MINING_SYSTEM = """You are Somnia's conversation harvester — a specialized process that reads 
past conversations between Matthew Zanni and Claude, then extracts observations that are 
genuinely worth adding to Somnia's memory graph.

Matthew is the Director of IT for Burrillville (school district + town + police), 
54 years old, military intelligence background (NSA), deep interests in cellular automata, 
AI philosophy, consciousness, Star Trek, and software development. He built the Somnia system.

OUTPUT FORMAT: Return a JSON object with a single key "observations" containing an array of 
strings. Each string is a concise observation (1-3 sentences). Return ONLY the JSON, no preamble.

EXTRACT (be selective — quality over quantity):
- Intellectual positions Matthew formed or solidified (philosophical, technical, political)
- Patterns in how Matthew thinks or approaches problems  
- Named concepts, frameworks, or terms Matthew coined or adopted
- Decisions made about ongoing projects (BIT, Somnia, DCAT, PRIMORDIUM, etc.)
- Key facts about Matthew's life, relationships, or context
- Unresolved threads worth following up on
- Moments where Matthew changed his mind or was genuinely challenged

SKIP:
- Operational tool calls and infrastructure debugging minutiae
- Purely transactional exchanges (simple product recommendations, quick lookups)
- Anything already obviously in the graph

Return 0-8 observations. Return empty array if nothing is worth keeping."""


def mine_conversation(conv_text, anthropic_api_key):
    """Returns list of observation strings, or raises on error."""
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1000,
        "system": MINING_SYSTEM,
        "messages": [{
            "role": "user",
            "content": (
                "Extract memory-worthy observations from this conversation:\n\n"
                + conv_text[:12000]
            )
        }]
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": anthropic_api_key,
            "anthropic-version": "2023-06-01"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:500]
        raise Exception(f"HTTP {e.code}: {body}") from e

    text = data["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    return json.loads(text).get("observations", [])


# ── Inbox insertion ────────────────────────────────────────────────────────

def add_to_inbox(observations, conv_name, conv_uuid):
    """Insert observations into STM inbox. Returns count inserted."""
    from db import execute
    inserted = 0
    for obs in observations:
        if not obs or not obs.strip():
            continue
        content = (
            f"[harvested from '{conv_name}' {conv_uuid[:8]}] {obs.strip()}"
        )
        execute(
            "INSERT INTO inbox (content, domain, source_conversation, captured_at) "
            "VALUES (%s, %s, %s, NOW())",
            (content, "harvested", "conversation_harvester")
        )
        inserted += 1
    return inserted


# ── Eligibility ────────────────────────────────────────────────────────────

def should_harvest():
    state = load_harvest_state()
    last = state.get("last_harvest_at")

    if last is None:
        return True, "First harvest run"

    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - last_dt
        if elapsed < timedelta(hours=HARVESTER_COOLDOWN_HOURS):
            remaining = timedelta(hours=HARVESTER_COOLDOWN_HOURS) - elapsed
            h = int(remaining.total_seconds() // 3600)
            m = int((remaining.total_seconds() % 3600) // 60)
            return False, f"Harvest cooldown: {h}h {m}m remaining"
    except Exception:
        pass

    return True, "Cooldown elapsed"


# ── Main entry point ───────────────────────────────────────────────────────

def run_harvest(anthropic_api_key):
    """
    Main harvest cycle. Returns result dict.
    Writes ledger entries incrementally per conversation so partial
    runs are fully recoverable.
    """
    # Import sticky notes — non-fatal if unavailable
    try:
        from sticky_notes import (
            append_ledger_entry, update_state_flags, update_for_next_claude
        )
        has_sticky = True
    except ImportError:
        has_sticky = False
        logger.warning("Harvester: sticky_notes module not available")

    logger.info("Conversation harvester: starting run")
    state = load_harvest_state()

    result = {
        "status": "ok",
        "conversations_scanned": 0,
        "conversations_mined": 0,
        "observations_added": 0,
        "skipped_already_processed": 0,
        "error": None
    }

    # ── Step 1: Auth ──
    session_key, auth_err = get_session_key()
    if auth_err:
        msg = f"Session key unavailable: {auth_err}"
        logger.warning(f"Harvester: {msg}")
        state["last_error"] = msg
        save_harvest_state(state)
        if has_sticky:
            update_state_flags(
                harvest_status="⚠️ Auth failed",
                nudge="Session key unavailable — check 1Password"
            )
        result["status"] = "auth_error"
        result["error"] = msg
        return result

    # ── Step 2: Fetch conversation list ──
    try:
        conversations = fetch_conversations(
            session_key, limit=MAX_CONVERSATIONS_PER_RUN * 2
        )
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403):
            msg = ("Claude.ai sessionKey expired — "
                   "refresh from Claude.ai DevTools > Application > Cookies > sessionKey, "
                   "then update 1Password item 'Claude AI Session Key'")
            logger.warning(f"Harvester: {msg}")
            state["last_error"] = msg
            save_harvest_state(state)
            if has_sticky:
                update_state_flags(
                    harvest_status="⚠️ Session expired",
                    nudge="Refresh Claude AI Session Key in 1Password"
                )
                update_for_next_claude(
                    "The Claude.ai session key has expired. Matthew needs to refresh it "
                    "from DevTools and update 1Password before the next harvest can run."
                )
            result["status"] = "session_expired"
            result["error"] = msg
            return result
        msg = f"HTTP error fetching conversations: {e}"
        logger.error(f"Harvester: {msg}")
        result["status"] = "error"
        result["error"] = msg
        return result
    except Exception as e:
        msg = f"Error fetching conversations: {e}"
        logger.error(f"Harvester: {msg}")
        result["status"] = "error"
        result["error"] = msg
        return result

    # ── Step 3: Filter to new conversations ──
    processed_set = set(state.get("processed_uuids", []))
    last_harvest_at = state.get("last_harvest_at")

    new_conversations = []
    for conv in conversations:
        conv_uuid = conv.get("uuid", "")
        conv_name = conv.get("name", "Untitled")

        if conv_uuid in processed_set:
            result["skipped_already_processed"] += 1
            continue

        # Belt-and-suspenders: skip if updated before last harvest
        if last_harvest_at:
            updated = conv.get("updated_at", "")
            try:
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                last_dt = datetime.fromisoformat(last_harvest_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if updated_dt < last_dt:
                    result["skipped_already_processed"] += 1
                    # Still mark in sticky notes as skipped if not in UUID set
                    # (means it's "new" UUID but old update — edge case, just skip quietly)
                    continue
            except Exception:
                pass

        new_conversations.append(conv)
        if len(new_conversations) >= MAX_CONVERSATIONS_PER_RUN:
            break

    result["conversations_scanned"] = len(new_conversations)
    logger.info(f"Harvester: {len(new_conversations)} new conversations to mine")

    if not new_conversations:
        state["last_harvest_at"] = datetime.now(timezone.utc).isoformat()
        state["last_error"] = None
        save_harvest_state(state)
        if has_sticky:
            update_state_flags(
                harvest_status="✓ Complete — nothing new",
                inbox_depth=None
            )
        result["status"] = "ok_nothing_new"
        return result

    # ── Step 4: Mine each conversation — write ledger entry per conversation ──
    total_obs = 0

    for conv in new_conversations:
        conv_uuid = conv.get("uuid", "")
        conv_name = conv.get("name", "Untitled")

        # Fetch messages
        try:
            conv_data = fetch_conversation_messages(session_key, conv_uuid)
        except Exception as e:
            logger.warning(f"Harvester: failed to fetch '{conv_name}': {e}")
            # Mark done anyway to avoid infinite retry on broken conversations
            mark_conversation_done(state, conv_uuid)
            if has_sticky:
                append_ledger_entry(conv_name, conv_uuid, 0, status="error")
            continue

        conv_text = extract_conversation_text(conv_data)

        # Mine for observations
        try:
            observations = mine_conversation(conv_text, anthropic_api_key)
        except Exception as e:
            logger.warning(f"Harvester: mining failed for '{conv_name}': {e}")
            mark_conversation_done(state, conv_uuid)
            if has_sticky:
                append_ledger_entry(conv_name, conv_uuid, 0, status="error")
            continue

        # Insert into inbox
        obs_count = 0
        if observations:
            try:
                obs_count = add_to_inbox(observations, conv_name, conv_uuid)
                total_obs += obs_count
                result["conversations_mined"] += 1
                logger.info(
                    f"Harvester: '{conv_name}' → {obs_count} observations"
                )
            except Exception as e:
                logger.error(
                    f"Harvester: inbox insert failed for '{conv_name}': {e}"
                )

        # Mark done in machine state + write ledger entry
        mark_conversation_done(state, conv_uuid)

        if has_sticky:
            ledger_status = "ok" if obs_count > 0 else "empty"
            append_ledger_entry(conv_name, conv_uuid, obs_count, status=ledger_status)

    # ── Step 5: Finalise state ──
    state["last_harvest_at"] = datetime.now(timezone.utc).isoformat()
    state["last_error"] = None
    state["total_observations"] = (
        state.get("total_observations", 0) + total_obs
    )
    state["total_conversations_processed"] = (
        state.get("total_conversations_processed", 0) + result["conversations_mined"]
    )
    save_harvest_state(state)

    summary = (
        f"{result['conversations_mined']} conversations mined, "
        f"{total_obs} observations added to inbox"
    )
    if has_sticky:
        update_state_flags(
            harvest_status=f"✓ Complete — {summary}",
            last_harvest_summary=summary
        )

    result["observations_added"] = total_obs
    logger.info(f"Harvester: complete — {summary}")
    return result
