"""صفحة التحليلات: رسوم للمبيعات والأرباح على 12 شهر، القنوات، الموديلات، والبائعين"""
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from .models import Expense, OnlineOrder, SaleInvoice
from .report_views import _allowed

ZERO = Decimal("0")
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس",
             "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
WEEKDAYS_AR = ["الاتنين", "التلات", "الأربع", "الخميس", "الجمعة", "السبت", "الحد"]


def _month_start(d, back):
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def build(today=None, months=12, top_n=10):
    today = today or timezone.localdate()
    start = _month_start(today, months - 1)
    keys = OrderedDict()
    for i in range(months - 1, -1, -1):
        s = _month_start(today, i)
        keys[(s.year, s.month)] = {"label": f"{MONTHS_AR[s.month - 1]} {s.year % 100:02d}",
                                   "revenue": ZERO, "profit": ZERO, "expenses": ZERO}
    channels = OrderedDict([("retail", ["قطاعي", ZERO]), ("wholesale", ["جملة", ZERO]),
                            ("online", ["أونلاين", ZERO])])
    pay = OrderedDict([("cash", ["نقدي", ZERO]), ("credit", ["آجل", ZERO])])
    models = {}
    sellers = {}
    weekday = [ZERO] * 7

    def add_model(name, qty, rev, cost):
        m = models.setdefault(name, {"name": name, "qty": 0, "revenue": ZERO, "profit": ZERO})
        m["qty"] += qty
        m["revenue"] += rev
        m["profit"] += rev - cost

    def add_seller(seller, rev, profit, n=1):
        name = seller.name if seller else "بدون بائع"
        s = sellers.setdefault(name, {"name": name, "count": 0, "revenue": ZERO, "profit": ZERO})
        s["count"] += n
        s["revenue"] += rev
        s["profit"] += profit

    for inv in (SaleInvoice.objects.filter(is_confirmed=True, date__range=(start, today))
                .select_related("seller").prefetch_related("items__variant__model")):
        items = list(inv.items.all())
        gross = sum((i.quantity * i.unit_price for i in items), ZERO)
        cost = sum((i.quantity * i.unit_cost for i in items), ZERO)
        rev = gross - inv.discount
        k = keys[(inv.date.year, inv.date.month)]
        k["revenue"] += rev
        k["profit"] += rev - cost
        channels[inv.channel][1] += rev
        pay[inv.payment_type][1] += rev
        weekday[inv.date.weekday()] += rev
        add_seller(inv.seller, rev, rev - cost)
        ratio = (rev / gross) if gross else Decimal("1")
        for i in items:
            add_model(i.variant.model.name, i.quantity, i.quantity * i.unit_price * ratio,
                      i.quantity * i.unit_cost)

    for o in (OnlineOrder.objects
              .filter(status__in=[OnlineOrder.COLLECTED, OnlineOrder.RETURNED],
                      status_at__date__gte=start)
              .select_related("seller").prefetch_related("items__variant__model")):
        d = timezone.localtime(o.status_at).date()
        k = keys.get((d.year, d.month))
        if k is None:
            continue
        k["profit"] += o.profit
        if o.status != OnlineOrder.COLLECTED:
            continue
        items = list(o.items.all())
        gross = o.items_total
        rev = gross - o.discount
        k["revenue"] += rev
        channels["online"][1] += rev
        weekday[d.weekday()] += rev
        add_seller(o.seller, rev, o.profit)
        ratio = (rev / gross) if gross else Decimal("1")
        for i in items:
            add_model(i.variant.model.name, i.quantity, i.quantity * i.unit_price * ratio,
                      i.quantity * i.unit_cost)

    for e in Expense.objects.filter(date__range=(start, today)).only("date", "amount"):
        keys[(e.date.year, e.date.month)]["expenses"] += e.amount

    months_list = list(keys.values())
    for m in months_list:
        m["net"] = m["profit"] - m["expenses"]
    cur, prev = months_list[-1], (months_list[-2] if len(months_list) > 1 else None)

    def growth(a, b):
        return float((a - b) * 100 / b) if b else None

    by_qty = sorted(models.values(), key=lambda m: -m["qty"])[:top_n]
    by_profit = sorted(models.values(), key=lambda m: -m["profit"])[:top_n]
    seller_rows = sorted(sellers.values(), key=lambda s: -s["revenue"])
    for s in seller_rows:
        s["avg"] = s["revenue"] / s["count"] if s["count"] else ZERO
        s["margin"] = float(s["profit"] * 100 / s["revenue"]) if s["revenue"] else None

    f = float
    return {
        "start": start, "today": today,
        "current": cur, "previous": prev,
        "growth_revenue": growth(cur["revenue"], prev["revenue"]) if prev else None,
        "growth_profit": growth(cur["profit"], prev["profit"]) if prev else None,
        "year_revenue": sum((m["revenue"] for m in months_list), ZERO),
        "year_net": sum((m["net"] for m in months_list), ZERO),
        "sellers": seller_rows,
        "chart": {
            "months": [m["label"] for m in months_list],
            "revenue": [f(m["revenue"]) for m in months_list],
            "profit": [f(m["profit"]) for m in months_list],
            "expenses": [f(m["expenses"]) for m in months_list],
            "net": [f(m["net"]) for m in months_list],
            "channels": {"labels": [v[0] for v in channels.values()],
                         "values": [f(v[1]) for v in channels.values()]},
            "pay": {"labels": [v[0] for v in pay.values()], "values": [f(v[1]) for v in pay.values()]},
            "top_qty": {"labels": [m["name"] for m in by_qty], "values": [m["qty"] for m in by_qty]},
            "top_profit": {"labels": [m["name"] for m in by_profit],
                           "values": [f(m["profit"]) for m in by_profit]},
            "sellers": {"labels": [s["name"] for s in seller_rows],
                        "revenue": [f(s["revenue"]) for s in seller_rows],
                        "profit": [f(s["profit"]) for s in seller_rows]},
            "weekday": {"labels": WEEKDAYS_AR, "values": [f(v) for v in weekday]},
        },
    }


@staff_member_required
def analytics(request):
    if not _allowed(request.user):
        raise PermissionDenied
    return render(request, "trade/analytics.html", {
        **admin.site.each_context(request), "title": "التحليلات", "data": build()})
