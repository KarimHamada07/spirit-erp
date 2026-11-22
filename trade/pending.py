"""الحاجات اللي مستنية حد يتصرف فيها، حسب صلاحيات كل مستخدم.

بتظهر في مكانين: أرقام حمرا جنب الصفحة في القايمة الجانبية، وكارت "محتاج تتصرف فيه" في لوحة التحكم.
كل عنصر: (المفتاح في القايمة، الاسم، العدد، الرابط، الأيقونة). المفتاح هو نفس الـ target في menu.MENU
عشان الرقم يتحط جنب الصفحة الصح.
"""
from django.db.models import OuterRef, Subquery
from django.urls import reverse
from django.utils import timezone

from .models import (CustodyClearance, Customer, DebtFollowUp, LeaveRequest, OnlineOrder,
                     PurchaseInvoice, SaleInvoice, StockCount, WebOrder)


def _due_followups(today):
    """العملاء اللي ميعاد متابعة تحصيلهم جه (آخر متابعة ليهم) ولسه عليهم فلوس"""
    latest = DebtFollowUp.objects.filter(customer=OuterRef("pk")).order_by("-created_at", "-id")
    ids = list(Customer.objects.annotate(nd=Subquery(latest.values("next_date")[:1]))
               .filter(nd__isnull=False, nd__lte=today).values_list("pk", flat=True))
    if not ids:
        return 0
    return sum(1 for c in Customer.objects.with_balance().filter(pk__in=ids) if c.balance > 0)


def pending_items(user):
    """[{key, label, count, url, icon}] للحاجات اللي عددها أكبر من صفر بس"""
    cached = getattr(user, "_erp_pending", None)
    if cached is not None:
        return cached
    today = timezone.localdate()
    out = []

    def add(key, label, count, url, icon):
        if count:
            out.append({"key": key, "label": label, "count": count, "url": url, "icon": icon})

    if user.has_perm("trade.approve_saleinvoice"):
        add("sale_approvals", "فواتير بيع مستنية موافقتك (حد الائتمان أو التاريخ)",
            SaleInvoice.objects.filter(approval="pending").count(), reverse("sale_approvals"), "fas fa-stamp")
    if user.has_perm("trade.change_weborder"):
        add("store_orders", "طلبات جديدة من الموقع محتاجة تأكيد",
            WebOrder.objects.filter(status=WebOrder.NEW).count(),
            reverse("store_orders"), "fas fa-globe")
    from .employee_views import leave_approver_qs
    add("leaves", "طلبات إجازة مستنية موافقتك", leave_approver_qs(user).count(),
        reverse("leaves"), "fas fa-umbrella-beach")
    if user.has_perm("trade.approve_custodyclearance"):
        add("custody_approvals", "طلبات تصفية عهدة مستنية موافقتك",
            CustodyClearance.objects.filter(status=CustodyClearance.PENDING).count(),
            reverse("custody_approvals"), "fas fa-clipboard-check")
    if user.has_perm("trade.view_debtfollowup"):
        add("debts", "عملاء ميعاد متابعة تحصيلهم جه", _due_followups(today),
            reverse("debts"), "fas fa-phone-volume")
    if user.has_perm("trade.view_onlineorder"):
        add("online_list", "أوردرات أونلاين متجهزة ولسه ما اتشحنتش",
            OnlineOrder.objects.filter(status=OnlineOrder.PREPARED).count(),
            reverse("online_list") + f"?status={OnlineOrder.PREPARED}", "fas fa-truck-fast")
    if user.has_perm("trade.change_purchaseinvoice"):
        add("admin:trade.purchaseinvoice", "فواتير توريد مستنية تأكيد استلام الكمية",
            PurchaseInvoice.objects.filter(is_confirmed=False).count(),
            reverse("admin:trade_purchaseinvoice_changelist") + "?status=pending", "fas fa-dolly")
    if user.has_perm("trade.change_stockcount"):
        add("stock_counts", "جرد شغال لسه ما اتطبقش",
            StockCount.objects.filter(status=StockCount.DRAFT).count(),
            reverse("stock_counts"), "fas fa-clipboard-list")
    try:
        user._erp_pending = out
    except AttributeError:
        pass
    return out


def badge_counts(user):
    return {i["key"]: i["count"] for i in pending_items(user)}
