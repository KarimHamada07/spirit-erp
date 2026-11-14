import os
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
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
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
    "show_theme_chooser": True,  # زرار لوحة الألوان: فاتح / داكن / تلقائي
    "related_modal_active": True,
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
