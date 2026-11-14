"""أعمار المديونيات وتذكيرات التحصيل للمحاسب.

عمر المديونية: الدفعات بتتخصم من أقدم فاتورة آجل الأول (FIFO)، فاللي بيفضل
هو أحدث الفواتير، وعمر المديونية = عمر أقدم فاتورة لسه عليها فلوس.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import F, Max, Sum
from django.utils import timezone

from .models import Customer, DebtFollowUp, SaleInvoice, SaleItem, SaleReturnItem

ZERO = Decimal("0")
BUCKETS = [(30, "لحد 30 يوم"), (60, "31 - 60 يوم"), (90, "61 - 90 يوم"), (None, "أكتر من 90 يوم")]


def bucket_of(days):
    for i, (limit, _label) in enumerate(BUCKETS):
        if limit is None or days <= limit:
            return i
    return len(BUCKETS) - 1


def _invoice_nets(customer_ids):
    """صافي كل فاتورة آجل مؤكدة = البنود - الخصم - المرتجع على الحساب"""
    gross = dict(SaleItem.objects
                 .filter(invoice__customer_id__in=customer_ids, invoice__is_confirmed=True,
                         invoice__payment_type="credit")
                 .values_list("invoice").annotate(s=Sum(F("quantity") * F("unit_price"))))
    returned = dict(SaleReturnItem.objects
                    .filter(return_doc__invoice__customer_id__in=customer_ids,
                            return_doc__is_confirmed=True, return_doc__refund_mode="account",
                            return_doc__invoice__payment_type="credit")
                    .values_list("return_doc__invoice")
                    .annotate(s=Sum(F("quantity") * F("unit_refund"))))
    per_customer = defaultdict(list)
    for inv in (SaleInvoice.objects
                .filter(customer_id__in=customer_ids, is_confirmed=True, payment_type="credit")
                .order_by("date", "id").only("id", "date", "discount", "customer_id")):
        net = (gross.get(inv.id) or ZERO) - inv.discount - (returned.get(inv.id) or ZERO)
        per_customer[inv.customer_id].append((inv, net))
    return per_customer


def debt_rows(today=None):
    """كل العملاء اللي عليهم فلوس، مع عمر المديونية وآخر دفعة وآخر متابعة"""
    today = today or timezone.localdate()
    debtors = [c for c in Customer.objects.with_balance().select_related("seller")
               if c.balance > 0]
    if not debtors:
        return []
    ids = [c.pk for c in debtors]
    invoices = _invoice_nets(ids)
    last_pay = dict(Customer.objects.filter(pk__in=ids)
                    .annotate(d=Max("payments__date")).values_list("pk", "d"))
    followups = {}
    for f in DebtFollowUp.objects.filter(customer_id__in=ids).order_by("customer_id", "-created_at", "-id"):
        followups.setdefault(f.customer_id, f)

    rows = []
    for c in debtors:
        left = c.paid_total
        oldest = None
        for inv, net in invoices.get(c.pk, []):
            if left >= net:
                left -= net
                continue
            oldest = inv
            break
        days = (today - oldest.date).days if oldest else 0
        f = followups.get(c.pk)
        due = bool(f and f.next_date and f.next_date <= today)
        rows.append({
            "customer": c, "balance": c.balance, "oldest": oldest, "days": days,
            "bucket": bucket_of(days), "last_payment": last_pay.get(c.pk),
            "followup": f, "due": due,
            # مستني ميعاد: لو المحاسب حدد ميعاد لسه ماجاش، مش هنزعجه بيه
            "snoozed": bool(f and f.next_date and f.next_date > today),
        })
    rows.sort(key=lambda r: (-r["days"], -r["balance"]))
    return rows


def bucket_totals(rows):
    totals = [{"label": label, "total": ZERO, "count": 0} for _l, label in BUCKETS]
    for r in rows:
        t = totals[r["bucket"]]
        t["total"] += r["balance"]
        t["count"] += 1
    return totals


def reminder_rows(rows, min_days=30):
    """اللي يتفكّر بيهم المحاسب: ميعاد متابعتهم جه، أو عدّى عليهم min_days من غير ميعاد"""
    return [r for r in rows if r["due"] or (not r["snoozed"] and r["days"] >= min_days)]


def reminder_text(rows, min_days=30, limit=25):
    from .notify import fmt
    if not rows:
        return ""
    total = sum((r["balance"] for r in rows), ZERO)
    lines = [f"📒 تذكير تحصيل للمحاسب — {len(rows)} عميل، الإجمالي {fmt(total)}"]
    for r in rows[:limit]:
        c = r["customer"]
        phone = f" — {c.phone}" if c.phone else ""
        why = "⏰ ميعاد المتابعة النهارده" if r["due"] else f"متأخر {r['days']} يوم"
        lines.append(f"• {c.name}{phone}: {fmt(r['balance'])} ({why})")
        if r["followup"]:
            lines.append(f"   آخر متابعة: {r['followup'].note}")
    if len(rows) > limit:
        lines.append(f"... و {len(rows) - limit} عميل كمان (افتح صفحة متابعة التحصيل)")
    lines.append(f"(العملاء اللي مديونيتهم أقدم من {min_days} يوم أو ميعاد متابعتهم جه)")
    return "\n".join(lines)
