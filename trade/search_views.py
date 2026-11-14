from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.urls import reverse

from .models import (ClothingModel, Customer, OnlineOrder, PurchaseInvoice,
                     SaleInvoice)

LIMIT = 6


def _digits(q):
    return int(q) if q.isdigit() and len(q) <= 9 else None


@staff_member_required
def search_api(request):
    """بحث لحظي في الموديلات والعملاء والفواتير والأوردرات (بيرجع JSON)"""
    q = request.GET.get("q", "").strip()[:60]
    if not q:
        return JsonResponse({"groups": []})
    num = _digits(q)
    groups = []

    products = (ClothingModel.objects.filter(Q(name__icontains=q) | Q(code__icontains=q))
                .annotate(stock=Coalesce(Sum("variants__quantity"), 0)).order_by("name")[:LIMIT])
    groups.append(("الموديلات", [
        {"label": m.name, "sub": f"{m.code} · {m.stock} قطعة",
         "url": reverse("inventory_detail", args=[m.pk])} for m in products]))

    customers = Customer.objects.filter(Q(name__icontains=q) | Q(phone__icontains=q)).order_by("name")[:LIMIT]
    groups.append(("العملاء", [
        {"label": c.name, "sub": c.phone or "", "url": reverse("customer_detail", args=[c.pk])}
        for c in customers]))

    cond = Q(customer__name__icontains=q)
    if num is not None:
        cond |= Q(pk=num)
    invoices = (SaleInvoice.objects.filter(cond).select_related("customer")
                .prefetch_related("items").order_by("-id")[:LIMIT])
    groups.append(("فواتير البيع", [{
        "label": f"فاتورة بيع #{i.pk}" + (f" — {i.customer.name}" if i.customer_id else ""),
        "sub": f"{i.date:%d/%m/%Y} · {i.total:,.2f}" + ("" if i.is_confirmed else " · مسودة"),
        "url": reverse("sale_print", args=[i.pk]) if i.is_confirmed
        else reverse("admin:trade_saleinvoice_change", args=[i.pk])} for i in invoices]))

    cond = Q(supplier__name__icontains=q)
    if num is not None:
        cond |= Q(pk=num)
    purchases = PurchaseInvoice.objects.filter(cond).select_related("supplier").order_by("-id")[:LIMIT]
    groups.append(("فواتير التوريد", [{
        "label": f"توريد #{p.pk} — {p.supplier}",
        "sub": f"{p.date:%d/%m/%Y}" + ("" if p.is_confirmed else " · بانتظار التأكيد"),
        "url": reverse("admin:trade_purchaseinvoice_change", args=[p.pk])} for p in purchases]))

    cond = Q(customer_name__icontains=q) | Q(phone__icontains=q)
    if num is not None:
        cond |= Q(pk=num)
    orders = OnlineOrder.objects.filter(cond).order_by("-id")[:LIMIT]
    groups.append(("الأونلاين", [{
        "label": f"أوردر #{o.pk} — {o.customer_name}",
        "sub": f"{o.phone} · {o.get_status_display()}",
        "url": reverse("online_detail", args=[o.pk])} for o in orders]))

    return JsonResponse({"groups": [{"title": t, "items": items} for t, items in groups if items]})
