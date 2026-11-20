from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .exports import xlsx_response
from .models import Expense, OnlineOrder, PurchaseInvoice, SaleInvoice

ZERO = Decimal("0")


def _allowed(user):
    return user.is_superuser or user.has_perm("trade.view_expense")


def _dates(request):
    today = timezone.localdate()
    try:
        d1 = date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        d1 = today.replace(day=1)
    try:
        d2 = date.fromisoformat(request.GET.get("to", ""))
    except ValueError:
        d2 = today
    if d2 < d1:
        d1, d2 = d2, d1
    return d1, min(d2, d1 + timedelta(days=800))


def collect(d1, d2):
    """كل أرقام التقارير لفترة معينة (فواتير مؤكدة + أونلاين محصّل/مرتجع + مصروفات)"""
    sales = []
    by_model, by_day = OrderedDict(), OrderedDict()

    def day(d):
        return by_day.setdefault(d, {"count": 0, "revenue": ZERO, "profit": ZERO})

    def model_row(name):
        return by_model.setdefault(name, {"qty": 0, "revenue": ZERO, "cost": ZERO})

    invoices = (SaleInvoice.objects.filter(is_confirmed=True, date__range=(d1, d2))
                .select_related("customer").prefetch_related("items__variant__model")
                .order_by("date", "id"))
    for inv in invoices:
        items = list(inv.items.all())
        gross = sum((i.quantity * i.unit_price for i in items), ZERO)
        profit = sum(((i.unit_price - i.unit_cost) * i.quantity for i in items), ZERO) - inv.discount
        sales.append({
            "no": inv.pk, "date": inv.date, "customer": inv.customer.name if inv.customer_id else "",
            "type": inv.get_payment_type_display(), "pieces": sum(i.quantity for i in items),
            "gross": gross, "discount": inv.discount, "total": gross - inv.discount,
            "profit": profit, "items": items})
        row = day(inv.date)
        row["count"] += 1
        row["revenue"] += gross - inv.discount
        row["profit"] += profit
        for i in items:
            m = model_row(i.variant.model.name)
            m["qty"] += i.quantity
            m["revenue"] += i.quantity * i.unit_price
            m["cost"] += i.quantity * i.unit_cost

    online = []
    orders = (OnlineOrder.objects
              .filter(status__in=[OnlineOrder.COLLECTED, OnlineOrder.RETURNED],
                      status_at__date__range=(d1, d2))
              .prefetch_related("items__variant__model").order_by("status_at"))
    for o in orders:
        d = timezone.localtime(o.status_at).date()
        collected = o.status == OnlineOrder.COLLECTED
        revenue = (o.items_total - o.discount) if collected else ZERO
        online.append({"no": o.pk, "date": d, "name": o.customer_name, "phone": o.phone,
                       "status": o.get_status_display(), "revenue": revenue,
                       "shipping_cost": o.shipping_cost, "profit": o.profit})
        row = day(d)
        row["revenue"] += revenue
        row["profit"] += o.profit
        if collected:
            row["count"] += 1
            for i in o.items.all():
                m = model_row(i.variant.model.name)
                m["qty"] += i.quantity
                m["revenue"] += i.quantity * i.unit_price
                m["cost"] += i.quantity * i.unit_cost

    purchases = []
    for p in (PurchaseInvoice.objects.filter(is_confirmed=True, date__range=(d1, d2))
              .select_related("supplier").prefetch_related("items__variant__model").order_by("date", "id")):
        items = list(p.items.all())
        purchases.append({"no": p.pk, "date": p.date, "supplier": str(p.supplier),
                          "pieces": sum(i.quantity for i in items),
                          "total": sum((i.quantity * i.unit_cost for i in items), ZERO)})

    expenses = list(Expense.objects.filter(date__range=(d1, d2)).order_by("date", "id"))
    revenue = sum((r["revenue"] for r in by_day.values()), ZERO)
    profit = sum((r["profit"] for r in by_day.values()), ZERO)
    expenses_total = sum((e.amount for e in expenses), ZERO)
    for m in by_model.values():
        m["profit"] = m["revenue"] - m["cost"]
    return {
        "d1": d1, "d2": d2, "sales": sales, "online": online, "purchases": purchases,
        "expenses": expenses, "by_model": by_model, "by_day": OrderedDict(sorted(by_day.items())),
        "summary": {"revenue": revenue, "profit": profit, "expenses": expenses_total,
                    "net": profit - expenses_total,
                    "purchases": sum((p["total"] for p in purchases), ZERO),
                    "invoices": len(sales), "online_orders": sum(1 for o in online if o["revenue"])},
    }


