"""إذن الصرف: ورقة للمخزن بالأصناف اللي هتخرج لكل فاتورة بيع (من غير أسعار).
فيها: التاريخ، العميل، كود الصنف، اسم الصنف، اللون، المقاس، الكمية. مش بتحرّك المخزون (الفاتورة هي اللي بتخصم)."""
from datetime import date

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from .models import SaleInvoice
from .pos_views import SHOP_NAME


def _allowed(user):
    return user.has_perm("trade.view_variant") or user.has_perm("trade.view_saleinvoice")


def _rows(inv):
    rows = {}
    for i in inv.items.select_related("variant__model").order_by("variant__model__code", "variant__color", "id"):
        v = i.variant
        key = (v.model.code, v.color, v.size)
        r = rows.setdefault(key, {"code": v.model.code, "name": v.model.name, "color": v.color,
                                  "size": v.size, "qty": 0})
        r["qty"] += i.quantity
    return list(rows.values())


@staff_member_required
def dispatch_list(request):
    if not _allowed(request.user):
        raise PermissionDenied
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate()
    q = request.GET.get("q", "").strip()
    qs = (SaleInvoice.objects.filter(is_confirmed=True, date=day).select_related("customer")
          .prefetch_related("items").order_by("id"))
    if q:
        qs = qs.filter(customer__name__icontains=q) | qs.filter(pk=int(q) if q.isdigit() else 0)
    invoices = list(qs)
    return render(request, "trade/dispatch_list.html", {
        **admin.site.each_context(request), "title": "إذن صرف", "invoices": invoices, "day": day, "q": q,
        "ids": ",".join(str(i.pk) for i in invoices)})


@staff_member_required
def dispatch_print(request):
    """إذن صرف لفاتورة أو أكتر (?ids=1,2,3) — كل إذن في صفحة"""
    if not _allowed(request.user):
        raise PermissionDenied
    ids = [int(x) for x in request.GET.get("ids", "").split(",") if x.strip().isdigit()]
    invoices = (SaleInvoice.objects.filter(pk__in=ids, is_confirmed=True).select_related("customer")
                .order_by("id"))
    notes = [{"inv": inv, "rows": _rows(inv)} for inv in invoices]
    for n in notes:
        n["pieces"] = sum(r["qty"] for r in n["rows"])
    return render(request, "trade/dispatch_print.html", {"notes": notes, "shop_name": SHOP_NAME})
