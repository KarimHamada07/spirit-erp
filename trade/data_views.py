"""صفحة الاستيراد والتصدير بـ Excel"""
import secrets

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .data_io import SPECS, RowError, preview, read_rows, run_import, template_xlsx
from .exports import XLSX_TYPE

MAX_UPLOAD = 5 * 1024 * 1024
SESSION_KEY = "data_import"


def _spec(key):
    spec = SPECS.get(key)
    if spec is None:
        raise Http404
    return spec


def _can_import(user, spec):
    return user.has_perm("trade.import_data") and user.has_perm(spec.perm_add)


@staff_member_required
def data_center(request):
    cards = [{"spec": s, "can_view": request.user.has_perm(s.perm_view),
              "can_import": _can_import(request.user, s)}
             for s in SPECS.values()]
    cards = [c for c in cards if c["can_view"]]
    if not cards:
        raise PermissionDenied
    return render(request, "trade/data_center.html", {
        **admin.site.each_context(request), "title": "استيراد وتصدير البيانات", "cards": cards})


@staff_member_required
def data_file(request, key, kind):
    """kind = template (فورم فاضي) أو export (البيانات الحالية بنفس الشكل)"""
    spec = _spec(key)
    if not request.user.has_perm(spec.perm_view):
        raise PermissionDenied
    with_data = kind == "export"
    resp = HttpResponse(template_xlsx(spec, with_data=with_data), content_type=XLSX_TYPE)
    name = f"{key}_{'data_' + timezone.localdate().isoformat() if with_data else 'template'}.xlsx"
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@staff_member_required
@require_POST
def data_upload(request, key):
    spec = _spec(key)
    if not _can_import(request.user, spec):
        raise PermissionDenied
    f = request.FILES.get("file")
    if f is None:
        messages.error(request, "اختار ملف Excel")
        return redirect("data_center")
    if f.size > MAX_UPLOAD:
        messages.error(request, "الملف أكبر من 5 ميجا. قسّمه على أكتر من ملف.")
        return redirect("data_center")
    try:
        rows = read_rows(spec, f)
    except RowError as e:
        messages.error(request, str(e))
        return redirect("data_center")
    token = secrets.token_urlsafe(8)
    request.session[SESSION_KEY] = {"token": token, "key": key, "rows": rows,
                                    "filename": f.name[:100]}
    return redirect("data_preview", token=token)


def _pending(request, token):
    data = request.session.get(SESSION_KEY)
    if not data or data.get("token") != token:
        messages.error(request, "الملف ده انتهى. ارفعه تاني.")
        return None
    return data


@staff_member_required
def data_preview(request, token):
    data = _pending(request, token)
    if data is None:
        return redirect("data_center")
    spec = _spec(data["key"])
    if not _can_import(request.user, spec):
        raise PermissionDenied
    result = preview(spec, [tuple(r) for r in data["rows"]])
    counts = {a: sum(1 for r in result if r["action"] == a) for a in ("new", "update", "skip", "error")}
    show = request.GET.get("show", "error" if counts["error"] else "all")
    rows = result if show == "all" else [r for r in result if r["action"] == show]
    return render(request, "trade/data_preview.html", {
        **admin.site.each_context(request), "title": f"معاينة استيراد {spec.title}",
        "spec": spec, "token": token, "filename": data["filename"], "counts": counts,
        "rows": rows[:1000], "hidden": max(0, len(rows) - 1000), "show": show})


@staff_member_required
@require_POST
def data_confirm(request, token):
    data = _pending(request, token)
    if data is None:
        return redirect("data_center")
    spec = _spec(data["key"])
    if not _can_import(request.user, spec):
        raise PermissionDenied
    try:
        counts = run_import(spec, [tuple(r) for r in data["rows"]], request.user)
    except RowError as e:
        messages.error(request, str(e))
        return redirect("data_preview", token=token)
    request.session.pop(SESSION_KEY, None)
    messages.success(request, f"تم استيراد {spec.title}: {counts['new']} جديد، {counts['update']} اتحدّث"
                     + (f"، {counts['skip']} كان موجود" if counts["skip"] else ""))
    return redirect("data_center")