def _sheets(data, kind):
    s = data["summary"]
    sheets = []
    if kind in ("sales", "all"):
        sheets.append(("المبيعات اليومية", ["التاريخ", "عدد الفواتير/الأوردرات", "المبيعات"],
                       [[d, v["count"], v["revenue"]] for d, v in data["by_day"].items()]))
        sheets.append(("فواتير البيع", ["رقم", "التاريخ", "العميل", "النوع", "القطع", "المجموع",
                                        "الخصم", "الصافي", "الربح"],
                       [[x["no"], x["date"], x["customer"], x["type"], x["pieces"], x["gross"],
                         x["discount"], x["total"], x["profit"]] for x in data["sales"]]))
        sheets.append(("الأونلاين", ["رقم", "التاريخ", "العميل", "الموبايل", "الحالة", "المبيعات",
                                     "تكلفة الشحن", "الربح"],
                       [[x["no"], x["date"], x["name"], x["phone"], x["status"], x["revenue"],
                         x["shipping_cost"], x["profit"]] for x in data["online"]]))
    if kind in ("profit", "all"):
        sheets.append(("ملخص الأرباح", ["البند", "القيمة"], [
            ["الفترة", f"{data['d1']} → {data['d2']}"], ["إجمالي المبيعات", s["revenue"]],
            ["إجمالي الربح", s["profit"]], ["المصروفات", s["expenses"]], ["صافي الربح", s["net"]],
            ["مشتريات الفترة (توريد)", s["purchases"]]]))
        sheets.append(("الربح حسب الموديل", ["الموديل", "الكمية", "المبيعات", "التكلفة", "الربح (قبل الخصم)"],
                       [[k, v["qty"], v["revenue"], v["cost"], v["profit"]] for k, v in data["by_model"].items()]))
        sheets.append(("الربح اليومي", ["التاريخ", "المبيعات", "الربح"],
                       [[d, v["revenue"], v["profit"]] for d, v in data["by_day"].items()]))
        sheets.append(("المصروفات", ["التاريخ", "البند", "المبلغ"],
                       [[e.date, e.title, e.amount] for e in data["expenses"]]))
    if kind in ("invoices", "all"):
        if kind == "invoices":
            sheets.append(("فواتير البيع", ["رقم", "التاريخ", "العميل", "النوع", "القطع", "المجموع",
                                            "الخصم", "الصافي", "الربح"],
                           [[x["no"], x["date"], x["customer"], x["type"], x["pieces"], x["gross"],
                             x["discount"], x["total"], x["profit"]] for x in data["sales"]]))
        sheets.append(("بنود فواتير البيع", ["رقم الفاتورة", "التاريخ", "الصنف", "الكمية", "السعر", "الإجمالي"],
                       [[x["no"], x["date"], str(i.variant), i.quantity, i.unit_price, i.quantity * i.unit_price]
                        for x in data["sales"] for i in x["items"]]))
        sheets.append(("فواتير التوريد", ["رقم", "التاريخ", "المورد", "القطع", "الإجمالي"],
                       [[x["no"], x["date"], x["supplier"], x["pieces"], x["total"]] for x in data["purchases"]]))
    return sheets


@staff_member_required
def reports(request):
    if not _allowed(request.user):
        raise PermissionDenied
    d1, d2 = _dates(request)
    data = collect(d1, d2)
    qs = f"from={d1.isoformat()}&to={d2.isoformat()}"
    return render(request, "trade/reports.html", {
        **admin.site.each_context(request), "title": "التقارير", "data": data,
        "d1": d1.isoformat(), "d2": d2.isoformat(),
        "export_url": reverse("report_export"), "print_url": reverse("report_print"), "qs": qs})


@staff_member_required
def report_export(request):
    if not _allowed(request.user):
        raise PermissionDenied
    kind = request.GET.get("kind", "all")
    if kind not in ("sales", "profit", "invoices", "all"):
        kind = "all"
    d1, d2 = _dates(request)
    return xlsx_response(_sheets(collect(d1, d2), kind), f"report_{kind}_{d1}_{d2}")


