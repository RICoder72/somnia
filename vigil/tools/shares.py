"""
Share publishing tools — create UUID-gated share links, revoke them, list them.

Files live in /data/publish/{subfolder}/{filename}.
Manifest: /data/publish/_shares.json

Tools:
  publish_file        — copy a local file into publish/, register share, return URL
  publish_md_as_pdf   — run MD→PDF pipeline in Forge, publish, return URL
  revoke_share        — remove a share entry (file is NOT deleted)
  list_shares         — table of active + expired shares
"""

import json
import mimetypes
import shutil
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastmcp import FastMCP

from config import DATA_ROOT, PUBLISH_DIR, PUBLIC_SHARE_BASE_URL
from core.paths import validate

SHARES_FILE = PUBLISH_DIR / "_shares.json"

# ── Manifest helpers ───────────────────────────────────────────────────────

def _load_shares() -> dict:
    if not SHARES_FILE.exists():
        return {"version": 1, "shares": {}}
    try:
        data = json.loads(SHARES_FILE.read_text())
        data.setdefault("shares", {})
        return data
    except Exception:
        return {"version": 1, "shares": {}}


def _save_shares(data: dict) -> None:
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    SHARES_FILE.write_text(json.dumps(data, indent=2, default=str))


def _make_entry(
    file_path: Path,
    rel_path: str,
    expiry_days: int,
    label: Optional[str] = None,
) -> dict:
    uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    mime, _ = mimetypes.guess_type(str(file_path))
    return {
        "uuid": uid,
        "path": rel_path,                              # relative to PUBLISH_DIR
        "filename": file_path.name,
        "content_type": mime or "application/octet-stream",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=expiry_days)).isoformat(),
        "label": label or file_path.name,
    }


def _share_url(uid: str) -> str:
    return f"{PUBLIC_SHARE_BASE_URL}/{uid}"


# ── Tool registration ──────────────────────────────────────────────────────

