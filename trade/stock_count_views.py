"""الجرد واقتراحات الطلب"""
import json
import math
from datetime import timedelta
from decimal import Decimal

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .exports import xlsx_response
from .models import (PurchaseInvoice, PurchaseItem, SaleItem, StockCount, StockCountLine, Supplier,
                     Variant)
from .notify import fmt, log_event


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


@staff_member_required
def stock_counts(request):
    _need(request, "trade.view_stockcount")
    if request.method == "POST":
        _need(request, "trade.add_stockcount")
        name = request.POST.get("name", "").strip()[:120] or f"جرد {timezone.localdate():%d/%m/%Y}"
        c = StockCount.objects.create(name=name, created_by=request.user)
        return redirect("stock_count_detail", pk=c.pk)
    return render(request, "trade/stock_counts.html", {
        **admin.site.each_context(request), "title": "الجرد",
        "counts": StockCount.objects.select_related("created_by", "applied_by")[:50]})


@staff_member_required
def stock_count_detail(request, pk):
    _need(request, "trade.view_stockcount")
    c = get_object_or_404(StockCount, pk=pk)
    lines = {ln.variant_id: ln for ln in c.lines.all()}
    variants = list(Variant.objects.filter(model__is_active=True).select_related("model")
                    .order_by("model__name", "size", "color"))
    data = [{"id": v.pk, "name": v.model.name, "code": v.model.code, "size": v.size, "color": v.color,
             "system": v.quantity, "counted": lines[v.pk].counted if v.pk in lines else None,
             "cost": float(v.avg_cost)} for v in variants]
    return render(request, "trade/stock_count_detail.html", {
        **admin.site.each_context(request), "title": c.name, "count": c, "variants": data,
        "editable": c.status == StockCount.DRAFT and request.user.has_perm("trade.change_stockcount"),
        "can_apply": request.user.has_perm("trade.apply_stockcount"),
        "applied_lines": _applied(c)})


def _applied(c):
    if c.status != StockCount.APPLIED:
        return []
    out = list(c.lines.select_related("variant__model"))
    for ln in out:
        ln.diff = ln.counted - (ln.expected or 0)
    return out


@staff_member_required
@require_POST
def stock_count_set(request, pk):
    _need(request, "trade.change_stockcount")
    c = get_object_or_404(StockCount, pk=pk, status=StockCount.DRAFT)
    try:
        body = json.loads(request.body)
        v = Variant.objects.get(pk=int(body["variant"]))
        value = body.get("counted")
    except (ValueError, KeyError, TypeError, Variant.DoesNotExist, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "بيانات غير صحيحة"}, status=400)
    if value in (None, ""):
        StockCountLine.objects.filter(count=c, variant=v).delete()
        return JsonResponse({"ok": True, "counted": None})
    try:
        value = max(0, int(value))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "اكتب رقم"}, status=400)
    StockCountLine.objects.update_or_create(count=c, variant=v, defaults={"counted": value})
    return JsonResponse({"ok": True, "counted": value})


@staff_member_required
@require_POST
def stock_count_action(request, pk):
    c = get_object_or_404(StockCount, pk=pk)
    action = request.POST.get("action")
    if action == "apply":
        _need(request, "trade.apply_stockcount")
        try:
            qty, value = c.apply(request.user)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect("stock_count_detail", pk=pk)
        sign = "+" if qty >= 0 else ""
        log_event(f"📋 اتطبق {c.name}: {c.lines.count()} صنف — الفرق {sign}{qty} قطعة "
                  f"(قيمته بالتكلفة {fmt(value)})")
        messages.success(request, f"اتطبق الجرد: الفرق {sign}{qty} قطعة بقيمة {fmt(value)}")
    elif action == "cancel":
        _need(request, "trade.change_stockcount")
        if c.status == StockCount.DRAFT:
            c.status = StockCount.CANCELLED
            c.save(update_fields=["status"])
            messages.info(request, "اتلغى الجرد")
    return redirect("stock_count_detail", pk=pk)


