from django.contrib.staticfiles import finders
from django.http import FileResponse, Http404


def apple_touch_icon(request):
    """أيقونة الشاشة الرئيسية للأيفون (iOS بيطلبها من جذر الموقع)."""
    path = finders.find("trade/img/apple-touch-icon.png")
    if not path:
        raise Http404
    return FileResponse(open(path, "rb"), content_type="image/png")
