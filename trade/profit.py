"""تقرير الربحية الحقيقي لكل موديل ولكل مقاس/لون.

الربح = سعر البيع الفعلي - تكلفة الشراء (متوسط التكلفة وقت البيع).
  - خصم الفاتورة بيتوزع على بنودها بنسبة قيمة كل بند.
  - مرتجعات العملاء المؤكدة في الفترة بتتخصم (بنفس سعر البيع بعد الخصم، وبنفس التكلفة).
  - الأونلاين المحصّل بيدخل بنفس الطريقة (والخصم بيتوزع)، ومصاريف الشحن بتظهر لوحدها
    لأنها على الأوردر كله مش على صنف.
"""
from collections import OrderedDict
from decimal import Decimal

from django.utils import timezone

from .models import OnlineOrder, SaleInvoice, SaleReturnItem, Variant

ZERO = Decimal("0")


def _ratio(discount, gross):
    """نسبة اللي بيفضل بعد الخصم (1 = من غير خصم)"""
    if not gross:
        return Decimal("1")
    return (gross - discount) / gross


def profitability(d1, d2):
    variants = OrderedDict()
    models = OrderedDict()

    def row(v):
        r = variants.get(v.pk)
        if r is None:
            r = variants[v.pk] = {"variant": v, "sold": 0, "returned": 0,
                                  "revenue": ZERO, "cost": ZERO}
        return r

    invoices = (SaleInvoice.objects.filter(is_confirmed=True, date__range=(d1, d2))
                .prefetch_related("items__variant__model"))
    for inv in invoices:
        items = list(inv.items.all())
        ratio = _ratio(inv.discount, sum((i.quantity * i.unit_price for i in items), ZERO))
        for i in items:
            r = row(i.variant)
            r["sold"] += i.quantity
            r["revenue"] += i.quantity * i.unit_price * ratio
            r["cost"] += i.quantity * i.unit_cost

    returns = (SaleReturnItem.objects
               .filter(return_doc__is_confirmed=True, return_doc__date__range=(d1, d2))
               .select_related("sale_item__variant__model"))
    for ri in returns:
        si = ri.sale_item
        r = row(si.variant)
        r["returned"] += ri.quantity
        r["revenue"] -= ri.quantity * ri.unit_refund  # اللي اترد فعلاً للعميل
        r["cost"] -= ri.quantity * si.unit_cost

    shipping = {"fee": ZERO, "cost": ZERO, "returned_cost": ZERO, "orders": 0}
    for o in (OnlineOrder.objects
              .filter(status__in=[OnlineOrder.COLLECTED, OnlineOrder.RETURNED],
                      status_at__date__range=(d1, d2))
              .prefetch_related("items__variant__model")):
        if o.status == OnlineOrder.RETURNED:
            shipping["returned_cost"] += o.shipping_cost
            continue
        shipping["orders"] += 1
        shipping["fee"] += o.shipping_fee
        shipping["cost"] += o.shipping_cost
        items = list(o.items.all())
        ratio = _ratio(o.discount, sum((i.quantity * i.unit_price for i in items), ZERO))
        for i in items:
            r = row(i.variant)
            r["sold"] += i.quantity
            r["revenue"] += i.quantity * i.unit_price * ratio
            r["cost"] += i.quantity * i.unit_cost

    for r in variants.values():
        r["revenue"] = r["revenue"].quantize(Decimal("0.01"))
        r["net_qty"] = r["sold"] - r["returned"]
        r["profit"] = r["revenue"] - r["cost"]
        r["margin"] = (r["profit"] * 100 / r["revenue"]) if r["revenue"] else None
        r["unit_profit"] = (r["profit"] / r["net_qty"]) if r["net_qty"] else None
        m = r["variant"].model
        mr = models.get(m.pk)
        if mr is None:
            mr = models[m.pk] = {"model": m, "sold": 0, "returned": 0, "net_qty": 0,
                                 "revenue": ZERO, "cost": ZERO, "variants": []}
        for k in ("sold", "returned", "net_qty", "revenue", "cost"):
            mr[k] += r[k]
        mr["variants"].append(r)

    stock = _stock_by_model()
    out = []
    for mr in models.values():
        mr["profit"] = mr["revenue"] - mr["cost"]
        mr["margin"] = (mr["profit"] * 100 / mr["revenue"]) if mr["revenue"] else None
        mr["stock_qty"], mr["stock_value"] = stock.get(mr["model"].pk, (0, ZERO))
        mr["variants"].sort(key=lambda r: -r["profit"])
        out.append(mr)
    out.sort(key=lambda m: -m["profit"])

    revenue = sum((m["revenue"] for m in out), ZERO)
    cost = sum((m["cost"] for m in out), ZERO)
    items_profit = revenue - cost
    shipping_net = shipping["fee"] - shipping["cost"] - shipping["returned_cost"]
    return {
        "models": out, "shipping": shipping,
        "totals": {"revenue": revenue, "cost": cost, "profit": items_profit,
                   "margin": (items_profit * 100 / revenue) if revenue else None,
                   "sold": sum(m["sold"] for m in out),
                   "returned": sum(m["returned"] for m in out),
                   "shipping_net": shipping_net,
                   "profit_after_shipping": items_profit + shipping_net},
        "generated": timezone.now(),
    }


