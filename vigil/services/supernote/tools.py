"""
Supernote MCP tools — 4 tools for bidirectional Supernote sync.

Workflow:
  1. supernote_pull(domain) — download from device, convert, archive, clean remote
  2. supernote_process(domain, stem, type) — return PNG pages for Claude vision
  3. supernote_push(domain) — upload outbox to device
  4. supernote_md2pdf(domain, source) — convert markdown to Supernote-optimised PDF
"""

import json
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastmcp import FastMCP

try:
    from fastmcp.utilities.types import Image
    from mcp.types import TextContent
    IMAGE_SUPPORT = True
except ImportError:
    IMAGE_SUPPORT = False
    TextContent = None

from .converter import (
    convert_note_to_png,
    convert_mark_to_merged_png,
    convert_md_to_pdf,
    PYMUPDF_AVAILABLE,
    REPORTLAB_AVAILABLE,
)

logger = logging.getLogger(__name__)

DOMAINS_ROOT = Path("/data/domains")


# ── Config & directory helpers ──────────────────────────────────────────────

def _plugin_path(domain: str) -> Path:
    return DOMAINS_ROOT / domain / "plugins" / "supernote"


def _config_path(domain: str) -> Path:
    return _plugin_path(domain) / "config.json"


def _load_config(domain: str) -> Optional[dict]:
    p = _config_path(domain)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        logger.error(f"Config load failed for {domain}: {e}")
        return None


def _save_config(domain: str, config: dict) -> None:
    pp = _plugin_path(domain)
    pp.mkdir(parents=True, exist_ok=True)
    _config_path(domain).write_text(json.dumps(config, indent=2))


def _ensure_dirs(domain: str) -> None:
    pp = _plugin_path(domain)
    for sub in ("inbox/notes", "inbox/annotations", "archive/notes", "archive/annotations", "outbox"):
        (pp / sub).mkdir(parents=True, exist_ok=True)


def _remote_paths(config: dict) -> tuple[str, str]:
    base = config.get("base_path", "").rstrip("/")
    sub = config["subfolder"]
    return f"{base}/Note/{sub}", f"{base}/Document/{sub}"


def _get_stems(directory: Path) -> dict[str, int]:
    stems = {}
    if directory.exists():
        for png in directory.glob("*.png"):
            parts = png.stem.rsplit("_", 1)
            stem = parts[0] if len(parts) == 2 and parts[1].isdigit() else png.stem
            stems[stem] = stems.get(stem, 0) + 1
    return stems


def _get_storage_manager():
    try:
        from services.storage.tools import storage_manager
        return storage_manager
    except Exception as e:
        logger.error(f"Storage manager unavailable: {e}")
        return None


# ── Pull helpers (internal) ─────────────────────────────────────────────────

async def _pull_notes(domain: str, config: dict) -> str:
    sm = _get_storage_manager()
    if not sm:
        return "Storage manager not available"

    pp = _plugin_path(domain)
    inbox = pp / "inbox" / "notes"
    archive = pp / "archive" / "notes"
    temp = pp / "temp"
    temp.mkdir(exist_ok=True)

    note_path, _ = _remote_paths(config)
    account = config["account"]

    try:
        files = await sm.list_files(account, note_path)
        note_files = [f for f in files if f.name.endswith(".note")]
        if not note_files:
            return "No notes on device"

        pulled, failed = [], []
        for f in note_files:
            stem = f.name.replace(".note", "")
            local = temp / f.name
            try:
                r = await sm.download(account, f"{note_path}/{f.name}", local)
                if "fail" in r.lower() or "error" in r.lower():
                    failed.append(f"{stem}: download failed"); continue
                conv = convert_note_to_png(local, inbox)
                if not conv["success"]:
                    failed.append(f"{stem}: {conv['error']}"); continue
                if not conv["pages"]:
                    failed.append(f"{stem}: no pages"); continue
                shutil.move(str(local), str(archive / f.name))
                await sm.delete(account, f"{note_path}/{f.name}")
                pulled.append(f"{stem} ({len(conv['pages'])} pages)")
            except Exception as e:
                failed.append(f"{stem}: {e}")

        shutil.rmtree(temp, ignore_errors=True)
        parts = []
        if pulled:
            parts.append(f"✅ Pulled {len(pulled)}: " + ", ".join(pulled))
        if failed:
            parts.append(f"❌ Failed {len(failed)}: " + ", ".join(failed))
        return "\n".join(parts) if parts else "Nothing to pull"
    except Exception as e:
        return f"Pull failed: {e}"


