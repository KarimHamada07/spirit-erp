"""صفحة متابعة التحصيل للمحاسب: أعمار المديونيات + ملاحظات المتابعة ومواعيدها"""
from datetime import date

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.utils import timezone

from .debts import BUCKETS, bucket_totals, debt_rows, reminder_rows
from .exports import xlsx_response
from .models import Customer, DebtFollowUp


def _can_view(user):
    return user.has_perm("trade.view_debtfollowup")


@staff_member_required
def debts(request):
    if not _can_view(request.user):
        raise PermissionDenied
    rows = debt_rows()
    try:
        min_days = max(0, int(request.GET.get("days", 30)))
    except ValueError:
        min_days = 30
    show = request.GET.get("show", "remind")
    bucket = request.GET.get("bucket", "")
    visible = rows
    if show == "remind":
        visible = reminder_rows(rows, min_days)
    if bucket.isdigit() and int(bucket) < len(BUCKETS):
        visible = [r for r in visible if r["bucket"] == int(bucket)]

    if request.GET.get("export") == "xlsx":
        return xlsx_response([("متابعة التحصيل",
                               ["العميل", "الموبايل", "البائع", "المتبقي", "عمر المديونية (يوم)",
                                "أقدم فاتورة مفتوحة", "آخر دفعة", "آخر متابعة", "ميعاد المتابعة"],
                               [[r["customer"].name, r["customer"].phone,
                                 r["customer"].seller.name if r["customer"].seller_id else "",
                                 r["balance"], r["days"],
                                 r["oldest"].date if r["oldest"] else "", r["last_payment"] or "",
                                 r["followup"].note if r["followup"] else "",
                                 (r["followup"].next_date or "") if r["followup"] else ""]
                                for r in visible])], f"debts_{timezone.localdate()}")

    return render(request, "trade/debts.html", {
        **admin.site.each_context(request),
        "title": "متابعة التحصيل",
        "rows": visible, "all_count": len(rows),
        "remind_count": len(reminder_rows(rows, min_days)),
        "due_count": sum(1 for r in rows if r["due"]),
        "buckets": bucket_totals(rows),
        "total": sum((r["balance"] for r in rows), 0),
        "show": show, "bucket": bucket, "min_days": min_days,
        "today": timezone.localdate().isoformat(),
        "can_add": request.user.has_perm("trade.add_debtfollowup"),
    })


@staff_member_required
@require_POST
def debt_followup_add(request, pk):
    if not request.user.has_perm("trade.add_debtfollowup"):
        raise PermissionDenied
    customer = get_object_or_404(Customer, pk=pk)
    note = request.POST.get("note", "").strip()[:300]
    next_date = None
    if request.POST.get("next_date"):
        try:
            next_date = date.fromisoformat(request.POST["next_date"])
        except ValueError:
            messages.error(request, "التاريخ مش صحيح")
            return _back(request)
    if not note:
        messages.error(request, "اكتب ملاحظة المتابعة")
        return _back(request)
    DebtFollowUp.objects.create(customer=customer, note=note, next_date=next_date,
                                created_by=request.user)
    messages.success(request, f"اتسجلت المتابعة لـ {customer.name}")
    return _back(request)


def _back(request):
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
        return redirect(nxt)
    return redirect("debts")
