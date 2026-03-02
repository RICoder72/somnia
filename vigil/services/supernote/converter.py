"""
Supernote file converters — .note→PNG, .mark→merged PNG, markdown→PDF.

Isolated from tool registration for clarity. Heavy dependencies
(PyMuPDF, reportlab) are optional and detected at import time.
"""

import re
import subprocess
import shutil
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable,
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


def convert_note_to_png(note_path: Path, output_dir: Path) -> dict:
    """Convert .note file to PNG pages via supernote-tool CLI."""
    try:
        output_file = output_dir / f"{note_path.stem}.png"
        result = subprocess.run(
            ["supernote-tool", "convert", "-t", "png", "-a", str(note_path), str(output_file)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            pages = sorted(output_dir.glob(f"{note_path.stem}_*.png"))
            return {"success": True, "pages": pages}
        return {"success": False, "error": result.stderr}
    except Exception as e:
        return {"success": False, "error": str(e)}


def convert_mark_to_merged_png(mark_path: Path, pdf_path: Path, output_dir: Path) -> dict:
    """Convert .mark annotation overlaid on PDF → merged PNG pages."""
    if not PYMUPDF_AVAILABLE:
        return {"success": False, "error": "PyMuPDF not installed"}

    doc_stem = mark_path.stem.replace(".pdf", "")
    try:
        # Convert .mark to transparent PNGs
        mark_png_base = output_dir / f"{doc_stem}_mark_temp.png"
        result = subprocess.run(
            ["supernote-tool", "convert", "-t", "png", "-a", "--exclude-background",
             str(mark_path), str(mark_png_base)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return {"success": False, "error": f"supernote-tool: {result.stderr}"}

        doc = fitz.open(pdf_path)
        merged_pages = []
        for i, page in enumerate(doc):
            mark_png = output_dir / f"{doc_stem}_mark_temp_{i}.png"
            if mark_png.exists():
                page.insert_image(page.rect, filename=str(mark_png), overlay=True)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            merged_path = output_dir / f"{doc_stem}_{i}.png"
            pix.save(merged_path)
            merged_pages.append(merged_path)
            if mark_png.exists():
                mark_png.unlink()
        doc.close()
        return {"success": True, "pages": merged_pages}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Markdown → PDF (Supernote-optimised) ────────────────────────────────────


class _RuledSpace(Flowable):
    """Light gray ruled lines for handwriting on Supernote."""

    def __init__(self, width, num_lines=4, line_spacing=22):
        Flowable.__init__(self)
        self.width = width
        self.num_lines = num_lines
        self.line_spacing = line_spacing
        self.height = num_lines * line_spacing + 6

    def draw(self):
        self.canv.setStrokeColor(colors.HexColor("#cccccc"))
        self.canv.setLineWidth(0.5)
        for i in range(self.num_lines):
            y = self.height - 6 - (i * self.line_spacing)
            self.canv.line(0, y, self.width, y)


def _parse_markdown_table(lines: List[str]) -> List[List[str]]:
    rows = []
    for line in lines:
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line[1:-1].split("|")]
            if not all(re.match(r"^[-:]+$", c) for c in cells):
                rows.append(cells)
    return rows


def convert_md_to_pdf(md_path: Path, pdf_path: Path = None) -> Path:
    """Convert markdown to PDF optimised for Supernote display.

    Supports headings, bold, tables, checkboxes (nested), pagebreak/space
    directives, horizontal rules, and bullet lists.
    """
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("reportlab not installed")

    if pdf_path is None:
        pdf_path = md_path.with_suffix(".pdf")

    content = md_path.read_text()
    lines = content.split("\n")

    available_width = 7.0 * inch
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=letter,
        rightMargin=0.75 * inch, leftMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=20, alignment=TA_CENTER, spaceAfter=18)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=16, spaceBefore=16, spaceAfter=8)
    h3_style = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=14, spaceBefore=12, spaceAfter=6)
    h4_style = ParagraphStyle("H4", parent=styles["Heading4"], fontSize=12, spaceBefore=10, spaceAfter=4)
    normal_style = ParagraphStyle("N", parent=styles["Normal"], fontSize=12, spaceAfter=8, leading=16)

    cb_styles = [
        ParagraphStyle("CB0", parent=styles["Normal"], fontSize=12, spaceAfter=4, leading=18, leftIndent=0),
        ParagraphStyle("CB1", parent=styles["Normal"], fontSize=11, spaceAfter=3, leading=16, leftIndent=18),
        ParagraphStyle("CB2", parent=styles["Normal"], fontSize=11, spaceAfter=3, leading=16, leftIndent=36),
    ]
    bl_styles = [
        ParagraphStyle("BL0", parent=styles["Normal"], fontSize=12, spaceAfter=4, leading=18, leftIndent=0),
        ParagraphStyle("BL1", parent=styles["Normal"], fontSize=11, spaceAfter=3, leading=16, leftIndent=18),
    ]

    checkbox_re = re.compile(r"^(\s*)- \[([ xX])\]\s*(.*)")
    space_re = re.compile(r"^\s*<!--\s*space(?::(\d+))?\s*-->\s*$")
    pagebreak_re = re.compile(r"^\s*<!--\s*pagebreak\s*-->\s*$")
    bullet_re = re.compile(r"^(\s*)[-*]\s+(.*)")

    def _fmt(text):
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', text)
        return text

    story = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line or line.startswith(">"):
            i += 1
            continue

        if pagebreak_re.match(line):
            story.append(PageBreak())
            i += 1
            continue

        m = space_re.match(line)
        if m:
            n = int(m.group(1)) if m.group(1) else 4
            story.append(_RuledSpace(available_width, n))
            i += 1
            continue

        if line in ("---", "***", "___"):
            story.append(Spacer(1, 8))
            story.append(_RuledSpace(available_width, 1, 1))
            story.append(Spacer(1, 8))
            i += 1
            continue

        for prefix, style in [("#### ", h4_style), ("### ", h3_style), ("## ", h2_style), ("# ", title_style)]:
            if line.startswith(prefix):
                story.append(Paragraph(_fmt(line[len(prefix):]), style))
                i += 1
                break
        else:
            cb = checkbox_re.match(raw)
            if cb:
                indent = len(cb.group(1))
                checked = cb.group(2) in ("x", "X")
                box = "☑" if checked else "☐"
                lvl = min(indent // 2, 2)
                story.append(Paragraph(f"{box}  {_fmt(cb.group(3))}", cb_styles[lvl]))
                i += 1
                continue

            bl = bullet_re.match(raw)
            if bl:
                indent = len(bl.group(1))
                lvl = min(indent // 2, 1)
                story.append(Paragraph(f"•  {_fmt(bl.group(2))}", bl_styles[lvl]))
                i += 1
                continue

            if line.startswith("|"):
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i])
                    i += 1
                rows = _parse_markdown_table(table_lines)
                if rows:
                    ncols = len(rows[0])
                    t = Table(rows, colWidths=[available_width / ncols] * ncols)
                    t.setStyle(TableStyle([
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 10),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ]))
                    story.append(t)
                    story.append(Spacer(1, 10))
                continue

            story.append(Paragraph(_fmt(line), normal_style))
            i += 1

    doc.build(story)
    return pdf_path
