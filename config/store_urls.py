"""روابط دومين المتجر (spiritegy.org): صفحات المتجر بس، من غير لوحة الإدارة ولا صفحات السيستم."""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path("", include("trade.store_urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
