"""
sticky_notes.py — Somnia sticky notes system

A server-side markdown file maintained autonomously by Quies.
Written incrementally during harvest, updated after dream cycles.
Read at session start to give new Claude instances working-memory
context — what was just done, what's been covered, what to pick up.

Two-layer architecture:
  harvest_state.json  — machine enforcement (UUID set, never re-mine)
  sticky-notes.md     — human-readable audit trail Claude can reason about

The notes file is ephemeral by design. It's the whiteboard, not the notebook.
The graph is the notebook.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from filelock import FileLock

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("SOMNIA_DATA_DIR", "/data/somnia"))
NOTES_FILE = DATA_DIR / "sticky-notes.md"
LOCK_FILE = DATA_DIR / "sticky-notes.lock"

# Rolling ledger: keep this many entries max
MAX_LEDGER_ENTRIES = 60
# How many ledger entries to show in session output
SESSION_LEDGER_PREVIEW = 10


# ── Parsing ────────────────────────────────────────────────────────────────

def _parse_notes():
    """
    Parse the current sticky notes file into structured sections.
    Returns a dict with keys: header, open_threads, last_dream_focus,
    state_flags, for_next_claude, ledger (list of strings).
    """
    if not NOTES_FILE.exists():
        return _empty_notes()

    try:
        content = NOTES_FILE.read_text()
    except Exception:
        return _empty_notes()

    sections = {
        "open_threads": "",
        "last_dream_focus": "",
        "state_flags": "",
        "for_next_claude": "",
        "ledger": []
    }

    current_section = None
    ledger_lines = []
    in_ledger = False

    for line in content.splitlines():
        if line.startswith("## Processed Ledger"):
            in_ledger = True
            current_section = None
            continue
        elif line.startswith("## Open Threads"):
            in_ledger = False
            current_section = "open_threads"
            continue
        elif line.startswith("## Last Dream Focus"):
            in_ledger = False
            current_section = "last_dream_focus"
            continue
        elif line.startswith("## State"):
            in_ledger = False
            current_section = "state_flags"
            continue
        elif line.startswith("## For Next Claude"):
            in_ledger = False
            current_section = "for_next_claude"
            continue
        elif line.startswith("## "):
            in_ledger = False
            current_section = None
            continue

        if in_ledger:
            if line.strip():
                ledger_lines.append(line)
        elif current_section:
            sections[current_section] += line + "\n"

    sections["ledger"] = ledger_lines
    return sections


def _empty_notes():
    return {
        "open_threads": "",
        "last_dream_focus": "",
        "state_flags": "",
        "for_next_claude": "",
        "ledger": []
    }


# ── Writing ────────────────────────────────────────────────────────────────

def _render_notes(sections):
    """Render sections dict back to markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Sticky Notes — Updated {now}",
        "",
        "## Processed Ledger",
        *(sections["ledger"][-MAX_LEDGER_ENTRIES:]),
        "",
        "## Open Threads",
        sections["open_threads"].strip(),
        "",
        "## Last Dream Focus",
        sections["last_dream_focus"].strip(),
        "",
        "## State Flags",
        sections["state_flags"].strip(),
        "",
        "## For Next Claude",
        sections["for_next_claude"].strip(),
        "",
    ]
    return "\n".join(lines)


