"""تصدير PDF: فاتورة بيع، وكشف حساب عميل أو مورد. الخط DejaVu Sans (بيدعم العربي)."""
import io
import os
from decimal import Decimal
from xml.sax.saxutils import escape

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

_HERE = os.path.dirname(os.path.abspath(__file__))
_FONT = os.path.join(_HERE, "fonts", "DejaVuSans.ttf")
_FONT_B = os.path.join(_HERE, "fonts", "DejaVuSans-Bold.ttf")
_LOGO = os.path.join(_HERE, "static", "trade", "img", "invoice_logo.png")

pdfmetrics.registerFont(TTFont("AR", _FONT))
pdfmetrics.registerFont(TTFont("AR-B", _FONT_B))

RED = colors.HexColor("#fe0000")
BLACK = colors.HexColor("#0b0b0c")
GREY = colors.HexColor("#e3e3e6")
SHOP = "SPIRIT"

_s_body = ParagraphStyle("body", fontName="AR", fontSize=10, leading=14, alignment=TA_RIGHT)
_s_small = ParagraphStyle("small", parent=_s_body, fontSize=9, leading=12)
_s_head = ParagraphStyle("head", parent=_s_body, fontName="AR-B", fontSize=10, textColor=colors.white)
_s_title = ParagraphStyle("title", parent=_s_body, fontName="AR-B", fontSize=16, leading=22,
                          alignment=TA_CENTER)
_s_shop = ParagraphStyle("shop", parent=_s_title, fontSize=13, textColor=BLACK)


def ar(text):
    """تشكيل النص العربي وترتيبه من اليمين (للعرض في PDF)."""
    text = "" if text is None else str(text)
    return get_display(arabic_reshaper.reshape(text))


def _p(text, style=_s_body):
    return Paragraph(escape(ar(text)), style)


def _money(value):
    return f"{Decimal(value or 0):,.2f}"


def _header(title):
    story = []
    if os.path.exists(_LOGO):
        story.append(Image(_LOGO, width=32 * mm, height=32 * mm, kind="proportional"))
    story.append(_p(SHOP, _s_shop))
    story.append(Spacer(1, 4 * mm))
    story.append(_p(title, _s_title))
    story.append(Spacer(1, 4 * mm))
    return story


def _table(data, widths, total_rows=()):
    """data: صفوف نصوص (أول صف عناوين). كل خلية بتتحول Paragraph عشان العربي يتلف صح."""
    rows = []
    for r_index, row in enumerate(data):
        style = _s_head if r_index == 0 else _s_body
        rows.append([_p(c, style) for c in row][::-1])  # RTL: أول عمود على اليمين
    t = Table(rows, colWidths=list(widths)[::-1], repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for idx in total_rows:
        cmds.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#f4f4f5")))
    t.setStyle(TableStyle(cmds))
    return t


def _build(story, footer_text):
    buf = io.BytesIO()

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("AR", 8)
        canvas.setFillColor(colors.HexColor("#6a6a70"))
        canvas.drawCentredString(A4[0] / 2, 10 * mm, ar(footer_text) if footer_text else "")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=18 * mm,
                            title=SHOP, author=SHOP)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def invoice_pdf(invoice, ctx):
    """فاتورة بيع. ctx = {rows[code,name,qty,price,total], gross, total, paid, remaining,
    pieces, payments[box,amount]} وبيانات الفاتورة نفسها"""
    story = _header(f"فاتورة بيع رقم {invoice.pk}")
    kind = "آجل" if invoice.payment_type == "credit" else "نقدي"
    customer = invoice.customer.name if invoice.customer else "عميل نقدي"
    seller = invoice.seller.name if invoice.seller else "—"
    channel = invoice.get_channel_display()
    story.append(_table([
        ["التاريخ", "العميل", "نوع الفاتورة", "القناة", "البائع"],
        [str(invoice.date), customer, kind, channel, seller],
    ], [26 * mm, 46 * mm, 26 * mm, 26 * mm, 36 * mm]))
    story.append(Spacer(1, 6 * mm))
    data = [["الكود", "الصنف", "الكمية", "السعر", "الإجمالي"]]
    for r in ctx["rows"]:
        data.append([r["code"], r["name"], str(r["qty"]), _money(r["price"]), _money(r["total"])])
    data.append(["", "عدد القطع", str(ctx["pieces"]), "", ""])
    story.append(_table(data, [24 * mm, 70 * mm, 20 * mm, 28 * mm, 28 * mm],
                        total_rows=(len(data) - 1,)))
    story.append(Spacer(1, 6 * mm))
    summary = [
        ["الإجمالي قبل الخصم", _money(ctx["gross"])],
        ["الخصم" + (f" ({invoice.discount_percent.normalize():f}%)" if invoice.discount_percent else ""),
         _money(invoice.discount)],
        ["الصافي", _money(ctx["total"])],
    ]
    for p in ctx["payments"]:
        summary.append([f"دفع: {p['box']}", _money(p["amount"])])
    if invoice.payment_type == "credit":
        summary.append(["المدفوع", _money(ctx["paid"])])
        summary.append(["المتبقي (آجل)", _money(ctx["remaining"])])
    t = Table([[_p(b), _p(a)] for a, b in summary], colWidths=[40 * mm, 60 * mm], hAlign="RIGHT")
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, GREY),
                           ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#f4f4f5"))]))
    story.append(t)
    story.append(Spacer(1, 10 * mm))
    story.append(_p("شكراً لتعاملكم معنا", _s_title))
    return _build(story, SHOP)


def statement_pdf(st):
    """كشف حساب (عميل أو مورد) من trade.statements"""
    t = st["totals"]
    story = _header(st["title"])
    period = "كل الحركات"
    if t["d1"] or t["d2"]:
        period = f"{t['d1'] or 'البداية'} → {t['d2'] or t['as_of']}"
    info = [["الهاتف", st["phone"] or "-"], ["الفترة", period], ["تاريخ الكشف", str(t["as_of"])]]
    story.append(_table([["البيان", "القيمة"]] + info, [60 * mm, 60 * mm]))
    story.append(Spacer(1, 6 * mm))
    data = [["التاريخ", "النوع", "المرجع", st["debit_label"], st["credit_label"], "الرصيد"]]
    for e in st["entries"]:
        data.append([str(e["date"]), e["kind"], e["ref"],
                     _money(e["debit"]) if e["debit"] else "-",
                     _money(e["credit"]) if e["credit"] else "-",
                     _money(e["balance"])])
    if len(data) == 1:
        data.append(["-", "مفيش حركات في الفترة دي", "", "", "", ""])
    story.append(_table(data, [27 * mm, 31 * mm, 36 * mm, 27 * mm, 27 * mm, 28 * mm]))
    story.append(Spacer(1, 6 * mm))
    if st.get("summary"):
        summary = [[label, _money(v)] for label, v in st["summary"]] + [[st["balance_label"], _money(t["balance"])]]
    else:
        summary = [
            [st["debit_label"], _money(t["debit"])],
            [st["credit_label"], _money(t["credit"])],
            [st["balance_label"], _money(t["balance"])],
        ]
    if t["previous"]:
        summary.insert(0, ["رصيد سابق", _money(t["previous"])])
    tbl = Table([[_p(b), _p(a)] for a, b in summary], colWidths=[40 * mm, 60 * mm], hAlign="RIGHT")
    tbl.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, GREY),
                             ("BACKGROUND", (0, len(summary) - 1), (-1, len(summary) - 1),
                              colors.HexColor("#f4f4f5"))]))
    story.append(tbl)
    return _build(story, "SPIRIT | سبيريت")
