"""
Build a compliance-review PDF from a finished scan.

Layout (readability-first):
  - Title + counts.
  - "Most common fixes needed" — top 3 problems with % of ads.
  - One block per ad: a grey header band (severity + #id + product), then a
    ratio-preserved picture (~8 lines of 11pt tall) beside the feedback, where
    each finding is split into labelled PROBLEM / FIX / WHY rows.

Input (from the extension):
  client     : str
  analysis   : [ {id, pct, ads, total, problem, fix} ]   # already top-3
  results    : [ {ad_id, product, general, image_base64,
                  edits:[ {severity, part, revised, issue} ]} ]
"""
from __future__ import annotations

import io
import base64

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    Table,
    TableStyle,
    KeepTogether,
    HRFlowable,
)

FONT_SIZE = 11
LEADING = 14
IMG_H = 8 * LEADING  # "height of 8 11pt lines"

_GREY = colors.HexColor("#666666")
_LABELC = colors.HexColor("#9098a3")
_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=FONT_SIZE, leading=LEADING)
_FIX = ParagraphStyle("fix", parent=_BODY, fontName="Helvetica-Bold")
_WHY = ParagraphStyle("why", fontName="Helvetica", fontSize=10.5, leading=13, textColor=_GREY)
_SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=10.5, leading=14, textColor=_GREY)
_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=2)
_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13.5, leading=17, spaceBefore=4, spaceAfter=3)
_ADHEAD = ParagraphStyle(
    "adhead", fontName="Helvetica-Bold", fontSize=11, leading=15,
    backColor=colors.HexColor("#eef0f3"), borderPadding=(5, 7, 5, 7), spaceAfter=5,
)

_BADGE = {
    "good_to_go": ("GOOD TO GO", colors.HexColor("#1a7f48")),
    "minor": ("MINOR EDITS", colors.HexColor("#b8860b")),
    "major": ("MAJOR EDITS", colors.HexColor("#c0392b")),
    "error": ("ERROR", colors.HexColor("#666666")),
}


def _esc(s) -> str:
    return str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _label(text: str) -> str:
    return f'<b><font size="8" color="{_LABELC.hexval()}">{text}</font></b>&nbsp;&nbsp;'


def _decode(image_b64: str) -> bytes:
    s = (image_b64 or "").strip()
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def _image_flowable(image_b64: str):
    raw = _decode(image_b64)
    iw, ih = ImageReader(io.BytesIO(raw)).getSize()
    w = IMG_H * (iw / ih) if ih else IMG_H
    return Image(io.BytesIO(raw), width=w, height=IMG_H), w


def _rule(color="#dddddd", thickness=0.5, sb=4, sa=4):
    return HRFlowable(width="100%", thickness=thickness, color=colors.HexColor(color), spaceBefore=sb, spaceAfter=sa)


def _ad_header(r: dict) -> Paragraph:
    label, color = _BADGE.get(r.get("general"), _BADGE["error"])
    extra = f' &nbsp; <font color="#888888">#{_esc(r.get("ad_id"))}'
    extra += f' · {_esc(r.get("product"))}' if r.get("product") else ""
    extra += "</font>"
    return Paragraph(f'<font color="{color.hexval()}">{label}</font>{extra}', _ADHEAD)


def _feedback_flowables(r: dict) -> list:
    if r.get("general") == "error":
        return [Paragraph(_label("ERROR") + _esc(r.get("error") or "check failed"), _WHY)]
    edits = r.get("edits") or []
    if not edits:
        return [Paragraph("No brand or legal issues found.", _WHY)]
    out = []
    for i, e in enumerate(edits):
        if i:
            out.append(_rule(sb=6, sa=6))
        bad = "#c0392b" if e.get("severity") == "major" else "#b8860b"
        out.append(Paragraph(_label("PROBLEM") + f'<font color="{bad}">{_esc(e.get("part"))}</font>', _BODY))
        if e.get("revised"):
            out.append(Paragraph(_label("FIX") + _esc(e.get("revised")), _FIX))
        if e.get("issue"):
            out.append(Paragraph(_label("WHY") + _esc(e.get("issue")), _WHY))
    return out


def build_report(client: str, results: list, analysis: list, today: str = "") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        title=f"Compliance Report — {client}",
    )
    avail = doc.width
    story: list = []

    # ---- Header -----------------------------------------------------------
    story.append(Paragraph(f"Compliance Report — {_esc(client)}", _H1))
    n = len(results)
    c = {k: sum(1 for r in results if r.get("general") == k) for k in ("good_to_go", "minor", "major", "error")}
    sub = f"{n} creative(s) reviewed"
    if today:
        sub += f" · {_esc(today)}"
    sub += f" &nbsp;&nbsp;|&nbsp;&nbsp; {c['good_to_go']} good · {c['minor']} minor · {c['major']} major"
    if c["error"]:
        sub += f" · {c['error']} error"
    story.append(Paragraph(sub, _SUB))
    story.append(_rule(color="#333333", thickness=1, sb=8, sa=8))

    # ---- Top-3 analysis ---------------------------------------------------
    if analysis:
        story.append(Paragraph("Most common fixes needed", _H2))
        for j, a in enumerate(analysis[:3]):
            if j:
                story.append(Spacer(1, 7))
            head = (
                f'<b><font size="14">{a.get("pct")}%</font></b> '
                f'<font color="#888888">({a.get("ads")}/{a.get("total")} ads)</font> &nbsp; '
                f'<b>{_esc(a.get("id"))}</b>'
            )
            story.append(Paragraph(head, _BODY))
            story.append(Paragraph(_label("PROBLEM") + _esc(a.get("problem")), _BODY))
            if a.get("fix"):
                story.append(Paragraph(_label("FIX") + _esc(a.get("fix")), _FIX))
        story.append(_rule(color="#333333", thickness=1, sb=10, sa=8))

    # ---- Per-ad review ----------------------------------------------------
    story.append(Paragraph("Per-ad review", _H2))
    story.append(Spacer(1, 2))
    for r in results:
        block = [_ad_header(r)]
        try:
            img, w = _image_flowable(r.get("image_base64"))
            left = img
        except Exception:
            left, w = Paragraph("(image unavailable)", _WHY), 90
        tbl = Table([[left, _feedback_flowables(r)]], colWidths=[w + 12, avail - w - 12])
        tbl.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (0, 0), 0),
                    ("LEFTPADDING", (1, 0), (1, 0), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        block.append(tbl)
        block.append(_rule(color="#cccccc", thickness=0.75, sb=9, sa=9))
        story.append(KeepTogether(block))

    doc.build(story)
    return buf.getvalue()