# ================= اقتراحات الطلب =================
def reorder_rows(days=30, cover=30):
    since = timezone.localdate() - timedelta(days=days)
    sold = dict(SaleItem.objects.filter(invoice__is_confirmed=True, invoice__date__gte=since)
                .values_list("variant").annotate(s=Sum("quantity")))
    last_buy = {}
    for it in (PurchaseItem.objects.filter(invoice__is_confirmed=True)
               .select_related("invoice__supplier").order_by("invoice__date", "id")):
        last_buy[it.variant_id] = (it.invoice.supplier, it.unit_cost)
    groups = {}
    for v in Variant.objects.filter(model__is_active=True).select_related("model"):
        s = sold.get(v.pk, 0)
        need = math.ceil(s * cover / days) + v.model.low_stock_level - v.quantity
        if need <= 0 or (s == 0 and v.quantity > v.model.low_stock_level):
            continue
        supplier, cost = last_buy.get(v.pk, (None, v.avg_cost))
        key = supplier.pk if supplier else 0
        g = groups.setdefault(key, {"supplier": supplier, "rows": [], "total": Decimal("0")})
        g["rows"].append({"variant": v, "sold": s, "qty": v.quantity, "suggest": need, "cost": cost,
                          "value": cost * need})
        g["total"] += cost * need
    out = sorted(groups.values(), key=lambda g: (g["supplier"] is None, -g["total"]))
    for g in out:
        g["rows"].sort(key=lambda r: (-r["sold"], str(r["variant"])))
    return out


@staff_member_required
def reorder(request):
    _need(request, "trade.view_purchaseinvoice")
    try:
        days = max(7, min(int(request.GET.get("days", 30)), 180))
        cover = max(7, min(int(request.GET.get("cover", 30)), 180))
    except ValueError:
        days = cover = 30
    groups = reorder_rows(days, cover)
    if request.GET.get("export") == "xlsx":
        return xlsx_response([("اقتراحات الطلب", ["المورد", "الموديل", "المقاس", "اللون", f"اتباع في {days} يوم",
                                                   "الموجود", "المقترح", "آخر سعر شراء", "القيمة"],
                               [[str(g["supplier"] or "من غير مورد"), r["variant"].model.name, r["variant"].size,
                                 r["variant"].color, r["sold"], r["qty"], r["suggest"], r["cost"], r["value"]]
                                for g in groups for r in g["rows"]])], f"reorder_{timezone.localdate()}")
    return render(request, "trade/reorder.html", {
        **admin.site.each_context(request), "title": "اقتراحات الطلب", "groups": groups, "days": days,
        "cover": cover, "can_create": request.user.has_perm("trade.add_purchaseinvoice")})


@staff_member_required
@require_POST
def reorder_create(request):
    _need(request, "trade.add_purchaseinvoice")
    supplier = get_object_or_404(Supplier, pk=request.POST.get("supplier"))
    days = int(request.POST.get("days", 30))
    cover = int(request.POST.get("cover", 30))
    group = next((g for g in reorder_rows(days, cover) if g["supplier"] and g["supplier"].pk == supplier.pk), None)
    if not group:
        messages.info(request, "مفيش أصناف مقترحة للمورد ده")
        return redirect("reorder")
    inv = PurchaseInvoice.objects.create(supplier=supplier, date=timezone.localdate(),
                                         notes="مسودة من اقتراحات الطلب")
    for r in group["rows"]:
        PurchaseItem.objects.create(invoice=inv, variant=r["variant"], quantity=r["suggest"], unit_cost=r["cost"])
    messages.success(request, f"اتعملت فاتورة توريد مسودة لـ {supplier} — راجع الكميات وأكّد لما البضاعة توصل")
    return redirect(reverse("admin:trade_purchaseinvoice_change", args=[inv.pk]))
