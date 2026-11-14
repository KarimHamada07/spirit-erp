from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import CashBox, Customer, Payment, SaleInvoice, SaleReturn, Seller
from .pdf_export import statement_pdf
from .treasury import record_customer_payment

ZERO = Decimal("0")


@staff_member_required
def customers(request):
    q = request.GET.get("q", "").strip()
    only_debt = request.GET.get("debt") == "1"

    all_customers = list(Customer.objects.with_balance().order_by("name"))
    receivable = sum((c.balance for c in all_customers if c.balance > 0), ZERO)
    debtors = [c for c in all_customers if c.balance > 0]

    rows = all_customers
    if q:
        ql = q.lower()
        rows = [c for c in rows if ql in c.name.lower() or q in (c.phone or "")]
    if only_debt:
        rows = [c for c in rows if c.balance > 0]
    rows.sort(key=lambda c: (-c.balance, c.name))

    context = {
        **admin.site.each_context(request),
        "title": "العملاء والآجل",
        "rows": rows,
        "q": q,
        "only_debt": only_debt,
        "receivable": receivable,
        "debtors_count": len(debtors),
        "customers_count": len(all_customers),
        "sellers": Seller.objects.filter(is_active=True).order_by("name"),
    }
    return render(request, "trade/customers.html", context)


@staff_member_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer.objects.with_balance(), pk=pk)

    invoices = (SaleInvoice.objects
                .filter(customer=customer, is_confirmed=True, payment_type="credit")
                .prefetch_related("items", "payments")
                .order_by("-date", "-id"))
    invoice_rows = []
    for inv in invoices:
        total = inv.total
        paid = sum((p.amount for p in inv.payments.all()), ZERO)
        invoice_rows.append({
            "invoice": inv, "total": total, "paid": paid,
            "remaining": total - paid,
        })

    payments = customer.payments.select_related("invoice").order_by("-date", "-id")

    context = {
        **admin.site.each_context(request),
        "title": customer.name,
        "customer": customer,
        "invoice_rows": invoice_rows,
        "open_invoices": [r for r in invoice_rows if r["remaining"] > 0],
        "payments": payments,
        "today": date.today().isoformat(),
        "boxes": CashBox.objects.filter(is_active=True).exclude(method="shipping"),
        "sellers": Seller.objects.filter(is_active=True).order_by("name"),
        "can_change_seller": request.user.has_perm("trade.change_customer_seller"),
    }
    return render(request, "trade/customer_detail.html", context)


@staff_member_required
@require_POST
def customer_add(request):
    if not request.user.has_perm("trade.add_customer"):
        raise PermissionDenied
    name = request.POST.get("name", "").strip()
    phone = request.POST.get("phone", "").strip()
    seller = Seller.objects.filter(pk=request.POST.get("seller"), is_active=True).first()
    if not name:
        messages.error(request, "اسم العميل مطلوب")
        return redirect("customers")
    if seller is None:
        messages.error(request, "اختار البائع المرتبط بالعميل")
        return redirect("customers")
    customer = Customer.objects.create(name=name, phone=phone, seller=seller)
    messages.success(request, f"تمت إضافة العميل {customer.name} مرتبط بالبائع {seller.name}")
    return redirect("customer_detail", pk=customer.pk)


@staff_member_required
@require_POST
def customer_set_seller(request, pk):
    """تغيير البائع المرتبط بالعميل: مدير الحسابات بس"""
    if not request.user.has_perm("trade.change_customer_seller"):
        raise PermissionDenied
    customer = get_object_or_404(Customer, pk=pk)
    seller = Seller.objects.filter(pk=request.POST.get("seller"), is_active=True).first()
    if seller is None:
        messages.error(request, "اختار بائع صحيح")
    else:
        customer.seller = seller
        customer.save(update_fields=["seller"])
        messages.success(request, f"تم ربط {customer.name} بالبائع {seller.name}")
    return redirect("customer_detail", pk=pk)


