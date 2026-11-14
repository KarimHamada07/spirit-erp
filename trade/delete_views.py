from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .models import (ClothingModel, Customer, OnlineOrderItem, PurchaseInvoice,
                     PurchaseItem, SaleInvoice, SaleItem)


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


@staff_member_required
@require_POST
def customer_delete(request, pk):
    _need(request, "trade.delete_customer")
    c = get_object_or_404(Customer, pk=pk)
    if SaleInvoice.objects.filter(customer=c).exists() or c.payments.exists():
        messages.error(request, "مينفعش تحذف عميل ليه فواتير أو دفعات، عشان حساباته تفضل محفوظة.")
        return redirect("customer_detail", pk=pk)
    name = c.name
    c.delete()
    messages.success(request, f"تم حذف العميل {name}")
    return redirect("customers")


@staff_member_required
@require_POST
def model_delete(request, pk):
    _need(request, "trade.delete_clothingmodel")
    m = get_object_or_404(ClothingModel, pk=pk)
    used = (SaleItem.objects.filter(variant__model=m).exists()
            or PurchaseItem.objects.filter(variant__model=m).exists()
            or OnlineOrderItem.objects.filter(variant__model=m).exists()
            or any(v.quantity for v in m.variants.all()))
    if used:
        messages.error(request, "الموديل ده عليه حركة أو مخزون، فمينفعش يتحذف. "
                                "لو مش عايزه يظهر، عطّله من 'نشط' في صفحة تعديل الموديل.")
        return redirect("inventory_detail", pk=pk)
    try:
        name = m.name
        m.delete()
    except ProtectedError:
        messages.error(request, "الموديل ده مرتبط ببيانات تانية فمينفعش يتحذف.")
        return redirect("inventory_detail", pk=pk)
    messages.success(request, f"تم حذف الموديل {name}")
    return redirect("inventory")


@staff_member_required
@require_POST
def sale_delete(request, pk):
    _need(request, "trade.delete_saleinvoice")
    inv = get_object_or_404(SaleInvoice, pk=pk)
    if inv.is_confirmed:
        messages.error(request, "الفاتورة دي مؤكدة وأثّرت على المخزون، فمينفعش تتحذف.")
    else:
        inv.delete()
        messages.success(request, f"تم حذف مسودة فاتورة البيع #{pk}")
    return redirect("dashboard")


@staff_member_required
@require_POST
def purchase_delete(request, pk):
    _need(request, "trade.delete_purchaseinvoice")
    inv = get_object_or_404(PurchaseInvoice, pk=pk)
    if inv.is_confirmed:
        messages.error(request, "فاتورة التوريد دي مؤكدة وأثّرت على المخزون، فمينفعش تتحذف.")
    else:
        inv.delete()
        messages.success(request, f"تم حذف فاتورة التوريد #{pk}")
    return redirect("dashboard")
