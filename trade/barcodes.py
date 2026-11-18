"""الباركود: لكل مقاس/لون باركود، ولكل فاتورة باركود بيفتحها.

الأرقام كلها أرقام بس (من غير حروف) عشان الإسكانر بيكتب زي الكيبورد، ولو الكيبورد عربي الحروف بتطلع غلط.
- الصنف (مقاس/لون): 20 + رقم الـ Variant (6 خانات) = 8 أرقام، أو باركود المصنع لو اتكتب في تكويد الصنف.
- فاتورة البيع (جملة/قطاعي/تسويق/أون لاين): 31 + رقم الفاتورة.
- أوردر الأونلاين: 32 + رقم الأوردر.
"""
import re

from django.utils.safestring import mark_safe

VARIANT, SALE, ONLINE = "20", "31", "32"
DOC_RE = re.compile(r"^(3[12])(\d{6,})$")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def clean(code):
    """أرقام عربي ← إنجليزي، ومن غير مسافات"""
    return re.sub(r"\s+", "", (code or "").translate(_AR_DIGITS))


def make(prefix, pk):
    return f"{prefix}{pk:06d}"


def sale_code(pk):
    return make(SALE, pk)


def online_code(pk):
    return make(ONLINE, pk)


def parse_doc(code):
    """("sale"|"online", pk) لو الباركود بتاع فاتورة، وإلا None"""
    m = DOC_RE.match(clean(code))
    if not m:
        return None
    return ("sale" if m.group(1) == SALE else "online"), int(m.group(2))


def is_doc_code(code):
    return parse_doc(code) is not None


def barcode_error(code, variant_pk=None):
    """رسالة غلط لو الباركود اللي اتكتب للصنف مش مقبول (فاضي = تمام، السيستم بيعمله)"""
    code = clean(code)
    if not code:
        return ""
    if not re.fullmatch(r"[0-9A-Za-z\-]{3,40}", code):
        return f"الباركود {code}: أرقام أو حروف إنجليزي بس"
    if is_doc_code(code):
        return f"الباركود {code} شبه باركود الفواتير (بيبدأ بـ 31 أو 32). اكتب غيره"
    if re.fullmatch(VARIANT + r"\d{6}", code) and (variant_pk is None or code != make(VARIANT, variant_pk)):
        return f"الباركود {code} محجوز للسيستم (20 + 6 أرقام). سيبه فاضي أو اكتب باركود المصنع"
    from .models import Variant
    other = Variant.objects.filter(barcode=code).exclude(pk=variant_pk).select_related("model").first()
    if other:
        return f"الباركود {code} متسجل لـ {other}"
    return ""


def svg(value, height=12, bar=0.33, text=True):
    """Code128 كـ SVG للطباعة في صفحات HTML (الارتفاع وعرض الخط بالمليمتر)"""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode import createBarcodeDrawing
    from reportlab.lib.units import mm
    from django.utils.html import escape
    d = createBarcodeDrawing("Code128", value=value, barWidth=bar * mm, barHeight=height * mm,
                             humanReadable=False, quiet=False)
    out = renderSVG.drawToString(d)
    out = out[out.index("<svg"):]
    out = re.sub(r"<title>.*?</title>|<desc>.*?</desc>", "", out, flags=re.S)
    # الرقم تحت الباركود بـ HTML (الـ SVG text بيتلخبط في الصفحات العربي RTL)
    num = f'<span class="bc-num">{escape(value)}</span>' if text else ""
    return mark_safe(f'<span class="bc-wrap" dir="ltr">{out}{num}</span>')
