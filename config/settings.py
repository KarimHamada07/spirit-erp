import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# الإنتاج: DEBUG بيكون False إلا لو حطيت DJANGO_DEBUG=1 (للتجربة على جهازك بس)
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"

_secret = os.environ.get("DJANGO_SECRET_KEY")
if not _secret:
    if not DEBUG:
        raise ImproperlyConfigured("لازم تحدد DJANGO_SECRET_KEY في الإنتاج")
    _secret = "dev-only-insecure-key"
SECRET_KEY = _secret

ALLOWED_HOSTS = [
    "erp.spiritegy.org",
    "spiritegy.pythonanywhere.com",
]
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1"]

CSRF_TRUSTED_ORIGINS = [
    "https://erp.spiritegy.org",
    "https://spiritegy.pythonanywhere.com",
]
# وقت تشغيل الاختبارات (manage.py test) مفيش HTTPS، فمنحوّلش لـ https
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
if TESTING:
    ALLOWED_HOSTS += ["testserver"]
if not DEBUG and not TESTING:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    # HSTS: المتصفح يفتح الموقع بـ https بس. نبدأ بشهر من غير الدومينات الفرعية التانية
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", 60 * 60 * 24 * 30))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
# نوافذ الإضافة السريعة في لوحة الإدارة بتفتح في iframe من نفس الموقع
X_FRAME_OPTIONS = "SAMEORIGIN"
INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "django.contrib.humanize",
    "trade",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "trade.notify.CurrentUserMiddleware",
    "trade.middleware.MobileSidebarMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
LANGUAGE_CODE = "ar"
# عربي بس، مهما كانت لغة الموبايل أو المتصفح (من غير كده اللي موبايله إنجليزي بيشوف لوحة الإدارة إنجليزي)
LANGUAGES = [("ar", "العربية")]
LOCALE_PATHS = [BASE_DIR / "locale"]  # ترجمات إضافية (Jazzmin وحاجات Django اللي مش مترجمة)
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True
FORMAT_MODULE_PATH = ["config.formats"]
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGIN_URL = "/admin/login/"

# ---- الشكل العام (Jazzmin) ----
JAZZMIN_SETTINGS = {
    "site_title": "SPIRIT",
    "site_header": "SPIRIT",
    "site_brand": "SPIRIT",
    "site_logo": "trade/img/logo.png",
    "login_logo": "trade/img/logo.png",
    "site_icon": "trade/img/logo-icon.png",
    "welcome_sign": "أهلاً بيك في SPIRIT",
    "copyright": "SPIRIT",
    "custom_css": "trade/css/spirit.css",
    "use_google_fonts_cdn": False,  # الخط بييجي من spirit.css
    "show_ui_builder": False,
    # الوضع الداكن/الفاتح من زرار القمر (middleware). قوالب bootswatch بتبوّظ ألوان المحل
    "show_theme_chooser": False,
    "related_modal_active": True,
    # صفحات النظام في القايمة الجانبية (كل رابط بيظهر بس لللي عنده الصلاحية)
    "custom_links": {
        "trade": [
            {"name": "نقطة البيع", "url": "pos", "icon": "fas fa-cash-register",
             "permissions": ["trade.add_saleinvoice"]},
            {"name": "المخزون", "url": "inventory", "icon": "fas fa-boxes",
             "permissions": ["trade.view_variant"]},
            {"name": "العملاء والآجل", "url": "customers", "icon": "fas fa-user-friends",
             "permissions": ["trade.view_customer"]},
            {"name": "متابعة التحصيل", "url": "debts", "icon": "fas fa-phone-alt",
             "permissions": ["trade.view_debtfollowup"]},
            {"name": "الأونلاين", "url": "online_list", "icon": "fas fa-shipping-fast",
             "permissions": ["trade.view_onlineorder"]},
            {"name": "الخزينة", "url": "treasury", "icon": "fas fa-vault",
             "permissions": ["trade.view_cashbox"]},
            {"name": "التقفيل اليومي", "url": "daily_close", "icon": "fas fa-calendar-check",
             "permissions": ["trade.view_cashentry"]},
            {"name": "العهدة", "url": "custody", "icon": "fas fa-hand-holding-usd",
             "permissions": ["trade.view_custodysettlement"]},
            {"name": "التحليلات", "url": "analytics", "icon": "fas fa-chart-pie",
             "permissions": ["trade.view_expense"]},
            {"name": "تقرير الربحية", "url": "profit_report", "icon": "fas fa-coins",
             "permissions": ["trade.view_expense"]},
            {"name": "التقارير", "url": "reports", "icon": "fas fa-file-invoice",
             "permissions": ["trade.view_expense"]},
            {"name": "سجل المراجعة", "url": "audit", "icon": "fas fa-history",
             "permissions": ["trade.view_auditlog"]},
        ],
    },
    "icons": {
        "trade.rawmaterial": "fas fa-scroll",
        "trade.productionorder": "fas fa-industry",
        "trade.materialpurchase": "fas fa-truck-loading",
        "trade.debtfollowup": "fas fa-phone-alt",
    },
}
JAZZMIN_UI_TWEAKS = {
    "theme": "default",
    "navbar": "navbar-white navbar-light",
    "sidebar": "sidebar-dark-primary",
    "accent": "accent-danger",
    "navbar_small_text": False,
    "no_navbar_border": True,
    "sidebar_nav_small_text": False,
    "sidebar_nav_flat_style": True,
    "sidebar_nav_compact_style": False,
    "actions_sticky_top": True,
    "button_classes": {
        "primary": "btn-danger",
        "secondary": "btn-outline-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-dark",
    },
}

# تحذيرات مقصودة في check --deploy:
#  W005/W021: HSTS على erp.spiritegy.org بس (مش كل الدومينات الفرعية ولا قايمة preload)
#  W019: SAMEORIGIN عشان نوافذ الإضافة السريعة في لوحة الإدارة
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W019", "security.W021"]
