from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from .models import CashBox, Customer, SaleInvoice, Seller
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
        rows = [c for c in rows if ql in c.name.lower() or ql in (c.shop_name or "").lower()
                or q in (c.phone or "") or q in (c.other_phones or "") or ql in (c.area or "").lower()]
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
        total = inv.total - inv.returned_on_account  # المرتجع على الحساب بيقلل الفاتورة
        paid = sum((p.amount for p in inv.payments.all()), ZERO)
        invoice_rows.append({
            "invoice": inv, "total": total, "paid": paid,
            "remaining": total - paid,
        })

    payments = customer.payments.select_related("invoice", "box").order_by("-date", "-id")

    # الأسعار الثابتة للعميل (آخر سعر لكل صنف) وآخر فواتيره كلها (كاش وآجل)
    from .models import DebtFollowUp, SaleItem, Variant
    from .pos_views import customer_last_prices
    last = customer_last_prices(customer)
    variants = {v.pk: v for v in Variant.objects.filter(pk__in=last).select_related("model")}
    last_dates = {}
    for vid, d in (SaleItem.objects.filter(invoice__customer=customer, invoice__is_confirmed=True)
                   .order_by("invoice__date", "invoice_id").values_list("variant_id", "invoice__date")):
        last_dates[vid] = d
    fixed_prices = sorted(
        ({"variant": variants[vid], "price": price, "date": last_dates.get(vid)}
         for vid, price in last.items() if vid in variants),
        key=lambda r: (r["variant"].model.name, r["variant"].size, r["variant"].color))
    recent_invoices = (SaleInvoice.objects.filter(customer=customer)
                       .prefetch_related("items").order_by("-date", "-id")[:10])
    followup = DebtFollowUp.objects.filter(customer=customer).first()

    context = {
        **admin.site.each_context(request),
        "title": customer.name,
        "customer": customer,
        "fixed_prices": fixed_prices,
        "recent_invoices": recent_invoices,
        "followup": followup,
        "wa_chat": _wa_chat(customer),
        "wa_statement": _wa_statement(request, customer),
        "invoice_rows": invoice_rows,
        "open_invoices": [r for r in invoice_rows if r["remaining"] > 0],
        "payments": payments,
        "today": timezone.localdate().isoformat(),
        "boxes": CashBox.objects.filter(is_active=True).exclude(method="shipping"),
        "sellers": Seller.objects.filter(is_active=True).order_by("name"),
        "can_change_seller": request.user.has_perm("trade.change_customer_seller"),
    }
    return render(request, "trade/customer_detail.html", context)


STATEMENT_SALT = "statement-share"


def _wa_chat(customer):
    from .pos_views import wa_phone
    phone = wa_phone(customer.phone)
    return f"https://wa.me/{phone}" if phone else ""


def _wa_statement(request, customer):
    """رسالة واتساب جاهزة فيها رصيد العميل ورابط كشف حسابه (PDF، الرابط شغال 30 يوم)"""
    from urllib.parse import quote

    from django.core import signing
    from django.urls import reverse

    from .pos_views import SHOP_NAME, wa_phone
    token = signing.dumps(customer.pk, salt=STATEMENT_SALT, compress=True)
    link = request.build_absolute_uri(reverse("statement_public", args=[token]))
    text = (f"أهلاً {customer.name} 👋\nده كشف حسابك من {SHOP_NAME}.\n"
            f"الرصيد المتبقي: {customer.balance:,.2f} ج.م\nتقدر تشوف الكشف وتنزّله من هنا:\n{link}")
    return f"https://wa.me/{wa_phone(customer.phone)}?text={quote(text)}"


def statement_public(request, token):
    """كشف حساب العميل من الرابط اللي اتبعتله على واتساب (من غير دخول)"""
    from django.core import signing
    from django.http import Http404

    from .statements import customer_statement
    try:
        pk = signing.loads(token, salt=STATEMENT_SALT, max_age=60 * 60 * 24 * 30)
    except signing.BadSignature:
        raise Http404
    customer = get_object_or_404(Customer, pk=pk)
    resp = statement_response(customer_statement(customer), "pdf", f"statement-{customer.pk}")
    resp["X-Robots-Tag"] = "noindex"
    return resp


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


def take_payment(request, customer):
    """تسجيل تحصيل من عميل من فورم (صفحة العميل أو التحصيل السريع). بترجع (تمام؟، الرسالة)"""
    try:
        amount = Decimal(request.POST.get("amount", "0").strip() or "0")
    except InvalidOperation:
        amount = ZERO
    if amount <= 0:
        return False, "اكتب مبلغ صحيح أكبر من صفر"
    if amount > customer.balance:
        return False, f"المبلغ أكبر من المتبقي على العميل ({customer.balance:.2f})"

    invoice = None
    invoice_id = request.POST.get("invoice")
    if invoice_id:
        invoice = SaleInvoice.objects.filter(
            pk=invoice_id, customer=customer, payment_type="credit",
            is_confirmed=True).first()
        if invoice is None:
            return False, "الفاتورة المختارة غير صحيحة"

    box = CashBox.objects.filter(pk=request.POST.get("box"), is_active=True).exclude(
        method="shipping").first()
    if box is None:
        return False, "اختار الخزنة اللي استلمت فيها الفلوس"
    try:
        pay_date = date.fromisoformat(request.POST.get("date", ""))
    except ValueError:
        pay_date = timezone.localdate()

    record_customer_payment(customer, box, amount, pay_date, invoice=invoice,
                            notes=request.POST.get("notes", "").strip()[:200])
    return True, f"تم تسجيل دفعة {amount:.2f} من {customer.name}"


@staff_member_required
@require_POST
def customer_pay(request, pk):
    if not request.user.has_perm("trade.add_payment"):
        raise PermissionDenied
    customer = get_object_or_404(Customer.objects.with_balance(), pk=pk)
    ok, msg = take_payment(request, customer)
    (messages.success if ok else messages.error)(request, msg)
    return redirect("customer_detail", pk=pk)


def _period(request):
    def d(key):
        try:
            return date.fromisoformat(request.GET.get(key, ""))
        except ValueError:
            return None
    d1, d2 = d("from"), d("to")
    if d1 and d2 and d2 < d1:
        d1, d2 = d2, d1
    return d1, d2


def statement_response(st, fmt, filename):
    """كشف الحساب كـ PDF أو Excel"""
    from .exports import xlsx_response
    from .statements import statement_sheets
    if fmt == "xlsx":
        return xlsx_response(statement_sheets(st), filename)
    response = HttpResponse(statement_pdf(st), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response


@staff_member_required
def customer_statement_pdf(request, pk, fmt="pdf"):
    """كشف حساب العميل (PDF أو Excel): كل الفواتير والمرتجعات والدفعات مع الرصيد بعد كل حركة"""
    from .statements import customer_statement
    if not request.user.has_perm("trade.view_customer"):
        raise PermissionDenied
    customer = get_object_or_404(Customer, pk=pk)
    d1, d2 = _period(request)
    return statement_response(customer_statement(customer, d1, d2), fmt, f"statement-{customer.pk}")
