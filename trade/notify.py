"""سجل النشاط + إرساله لتليجرام.

كل حدث مهم في النظام بيتسجل في جدول ActivityLog (جوه نفس العملية، فلو العملية فشلت
الحدث ما يتسجلش). بعدها:
  - لو التوكن موجود على السيرفر (حساب مدفوع): بيتبعت لتليجرام فوراً.
  - لو مش موجود (حساب مجاني): بيستنى الناقل telegram_forwarder.py اللي بيسحبه
    ويبعته من جهاز عنده إنترنت كامل.
"""
import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from contextvars import ContextVar
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

SHOW_PROFIT = True   # اجعلها False لو مش عايز الربح يظهر في الرسائل
LOW_STOCK = 5
CONFIG_FILE = os.path.expanduser("~/.telegram_bot")

_current_user = ContextVar("trade_current_user", default=None)
_current_ip = ContextVar("trade_current_ip", default="")
_muted = ContextVar("trade_notify_muted", default=False)
_blocked_until = 0.0


# ---------------------------------------------------------------- الإعدادات
def config():
    """TOKEN و CHAT_ID و FEED_KEY: من متغيرات البيئة أو من الملف ~/.telegram_bot"""
    cfg = {
        "TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "FEED_KEY": os.environ.get("TELEGRAM_FEED_KEY", ""),
        # اختياري: شات المحاسب لتذكيرات التحصيل (لو مش موجود بتروح للشات العام)
        "ACCOUNTANT_CHAT_ID": os.environ.get("TELEGRAM_ACCOUNTANT_CHAT_ID", ""),
    }
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().upper()
                if key in cfg and not cfg[key]:
                    cfg[key] = value.strip()
    except OSError:
        pass
    return cfg


