"""ملصقات الباركود: ملصق لكل قطعة (مقاس/لون) عليه باركود الصنف Code128.
المقاس الافتراضي 50×30 مم (طابعة ملصقات حرارية)، كل ملصق في صفحة لوحده."""
import io

from reportlab.graphics.barcode import code128
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .pdf_export import ar

SIZES = {"50x30": (50, 30), "40x25": (40, 25), "38x25": (38, 25)}


def _fit_barcode(value, max_w, height):
    """يختار عرض الخط بحيث الباركود يدخل في الملصق"""
    width = 0.5
    while True:
        bc = code128.Code128(value, barWidth=width * mm, barHeight=height, humanReadable=True, fontSize=7)
        if bc.width <= max_w or width <= 0.2:
            return bc
        width -= 0.03


def labels_pdf(entries, size="50x30", show_price=True, shop="SPIRIT"):
    """entries = [(variant, عدد النسخ)]"""
    w, h = SIZES.get(size, SIZES["50x30"])
    W, H = w * mm, h * mm
    small = h < 30
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(W, H))
    pdf.setTitle(f"ملصقات باركود {shop}")
    for v, copies in entries:
        name = ar(v.model.name)[:34]
        meta = ar(" / ".join(x for x in (v.size, v.color) if x))
        bc = _fit_barcode(v.barcode, W - 4 * mm, (8.5 if small else 12) * mm)
        for _ in range(copies):
            pdf.setFont("AR-B", 6.5 if small else 7.5)
            pdf.drawString(2 * mm, H - 4 * mm, shop)
            pdf.drawRightString(W - 2 * mm, H - 4 * mm, name)
            pdf.setFont("AR", 7 if small else 8)
            pdf.drawRightString(W - 2 * mm, H - (7.5 if small else 8.5) * mm, meta)
            if show_price and v.sale_price:
                pdf.setFont("AR-B", 8 if small else 9)
                pdf.drawString(2 * mm, H - (7.5 if small else 8.5) * mm, f"{v.sale_price:,.0f} EGP")
            bc.drawOn(pdf, (W - bc.width) / 2, 3.8 * mm)
            pdf.showPage()
    pdf.save()
    return buf.getvalue()
