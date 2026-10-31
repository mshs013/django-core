"""PDF output with reportlab (django-import-export cannot produce PDF).

Every PDF is built with :func:`build_branded`, which puts the site letterhead (header + footer, see :mod:`core.reports.letterhead`)
on every page and optional signature boxes at the bottom of the last page. The body is a list of reportlab flowables.

A report source may register its own ``pdf_template`` callable ``fn(buffer, title, columns, rows, meta)``; the default is a
paginated table. A custom template gets the same letterhead by calling ``build_branded(buffer, title, story, meta)``.

``meta`` keys understood here: ``subtitle``, ``truncated``, ``generated_by``, ``generated_at``, ``company_id``, ``signatures``
(list of :class:`~core.reports.letterhead.Signature`), ``letterhead`` (a :class:`~core.reports.letterhead.Letterhead`),
``letterhead_class``, ``pagesize``.
"""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from django.utils import timezone

from core.formatting import format_value
from core.reports.letterhead import Signature, build_template  # noqa: F401 - Signature re-exported for templates

MAX_PDF_ROWS = 5000


def _cell(value, date_formats=None) -> str:
    text = format_value(value, date_formats)         # dates / times: ``date_formats`` if given, else the site formats (CORE_DATE_FORMAT ...)
    return text if len(text) <= 120 else text[:117] + "..."


class ImageCell:
    """A stored image (name in private storage) to embed in a PDF cell; anything unreadable becomes the word "image"."""

    def __init__(self, name):
        self.name = str(name)

    def __str__(self) -> str:
        return "Yes"


def _flowable(value, style):
    from reportlab.platypus import Image, Paragraph

    if isinstance(value, ImageCell):
        try:
            from core.files.storage import private_storage

            with private_storage.open(value.name, "rb") as handle:
                data = handle.read(3_000_000)
            image = Image(io.BytesIO(data))
            ratio = image.imageWidth / float(image.imageHeight or 1)
            image.drawHeight, image.drawWidth = 30, min(90, 30 * ratio)
            return image
        except Exception:  # noqa: BLE001 - a broken image must not break the whole report
            return Paragraph("image", style)
    return Paragraph(escape(_cell(value)), style)


# ------------------------------------------------------------------------------------------------------------------- letterhead
def _numbered_canvas(template):
    """A canvas class that holds every page until the end, so the footer can say "Page X of Y"."""
    from reportlab.pdfgen.canvas import Canvas

    class NumberedCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages = []

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_pages)
            for number, state in enumerate(self._saved_pages, start=1):
                self.__dict__.update(state)
                template.on_page_end(self, number, total)
                Canvas.showPage(self)
            Canvas.save(self)

    return NumberedCanvas


def _signature_block(template, signatures):
    """Signature row anchored to the bottom of the page it lands on (it moves to a new page when it does not fit)."""
    from reportlab.platypus import Flowable

    class SignatureBlock(Flowable):
        def wrap(self, avail_width, avail_height):
            self.width = avail_width
            needed = template.signature_height
            self.height = max(needed, avail_height - 0.5) if avail_height >= needed else needed
            return self.width, self.height

        def draw(self):
            template.draw_signatures(self.canv, signatures, 0, 0, self.width)

    return SignatureBlock()


def build_branded(buffer, title: str, story: list, meta: dict | None = None) -> None:
    """Build a PDF into ``buffer``: the letterhead on every page, ``story`` (reportlab flowables) as the body, then the signature
    row from ``meta["signatures"]`` at the bottom of the last page."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate

    meta = {**(meta or {})}
    meta.setdefault("title", title)
    meta.setdefault("generated_at", timezone.now())
    template = build_template(meta)
    doc = SimpleDocTemplate(buffer, pagesize=meta.get("pagesize") or landscape(A4), leftMargin=template.margin_x,
                            rightMargin=template.margin_x, topMargin=template.top_margin, bottomMargin=template.bottom_margin,
                            title=str(title)[:120], author=template.letterhead.brand or template.letterhead.name)
    body = list(story)
    if meta.get("signatures"):
        body.append(_signature_block(template, list(meta["signatures"])))
    doc.build(body, onFirstPage=template.on_page, onLaterPages=template.on_page, canvasmaker=_numbered_canvas(template))


# ------------------------------------------------------------------------------------------------------------------- default body
def default_pdf(buffer, title: str, columns: list[str], rows: list[list], meta: dict) -> None:
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body = []
    if meta.get("subtitle"):
        body.append(Paragraph(escape(meta["subtitle"]), styles["Normal"]))
    body.append(Spacer(1, 8))
    small = styles["BodyText"].clone("cell", fontSize=7, leading=9)
    data = [[Paragraph(f"<b>{escape(c)}</b>", small) for c in columns]]
    data += [[_flowable(v, small) for v in row] for row in rows]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    body.append(table)
    if meta.get("truncated"):
        body += [Spacer(1, 6), Paragraph(escape(f"Truncated to the first {MAX_PDF_ROWS} rows."), styles["Italic"])]
    build_branded(buffer, title, body, meta)


def render_pdf(title: str, columns: list[str], rows, meta: dict | None = None, template=None) -> bytes:
    meta = dict(meta or {})
    rows = list(rows)
    if len(rows) > MAX_PDF_ROWS:
        rows, meta["truncated"] = rows[:MAX_PDF_ROWS], True
    buffer = io.BytesIO()
    (template or default_pdf)(buffer, title, columns, rows, meta)
    return buffer.getvalue()