async def _pull_annotations(domain: str, config: dict) -> str:
    if not PYMUPDF_AVAILABLE:
        return "PyMuPDF not installed"

    sm = _get_storage_manager()
    if not sm:
        return "Storage manager not available"

    pp = _plugin_path(domain)
    inbox = pp / "inbox" / "annotations"
    archive = pp / "archive" / "annotations"
    temp = pp / "temp"
    temp.mkdir(exist_ok=True)

    _, doc_path = _remote_paths(config)
    account = config["account"]

    try:
        files = await sm.list_files(account, doc_path)
        mark_files = [f for f in files if f.name.endswith(".mark")]
        if not mark_files:
            return "No annotations on device"

        pulled, failed = [], []
        for f in mark_files:
            pdf_name = f.name[:-5]
            doc_stem = f.name[:-9]
            local_mark = temp / f.name
            local_pdf = temp / pdf_name
            try:
                r = await sm.download(account, f"{doc_path}/{f.name}", local_mark)
                if "fail" in r.lower() or "error" in r.lower():
                    failed.append(f"{doc_stem}: mark download failed"); continue
                r = await sm.download(account, f"{doc_path}/{pdf_name}", local_pdf)
                if "fail" in r.lower() or "error" in r.lower():
                    failed.append(f"{doc_stem}: PDF download failed"); continue
                merge = convert_mark_to_merged_png(local_mark, local_pdf, inbox)
                if not merge["success"]:
                    failed.append(f"{doc_stem}: {merge['error']}"); continue
                if not merge["pages"]:
                    failed.append(f"{doc_stem}: no pages"); continue
                shutil.move(str(local_mark), str(archive / f.name))
                await sm.delete(account, f"{doc_path}/{f.name}")
                local_pdf.unlink(missing_ok=True)
                pulled.append(f"{doc_stem} ({len(merge['pages'])} pages)")
            except Exception as e:
                failed.append(f"{doc_stem}: {e}")

        shutil.rmtree(temp, ignore_errors=True)
        parts = []
        if pulled:
            parts.append(f"✅ Pulled {len(pulled)}: " + ", ".join(pulled))
        if failed:
            parts.append(f"❌ Failed {len(failed)}: " + ", ".join(failed))
        return "\n".join(parts) if parts else "Nothing to pull"
    except Exception as e:
        return f"Pull failed: {e}"


# ── Tool registration ───────────────────────────────────────────────────────

