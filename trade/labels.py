"""ملصقات باركود للموديلات: باركود Code128 على كود الموديل.
المقاس 50×30 مم (طابعة حرارية)، ملصق لكل موديل في صفحة مستقلة."""
import io

from reportlab.graphics.barcode import code128
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .pdf_export import ar

LABEL_W, LABEL_H = 50 * mm, 30 * mm
MAX_BARCODE_W = 44 * mm


def _fit_barcode(value):
    """يختار عرض الخط بحيث الباركود يدخل في الملصق"""
    width = 0.6
    while True:
        bc = code128.Code128(value, barWidth=width * mm, barHeight=12 * mm, humanReadable=True)
        if bc.width <= MAX_BARCODE_W or width <= 0.2:
            return bc
        width -= 0.05


def labels_pdf(models):
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(LABEL_W, LABEL_H))
    pdf.setTitle("ملصقات باركود SPIRIT")
    for m in models:
        bc = _fit_barcode(m.code)
        pdf.setFont("AR-B", 9)
        pdf.drawRightString(LABEL_W - 3 * mm, LABEL_H - 6 * mm, ar(m.name)[:40])
        bc.drawOn(pdf, (LABEL_W - bc.width) / 2, 7 * mm)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()