@staff_member_required
def report_print(request):
    if not _allowed(request.user):
        raise PermissionDenied
    d1, d2 = _dates(request)
    return render(request, "trade/report_print.html",
                  {"data": collect(d1, d2), "shop_name": "SPIRIT | سبيريت"})


# ================= تقرير الربحية (لكل موديل ومقاس) =================
@staff_member_required
def profit_report(request):
    from .profit import profitability
    if not _allowed(request.user):
        raise PermissionDenied
    d1, d2 = _dates(request)
    data = profitability(d1, d2)
    if request.GET.get("export") == "xlsx":
        t = data["totals"]
        def pct(x):
            return round(float(x), 1) if x is not None else ""
        return xlsx_response([
            ("الربحية حسب الموديل",
             ["الموديل", "الكود", "اتباع", "مرتجع", "الصافي", "المبيعات", "التكلفة", "الربح",
              "الهامش %", "باقي بالمخزن", "قيمة المخزن"],
             [[m["model"].name, m["model"].code, m["sold"], m["returned"], m["net_qty"],
               m["revenue"], m["cost"], m["profit"], pct(m["margin"]), m["stock_qty"],
               m["stock_value"]] for m in data["models"]]
             + [["الإجمالي", "", t["sold"], t["returned"], t["sold"] - t["returned"],
                 t["revenue"], t["cost"], t["profit"], pct(t["margin"]), "", ""]]),
            ("الربحية حسب المقاس",
             ["الموديل", "المقاس", "اللون", "اتباع", "مرتجع", "المبيعات", "التكلفة", "الربح",
              "الهامش %", "ربح القطعة"],
             [[m["model"].name, r["variant"].size, r["variant"].color, r["sold"], r["returned"],
               r["revenue"], r["cost"], r["profit"], pct(r["margin"]),
               r["unit_profit"] if r["unit_profit"] is not None else ""]
              for m in data["models"] for r in m["variants"]]),
            ("الشحن", ["البند", "القيمة"], [
                ["أوردرات محصّلة", data["shipping"]["orders"]],
                ["شحن على العملاء", data["shipping"]["fee"]],
                ["تكلفة الشحن علينا", data["shipping"]["cost"]],
                ["تكلفة شحن المرتجعات", data["shipping"]["returned_cost"]],
                ["صافي الشحن", t["shipping_net"]],
                ["الربح بعد الشحن", t["profit_after_shipping"]]]),
        ], f"profit_{d1}_{d2}")
    return render(request, "trade/profit.html", {
        **admin.site.each_context(request), "title": "تقرير الربحية", "data": data,
        "d1": d1.isoformat(), "d2": d2.isoformat()})


@staff_member_required
def customer_profit(request):
    """ربحية التجار: كسبت كام من كل عميل، عشان تحدد نسبة الخصم المناسبة ليه"""
    from .profit import customer_profitability
    if not _allowed(request.user):
        raise PermissionDenied
    d1, d2 = _dates(request)
    if "from" not in request.GET:                  # افتراضياً آخر 3 شهور
        d1 = d2 - timedelta(days=90)
    channel = request.GET.get("channel", "")
    channels = SaleInvoice.CHANNELS
    if channel not in dict(channels):
        channel = ""
    data = customer_profitability(d1, d2, channel)
    if request.GET.get("export") == "xlsx":
        def pct(x):
            return round(float(x), 1) if x is not None else ""
        return xlsx_response([("ربحية التجار",
            ["العميل", "المحل", "الفواتير", "القطع", "مرتجع", "قبل الخصم", "الخصم", "نسبة الخصم %",
             "المبيعات الصافية", "التكلفة", "الربح", "الهامش %", "ربح القطعة", "أقصى خصم من غير خسارة %"],
            [[r["customer"].name if r["customer"] else "عملاء نقدي", r["customer"].shop_name if r["customer"] else "",
              r["invoices"], r["pieces"], r["returned"], r["gross"], r["discount"], pct(r["discount_pct"]),
              r["revenue"], r["cost"], r["profit"], pct(r["margin"]),
              round(r["unit_profit"], 2) if r["unit_profit"] is not None else "", pct(r["room_pct"])]
             for r in data["rows"]])], "customer_profit")
    return render(request, "trade/customer_profit.html", {
        **admin.site.each_context(request), "title": "ربحية التجار", "data": data,
        "d1": d1, "d2": d2, "channel": channel, "channels": channels})
