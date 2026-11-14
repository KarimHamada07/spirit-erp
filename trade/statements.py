"""كشوف الحساب للعملاء والموردين (PDF و Excel) بفترة اختيارية.

كل كشف = حركات مرتبة بالتاريخ، كل حركة ليها مدين (بيزوّد الرصيد) ودائن (بيقلله)، والرصيد بعد كل حركة.
لو فيه "من تاريخ"، الحركات اللي قبله بتتجمع في سطر "رصيد سابق".
"""
from decimal import Decimal

from django.utils import timezone

from .models import (MaterialPurchase, PurchaseInvoice, PurchaseReturn, SaleInvoice, SaleReturn,
                     SupplierPayment)

ZERO = Decimal("0")


def _finish(rows, d1, d2, opening_label="رصيد سابق"):
    """rows = [(date, order, id, {kind, ref, debit, credit})] → كشف بالرصيد الجاري"""
    rows.sort(key=lambda e: (e[0], e[1], e[2]))
    before = ZERO
    entries, running = [], ZERO
    debit_total = credit_total = ZERO
    for day, _o, _i, row in rows:
        delta = row["debit"] - row["credit"]
        if d1 and day < d1:
            before += delta
            continue
        if d2 and day > d2:
            continue
        if not entries and before:
            running = before
            entries.append({"date": d1, "kind": opening_label, "ref": "", "debit": ZERO,
                            "credit": ZERO, "balance": before})
        elif not entries:
            running = before
        running += delta
        debit_total += row["debit"]
        credit_total += row["credit"]
        entries.append({**row, "date": day, "balance": running})
    if not entries and before:
        running = before
        entries.append({"date": d1, "kind": opening_label, "ref": "", "debit": ZERO,
                        "credit": ZERO, "balance": before})
    return entries, {"previous": before, "debit": debit_total, "credit": credit_total,
                     "balance": running if entries else before,
                     "as_of": timezone.localdate(), "d1": d1, "d2": d2}


def customer_statement(customer, d1=None, d2=None):
    rows = []
    if customer.opening_balance:
        rows.append((customer.opening_date or timezone.localdate(), -1, 0, {
            "kind": "رصيد افتتاحي", "ref": "", "debit": customer.opening_balance, "credit": ZERO}))
    for inv in (SaleInvoice.objects.filter(customer=customer, is_confirmed=True)
                .prefetch_related("items")):
        total = inv.total
        if inv.payment_type == "credit":
            rows.append((inv.date, 0, inv.pk, {"kind": "فاتورة آجل", "ref": f"فاتورة #{inv.pk}",
                                               "debit": total, "credit": ZERO}))
        else:  # الكاش متسدد وقت البيع: بيظهر في الكشف ومش بيأثر على الرصيد
            rows.append((inv.date, 0, inv.pk, {"kind": "فاتورة نقدي", "ref": f"فاتورة #{inv.pk}",
                                               "debit": total, "credit": total}))
    for ret in (SaleReturn.objects.filter(invoice__customer=customer, is_confirmed=True)
                .select_related("invoice")):
        amount = ret.total
        if ret.refund_mode == "account":
            rows.append((ret.date, 1, ret.pk, {"kind": "مرتجع (خصم من الحساب)",
                                               "ref": f"مرتجع #{ret.pk} (فاتورة #{ret.invoice_id})",
                                               "debit": ZERO, "credit": amount}))
        else:
            rows.append((ret.date, 1, ret.pk, {"kind": "مرتجع (استرداد نقدي)",
                                               "ref": f"مرتجع #{ret.pk} (فاتورة #{ret.invoice_id})",
                                               "debit": amount, "credit": amount}))
    for p in customer.payments.all():
        ref = f"دفعة #{p.pk}" + (f" (فاتورة #{p.invoice_id})" if p.invoice_id else "")
        rows.append((p.date, 2, p.pk, {"kind": f"دفعة {p.get_method_display()}", "ref": ref,
                                       "debit": ZERO, "credit": p.amount}))
    entries, totals = _finish(rows, d1, d2)
    return {"kind": "customer", "title": f"كشف حساب عميل: {customer.name}", "name": customer.name,
            "phone": customer.phone, "entries": entries, "totals": totals,
            "debit_label": "فواتير (عليه)", "credit_label": "دفعات (ليه)",
            "balance_label": "الرصيد المستحق على العميل"}


def supplier_statement(supplier, d1=None, d2=None):
    rows = []
    if supplier.opening_balance:
        rows.append((supplier.opening_date or timezone.localdate(), -1, 0, {
            "kind": "رصيد افتتاحي", "ref": "", "debit": supplier.opening_balance, "credit": ZERO}))
    for inv in (PurchaseInvoice.objects.filter(supplier=supplier, is_confirmed=True)
                .prefetch_related("items")):
        rows.append((inv.date, 0, inv.pk, {"kind": "فاتورة توريد", "ref": f"توريد #{inv.pk}",
                                           "debit": inv.total, "credit": ZERO}))
    for mp in (MaterialPurchase.objects.filter(supplier=supplier, is_confirmed=True)
               .prefetch_related("items")):
        rows.append((mp.date, 0, 100000 + mp.pk, {"kind": "شراء خامات", "ref": f"خامات #{mp.pk}",
                                                  "debit": mp.total, "credit": ZERO}))
    for ret in (PurchaseReturn.objects.filter(supplier=supplier, is_confirmed=True)
                .prefetch_related("items")):
        rows.append((ret.date, 1, ret.pk, {"kind": "مرتجع للمورد", "ref": f"مرتجع #{ret.pk}",
                                           "debit": ZERO, "credit": ret.total}))
    for p in SupplierPayment.objects.filter(supplier=supplier).select_related("box"):
        rows.append((p.date, 2, p.pk, {"kind": "سداد", "ref": f"سداد #{p.pk} من {p.box}",
                                       "debit": ZERO, "credit": p.amount}))
    entries, totals = _finish(rows, d1, d2)
    return {"kind": "supplier", "title": f"كشف حساب مورد: {supplier.name}", "name": supplier.name,
            "phone": supplier.phone, "entries": entries, "totals": totals,
            "debit_label": "مشتريات (لينا عليه)", "credit_label": "سداد ومرتجعات",
            "balance_label": "المستحق للمورد"}


def statement_sheets(st):
    """ورقة Excel للكشف"""
    t = st["totals"]
    rows = [[e["date"], e["kind"], e["ref"], e["debit"], e["credit"], e["balance"]]
            for e in st["entries"]]
    rows += [[], ["", "الإجمالي", "", t["debit"], t["credit"], t["balance"]]]
    period = ""
    if t["d1"] or t["d2"]:
        period = f"{t['d1'] or 'البداية'} → {t['d2'] or t['as_of']}"
    info = [["الاسم", st["name"]], ["الموبايل", st["phone"] or ""], ["الفترة", period or "كل الحركات"],
            ["تاريخ الكشف", t["as_of"]], [st["balance_label"], t["balance"]]]
    return [("كشف الحساب", ["التاريخ", "النوع", "المرجع", st["debit_label"], st["credit_label"],
                            "الرصيد"], rows),
            ("ملخص", ["البيان", "القيمة"], info)]
