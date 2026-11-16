"""الموظفين: كشف الحساب، المرتبات من البصمة، والإجازات والأذونات"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .customer_views import _period, statement_response
from .models import CashBox, CashEntry, EmployeeProfile, EmployeeTxn, LeaveRequest
from .notify import fmt, log_event
from .payroll import approve_payroll, balance, employee_statement, payroll_rows

User = get_user_model()


def _name(u):
    return u.get_full_name() or u.get_username()


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


@staff_member_required
def employees(request):
    _need(request, "trade.view_employeetxn")
    rows = [{"p": p, "balance": balance(p.user)}
            for p in EmployeeProfile.objects.select_related("user").order_by("-is_active", "user__first_name")]
    return render(request, "trade/employees.html", {
        **admin.site.each_context(request), "title": "الموظفين والمرتبات", "rows": rows,
        "period": timezone.localdate().strftime("%Y-%m")})


@staff_member_required
def employee_detail(request, user_id):
    if user_id != request.user.pk:
        _need(request, "trade.view_employeetxn")
    emp = get_object_or_404(User, pk=user_id)
    txns = list(EmployeeTxn.objects.filter(user=emp).select_related("box").order_by("date", "id"))
    running, rows = Decimal("0"), []
    for t in txns:
        running += t.signed
        rows.append({"t": t, "balance": running})
    return render(request, "trade/employee_detail.html", {
        **admin.site.each_context(request), "title": f"كشف حساب {_name(emp)}", "emp": emp,
        "profile": getattr(emp, "employee", None), "rows": list(reversed(rows)), "balance": running,
        "can_add": request.user.has_perm("trade.add_employeetxn"),
        "kinds": [k for k in EmployeeTxn.KINDS if k[0] != "salary"],
        "boxes": CashBox.objects.filter(is_active=True).exclude(method="shipping"),
        "today": timezone.localdate().isoformat(), "is_me": emp.pk == request.user.pk})


@staff_member_required
def my_statement(request):
    return redirect("employee_detail", user_id=request.user.pk)


@staff_member_required
def employee_statement_file(request, user_id, fmt_="pdf"):
    if user_id != request.user.pk:
        _need(request, "trade.view_employeetxn")
    emp = get_object_or_404(User, pk=user_id)
    d1, d2 = _period(request)
    return statement_response(employee_statement(emp, d1, d2), fmt_, f"employee-{emp.pk}")


@staff_member_required
@require_POST
def employee_txn_add(request, user_id):
    _need(request, "trade.add_employeetxn")
    emp = get_object_or_404(User, pk=user_id)
    kind = request.POST.get("kind")
    if kind not in dict(EmployeeTxn.KINDS) or kind == "salary":
        messages.error(request, "اختار نوع الحركة")
        return redirect("employee_detail", user_id=user_id)
    try:
        amount = Decimal(request.POST.get("amount", "").replace(",", "") or "0")
    except InvalidOperation:
        amount = Decimal("0")
    if amount <= 0:
        messages.error(request, "اكتب مبلغ صحيح")
        return redirect("employee_detail", user_id=user_id)
    try:
        d = date.fromisoformat(request.POST.get("date", ""))
    except ValueError:
        d = timezone.localdate()
    box = None
    if kind in ("advance", "payment"):
        box = CashBox.objects.filter(pk=request.POST.get("box") or 0, is_active=True).first()
        if box is None:
            messages.error(request, "اختار الخزنة اللي الفلوس طلعت منها")
            return redirect("employee_detail", user_id=user_id)
    desc = request.POST.get("description", "").strip()[:200]
    with transaction.atomic():
        t = EmployeeTxn.objects.create(user=emp, kind=kind, amount=amount, date=d, box=box,
                                       description=desc, created_by=request.user)
        if box:
            CashEntry.post(box, -amount, "salary", f"{t.get_kind_display()} — {_name(emp)}", date=d)
    log_event(f"👷 {t.get_kind_display()} للموظف {_name(emp)}: {fmt(amount)}"
              + (f" من {box}" if box else "") + (f"\n{desc}" if desc else "")
              + f"\nالباقي له: {fmt(balance(emp))}")
    messages.success(request, f"اتسجلت {t.get_kind_display()} {fmt(amount)}")
    return redirect("employee_detail", user_id=user_id)


@staff_member_required
def payroll(request):
    _need(request, "trade.view_employeetxn")
    period = request.GET.get("month") or timezone.localdate().strftime("%Y-%m")
    try:
        y, m = (int(x) for x in period.split("-"))
        period = f"{y:04d}-{m:02d}"
    except ValueError:
        period = timezone.localdate().strftime("%Y-%m")
    rows = payroll_rows(period)
    return render(request, "trade/payroll.html", {
        **admin.site.each_context(request), "title": f"مرتبات {period}", "rows": rows, "period": period,
        "total": sum((r["st"]["net"] for r in rows), Decimal("0")),
        "can_approve": request.user.has_perm("trade.add_employeetxn"),
        "pending": sum(1 for r in rows if not r["done"] and r["st"]["net"] > 0)})


@staff_member_required
@require_POST
def payroll_approve(request):
    _need(request, "trade.add_employeetxn")
    period = request.POST.get("month", "")
    n = approve_payroll(period, request.user)
    if n:
        log_event(f"💵 اتعتمدت مرتبات {period} لـ {n} موظف")
        messages.success(request, f"اتسجلت مرتبات {n} موظف في كشوف حسابهم ومصروفات الشهر")
    else:
        messages.info(request, "مفيش مرتبات جديدة تتعتمد للشهر ده")
    return redirect(f"{reverse('payroll')}?month={period}")


# ================= الإجازات والأذونات =================
def leave_approver_qs(user):
    """الطلبات اللي اليوزر ده يقدر يوافق عليها: كل الطلبات لمدير الحسابات، وطلبات فريقه للمدير المباشر"""
    qs = LeaveRequest.objects.filter(status=LeaveRequest.PENDING).exclude(user=user)
    if user.is_superuser or user.has_perm("trade.approve_leaverequest"):
        return qs
    return qs.filter(user__employee__manager=user)


def can_review_leave(user, lv):
    if lv.user_id == user.pk and not user.is_superuser:
        return False
    if user.is_superuser or user.has_perm("trade.approve_leaverequest"):
        return True
    prof = getattr(lv.user, "employee", None)
    return prof is not None and prof.manager_id == user.pk


@staff_member_required
def leaves(request):
    pending = list(leave_approver_qs(request.user).select_related("user"))
    approver = bool(pending) or request.user.has_perm("trade.approve_leaverequest") or request.user.team.exists()
    return render(request, "trade/leaves.html", {
        **admin.site.each_context(request), "title": "الإجازات والأذونات",
        "mine": LeaveRequest.objects.filter(user=request.user).select_related("reviewed_by")[:30],
        "pending": pending,
        "approver": approver, "kinds": LeaveRequest.KINDS, "today": timezone.localdate().isoformat()})


@staff_member_required
@require_POST
def leave_request(request):
    kind = request.POST.get("kind")
    if kind not in dict(LeaveRequest.KINDS):
        kind = "annual"
    try:
        d1 = date.fromisoformat(request.POST.get("start_date", ""))
        d2 = date.fromisoformat(request.POST.get("end_date") or request.POST.get("start_date", ""))
    except ValueError:
        messages.error(request, "اختار التاريخ")
        return redirect("leaves")
    if d2 < d1:
        d1, d2 = d2, d1
    hours = None
    if kind == "permission":
        try:
            hours = Decimal(request.POST.get("hours") or "0")
        except InvalidOperation:
            hours = Decimal("0")
        if not 0 < hours <= 12:
            messages.error(request, "اكتب عدد ساعات الإذن")
            return redirect("leaves")
        d2 = d1
    lv = LeaveRequest.objects.create(user=request.user, kind=kind, start_date=d1, end_date=d2, hours=hours,
                                     reason=request.POST.get("reason", "").strip()[:200])
    log_event(f"🗓 طلب {lv.get_kind_display()}: {_name(request.user)} — من {d1} لحد {d2}"
              + (f" ({hours} ساعة)" if hours else "") + (f"\n{lv.reason}" if lv.reason else "")
              + "\nمستني موافقة مدير الحسابات", user=request.user)
    messages.success(request, "اتبعت الطلب لمدير الحسابات")
    return redirect("leaves")


@staff_member_required
@require_POST
def leave_review(request, pk):
    lv = get_object_or_404(LeaveRequest.objects.select_related("user"), pk=pk, status=LeaveRequest.PENDING)
    if lv.user_id == request.user.pk and not request.user.is_superuser:
        messages.error(request, "مينفعش توافق على طلبك انت")
        return redirect("leaves")
    if not can_review_leave(request.user, lv):
        raise PermissionDenied
    approve = request.POST.get("decision") == "approve"
    lv.status = LeaveRequest.APPROVED if approve else LeaveRequest.REJECTED
    lv.reviewed_by = request.user
    lv.review_note = request.POST.get("note", "").strip()[:200]
    lv.save()
    log_event(f"{'✅ اتوافق على' if approve else '❌ اترفض'} {lv.get_kind_display()} {_name(lv.user)} "
              f"({lv.start_date} → {lv.end_date})")
    messages.success(request, "اتوافق على الطلب" if approve else "اترفض الطلب")
    return redirect("leaves")
