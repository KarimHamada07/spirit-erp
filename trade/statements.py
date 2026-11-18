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
    """كشف حساب العميل: الفواتير (بعد الخصم)، والدفعات وطريقتها، والمرتجعات، واللي اترد نقدي.
    الرصيد = افتتاحي + الفواتير − المرتجعات − الدفعات + اللي اترد نقدي (نفس رصيد with_balance)."""
    from .models import CashEntry
    rows = []
    if customer.opening_balance:
        rows.append((customer.opening_date or timezone.localdate(), -1, 0, {
            "kind": "رصيد افتتاحي", "ref": "", "cat": "opening",
            "debit": customer.opening_balance, "credit": ZERO}))
    invoices = list(SaleInvoice.objects.filter(customer=customer, is_confirmed=True).prefetch_related("items"))
    for inv in invoices:
        total = inv.total
        disc = f" — بعد خصم {inv.discount:,.2f}" if inv.discount else ""
        rows.append((inv.date, 0, inv.pk, {"kind": f"فاتورة {inv.get_channel_display()}",
                                           "ref": f"فاتورة #{inv.pk}{disc}", "cat": "invoice",
                                           "debit": total, "credit": ZERO}))
    # الفلوس اللي اتدفعت مع فواتير الكاش (من الخزن)
    cash_ids = [i.pk for i in invoices if i.payment_type == "cash"]
    for e in (CashEntry.objects.filter(invoice_id__in=cash_ids, kind="sale").select_related("box")):
        rows.append((e.date, 0, e.invoice_id, {"kind": "دفعة مع الفاتورة", "ref": f"{e.box.name} — فاتورة #{e.invoice_id}",
                                               "cat": "payment", "debit": ZERO, "credit": e.amount}))
    for ret in (SaleReturn.objects.filter(invoice__customer=customer, is_confirmed=True)
                .select_related("invoice", "refund_box").prefetch_related("items")):
        amount = ret.total
        why = f" — {ret.reason}" if ret.reason else ""
        rows.append((ret.date, 1, ret.pk, {"kind": "مرتجع", "ref": f"مرتجع #{ret.pk} (فاتورة #{ret.invoice_id}){why}",
                                           "cat": "return", "debit": ZERO, "credit": amount}))
        cash = amount - ret.account_amount
        if cash > 0:
            boxes = "، ".join(p["name"] for p in ret.refund_plan if p.get("box") != "account") or \
                (ret.refund_box.name if ret.refund_box_id else "خزنة")
            rows.append((ret.date, 1, ret.pk, {"kind": "اترد نقدي", "ref": f"مرتجع #{ret.pk} — {boxes}",
                                               "cat": "refund", "debit": cash, "credit": ZERO}))
    for p in customer.payments.select_related("box"):
        how = p.box.name if p.box_id else p.get_method_display()
        ref = f"دفعة #{p.pk} — {how}" + (f" (فاتورة #{p.invoice_id})" if p.invoice_id else "")
        if p.notes:
            ref += f" — {p.notes}"
        rows.append((p.date, 2, p.pk, {"kind": "دفعة", "ref": ref, "cat": "payment",
                                       "debit": ZERO, "credit": p.amount}))
    entries, totals = _finish(rows, d1, d2)
    sums = {k: ZERO for k in ("invoice", "return", "payment", "refund")}
    for e in entries:
        if e.get("cat") in sums:
            sums[e["cat"]] += e["debit"] or e["credit"]
    summary = [("إجمالي الفواتير", sums["invoice"]), ("إجمالي المرتجعات", sums["return"]),
               ("إجمالي الدفعات", sums["payment"])]
    if sums["refund"]:
        summary.append(("اترد نقدي", sums["refund"]))
    return {"kind": "customer", "title": f"كشف حساب عميل: {customer.name}", "name": customer.name,
            "phone": " / ".join(customer.phones), "entries": entries, "totals": totals, "summary": summary,
            "debit_label": "فواتير (عليه)", "credit_label": "دفعات ومرتجعات (ليه)",
            "balance_label": "الرصيد المستحق على العميل"}


def supplier_statement(supplier, d1=None, d2=None):
    rows = []
    if supplier.opening_balance:
        ob = supplier.opening_balance   # سالب = دفعنا مقدم قبل النظام (بيتحط في خانة السداد)
        rows.append((supplier.opening_date or timezone.localdate(), -1, 0, {
            "kind": "رصيد افتتاحي", "ref": "", "debit": max(ob, ZERO), "credit": max(-ob, ZERO)}))
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
        if p.kind == SupplierPayment.REFUND:
            rows.append((p.date, 2, p.pk, {"kind": "استرداد من المورد", "ref": f"استرداد #{p.pk} في {p.box}",
                                           "debit": p.amount, "credit": ZERO}))
        else:
            rows.append((p.date, 2, p.pk, {"kind": "سداد / تحت الحساب", "ref": f"سداد #{p.pk} من {p.box}",
                                           "debit": ZERO, "credit": p.amount}))
    entries, totals = _finish(rows, d1, d2)
    bal = totals["balance"]
    return {"kind": "supplier", "title": f"كشف حساب مورد: {supplier.name}", "name": supplier.name,
            "phone": supplier.phone, "entries": entries, "totals": totals,
            "debit_label": "مشتريات واسترداد", "credit_label": "سداد ومرتجعات",
            "balance_label": ("الرصيد: ليه عندنا" if bal > 0 else "الرصيد: لينا عنده (دفعة مقدمة)" if bal < 0
                              else "الرصيد: خالص") + " — موجب = ليه، سالب = لينا"}


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
            ["تاريخ الكشف", t["as_of"]]]
    info += [[label, value] for label, value in st.get("summary", [])]
    info.append([st["balance_label"], t["balance"]])
    return [("كشف الحساب", ["التاريخ", "النوع", "المرجع", st["debit_label"], st["credit_label"],
                            "الرصيد"], rows),
            ("ملخص", ["البيان", "القيمة"], info)]