def _write_notes(sections):
    """Write notes atomically using a file lock."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(str(LOCK_FILE), timeout=10)
        with lock:
            NOTES_FILE.write_text(_render_notes(sections))
    except Exception as e:
        logger.error(f"sticky_notes: write failed: {e}")


# ── Public API ─────────────────────────────────────────────────────────────

def append_ledger_entry(conv_name, conv_uuid, obs_count, status="ok"):
    """
    Append one entry to the processed ledger.
    Called immediately after each conversation is mined.

    status: "ok" (obs found), "empty" (mined, nothing worth keeping),
            "error" (mining failed), "skipped" (already processed)
    """
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_uuid = conv_uuid[:8] if conv_uuid else "????????"
    name_trunc = conv_name[:50] if conv_name else "Untitled"

    if status == "ok":
        icon = "✓"
        detail = f"{obs_count} obs extracted"
    elif status == "empty":
        icon = "⊘"
        detail = "nothing worth keeping"
    elif status == "error":
        icon = "✗"
        detail = "mining failed"
    else:
        icon = "↷"
        detail = "skipped (already processed)"

    entry = f"{icon} {date} | \"{name_trunc}\" [{short_uuid}] | {detail}"

    try:
        lock = FileLock(str(LOCK_FILE), timeout=10)
        with lock:
            sections = _parse_notes()
            sections["ledger"].append(entry)
            # Trim to max entries
            if len(sections["ledger"]) > MAX_LEDGER_ENTRIES:
                sections["ledger"] = sections["ledger"][-MAX_LEDGER_ENTRIES:]
            NOTES_FILE.write_text(_render_notes(sections))
        logger.debug(f"sticky_notes: ledger entry added: {entry}")
    except Exception as e:
        logger.error(f"sticky_notes: append_ledger_entry failed: {e}")


def update_state_flags(harvest_status=None, inbox_depth=None,
                       last_harvest_summary=None, nudge=None):
    """
    Update the State Flags section. Called after a harvest run completes.
    """
    try:
        lock = FileLock(str(LOCK_FILE), timeout=10)
        with lock:
            sections = _parse_notes()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            flags = []
            if harvest_status:
                flags.append(f"- Last harvest: {now} — {harvest_status}")
            if inbox_depth is not None:
                flags.append(f"- Inbox depth: {inbox_depth} items")
            if last_harvest_summary:
                flags.append(f"- Last harvest summary: {last_harvest_summary}")
            if nudge:
                flags.append(f"- ⚠️ Nudge: {nudge}")

            if flags:
                # Preserve existing non-harvest flags (keep "Nudge" lines only if fresh)
                existing = sections["state_flags"].strip()
                existing_lines = [
                    l for l in existing.splitlines()
                    if l.strip() and not l.startswith("- Last harvest")
                    and not l.startswith("- Inbox depth")
                    and not l.startswith("- Last harvest summary")
                    and (not l.startswith("- ⚠️") or nudge)
                ]
                sections["state_flags"] = "\n".join(existing_lines + flags)
                NOTES_FILE.write_text(_render_notes(sections))
    except Exception as e:
        logger.error(f"sticky_notes: update_state_flags failed: {e}")


def update_dream_focus(mode, summary):
    """
    Update the Last Dream Focus section after a dream cycle completes.
    Called by the daemon after run_consolidation().
    """
    try:
        lock = FileLock(str(LOCK_FILE), timeout=10)
        with lock:
            sections = _parse_notes()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            sections["last_dream_focus"] = (
                f"Mode: {mode} at {now}\n{summary[:500]}"
            )
            NOTES_FILE.write_text(_render_notes(sections))
    except Exception as e:
        logger.error(f"sticky_notes: update_dream_focus failed: {e}")


def update_open_threads(threads_text):
    """
    Replace the Open Threads section.
    Called by the daemon after rumination/solo-work when new threads emerge.
    """
    try:
        lock = FileLock(str(LOCK_FILE), timeout=10)
        with lock:
            sections = _parse_notes()
            sections["open_threads"] = threads_text.strip()
            NOTES_FILE.write_text(_render_notes(sections))
    except Exception as e:
        logger.error(f"sticky_notes: update_open_threads failed: {e}")


def update_for_next_claude(message):
    """
    Update the For Next Claude section. Freeform. Prepends to existing content
    with a timestamp so the most recent message is always on top.
    """
    try:
        lock = FileLock(str(LOCK_FILE), timeout=10)
        with lock:
            sections = _parse_notes()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            new_entry = f"[{now}] {message.strip()}"
            existing = sections["for_next_claude"].strip()
            # Keep last 3 entries
            existing_entries = [e for e in existing.split("\n\n") if e.strip()]
            combined = [new_entry] + existing_entries[:2]
            sections["for_next_claude"] = "\n\n".join(combined)
            NOTES_FILE.write_text(_render_notes(sections))
    except Exception as e:
        logger.error(f"sticky_notes: update_for_next_claude failed: {e}")


def read_for_session():
    """
    Return a formatted string for inclusion in somnia_session output.
    Shows the last N ledger entries and key state sections.
    """
    if not NOTES_FILE.exists():
        return None

    try:
        sections = _parse_notes()
        lines = ["Sticky Notes (from last active session):"]

        # State flags
        sf = sections["state_flags"].strip()
        if sf:
            lines.append("")
            for l in sf.splitlines():
                if l.strip():
                    lines.append(f"  {l.strip()}")

        # Recent ledger
        ledger = sections["ledger"]
        if ledger:
            lines.append("")
            lines.append(f"  Recent processed conversations (last {SESSION_LEDGER_PREVIEW}):")
            for entry in ledger[-SESSION_LEDGER_PREVIEW:]:
                lines.append(f"    {entry}")

        # Open threads
        ot = sections["open_threads"].strip()
        if ot:
            lines.append("")
            lines.append("  Open threads:")
            for l in ot.splitlines()[:5]:
                if l.strip():
                    lines.append(f"    {l.strip()}")

        # For next claude
        fnc = sections["for_next_claude"].strip()
        if fnc:
            lines.append("")
            lines.append("  For next Claude:")
            first_entry = fnc.split("\n\n")[0]
            for l in first_entry.splitlines()[:3]:
                if l.strip():
                    lines.append(f"    {l.strip()}")

        return "\n".join(lines) if len(lines) > 1 else None

    except Exception as e:
        logger.error(f"sticky_notes: read_for_session failed: {e}")
        return None
