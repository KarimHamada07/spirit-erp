from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from django.utils import timezone

from .models import (CashBox, CashEntry, Customer, SaleInvoice, Supplier)
from .treasury import (box_rows, commission_report, create_transfer, daily_close,
                       record_customer_payment, record_supplier_payment, record_supplier_refund,
                       shipping_outstanding)


def _money(raw):
    try:
        return Decimal(str(raw or "0").strip() or "0")
    except InvalidOperation:
        raise ValidationError("المبلغ غير صحيح")


def _date(raw):
    return parse_date(raw or "") or timezone.localdate()


@staff_member_required
def treasury(request):
    if not request.user.has_perm("trade.view_cashbox"):
        raise PermissionDenied
    box_id = request.GET.get("box", "")
    entries = CashEntry.objects.select_related("box", "customer", "supplier", "invoice")
    if box_id.isdigit():
        entries = entries.filter(box_id=box_id)
    context = {
        **admin.site.each_context(request),
        "title": "الخزينة",
        "boxes": box_rows(),
        "shipping_due": shipping_outstanding(),
        "entries": entries[:150],
        "selected_box": box_id,
        "customers": Customer.objects.order_by("name"),
        "suppliers": Supplier.objects.order_by("name"),
        "pay_boxes": CashBox.objects.filter(is_active=True).exclude(method="shipping"),
        "all_boxes": CashBox.objects.filter(is_active=True),
        "invoices": SaleInvoice.objects.filter(is_confirmed=True, payment_type="credit").order_by("-date", "-id")[:300],
        "today": timezone.localdate().isoformat(),
    }
    return render(request, "trade/treasury.html", context)


@staff_member_required
@require_POST
def treasury_action(request):
    action = request.POST.get("action", "")
    try:
        if action == "customer_pay":
            if not request.user.has_perm("trade.add_payment"):
                raise PermissionDenied
            customer = Customer.objects.filter(pk=request.POST.get("customer")).first()
            box = CashBox.objects.filter(pk=request.POST.get("box"), is_active=True).exclude(
                method="shipping").first()
            if customer is None or box is None:
                raise ValidationError("اختار العميل والخزنة")
            invoice = None
            if request.POST.get("invoice"):
                invoice = SaleInvoice.objects.filter(pk=request.POST["invoice"], customer=customer,
                                                     payment_type="credit", is_confirmed=True).first()
                if invoice is None:
                    raise ValidationError("الفاتورة المختارة مش بتاعة العميل")
            balance = Customer.objects.with_balance().get(pk=customer.pk).balance
            amount = _money(request.POST.get("amount"))
            if amount <= 0:
                raise ValidationError("اكتب مبلغ أكبر من صفر")
            if amount > balance:
                raise ValidationError(f"المبلغ أكبر من المتبقي على العميل ({balance:.2f})")
            record_customer_payment(customer, box, amount, _date(request.POST.get("date")),
                                    invoice=invoice, notes=request.POST.get("notes", "")[:200])
            messages.success(request, f"تم تسجيل تحصيل {amount:.2f} من {customer.name}")

        elif action == "supplier_pay":
            if not request.user.has_perm("trade.add_supplierpayment"):
                raise PermissionDenied
            supplier = Supplier.objects.filter(pk=request.POST.get("supplier")).first()
            box = CashBox.objects.filter(pk=request.POST.get("box"), is_active=True).exclude(
                method="shipping").first()
            if supplier is None or box is None:
                raise ValidationError("اختار المورد والخزنة")
            amount = _money(request.POST.get("amount"))
            if request.POST.get("kind") == "refund":
                record_supplier_refund(supplier, box, amount, _date(request.POST.get("date")),
                                       notes=request.POST.get("notes", "")[:200])
                msg = f"اتسجل استرداد {amount:.2f} من {supplier.name}"
            else:
                record_supplier_payment(supplier, box, amount, _date(request.POST.get("date")),
                                        notes=request.POST.get("notes", "")[:200])
                msg = f"تم سداد {amount:.2f} لـ {supplier.name}"
            b = supplier.balance
            msg += f" — رصيده دلوقتي: {'ليه عندنا' if b > 0 else 'لينا عنده' if b < 0 else 'خالص'} {abs(b):,.2f}"
            messages.success(request, msg)

        elif action == "transfer":
            if not request.user.has_perm("trade.add_cashtransfer"):
                raise PermissionDenied
            from_box = CashBox.objects.filter(pk=request.POST.get("from_box")).first()
            to_box = CashBox.objects.filter(pk=request.POST.get("to_box")).first()
            if from_box is None or to_box is None:
                raise ValidationError("اختار الخزنتين")
            create_transfer(from_box, to_box, _money(request.POST.get("amount")),
                            _date(request.POST.get("date")),
                            note=request.POST.get("note", ""), user=request.user)
            messages.success(request, f"تم التحويل من {from_box.name} إلى {to_box.name}")
        else:
            raise ValidationError("عملية غير معروفة")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    except PermissionDenied:
        messages.error(request, "مفيش صلاحية للعملية دي")
    return redirect("treasury")


def _month(raw):
    try:
        y, m = (raw or "").split("-")
        y, m = int(y), int(m)
        if not 1 <= m <= 12:
            raise ValueError
        return y, m
    except ValueError:
        today = timezone.localdate()
        return today.year, today.month


@staff_member_required
def daily_close_view(request):
    if not request.user.has_perm("trade.view_cashentry"):
        raise PermissionDenied
    day = _date(request.GET.get("day"))
    context = {**admin.site.each_context(request), "title": "تقفيل اليوم",
               "report": daily_close(day), "day": day.isoformat()}
    return render(request, "trade/daily_close.html", context)


@staff_member_required
def commissions_view(request):
    if not request.user.has_perm("trade.view_cashentry"):
        raise PermissionDenied
    year, month = _month(request.GET.get("month"))
    context = {**admin.site.each_context(request), "title": "العمولات",
               "report": commission_report(year, month),
               "month": f"{year:04d}-{month:02d}"}
    return render(request, "trade/commissions.html", context)
