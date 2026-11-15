"""المأموريات: الموظف بيسجل بداية المأمورية ووصوله لكل مكان ونهايتها باللوكيشن (وصورة لو عايز)"""
import uuid
from datetime import date, timedelta

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db.models import Prefetch
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .attendance import PunchError, parse_location
from .custody_views import _read_image
from .models import Customer, Mission, MissionPoint
from .notify import log_event


def _who(user):
    return user.get_full_name() or user.get_username()


def _point(request, mission, kind):
    lat, lng, acc = parse_location(request.POST.get("lat"), request.POST.get("lng"),
                                   request.POST.get("accuracy"))
    p = MissionPoint(mission=mission, kind=kind, latitude=lat, longitude=lng, accuracy_m=acc,
                     note=request.POST.get("note", "").strip()[:200])
    if request.FILES.get("photo"):
        try:
            data, ext = _read_image(request.FILES["photo"])
        except ValueError as e:
            raise PunchError(str(e))
        p.photo.save(f"{uuid.uuid4().hex}.{ext}", ContentFile(data), save=False)
    p.save()
    return p


@staff_member_required
def my_missions(request):
    open_m = (Mission.objects.filter(user=request.user, status=Mission.OPEN)
              .prefetch_related("points").first())
    recent = (Mission.objects.filter(user=request.user, status=Mission.DONE)
              .prefetch_related("points")[:10])
    return render(request, "trade/missions_me.html", {
        **admin.site.each_context(request), "title": "مأمورياتي", "open": open_m, "recent": recent,
        "customers": Customer.objects.order_by("name")})


@staff_member_required
@require_POST
def mission_start(request):
    if Mission.objects.filter(user=request.user, status=Mission.OPEN).exists():
        messages.error(request, "عندك مأمورية لسه ما خلصتش")
        return redirect("my_missions")
    purpose = request.POST.get("purpose", "").strip()[:200]
    if not purpose:
        messages.error(request, "اكتب المأمورية رايح فيها ليه")
        return redirect("my_missions")
    try:
        m = Mission(user=request.user, purpose=purpose,
                    destination=request.POST.get("destination", "").strip()[:200],
                    customer=Customer.objects.filter(pk=request.POST.get("customer") or 0).first())
        m.save()
        p = _point(request, m, "start")
    except PunchError as e:
        Mission.objects.filter(pk=getattr(m, "pk", None), points__isnull=True).delete()
        messages.error(request, str(e))
        return redirect("my_missions")
    log_event(f"🚗 بدأ مأمورية: {_who(request.user)}\n{purpose}"
              + (f" — {m.destination}" if m.destination else "") + f"\n{p.map_url}", user=request.user)
    messages.success(request, "بدأت المأمورية. دوس \"وصلت\" عند كل مكان توصله.")
    return redirect("my_missions")


@staff_member_required
@require_POST
def mission_point(request, pk):
    m = get_object_or_404(Mission, pk=pk, user=request.user, status=Mission.OPEN)
    kind = request.POST.get("kind")
    if kind not in ("arrive", "note", "end"):
        kind = "arrive"
    try:
        p = _point(request, m, kind)
    except PunchError as e:
        messages.error(request, str(e))
        return redirect("my_missions")
    if kind == "end":
        m.status, m.ended_at = Mission.DONE, p.at
        m.save()
        log_event(f"🏁 خلّص مأمورية: {_who(request.user)} — {m.purpose}\nالمدة: {m.duration_minutes} دقيقة"
                  f" — {m.points.count()} نقطة\n{m.route_url}", user=request.user)
        messages.success(request, "اتقفلت المأمورية. شكراً!")
    else:
        if kind == "arrive":
            log_event(f"📍 {_who(request.user)} وصل" + (f": {p.note}" if p.note else "")
                      + f" ({m.purpose})\n{p.map_url}", user=request.user)
        messages.success(request, "اتسجل")
    return redirect("my_missions")


def _dates(request):
    today = timezone.localdate()
    try:
        d1 = date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        d1 = today - timedelta(days=6)
    try:
        d2 = date.fromisoformat(request.GET.get("to", ""))
    except ValueError:
        d2 = today
    return (d1, d2) if d1 <= d2 else (d2, d1)


@staff_member_required
def missions_report(request):
    if not request.user.has_perm("trade.view_mission"):
        raise PermissionDenied
    d1, d2 = _dates(request)
    qs = (Mission.objects.filter(started_at__date__range=(d1, d2)).select_related("user", "customer")
          .prefetch_related(Prefetch("points", queryset=MissionPoint.objects.order_by("at", "id"))))
    if request.GET.get("user"):
        qs = qs.filter(user_id=request.GET["user"])
    return render(request, "trade/missions_report.html", {
        **admin.site.each_context(request), "title": "المأموريات", "missions": qs[:300],
        "d1": d1.isoformat(), "d2": d2.isoformat(), "user_id": request.GET.get("user", ""),
        "users": get_user_model().objects.filter(missions__isnull=False).distinct().order_by("username")})


@staff_member_required
def mission_photo(request, pk):
    p = get_object_or_404(MissionPoint.objects.select_related("mission"), pk=pk)
    if p.mission.user_id != request.user.pk and not request.user.has_perm("trade.view_mission"):
        raise Http404
    if not p.photo:
        raise Http404
    resp = FileResponse(p.photo.open("rb"), content_type="image/jpeg")
    resp["X-Content-Type-Options"] = "nosniff"
    return resp