# ---------------------------------------------------------------- المستخدم الحالي
class CurrentUserMiddleware:
    """يحفظ المستخدم الحالي عشان يظهر في الرسالة مين عمل الخطوة"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_user.set(getattr(request, "user", None))
        ip_token = _current_ip.set(client_ip(request))
        try:
            return self.get_response(request)
        finally:
            _current_user.reset(token)
            _current_ip.reset(ip_token)


def client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    return fwd or request.META.get("REMOTE_ADDR", "") or ""


def current_actor():
    """(المستخدم، الـ IP) بتوع الطلب الحالي، لو فيه"""
    user = _current_user.get()
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    return user, _current_ip.get()


# ---------------------------------------------------------------- التسجيل والإرسال
def send_telegram(token, chat_id, text, api_base=None):
    base = api_base or os.environ.get("TELEGRAM_API", "https://api.telegram.org")
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text[:4000],
        "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"{base}/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return bool(body.get("ok")), body.get("description", "")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("description", str(e))
        except Exception:
            detail = str(e)
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:  # شبكة، بروكسي، timeout...
        return False, str(e)


def deliver_pending():
    """إرسال مباشر (لو التوكن موجود على السيرفر). لو فشل بنوقف دقيقة عشان ما نبطّئش الموقع."""
    global _blocked_until
    try:
        cfg = config()
        if not cfg["TOKEN"] or not cfg["CHAT_ID"]:
            return  # وضع الناقل
        if time.time() < _blocked_until:
            return
        from .models import ActivityLog
        for ev in ActivityLog.objects.filter(sent=False).order_by("id")[:20]:
            ok, detail = send_telegram(cfg["TOKEN"], chat_for(ev.audience, cfg), ev.message)
            if not ok:
                _blocked_until = time.time() + 60
                logger.warning("telegram send failed: %s", detail)
                return
            ev.sent = True
            ev.save(update_fields=["sent"])
    except Exception:
        logger.exception("deliver_pending failed")


def chat_for(audience, cfg=None):
    """الشات اللي الرسالة تروحله: المحاسب له شات خاص لو متحدد"""
    cfg = cfg or config()
    if audience == "accountant" and cfg["ACCOUNTANT_CHAT_ID"]:
        return cfg["ACCOUNTANT_CHAT_ID"]
    return cfg["CHAT_ID"]


class muted:
    """جوه الـ with ده log_event مش بيسجل حاجة (للاستيراد بالجملة: رسالة واحدة بدل ألف)"""

    def __enter__(self):
        self._token = _muted.set(True)

    def __exit__(self, *exc):
        _muted.reset(self._token)


def log_event(text, user=None, audience=""):
    """يسجل حدث. عمره ما يكسر العملية الأصلية. audience="accountant" = تذكير للمحاسب."""
    if _muted.get():
        return
    try:
        from .models import ActivityLog
        user = user or _current_user.get()
        name = ""
        if user is not None and getattr(user, "is_authenticated", False):
            name = user.get_username()
        with transaction.atomic():
            ActivityLog.objects.create(text=text, user_name=name, audience=audience)
            if random.random() < 0.01:  # تنظيف الأحداث القديمة المرسلة
                ActivityLog.objects.filter(
                    sent=True,
                    created_at__lt=timezone.now() - timedelta(days=90)).delete()
        transaction.on_commit(deliver_pending)
    except Exception:
        logger.exception("log_event failed")


# ---------------------------------------------------------------- صياغة الرسائل
def fmt(n):
    return f"{Decimal(n):,.2f}"


def sale_text(inv):
    kind = "آجل" if inv.payment_type == "credit" else "نقدي"
    lines = [f"🧾 فاتورة بيع #{inv.pk} — {kind}"]
    if inv.customer_id:
        lines.append(f"العميل: {inv.customer.name}")
    for i in inv.items.select_related("variant__model"):
        lines.append(f"• {i.variant} × {i.quantity} @ {fmt(i.unit_price)}")
    total = f"الإجمالي: {fmt(inv.total)}"
    if inv.discount:
        pct = f" = {inv.discount_percent.normalize():f}%" if inv.discount_percent else ""
        total += f" (خصم {fmt(inv.discount)}{pct})"
    lines.append(total)
    if SHOW_PROFIT:
        lines.append(f"الربح: {fmt(inv.profit)}")
    if inv.payment_type == "credit":
        lines.append("⏳ آجل على حساب العميل")
    return "\n".join(lines)


def purchase_text(inv):
    lines = [f"📦 توريد #{inv.pk} — {inv.supplier}"]
    for i in inv.items.select_related("variant__model"):
        lines.append(f"• {i.variant} × {i.quantity} @ {fmt(i.unit_cost)}")
    lines.append(f"الإجمالي: {fmt(inv.total)}")
    lines.append("تم تأكيد الاستلام وتحديث المخزون")
    return "\n".join(lines)


def payment_text(p):
    from .models import Customer
    c = Customer.objects.with_balance().get(pk=p.customer_id)
    line = f"💰 دفعة تسديد {fmt(p.amount)} من {c.name} ({p.get_method_display()})"
    if p.invoice_id:
        line += f" على فاتورة #{p.invoice_id}"
    return f"{line}\nالمتبقي عليه: {fmt(c.balance)}"


def online_created_text(o):
    lines = [f"🛍 أوردر أونلاين جديد #{o.pk} — {o.get_status_display()}",
             f"العميل: {o.customer_name} — {o.phone}"]
    if o.address:
        lines.append(f"العنوان: {o.address}")
    for i in o.items.select_related("variant__model"):
        lines.append(f"• {i.variant} × {i.quantity} @ {fmt(i.unit_price)}")
    lines.append(f"المطلوب تحصيله: {fmt(o.total_to_collect)}")
    return "\n".join(lines)


def online_status_text(o, old_status):
    labels = dict(o.STATUS_CHOICES)
    icon = {"shipped": "🚚", "collected": "✅", "returned": "↩️"}.get(o.status, "🔄")
    lines = [f"{icon} أوردر أونلاين #{o.pk} ({o.customer_name}): "
             f"{labels[old_status]} ➜ {labels[o.status]}"]
    if o.status == o.COLLECTED:
        lines.append(f"تم تحصيل: {fmt(o.total_to_collect)}")
        if SHOW_PROFIT:
            lines.append(f"الربح: {fmt(o.profit)}")
    if o.status == o.RETURNED:
        lines.append("الكميات رجعت للمخزن")
    return "\n".join(lines)


def sale_return_text(ret):
    lines = [f"↩️ مرتجع بيع #{ret.pk} — فاتورة #{ret.invoice_id}"]
    for i in ret.items.select_related("sale_item__variant__model"):
        lines.append(f"• {i.sale_item.variant} × {i.quantity}")
    lines.append(f"قيمة المرتجع: {fmt(ret.total)}")
    if ret.reason:
        lines.append(f"السبب: {ret.reason}")
    lines.append("الكميات رجعت للمخزن")
    return "\n".join(lines)


def purchase_return_text(ret):
    lines = [f"↩️ مرتجع توريد #{ret.pk} — {ret.supplier}"]
    for i in ret.items.select_related("variant__model"):
        lines.append(f"• {i.variant} × {i.quantity} @ {fmt(i.unit_cost)}")
    lines.append(f"قيمة المرتجع: {fmt(ret.total)}")
    if ret.reason:
        lines.append(f"السبب: {ret.reason}")
    return "\n".join(lines)


def transfer_text(t):
    line = f"🔄 تحويل {fmt(t.amount)} من {t.from_box} إلى {t.to_box}"
    if t.note:
        line += f"\nملاحظة: {t.note}"
    return line


def supplier_payment_text(p):
    line = f"💳 تسديد مورد {p.supplier} — {fmt(p.amount)} من {p.box}"
    if p.notes:
        line += f"\nملاحظة: {p.notes}"
    return line


# أسماء الموديلات بالعربي للرسايل (بدل أسماء Django الإنجليزي)
MODEL_AR = {
    "clothingmodel": "موديل", "variant": "صنف", "supplier": "مورد", "customer": "عميل",
    "seller": "بائع", "commissionrate": "نسبة عمولة", "user": "مستخدم", "group": "صلاحيات",
    "saleinvoice": "فاتورة بيع", "purchaseinvoice": "فاتورة شراء", "payment": "دفعة",
    "onlineorder": "طلب أونلاين", "expense": "مصروف", "custodydeposit": "إيداع عهدة",
    "custodysettlement": "تصفية عهدة", "cashbox": "خزنة", "cashentry": "حركة خزنة",
    "cashtransfer": "تحويل", "supplierpayment": "تسديد مورد",
    "salereturn": "مرتجع بيع", "purchasereturn": "مرتجع توريد",
    "custodyholder": "صاحب عهدة", "custodyclearance": "تصفية عهدة شخص",
}


def model_ar(model_name, fallback=""):
    return MODEL_AR.get((model_name or "").lower(), fallback or model_name or "")
