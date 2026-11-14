"""الخزينة: كل حركة فلوس (بيع، تحصيل، سداد مورد، تحويل، مرتجع) بتمر من هنا.

الخزن: كاش لكل شخص (دالي، كريم، المحل)، إنستاباي، محفظة، تحويل بنكي، وشركة الشحن (المستحق عندها).
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from .models import (CashBox, CashEntry, CashTransfer, CommissionRate, OnlineOrder,
                     Payment, Seller, SaleInvoice, Supplier, SupplierPayment)

ZERO = Decimal("0")

DEFAULT_BOXES = [
    ("كاش دالي", "cash", "دالي"),
    ("كاش كريم", "cash", "كريم"),
    ("كاش المحل", "cash", "المحل"),
    ("إنستاباي دالي", "instapay", "دالي"),
    ("محفظة كريم", "wallet", "كريم"),
    ("تحويل بنكي", "bank", "المحل"),
    ("شركة الشحن", "shipping", "شركة الشحن"),
]
DEFAULT_SELLERS = [
    ("خالد فلكس", True),
    ("عمرو عصام", True),
    ("محمد أسامة", True),
    ("مباشر", False),
]
# نوع الخزنة -> طريقة الدفع المسجلة على الدفعة
PAYMENT_METHOD_FOR_BOX = {"cash": "cash", "instapay": "instapay", "wallet": "wallet",
                          "bank": "transfer", "shipping": "shipping"}


def ensure_defaults(using="default", **kwargs):
    """بيتنفذ بعد كل migrate: يضيف الخزن والبائعين ونسب العمولة لو مش موجودين (من غير ما يمسح أي تعديل)"""
    for name, method, holder in DEFAULT_BOXES:
        CashBox.objects.get_or_create(name=name, defaults={"method": method, "holder": holder})
    for name, commissionable in DEFAULT_SELLERS:
        Seller.objects.get_or_create(name=name, defaults={"is_commissionable": commissionable})
    for channel, _label in CommissionRate.CHANNELS:
        CommissionRate.objects.get_or_create(channel=channel)


@transaction.atomic
def record_sale_payments(invoice, allocations):
    """allocations = [(box, amount)]. الكاش لازم يتدفع كله. الآجل ممكن يتدفع جزء ويفضل الباقي على العميل."""
    total = invoice.total
    paid = sum((amt for _, amt in allocations), ZERO)
    if invoice.payment_type == "cash":
        if paid != total:
            raise ValidationError(f"مجموع طرق الدفع ({paid:.2f}) لازم يساوي إجمالي الفاتورة ({total:.2f})")
        for box, amt in allocations:
            if amt > 0:
                CashEntry.post(box, amt, "sale", f"بيع #{invoice.pk}",
                               invoice=invoice, customer=invoice.customer)
        return
    if paid > total:
        raise ValidationError("المدفوع أكبر من إجمالي الفاتورة")
    for box, amt in allocations:
        if amt > 0:
            record_customer_payment(invoice.customer, box, amt, invoice.date,
                                    invoice=invoice, notes="مقدم")


@transaction.atomic
def record_customer_payment(customer, box, amount, pay_date=None, invoice=None, notes=""):
    """تحصيل من عميل: بيتسجل كدفعة على حسابه (وبيسقط من كشف حسابه) وكحركة في الخزنة"""
    amount = Decimal(amount)
    if amount <= 0:
        raise ValidationError("المبلغ لازم يكون أكبر من صفر")
    pay_date = pay_date or date.today()
    payment = Payment.objects.create(
        customer=customer, invoice=invoice, box=box, amount=amount, date=pay_date,
        method=PAYMENT_METHOD_FOR_BOX[box.method], notes=notes[:200])
    CashEntry.post(box, amount, "payment", f"تحصيل من {customer.name}", date=pay_date,
                   invoice=invoice, customer=customer)
    return payment


@transaction.atomic
def record_supplier_payment(supplier, box, amount, pay_date=None, notes=""):
    amount = Decimal(amount)
    if amount <= 0:
        raise ValidationError("المبلغ لازم يكون أكبر من صفر")
    if amount > supplier.balance:
        raise ValidationError(f"المبلغ أكبر من المستحق للمورد ({supplier.balance:.2f})")
    pay_date = pay_date or date.today()
    payment = SupplierPayment.objects.create(supplier=supplier, box=box, amount=amount,
                                             date=pay_date, notes=notes[:200])
    CashEntry.post(box, -amount, "supplier_payment", f"سداد {supplier.name}", date=pay_date,
                   supplier=supplier)
    return payment


@transaction.atomic
def create_transfer(from_box, to_box, amount, transfer_date=None, note="", user=None):
    """تحويل بين خزنتين (نقل العهد). ولاستلام فلوس من شركة الشحن: تحويل من خزنة الشحن للخزنة اللي استلمت"""
    amount = Decimal(amount)
    if amount <= 0:
        raise ValidationError("المبلغ لازم يكون أكبر من صفر")
    transfer_date = transfer_date or date.today()
    t = CashTransfer(from_box=from_box, to_box=to_box, amount=amount, date=transfer_date,
                     note=note[:200], created_by=user)
    t.clean()
    t.save()
    CashEntry.post(from_box, -amount, "transfer", f"تحويل إلى {to_box}", date=transfer_date,
                   transfer=t)
    CashEntry.post(to_box, amount, "transfer", f"تحويل من {from_box}", date=transfer_date,
                   transfer=t)
    return t


def box_rows():
    return [{"box": b, "balance": b.balance} for b in CashBox.objects.filter(is_active=True)]


def shipping_outstanding():
    """الفلوس اللي شركة الشحن لسه متحصلتش منها"""
    box = CashBox.objects.filter(method="shipping", is_active=True).first()
    return box.balance if box else ZERO


def daily_close(day):
    """تقفيل اليوم: الحركات (من غير التحويلات الداخلية) مجمعة بطريقة الدفع وبالشخص المسؤول، وبكل خزنة"""
    entries = (CashEntry.objects.filter(date=day).select_related("box"))
    flows = entries.exclude(kind="transfer")

    by_method = {}
    by_holder = {}
    for e in flows:
        m = e.box.get_method_display()
        by_method[m] = by_method.get(m, ZERO) + e.amount
        h = e.box.holder or "—"
        by_holder.setdefault(h, {})
        by_holder[h][m] = by_holder[h].get(m, ZERO) + e.amount

    by_box = {}
    for e in entries:
        row = by_box.setdefault(e.box.pk, {"box": e.box, "in": ZERO, "out": ZERO, "transfers": ZERO})
        if e.kind == "transfer":
            row["transfers"] += e.amount
        elif e.amount >= 0:
            row["in"] += e.amount
        else:
            row["out"] += e.amount

    return {
        "day": day,
        "by_method": sorted(by_method.items()),
        "by_holder": sorted(by_holder.items()),
        "by_box": sorted(by_box.values(), key=lambda r: r["box"].pk),
        "net": sum((e.amount for e in flows), ZERO),
        "shipping_outstanding": shipping_outstanding(),
    }


def commission_report(year, month):
    """العمولة: بتتحسب على الفلوس المحصّلة في الشهر (كاش + تحصيلات الآجل + أونلاين المتحصّل).
    النسبة واحدة لكل البائعين، وبتختلف حسب نوع الفاتورة. مباشر ملوش عمولة."""
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    rates = {r.channel: r.rate for r in CommissionRate.objects.all()}

    collected = {}  # seller_id -> {channel: amount}
    names = {}

    def add(seller, channel, amount):
        key = seller.pk if seller else None
        names[key] = seller.name if seller else "بدون بائع"
        collected.setdefault(key, {})
        collected[key][channel] = collected[key].get(channel, ZERO) + amount

    for inv in (SaleInvoice.objects.filter(is_confirmed=True, payment_type="cash",
                                           date__gte=start, date__lt=end)
                .select_related("seller").prefetch_related("items")):
        add(inv.seller, inv.channel, inv.total)

    for p in (Payment.objects.filter(invoice__isnull=False, date__gte=start, date__lt=end,
                                     invoice__is_confirmed=True)
              .select_related("invoice__seller")):
        add(p.invoice.seller, p.invoice.channel, p.amount)

    for o in (OnlineOrder.objects.filter(status=OnlineOrder.COLLECTED,
                                         updated_at__date__gte=start, updated_at__date__lt=end)
              .select_related("seller").prefetch_related("items")):
        add(o.seller, "online", o.total_to_collect)

    rows = []
    for key, by_channel in collected.items():
        seller = Seller.objects.filter(pk=key).first() if key else None
        if seller and not seller.is_commissionable:
            commission_on = {}
        else:
            commission_on = by_channel
        commission = sum((amt * Decimal(rates.get(ch, 0)) / Decimal(100)
                          for ch, amt in commission_on.items()), ZERO)
        rows.append({
            "seller": names[key],
            "commissionable": bool(seller is None or seller.is_commissionable),
            "retail": by_channel.get("retail", ZERO),
            "wholesale": by_channel.get("wholesale", ZERO),
            "online": by_channel.get("online", ZERO),
            "total": sum(by_channel.values(), ZERO),
            "commission": commission,
        })
    rows.sort(key=lambda r: (-r["total"], r["seller"]))
    return {"start": start, "end": end, "rates": rates, "rows": rows,
            "grand_commission": sum((r["commission"] for r in rows), ZERO)}
