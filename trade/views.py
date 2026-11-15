from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, DecimalField, F, Sum
from django.shortcuts import render
from django.utils import timezone

from .models import (ClothingModel, Customer, Expense, OnlineOrder,
                     PurchaseInvoice, SaleInvoice, SaleItem, Variant)
from .custody_views import custody_summary
from .stock_views import _size_key

DEC = DecimalField(max_digits=14, decimal_places=2)


def _period(start, end):
    """مبيعات وربح فترة (فواتير مؤكدة فقط)"""
    items = SaleItem.objects.filter(
        invoice__is_confirmed=True, invoice__date__range=(start, end))
    agg = items.aggregate(
        rev=Sum(F("quantity") * F("unit_price"), output_field=DEC),
        cost=Sum(F("quantity") * F("unit_cost"), output_field=DEC),
        pcs=Sum("quantity"),
    )
    invoices = SaleInvoice.objects.filter(
        is_confirmed=True, date__range=(start, end))
    discount = invoices.aggregate(d=Sum("discount"))["d"] or 0
    revenue = (agg["rev"] or 0) - discount
    profit = revenue - (agg["cost"] or 0)
    pieces = agg["pcs"] or 0

    # الأونلاين: التحصيل بيدخل المبيعات والربح، والمرتجع بيخصم تكلفة الشحن
    online = (OnlineOrder.objects
              .filter(status__in=[OnlineOrder.COLLECTED, OnlineOrder.RETURNED],
                      status_at__date__range=(start, end))
              .prefetch_related("items"))
    for o in online:
        profit += o.profit
        if o.status == OnlineOrder.COLLECTED:
            revenue += o.items_total - o.discount
            pieces += o.pieces

    return {
        "revenue": revenue,
        "profit": profit,
        "pieces": pieces,
        "invoices": invoices.count(),
    }


def dashboard_sections(user):
    """كل واحد بيشوف في لوحة التحكم اللي يخص شغله بس (حسب صلاحياته)"""
    return {
        "sales": user.has_perm("trade.view_saleinvoice"),
        "money": user.has_perm("trade.view_expense"),      # الربح والمصروفات وقيمة المخزون
        "stock": user.has_perm("trade.view_variant"),
        "customers": user.has_perm("trade.view_customer"),
        "online": user.has_perm("trade.view_onlineorder"),
        "purchases": user.has_perm("trade.view_purchaseinvoice"),
        "mine": getattr(user, "seller_profile", None) is not None,
    }


def can_see_dashboard(user):
    sec = dashboard_sections(user)
    return any(sec[k] for k in ("sales", "money", "stock", "customers", "online"))


def _my_work(user, today):
    """البياع: مبيعاته النهارده، وعمولته وتحصيلاته الشهر ده، ومديونية عملاءه"""
    from .treasury import commission_report
    seller = user.seller_profile
    inv = SaleInvoice.objects.filter(is_confirmed=True, seller=seller, date=today).prefetch_related("items")
    rep = commission_report(today.year, today.month)
    row = next((r for r in rep["rows"] if r["seller"] == seller.name), None)
    debtors = [c for c in Customer.objects.with_balance().filter(seller=seller) if c.balance > 0]
    return {
        "seller": seller,
        "today_total": sum((i.total for i in inv), 0),
        "today_count": len(inv),
        "month_collected": row["total"] if row else 0,
        "month_commission": row["commission"] if row else 0,
        "debt": sum((c.balance for c in debtors), 0),
        "debtors": len(debtors),
    }