def register(mcp: FastMCP):

    @mcp.tool()
    def publish_file(
        src: str,
        subfolder: str,
        filename: str = None,
        expiry_days: int = 30,
    ) -> str:
        """Publish a file to the share system. Returns the full public URL immediately.

        Copies the source file into /data/publish/{subfolder}/, registers a UUID share
        entry with the specified expiry, and returns the live public link.

        Args:
            src:         Source file path (absolute, or relative to /data)
            subfolder:   Destination folder under /data/publish/ — use a short slug
                         matching the project (e.g. "dcat", "burrillville")
            filename:    Override output filename (default: source filename)
            expiry_days: Days until the share expires. Common values: 30, 60, 90.
        """
        source = validate(src)
        if not source.exists():
            return f"❌ Source not found: {src}"
        if not source.is_file():
            return f"❌ Not a file: {src}"

        dest_filename = filename or source.name
        dest_dir = PUBLISH_DIR / subfolder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / dest_filename

        shutil.copy2(source, dest)

        rel_path = f"{subfolder}/{dest_filename}"
        entry = _make_entry(dest, rel_path, expiry_days)

        manifest = _load_shares()
        manifest["shares"][entry["uuid"]] = entry
        _save_shares(manifest)

        url = _share_url(entry["uuid"])
        expires = datetime.fromisoformat(entry["expires_at"]).strftime("%Y-%m-%d")
        size_kb = dest.stat().st_size // 1024
        return (
            f"✅ Published: {rel_path} ({size_kb} KB)\n"
            f"🔗 {url}\n"
            f"⏳ Expires: {expires} ({expiry_days}d)"
        )


    @mcp.tool()
    def publish_md_as_pdf(
        src_md: str,
        subfolder: str,
        filename: str,
        expiry_days: int = 30,
    ) -> str:
        """Convert a Markdown file to PDF using the Forge pipeline, then publish it.

        Runs preprocess_md.py (Unicode→LaTeX subscript converter) + pandoc/XeLaTeX
        inside the Forge container, writes the PDF directly to /data/publish/{subfolder}/,
        registers a UUID share entry, and returns the public URL — all in one step.

        Prerequisites:
          - /workspace/preprocess_md.py and /workspace/dcat-preamble.tex in Forge
          - workspaces mounted at /data/workspaces in Forge (ro)
          - publish-data volume mounted at /data/publish in Forge (rw)

        Args:
            src_md:      Source Markdown path. Absolute, or relative to /data/workspaces.
                         Example: "dcat/DCAT.md"  →  /data/workspaces/dcat/DCAT.md
            subfolder:   Destination folder under /data/publish/ (e.g. "dcat")
            filename:    Output PDF filename (e.g. "DCAT.pdf")
            expiry_days: Days until the share expires (default 30)
        """
        # Resolve source path
        md_path = src_md if src_md.startswith("/") else f"/data/workspaces/{src_md}"

        # Ensure dest dir exists (Forge will also mkdir -p, but belt+suspenders)
        dest_dir = PUBLISH_DIR / subfolder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_pdf = dest_dir / filename
        out_path = f"/data/publish/{subfolder}/{filename}"

        # Build the pipeline command executed inside Forge's container
        command = (
            f"set -e && "
            f"python /workspace/preprocess_md.py '{md_path}' > /tmp/_pub_preprocess.md && "
            f"pandoc /tmp/_pub_preprocess.md "
            f"--pdf-engine=xelatex "
            f"--include-in-header=/workspace/dcat-preamble.tex "
            f"-o '{out_path}' && "
            f"echo OK"
        )

        try:
            resp = httpx.post(
                "http://forge:8003/forge/internal/run",
                json={"command": command, "workdir": "/workspace", "timeout": 180},
                timeout=200,
            )
            resp.raise_for_status()
            result = resp.json()
        except httpx.HTTPStatusError as e:
            return f"❌ Forge HTTP {e.response.status_code}: {e.response.text[:300]}"
        except httpx.ConnectError:
            return "❌ Cannot reach Forge (http://forge:8003) — is it running?"
        except Exception as e:
            return f"❌ Forge call failed: {e}"

        output = result.get("output", "").strip()
        ok = result.get("ok", False)

        if not ok or not dest_pdf.exists():
            return (
                f"❌ PDF generation failed.\n"
                f"Command: {command[:200]}\n"
                f"Forge output:\n{output[:500]}"
            )

        # Register the share
        rel_path = f"{subfolder}/{filename}"
        entry = _make_entry(dest_pdf, rel_path, expiry_days)

        manifest = _load_shares()
        manifest["shares"][entry["uuid"]] = entry
        _save_shares(manifest)

        url = _share_url(entry["uuid"])
        expires = datetime.fromisoformat(entry["expires_at"]).strftime("%Y-%m-%d")
        size_kb = dest_pdf.stat().st_size // 1024

        return (
            f"✅ PDF published: {rel_path} ({size_kb} KB)\n"
            f"🔗 {url}\n"
            f"⏳ Expires: {expires} ({expiry_days}d)\n"
            f"Forge: {output[:120]}"
        )


    @mcp.tool()
    def revoke_share(share_uuid: str) -> str:
        """Revoke a share link. The underlying file is NOT deleted — only the share
        entry is removed from the manifest. The UUID becomes a dead link immediately.

        Args:
            share_uuid: UUID of the share to revoke (from list_shares or a previous
                        publish_file / publish_md_as_pdf call)
        """
        manifest = _load_shares()
        shares = manifest.get("shares", {})

        if share_uuid not in shares:
            return f"❌ Share not found: {share_uuid}"

        entry = shares.pop(share_uuid)
        _save_shares(manifest)

        url = _share_url(share_uuid)
        return (
            f"✅ Revoked: {entry['filename']} ({entry['path']})\n"
            f"🔗 {url}  ← now dead"
        )


    @mcp.tool()
    def list_shares() -> str:
        """List all share entries — active and expired — with their public URLs.

        Shows UUID, status, expiry date, file path, and days remaining for active shares.
        Active share URLs are listed at the bottom for easy copying.
        """
        manifest = _load_shares()
        shares = manifest.get("shares", {})

        if not shares:
            return "No shares registered yet."

        now = datetime.now(timezone.utc)

        active, expired = [], []
        for uid, entry in shares.items():
            try:
                exp = datetime.fromisoformat(entry["expires_at"])
            except Exception:
                exp = now
            (expired if exp < now else active).append((uid, entry, exp))

        lines = [f"{'STATUS':11} {'EXPIRES':12} {'FILE':<40} UUID", "─" * 100]

        for uid, entry, exp in sorted(active, key=lambda x: x[2]):
            days_left = (exp - now).days
            lines.append(
                f"{'✅ active':11} {exp.strftime('%Y-%m-%d'):12} "
                f"{entry['path']:<40} {uid}  ({days_left}d left)"
            )

        for uid, entry, exp in sorted(expired, key=lambda x: x[2]):
            lines.append(
                f"{'⌛ expired':11} {exp.strftime('%Y-%m-%d'):12} "
                f"{entry['path']:<40} {uid}"
            )

        lines.append("")
        lines.append(f"Total: {len(active)} active, {len(expired)} expired")

        if active:
            lines.append("")
            lines.append("Active URLs:")
            for uid, entry, _ in sorted(active, key=lambda x: x[2]):
                lines.append(f"  🔗 {_share_url(uid)}")
                lines.append(f"     → {entry['filename']}  ({entry['label']})")

        return "\n".join(lines)