def register(mcp: FastMCP):

    @mcp.tool()
    async def supernote_pull(domain: str) -> str:
        """Pull notes and annotations from Supernote device.

        Downloads .note/.mark files, converts to PNG, archives originals,
        deletes from remote. Run supernote_list_unprocessed next."""
        config = _load_config(domain)
        if not config:
            return f"❌ Not configured for '{domain}'"

        _ensure_dirs(domain)
        notes_result = await _pull_notes(domain, config)
        annot_result = await _pull_annotations(domain, config)
        config["last_pull"] = datetime.now().isoformat()
        _save_config(domain, config)

        return f"📥 Pull complete for {domain}\n\nNotes:\n{notes_result}\n\nAnnotations:\n{annot_result}"

    @mcp.tool()
    def supernote_list_unprocessed(domain: str) -> str:
        """List notes and annotations waiting to be processed."""
        config = _load_config(domain)
        if not config:
            return f"❌ Not configured for '{domain}'"

        pp = _plugin_path(domain)
        note_stems = _get_stems(pp / "inbox" / "notes")
        annot_stems = _get_stems(pp / "inbox" / "annotations")

        lines = [f"📋 Unprocessed in {domain}", "─" * 40]
        if note_stems:
            lines.append(f"\nNotes ({len(note_stems)}):")
            for stem, pgs in sorted(note_stems.items()):
                lines.append(f"  📝 {stem} ({pgs} pages)")
        else:
            lines.append("\nNotes: (none)")

        if annot_stems:
            lines.append(f"\nAnnotations ({len(annot_stems)}):")
            for stem, pgs in sorted(annot_stems.items()):
                lines.append(f"  ✏️ {stem} ({pgs} pages)")
        else:
            lines.append("\nAnnotations: (none)")

        return "\n".join(lines)

    @mcp.tool()
    def supernote_process(domain: str, stem: str, type: str = "note") -> list | str:
        """Process a note or annotation via vision — returns PNG pages as images.

        Args:
            domain: Domain name
            stem: File stem (from supernote_list_unprocessed)
            type: "note" or "annotation" """
        if not IMAGE_SUPPORT:
            return "❌ Image support not available"
        config = _load_config(domain)
        if not config:
            return f"❌ Not configured"

        subdir = "notes" if type == "note" else "annotations"
        inbox = _plugin_path(domain) / "inbox" / subdir
        pages = sorted(inbox.glob(f"{stem}_*.png"))
        if not pages:
            return f"❌ '{stem}' not found in {type}s inbox"

        label = "📝 Note" if type == "note" else "✏️ Annotation"
        result = [TextContent(type="text", text=f"{label}: {stem} ({len(pages)} pages)\n")]
        for i, p in enumerate(pages):
            result.append(TextContent(type="text", text=f"\nPage {i + 1}:"))
            result.append(Image(path=p).to_image_content())

        # Auto-archive after processing
        archive = _plugin_path(domain) / "archive" / subdir
        for p in pages:
            shutil.move(str(p), str(archive / p.name))
        result.append(TextContent(
            type="text",
            text=f"\n✅ Auto-archived {len(pages)} pages.",
        ))
        return result

    @mcp.tool()
    async def supernote_push(domain: str) -> str:
        """Push documents from outbox to Supernote device."""
        config = _load_config(domain)
        if not config:
            return "❌ Not configured"

        sm = _get_storage_manager()
        if not sm:
            return "❌ Storage manager not available"

        outbox = _plugin_path(domain) / "outbox"
        if not outbox.exists():
            return "📂 Outbox empty"

        files = [f for f in outbox.iterdir() if f.is_file()]
        if not files:
            return "📂 Outbox empty"

        _, doc_path = _remote_paths(config)
        uploaded, failed = [], []
        for local_file in files:
            try:
                r = await sm.upload(config["account"], local_file, f"{doc_path}/{local_file.name}")
                if "fail" not in r.lower() and "error" not in r.lower():
                    uploaded.append(local_file.name)
                    local_file.unlink()
                else:
                    failed.append(local_file.name)
            except Exception as e:
                failed.append(f"{local_file.name}: {e}")

        parts = [f"📤 Push for {domain}:"]
        if uploaded:
            parts.append(f"✅ Uploaded: {', '.join(uploaded)}")
        if failed:
            parts.append(f"❌ Failed: {', '.join(failed)}")
        return "\n".join(parts)

    @mcp.tool()
    def supernote_md2pdf(domain: str, source: str, to_outbox: bool = True) -> str:
        """Convert markdown to Supernote-optimised PDF.

        Args:
            domain: Domain name
            source: Markdown file path relative to domain root
            to_outbox: Copy PDF to outbox for pushing (default: True)"""
        if not REPORTLAB_AVAILABLE:
            return "❌ reportlab not installed"

        domain_path = DOMAINS_ROOT / domain
        md_path = domain_path / source
        if not md_path.exists():
            return f"❌ File not found: {source}"
        if md_path.suffix.lower() != ".md":
            return f"❌ Not markdown: {source}"

        pdf_path = md_path.with_suffix(".pdf")
        try:
            convert_md_to_pdf(md_path, pdf_path)
            result = f"✅ Created: {pdf_path.name}"
            if to_outbox:
                outbox = _plugin_path(domain) / "outbox"
                outbox.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pdf_path, outbox / pdf_path.name)
                result += " (copied to outbox)"
            return result
        except Exception as e:
            return f"❌ Failed: {e}"

    logger.info("✅ Registered 5 supernote tools")