@staff_member_required
def dashboard(request):
    from django.shortcuts import redirect

    from .pending import pending_items
    user = request.user
    # الموظف اللي مالوش أي صلاحية على الشغل (بيستخدم السيستم للبصمة) بيروح على صفحته
    if not can_see_dashboard(user):
        return redirect("my_attendance")
    sec = dashboard_sections(user)
    today = timezone.localdate()
    month_start = today.replace(day=1)
    ctx = {"sec": sec, "pending": pending_items(user)}

    if sec["mine"]:
        ctx["mine"] = _my_work(user, today)

    if sec["sales"]:
        ctx["today"] = _period(today, today)
        ctx["recent_sales"] = (SaleInvoice.objects.select_related("customer")
                               .prefetch_related("items").order_by("-id")[:8])

    if sec["money"]:
        month_stats = _period(month_start, today)
        month_expenses = Expense.objects.filter(
            date__range=(month_start, today)).aggregate(s=Sum("amount"))["s"] or 0
        stock = Variant.objects.aggregate(
            value=Sum(F("quantity") * F("avg_cost"), output_field=DEC), pieces=Sum("quantity"))
        labels, revenue, profit = [], [], []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            p = _period(d, d)
            labels.append(d.strftime("%d/%m"))
            revenue.append(float(p["revenue"]))
            profit.append(float(p["profit"]))
        ctx.update({
            "month": month_stats, "month_expenses": month_expenses,
            "net_profit": month_stats["profit"] - month_expenses,
            "stock_value": stock["value"] or 0, "stock_pieces": stock["pieces"] or 0,
            "chart": {"labels": labels, "revenue": revenue, "profit": profit},
            "top_models": (SaleItem.objects
                           .filter(invoice__is_confirmed=True, invoice__date__gte=today - timedelta(days=30))
                           .values("variant__model__name").annotate(qty=Sum("quantity")).order_by("-qty")[:5]),
        })
        if user.has_perm("trade.view_custodysettlement"):
            ctx["custody_balance"] = custody_summary()["balance"]

    if sec["stock"]:
        ctx["low_stock"] = (Variant.objects
                            .filter(quantity__lte=F("model__low_stock_level"), model__is_active=True)
                            .select_related("model").order_by("quantity")[:10])
        inventory_overview = []
        for m in (ClothingModel.objects.filter(is_active=True)
                  .prefetch_related("variants").order_by("name")):
            sizes = {}
            for v in sorted(m.variants.all(), key=_size_key):
                sizes[v.size] = sizes.get(v.size, 0) + v.quantity
            inventory_overview.append({
                "model": m, "total": sum(sizes.values()),
                "sizes": [{"size": k, "qty": q, "low": q <= m.low_stock_level} for k, q in sizes.items()],
            })
        ctx["inventory_overview"] = inventory_overview

    if sec["purchases"]:
        # فواتير التوريد اللي لسه ما اتأكدش استلام كميتها
        pending_qs = PurchaseInvoice.objects.filter(is_confirmed=False)
        ctx["pending_count"] = pending_qs.count()
        pending_purchases = []
        for inv in (pending_qs.select_related("supplier")
                    .prefetch_related("items__variant__model").order_by("-date", "-id")[:10]):
            items = list(inv.items.all())
            pending_purchases.append({
                "invoice": inv, "items": items, "qty": sum(i.quantity for i in items),
                "total": sum((i.quantity * i.unit_cost for i in items), 0),
            })
        ctx["pending_purchases"] = pending_purchases

    if sec["customers"]:
        debtors = [c for c in Customer.objects.with_balance() if c.balance > 0]
        ctx["receivable"] = sum((c.balance for c in debtors), 0)
        ctx["debtors_count"] = len(debtors)

    if sec["online"]:
        online_counts = dict(OnlineOrder.objects.values_list("status").annotate(c=Count("id")))
        ctx["online_cards"] = [{"key": key, "label": label, "count": online_counts.get(key, 0)}
                               for key, label in OnlineOrder.STATUS_CHOICES]

    hour = timezone.localtime().hour
    ctx.update({
        **admin.site.each_context(request),
        "title": "لوحة التحكم",
        "greeting": "صباح الخير" if hour < 12 else "مساء الخير",
        "display_name": user.first_name or user.get_username(),
    })
    return render(request, "trade/dashboard.html", ctx)
