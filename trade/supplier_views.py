"""الموردين: الأرصدة وكشف الحساب"""
from decimal import Decimal

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from .customer_views import _period, statement_response
from .models import Supplier
from .statements import supplier_statement


@staff_member_required
def suppliers(request):
    if not request.user.has_perm("trade.view_supplier"):
        raise PermissionDenied
    q = request.GET.get("q", "").strip()
    rows = []
    for s in Supplier.objects.order_by("name"):
        if q and q.lower() not in s.name.lower() and q not in (s.phone or ""):
            continue
        rows.append({"supplier": s, "balance": s.balance})
    rows.sort(key=lambda r: (-r["balance"], r["supplier"].name))
    return render(request, "trade/suppliers.html", {
        **admin.site.each_context(request), "title": "الموردين", "rows": rows, "q": q,
        "payable": sum((r["balance"] for r in rows if r["balance"] > 0), Decimal("0")),
        "advances": -sum((r["balance"] for r in rows if r["balance"] < 0), Decimal("0")),
        "can_pay": request.user.has_perm("trade.add_supplierpayment"),
    })


@staff_member_required
def supplier_statement_view(request, pk, fmt="pdf"):
    if not request.user.has_perm("trade.view_supplier"):
        raise PermissionDenied
    supplier = get_object_or_404(Supplier, pk=pk)
    d1, d2 = _period(request)
    return statement_response(supplier_statement(supplier, d1, d2), fmt, f"supplier-{supplier.pk}")