def _stock_by_model():
    res = {}
    for v in Variant.objects.only("model_id", "quantity", "avg_cost"):
        q, val = res.get(v.model_id, (0, ZERO))
        res[v.model_id] = (q + v.quantity, val + v.quantity * v.avg_cost)
    return res


def customer_profitability(d1, d2, channel=""):
    """المكسب من كل تاجر/عميل في الفترة: مبيعاته بعد الخصم ناقص المرتجعات، والتكلفة، والربح، ونسبة الخصم
    اللي بياخدها. العملاء النقدي (من غير اسم) متجمعين في سطر واحد."""
    rows = OrderedDict()

    def row(customer):
        key = customer.pk if customer else 0
        r = rows.get(key)
        if r is None:
            r = rows[key] = {"customer": customer, "invoices": 0, "pieces": 0, "returned": 0,
                             "gross": ZERO, "discount": ZERO, "revenue": ZERO, "cost": ZERO, "channels": set()}
        return r

    invoices = (SaleInvoice.objects.filter(is_confirmed=True, date__range=(d1, d2))
                .select_related("customer").prefetch_related("items"))
    if channel:
        invoices = invoices.filter(channel=channel)
    for inv in invoices:
        items = list(inv.items.all())
        gross = sum((i.quantity * i.unit_price for i in items), ZERO)
        r = row(inv.customer)
        r["invoices"] += 1
        r["channels"].add(inv.get_channel_display())
        r["pieces"] += sum(i.quantity for i in items)
        r["gross"] += gross
        r["discount"] += inv.discount
        r["revenue"] += gross - inv.discount
        r["cost"] += sum((i.quantity * i.unit_cost for i in items), ZERO)

    returns = (SaleReturnItem.objects
               .filter(return_doc__is_confirmed=True, return_doc__date__range=(d1, d2))
               .select_related("sale_item", "return_doc__invoice__customer"))
    if channel:
        returns = returns.filter(return_doc__invoice__channel=channel)
    for ri in returns:
        r = row(ri.return_doc.invoice.customer)
        r["returned"] += ri.quantity
        r["revenue"] -= ri.quantity * ri.unit_refund
        r["cost"] -= ri.quantity * ri.sale_item.unit_cost

    out = []
    for r in rows.values():
        r["revenue"] = r["revenue"].quantize(Decimal("0.01"))
        r["profit"] = r["revenue"] - r["cost"]
        r["margin"] = (r["profit"] * 100 / r["revenue"]) if r["revenue"] else None
        r["discount_pct"] = (r["discount"] * 100 / r["gross"]) if r["gross"] else None
        # أقصى خصم إضافي من غير خسارة = الربح ÷ المبيعات قبل الخصم
        r["room_pct"] = (r["profit"] * 100 / r["gross"]) if r["gross"] else None
        r["unit_profit"] = (r["profit"] / (r["pieces"] - r["returned"])) if r["pieces"] > r["returned"] else None
        r["channels"] = "، ".join(sorted(r["channels"]))
        out.append(r)
    out.sort(key=lambda r: r["profit"], reverse=True)
    t = {k: sum((r[k] for r in out), ZERO) for k in ("gross", "discount", "revenue", "cost", "profit")}
    t["pieces"] = sum(r["pieces"] for r in out)
    t["returned"] = sum(r["returned"] for r in out)
    t["margin"] = (t["profit"] * 100 / t["revenue"]) if t["revenue"] else None
    t["discount_pct"] = (t["discount"] * 100 / t["gross"]) if t["gross"] else None
    return {"rows": out, "totals": t}