@staff_member_required
@require_POST
def customer_pay(request, pk):
    if not request.user.has_perm("trade.add_payment"):
        raise PermissionDenied
    customer = get_object_or_404(Customer.objects.with_balance(), pk=pk)

    try:
        amount = Decimal(request.POST.get("amount", "0").strip() or "0")
    except InvalidOperation:
        amount = ZERO
    if amount <= 0:
        messages.error(request, "اكتب مبلغ صحيح أكبر من صفر")
        return redirect("customer_detail", pk=pk)
    if amount > customer.balance:
        messages.error(
            request, f"المبلغ أكبر من المتبقي على العميل ({customer.balance:.2f})")
        return redirect("customer_detail", pk=pk)

    invoice = None
    invoice_id = request.POST.get("invoice")
    if invoice_id:
        invoice = SaleInvoice.objects.filter(
            pk=invoice_id, customer=customer, payment_type="credit",
            is_confirmed=True).first()
        if invoice is None:
            messages.error(request, "الفاتورة المختارة غير صحيحة")
            return redirect("customer_detail", pk=pk)

    box = CashBox.objects.filter(pk=request.POST.get("box"), is_active=True).exclude(
        method="shipping").first()
    if box is None:
        messages.error(request, "اختار الخزنة اللي استلمت فيها الفلوس")
        return redirect("customer_detail", pk=pk)
    try:
        pay_date = date.fromisoformat(request.POST.get("date", ""))
    except ValueError:
        pay_date = date.today()

    record_customer_payment(customer, box, amount, pay_date, invoice=invoice,
                            notes=request.POST.get("notes", "").strip()[:200])
    messages.success(request, f"تم تسجيل دفعة {amount:.2f} من {customer.name}")
    return redirect("customer_detail", pk=pk)


@staff_member_required
def customer_statement_pdf(request, pk):
    """كشف حساب العميل PDF: كل الفواتير (آجل ونقدي) وكل الدفعات بالتاريخ مع الرصيد بعد كل حركة"""
    if not request.user.has_perm("trade.view_customer"):
        raise PermissionDenied
    customer = get_object_or_404(Customer, pk=pk)

    events = []  # (التاريخ، الترتيب، الحركة)
    credit_total = ZERO
    for inv in (SaleInvoice.objects.filter(customer=customer, is_confirmed=True)
                .prefetch_related("items", "payments")):
        total = inv.total
        if inv.payment_type == "credit":
            credit_total += total
            events.append((inv.date, 0, inv.pk, {
                "kind": "فاتورة آجل", "ref": f"فاتورة #{inv.pk}",
                "debit": total, "credit": ZERO}))
        else:
            # الكاش متسدد وقت البيع: بيظهر في الكشف بس ومش بيأثر على الرصيد
            events.append((inv.date, 0, inv.pk, {
                "kind": "فاتورة نقدي", "ref": f"فاتورة #{inv.pk}",
                "debit": total, "credit": total}))

    for ret in (SaleReturn.objects.filter(invoice__customer=customer, is_confirmed=True,
                                          refund_mode="account")
                .select_related("invoice")):
        amount = ret.total
        credit_total -= amount
        events.append((ret.date, 1, ret.pk, {
            "kind": "مرتجع", "ref": f"مرتجع #{ret.pk} (فاتورة #{ret.invoice_id})",
            "debit": ZERO, "credit": amount}))

    paid_total = ZERO
    for p in customer.payments.all():
        paid_total += p.amount
        ref = f"دفعة #{p.pk}"
        if p.invoice_id:
            ref += f" (فاتورة #{p.invoice_id})"
        events.append((p.date, 1, p.pk, {
            "kind": "دفعة", "ref": ref, "debit": ZERO, "credit": p.amount}))

    events.sort(key=lambda e: (e[0], e[1], e[2]))
    entries, running = [], ZERO
    for day, _, _, row in events:
        running += row["debit"] - row["credit"]
        entries.append({**row, "date": day, "balance": running})

    totals = {
        "credit_total": credit_total,
        "paid_total": paid_total,
        "balance": credit_total - paid_total,
        "as_of": date.today().isoformat(),
    }
    content = statement_pdf(customer, entries, totals)
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="statement-{customer.pk}.pdf"'
    return response
