"""صفحات البصمة: بصمتي (للموظف) وتقرير الحضور (للإدارة)"""
import uuid
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .attendance import (REPEAT_SECONDS, PunchError, check_photo, day_summary, issue_token,
                         nearest_workplace, next_kind, parse_location, use_token)
from .exports import xlsx_response
from .models import Attendance, Workplace
from .notify import client_ip, log_event


@staff_member_required
def my_attendance(request):
    today = timezone.localdate()
    punches = list(Attendance.objects.filter(user=request.user, at__date=today).order_by("at", "id"))
    return render(request, "trade/attendance_me.html", {
        **admin.site.each_context(request), "title": "بصمتي",
        "token": issue_token(request.session), "kind": next_kind(request.user),
        "punches": punches, "summary": day_summary(punches),
        "has_workplace": Workplace.objects.filter(is_active=True).exists(),
    })


@staff_member_required
@require_POST
def punch(request):
    try:
        use_token(request.session, request.POST.get("token"))
        lat, lng, acc = parse_location(request.POST.get("lat"), request.POST.get("lng"),
                                       request.POST.get("accuracy"))
        upload = request.FILES.get("photo")
        if upload is None:
            raise PunchError("مفيش صورة. صوّر نفسك الأول")
        jpeg = check_photo(upload.read())
        with transaction.atomic():
            last = (Attendance.objects.select_for_update().filter(user=request.user)
                    .order_by("-at", "-id").first())
            if last and (timezone.now() - last.at).total_seconds() < REPEAT_SECONDS:
                raise PunchError("انت لسه بصمت من شوية")
            kind = next_kind(request.user)
            near = nearest_workplace(lat, lng)
            a = Attendance(user=request.user, kind=kind, latitude=lat, longitude=lng, accuracy_m=acc,
                           ip=client_ip(request)[:64],
                           device=request.META.get("HTTP_USER_AGENT", "")[:200],
                           note=request.POST.get("note", "").strip()[:200])
            if near:
                a.workplace, a.distance_m = near
                a.inside = near[1] <= near[0].radius_m + min(acc, 100)
            a.photo.save(f"{uuid.uuid4().hex}.jpg", ContentFile(jpeg), save=False)
            a.save()
    except PunchError as e:
        return JsonResponse({"ok": False, "error": str(e), "token": issue_token(request.session)},
                            status=400)
    who = request.user.get_full_name() or request.user.get_username()
    when = timezone.localtime(a.at).strftime("%H:%M")
    where = (f"في {a.workplace}" if a.inside else
             (f"⚠️ برا مكان الشغل ({a.distance_m} متر من {a.workplace})" if a.workplace else "⚠️ مفيش مكان شغل متحدد"))
    log_event(f"{'🟢' if kind == 'in' else '🔴'} {a.get_kind_display()}: {who} — {when}\n{where}\n{a.map_url}",
              user=request.user)
    return JsonResponse({"ok": True, "kind": a.get_kind_display(), "time": when, "inside": a.inside,
                         "where": where})


def _can_view(user):
    return user.has_perm("trade.view_attendance")


@staff_member_required
def attendance_photo(request, pk):
    a = get_object_or_404(Attendance, pk=pk)
    if a.user_id != request.user.pk and not _can_view(request.user):
        raise Http404
    try:
        f = a.photo.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404
    resp = FileResponse(f, content_type="image/jpeg")
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


def _dates(request):
    today = timezone.localdate()
    try:
        d1 = date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        d1 = today
    try:
        d2 = date.fromisoformat(request.GET.get("to", ""))
    except ValueError:
        d2 = d1 if request.GET.get("from") else today
    if d2 < d1:
        d1, d2 = d2, d1
    return d1, min(d2, d1 + timedelta(days=62))


@staff_member_required
def attendance_report(request):
    if not _can_view(request.user):
        raise PermissionDenied
    d1, d2 = _dates(request)
    qs = (Attendance.objects.filter(at__date__range=(d1, d2))
          .select_related("user", "workplace").order_by("user__username", "at", "id"))
    user_id = request.GET.get("user")
    if user_id:
        qs = qs.filter(user_id=user_id)
    days = OrderedDict()
    for p in qs:
        key = (timezone.localtime(p.at).date(), p.user_id)
        days.setdefault(key, {"date": key[0], "user": p.user, "punches": []})["punches"].append(p)
    rows = []
    for row in days.values():
        row.update(day_summary(row["punches"]))
        rows.append(row)
    rows.sort(key=lambda r: (r["date"], r["user"].get_full_name() or r["user"].username), reverse=True)
    totals = OrderedDict()
    for r in rows:
        t = totals.setdefault(r["user"].pk, {"user": r["user"], "days": 0, "hours": 0.0, "outside": 0})
        t["days"] += 1
        t["hours"] += r["hours"]
        t["outside"] += r["outside"]

    if request.GET.get("export") == "xlsx":
        def name(u):
            return u.get_full_name() or u.username
        return xlsx_response([
            ("الأيام", ["التاريخ", "الموظف", "أول حضور", "آخر انصراف", "الساعات", "بصمات برا المكان"],
             [[r["date"], name(r["user"]),
               timezone.localtime(r["first_in"].at).strftime("%H:%M") if r["first_in"] else "",
               timezone.localtime(r["last_out"].at).strftime("%H:%M") if r["last_out"] else "",
               r["hours"], r["outside"]] for r in rows]),
            ("الإجمالي", ["الموظف", "أيام الحضور", "إجمالي الساعات", "بصمات برا المكان"],
             [[name(t["user"]), t["days"], round(t["hours"], 2), t["outside"]] for t in totals.values()]),
            ("كل البصمات", ["الوقت", "الموظف", "النوع", "في المكان", "البعد (متر)", "دقة اللوكيشن", "الخريطة"],
             [[timezone.localtime(p.at).replace(tzinfo=None), name(p.user), p.get_kind_display(),
               "أيوه" if p.inside else "لأ", p.distance_m if p.distance_m is not None else "",
               p.accuracy_m, p.map_url] for p in qs]),
        ], f"attendance_{d1}_{d2}")

    from django.contrib.auth import get_user_model
    return render(request, "trade/attendance_report.html", {
        **admin.site.each_context(request), "title": "الحضور والانصراف",
        "rows": rows, "totals": totals.values(), "d1": d1.isoformat(), "d2": d2.isoformat(),
        "user_id": user_id or "",
        "users": get_user_model().objects.filter(attendance__isnull=False).distinct().order_by("username"),
        "workplaces": Workplace.objects.all(),
        "can_add_place": request.user.has_perm("trade.add_workplace"),
    })


@staff_member_required
@require_POST
def workplace_add(request):
    if not request.user.has_perm("trade.add_workplace"):
        raise PermissionDenied
    try:
        lat = Decimal(request.POST.get("lat", "")).quantize(Decimal("0.000001"))
        lng = Decimal(request.POST.get("lng", "")).quantize(Decimal("0.000001"))
        radius = max(30, min(int(request.POST.get("radius") or 150), 2000))
    except (InvalidOperation, ValueError):
        messages.error(request, "مقدرناش نجيب اللوكيشن. اسمح للموقع باستخدامه وجرّب تاني")
        return redirect("attendance_report")
    name = request.POST.get("name", "").strip()[:100] or "المحل"
    Workplace.objects.create(name=name, latitude=lat, longitude=lng, radius_m=radius)
    messages.success(request, f"اتضاف مكان الشغل '{name}' (المسافة المسموحة {radius} متر)")
    return redirect("attendance_report")
