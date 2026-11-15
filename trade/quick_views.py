"""فورم صغيرة للحاجات اليومية بدل شاشات لوحة الإدارة الطويلة: تحصيل من عميل، ومصروف."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone

from .customer_views import take_payment
from .models import CashBox, Customer, Expense


@staff_member_required
def quick_payment(request):
    if not request.user.has_perm("trade.add_payment"):
        raise PermissionDenied
    debtors = [c for c in Customer.objects.with_balance().order_by("name") if c.balance > 0]
    if request.method == "POST":
        customer = next((c for c in debtors if str(c.pk) == request.POST.get("customer")), None)
        if customer is None:
            messages.error(request, "اختار العميل")
        else:
            ok, msg = take_payment(request, customer)
            (messages.success if ok else messages.error)(request, msg)
            if ok:
                return redirect("quick_payment")
    context = {
        **admin.site.each_context(request),
        "title": "تحصيل من عميل",
        "debtors": debtors,
        "boxes": CashBox.objects.filter(is_active=True).exclude(method="shipping"),
        "selected": request.POST.get("customer") or request.GET.get("customer", ""),
        "today": timezone.localdate().isoformat(),
    }
    return render(request, "trade/quick_payment.html", context)


@staff_member_required
def quick_expense(request):
    if not request.user.has_perm("trade.add_expense"):
        raise PermissionDenied
    if request.method == "POST":
        title = request.POST.get("title", "").strip()[:150]
        try:
            amount = Decimal(request.POST.get("amount", "").strip() or "0")
        except InvalidOperation:
            amount = Decimal("0")
        try:
            day = date.fromisoformat(request.POST.get("date", ""))
        except ValueError:
            day = timezone.localdate()
        if not title:
            messages.error(request, "اكتب البند (المصروف ده على إيه)")
        elif amount <= 0:
            messages.error(request, "اكتب مبلغ صحيح أكبر من صفر")
        else:
            Expense.objects.create(title=title, amount=amount, date=day)
            messages.success(request, f"اتسجل مصروف {title}: {amount:,.2f}")
            return redirect("quick_expense")
    recent_titles = list(dict.fromkeys(
        Expense.objects.filter(custody_clearance__isnull=True).order_by("-id")
        .values_list("title", flat=True)[:200]))[:12]
    today = timezone.localdate()
    context = {
        **admin.site.each_context(request),
        "title": "تسجيل مصروف",
        "recent_titles": recent_titles,
        "recent": Expense.objects.order_by("-date", "-id")[:8],
        "today": today.isoformat(),
    }
    return render(request, "trade/quick_expense.html", context)
