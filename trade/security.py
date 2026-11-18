"""حماية الدخول والروابط.

- قفل الدخول: 5 محاولات غلط لنفس اليوزر من نفس الجهاز (أو 20 من نفس الجهاز لأي يوزر) = الدخول يتقفل 15 دقيقة.
  العدّاد في الكاش (ملفات على السيرفر عشان كل الـ workers يشوفوه).
- safe_back: الرجوع للصفحة اللي قبل كده بس لو على نفس الموقع (مش لموقع بره).
"""
from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.cache import cache
from django.utils.http import url_has_allowed_host_and_scheme

MAX_PER_USER = 5
MAX_PER_IP = 20
LOCK_SECONDS = 15 * 60


def client_ip(request):
    """IP العميل الحقيقي (PythonAnywhere بيحطه في X-Real-IP)"""
    ip = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or ""
    return ip.strip()[:64]


def safe_back(request, fallback):
    ref = request.META.get("HTTP_REFERER", "")
    if ref and url_has_allowed_host_and_scheme(ref, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return ref
    return fallback


def hit(key, limit, seconds):
    """بيزوّد العدّاد ويرجّع True لو عدّى الحد"""
    try:
        n = cache.incr(key)
    except ValueError:
        cache.set(key, 1, seconds)
        n = 1
    return n > limit


def over(key, limit):
    return (cache.get(key) or 0) >= limit


def _keys(request, username):
    ip = client_ip(request)
    return f"login:u:{(username or '').strip().lower()[:150]}:{ip}", f"login:ip:{ip}"


class LockoutLoginForm(AdminAuthenticationForm):
    """فورم الدخول بتاع السيستم بقفل بعد المحاولات الغلط"""
    def clean(self):
        ukey, ikey = _keys(self.request, self.cleaned_data.get("username"))
        if over(ukey, MAX_PER_USER) or over(ikey, MAX_PER_IP):
            raise forms.ValidationError(
                "محاولات دخول غلط كتير. الدخول اتقفل 15 دقيقة، جرّب بعدها أو كلم المدير.", code="locked")
        try:
            data = super().clean()
        except forms.ValidationError:
            if self.cleaned_data.get("username") and self.cleaned_data.get("password"):
                hit(ukey, MAX_PER_USER, LOCK_SECONDS)
                hit(ikey, MAX_PER_IP, LOCK_SECONDS)
                from .notify import log_event
                if over(ukey, MAX_PER_USER):
                    log_event(f"🔒 الدخول اتقفل 15 دقيقة لليوزر {self.cleaned_data.get('username')} "
                              f"بعد {MAX_PER_USER} محاولات غلط (IP {client_ip(self.request)})")
            raise
        cache.delete(ukey)
        return data
