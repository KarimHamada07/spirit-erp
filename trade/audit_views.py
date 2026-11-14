from datetime import date

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from .exports import xlsx_response
from .models import AuditLog


def _allowed(user):
    return user.is_superuser or user.has_perm("trade.view_auditlog")


def lines(a):
    ch = a.changes or {}
    if a.action == "update":
        return [f"{k}: {(v[0] or '—')} ← {(v[1] or '—')}" for k, v in ch.items()]
    return [f"{k}: {v}" for k, v in ch.items()]


def _filtered(request):
    g = request.GET
    qs = AuditLog.objects.all()
    q = g.get("q", "").strip()
    if q:
        qs = qs.filter(Q(object_repr__icontains=q) | Q(username__icontains=q) | Q(model__icontains=q))
    if g.get("user"):
        qs = qs.filter(username=g["user"])
    if g.get("action") in dict(AuditLog.ACTIONS):
        qs = qs.filter(action=g["action"])
    if g.get("model"):
        qs = qs.filter(model=g["model"])
    for key, op in (("from", "gte"), ("to", "lte")):
        try:
            d = date.fromisoformat(g.get(key, ""))
            qs = qs.filter(**{f"created_at__date__{op}": d})
        except ValueError:
            pass
    return qs


@staff_member_required
def audit(request):
    if not _allowed(request.user):
        raise PermissionDenied
    qs = _filtered(request)

    if request.GET.get("export") == "xlsx":
        rows = [[timezone.localtime(a.created_at).replace(tzinfo=None), a.username or "—",
                 a.get_action_display(), a.model, a.object_repr, " | ".join(lines(a)), a.ip]
                for a in qs[:5000]]
        return xlsx_response([("سجل المراجعة", ["الوقت", "المستخدم", "الإجراء", "النوع",
                                                "السجل", "التفاصيل", "IP"], rows)], "audit_log")

    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    rows = [{"a": a, "lines": lines(a)} for a in page]
    query = request.GET.copy()
    query.pop("page", None)
    context = {
        **admin.site.each_context(request),
        "title": "سجل المراجعة",
        "rows": rows, "page": page, "g": request.GET,
        "qs_without_page": query.urlencode(),
        "users": AuditLog.objects.exclude(username="").values_list("username", flat=True).distinct().order_by("username"),
        "models": AuditLog.objects.exclude(model="").values_list("model", flat=True).distinct().order_by("model"),
        "actions": AuditLog.ACTIONS,
    }
    return render(request, "trade/audit.html", context)
