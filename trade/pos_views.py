import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (CashBox, CashEntry, Customer, SaleInvoice, SaleItem, Seller,
                     Variant)
from .pdf_export import invoice_pdf
from .notify import log_event
from .treasury import record_sale_payments

SHOP_NAME = "SPIRIT"


def customer_last_prices(customer):
    """آخر سعر بيع لكل صنف للعميل، من فواتيره المؤكدة (الأحدث هو اللي بيفوز)."""
    if customer is None:
        return {}
    rows = (SaleItem.objects
            .filter(invoice__customer=customer, invoice__is_confirmed=True)
            .order_by("invoice__date", "invoice_id", "id")
            .values_list("variant_id", "unit_price"))
    prices = {}
    for variant_id, price in rows:
        prices[variant_id] = price
    return prices


@staff_member_required
def pos_customer_prices(request):
    """أسعار العميل السابقة للأصناف، عشان الكاشير يلاقيها جاهزة في السلة."""
    if not request.user.has_perm("trade.add_saleinvoice"):
        raise PermissionDenied
    customer = None
    if request.GET.get("customer", "").isdigit():
        customer = Customer.objects.filter(pk=int(request.GET["customer"])).first()
    prices = customer_last_prices(customer)
    cr = customer_credit(customer)
    return JsonResponse({
        "prices": {str(k): float(v) for k, v in prices.items()},
        "balance": float(cr["balance"]), "limit": None if cr["limit"] is None else float(cr["limit"]),
        "available": None if cr["available"] is None else float(cr["available"]), "type": cr["type"],
        "can_change": request.user.has_perm("trade.change_customer_price"),
    })


@staff_member_required
def pos(request):
    if not request.user.has_perm("trade.add_saleinvoice"):
        raise PermissionDenied
    variants = (Variant.objects
                .filter(model__is_active=True, quantity__gt=0)
                .select_related("model")
                .order_by("model__name", "size", "color"))
    data = [{
        "id": v.id,
        "name": v.model.name,
        "code": v.model.code,
        "size": v.size,
        "color": v.color,
        "qty": v.quantity,
        "price": float(v.sale_price),
        "wprice": float(v.wholesale_price) if v.wholesale_price else None,
        "category": v.model.get_category_display(),
        "series": v.model.series_list or None,
    } for v in variants]
    context = {
        **admin.site.each_context(request),
        "title": "بيع سريع",
        "variants": data,
        "customers": Customer.objects.select_related("seller").order_by("name"),
        "sellers": Seller.objects.filter(is_active=True).order_by("name"),
        "my_seller": getattr(getattr(request.user, "seller_profile", None), "pk", None),
        "today": timezone.localdate().isoformat(),
        "can_date": request.user.has_perm("trade.change_invoice_date"),
        "deliveries": SaleInvoice.DELIVERY,
        "boxes": CashBox.objects.filter(is_active=True),
        "channels": SaleInvoice.CHANNELS,
        "can_edit_price": request.user.has_perm("trade.change_sale_price"),
        "can_return": request.user.has_perm("trade.add_salereturn"),
        "return_boxes": CashBox.objects.filter(is_active=True).exclude(method="shipping"),
    }
    return render(request, "trade/pos.html", context)


def _parse_payments(raw):
    """طرق السداد من الشاشة: [(خزنة، مبلغ)] + المبلغ اللي "من الحساب" (آجل على العميل)"""
    allocations, account = [], Decimal("0")
    for p in raw or []:
        amount = Decimal(str(p.get("amount") or 0))
        if amount < 0:
            raise ValidationError("المبلغ غير صحيح")
        if p.get("box") == "account":
            account += amount
            continue
        box = CashBox.objects.filter(pk=p.get("box"), is_active=True).first()
        if box is None:
            raise ValidationError("طريقة الدفع غير صحيحة")
        if amount > 0:
            allocations.append((box, amount))
    return allocations, account


def payment_plan(allocations, account):
    plan = [{"box": b.pk, "name": b.name, "amount": str(a)} for b, a in allocations]
    if account:
        plan.append({"box": "account", "name": "من الحساب (آجل)", "amount": str(account)})
    return plan


def customer_credit(customer):
    """الرصيد وحد الائتمان والمتاح للعميل (المتاح None = من غير حد)"""
    if customer is None:
        return {"balance": Decimal("0"), "limit": None, "available": None, "type": ""}
    bal = Customer.objects.with_balance().get(pk=customer.pk).balance
    limit = customer.credit_limit
    return {"balance": bal, "limit": limit, "type": customer.customer_type,
            "available": None if limit is None else max(Decimal("0"), limit - bal)}


def _parse_date(raw):
    from datetime import date as _d
    try:
        return _d.fromisoformat(raw) if raw else timezone.localdate()
    except ValueError:
        raise ValidationError("التاريخ غير صحيح")


@staff_member_required
@require_POST
def pos_checkout(request):
    if not request.user.has_perm("trade.add_saleinvoice"):
        raise PermissionDenied
    pending_msg = ""
    try:
        payload = json.loads(request.body)
        items = payload.get("items") or []
        if not items:
            raise ValidationError("السلة فاضية")
        discount = Decimal(str(payload.get("discount") or 0))
        if discount < 0:
            raise ValidationError("الخصم غير صحيح")
        percent = None
        if payload.get("discount_type") == "percent":
            percent = discount
            if percent > 100:
                raise ValidationError("نسبة الخصم لازم تكون من 0 لـ 100")
            discount = Decimal("0")  # بيتحسب من النسبة بعد ما البنود تتضاف
        channel = payload.get("channel") or "retail"
        if channel not in dict(SaleInvoice.CHANNELS):
            raise ValidationError("نوع الفاتورة غير صحيح")
        delivery = payload.get("delivery") or "customer"
        if delivery not in dict(SaleInvoice.DELIVERY):
            raise ValidationError("طريقة التسليم غير صحيحة")
        allocations, account = _parse_payments(payload.get("payments"))
        if payload.get("credit") and not account:      # الشاشة القديمة: "بيع آجل" = الباقي على الحساب
            account = None
        inv_date = _parse_date(payload.get("date"))
        today = timezone.localdate()
        if inv_date > today:
            raise ValidationError("مينفعش تاريخ الفاتورة يبقى بعد النهارده")
        notes = (payload.get("notes") or "").strip()[:500]

        with transaction.atomic():
            customer = None
            if payload.get("customer"):
                customer = Customer.objects.filter(pk=payload["customer"]).first()
            # العميل المرتبط ببائع: البيع بيتسجل باسمه. العميل النقدي بيختار البائع
            seller = customer.seller if customer and customer.seller_id else None
            if seller is None:
                seller = Seller.objects.filter(pk=payload.get("seller"), is_active=True).first()
            if seller is None:
                raise ValidationError("اختار البائع")
            last_prices = customer_last_prices(customer)
            can_change_price = request.user.has_perm("trade.change_customer_price")
            can_edit_price = request.user.has_perm("trade.change_sale_price")
            invoice = SaleInvoice.objects.create(
                customer=customer, date=inv_date, discount=discount, discount_percent=percent or None,
                payment_type="cash", channel=channel, seller=seller, delivery=delivery,
                notes=notes, created_by=request.user,
            )
            gross = Decimal("0")
            for it in items:
                qty = int(it["qty"])
                price = Decimal(str(it["price"]))
                if qty <= 0 or price < 0:
                    raise ValidationError("كمية أو سعر غير صحيح")
                variant = Variant.objects.get(pk=it["id"])
                if variant.quantity < qty:
                    raise ValidationError(f"الكمية غير كافية للصنف: {variant} (المتاح {variant.quantity})")
                last = last_prices.get(variant.pk)
                if last is not None and price != last and not can_change_price:
                    raise ValidationError(
                        f"سعر {variant.model.name} للعميل ده لازم يكون {last:.2f} "
                        "زي الفاتورة السابقة، والتعديل من مدير الحسابات")
                listed = variant.price_for(channel)
                # أول مرة العميل ياخد الصنف: البياع يحدد السعر وبعدها يتثبت. من غير عميل: سعر القايمة
                if last is None and customer is None and price != listed and not can_edit_price:
                    kind = SaleInvoice(channel=channel).get_channel_display()
                    raise ValidationError(
                        f"سعر {variant} ({kind}) هو {listed:.2f}، وتعديل السعر لمدير الحسابات بس")
                SaleItem.objects.create(invoice=invoice, variant=variant, quantity=qty, unit_price=price)
                gross += qty * price
            if percent:
                discount = (gross * percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if discount > gross:
                raise ValidationError("الخصم أكبر من إجمالي الفاتورة")
            total = gross - discount
            paid = sum((a for _b, a in allocations), Decimal("0"))
            if account is None:
                account = max(Decimal("0"), total - paid)
            if abs(paid + account - total) > Decimal("0.01"):
                raise ValidationError(f"مجموع طرق السداد ({paid + account:,.2f}) لازم يساوي صافي الفاتورة ({total:,.2f})")
            if account and customer is None:
                raise ValidationError("اختار العميل عشان تسجّل عليه \"من الحساب\"")
            if account and customer.customer_type == "cash":
                raise ValidationError(f"{customer.name} عميل نقدي ومينفعش يتسجل عليه من الحساب")
            invoice.payment_type = "credit" if account else "cash"
            invoice.account_amount = account
            invoice.payment_plan = payment_plan(allocations, account)
            invoice.save()

            # محتاجة موافقة مدير الحسابات؟
            needs = []
            if inv_date != today and not request.user.has_perm("trade.change_invoice_date"):
                needs.append(f"تاريخ الفاتورة {inv_date:%d/%m/%Y} مش النهارده")
            if account and customer.credit_limit is not None:
                cr = customer_credit(customer)
                if account > cr["available"]:
                    needs.append(f"هتعدّي حد الائتمان: المتاح {cr['available']:,.2f} والمطلوب {account:,.2f}")
            if needs and not request.user.has_perm("trade.approve_saleinvoice"):
                invoice.approval, invoice.approval_reason = "pending", " — ".join(needs)
                invoice.save()
                pending_msg = ("الفاتورة اتحفظت ومستنية موافقة مدير الحسابات: " + " — ".join(needs)
                               + ". أول ما يوافق هتتأكد وتتخصم من المخزن.")
                log_event(f"⏳ فاتورة بيع #{invoice.pk} مستنية موافقتك\n"
                          f"العميل: {customer.name if customer else '—'} — صافي {total:,.2f}\nالسبب: "
                          + " — ".join(needs))
            else:
                if needs:
                    invoice.approval, invoice.approval_reason = "approved", " — ".join(needs)
                    invoice.approved_by = request.user
                    invoice.save()
                invoice.confirm()  # يخصم المخزون ويثبت التكلفة
                record_sale_payments(invoice, allocations)  # يسجل الدفع في الخزن

    except ValidationError as e:
        return JsonResponse({"ok": False, "error": " ".join(e.messages)}, status=400)
    except (ValueError, TypeError, KeyError, InvalidOperation,
            Variant.DoesNotExist, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "بيانات غير صحيحة"}, status=400)

    return JsonResponse({
        "ok": True, "id": invoice.id, "pending": bool(pending_msg), "message": pending_msg,
        "print_url": reverse("sale_print", args=[invoice.id]),
    })


def _invoice_context(invoice):
    rows = [{
        "code": i.variant.model.code,
        "name": i.variant.model.name,
        "size": i.variant.size,
        "color": i.variant.color,
        "category": i.variant.model.get_category_display(),
        "qty": i.quantity,
        "price": i.unit_price,
        "total": i.quantity * i.unit_price,
    } for i in invoice.items.select_related("variant", "variant__model")]
    gross = sum((r["total"] for r in rows), Decimal("0"))
    if invoice.payment_plan:      # طرق السداد اللي اتختارت وقت البيع (ومنها "من الحساب")
        payments = [{"box": p["name"], "amount": Decimal(p["amount"])} for p in invoice.payment_plan]
    else:
        payments = [{"box": e.box.name, "amount": e.amount}
                    for e in CashEntry.objects.filter(
                        invoice=invoice, kind__in=["sale", "payment"]).select_related("box")]
        if invoice.payment_type == "credit" and invoice.remaining > 0:
            payments.append({"box": "من الحساب (آجل)", "amount": invoice.remaining})
    return {
        "rows": rows,
        "gross": gross,
        "total": gross - invoice.discount,
        "paid": invoice.paid_total,
        "remaining": invoice.remaining,
        "pieces": invoice.pieces,
        "payments": payments,
        "shop_name": SHOP_NAME,
    }


SHARE_SALT = "invoice-share"


def share_url(request, invoice):
    """رابط للعميل يفتح الفاتورة من غير تسجيل دخول (موقّع، محدش يقدر يخمّن فاتورة تانية)"""
    from django.core import signing
    token = signing.dumps(invoice.pk, salt=SHARE_SALT, compress=True)
    return request.build_absolute_uri(reverse("invoice_public", args=[token]))


def wa_phone(raw):
    """رقم الموبايل بالشكل اللي واتساب عايزه (كود مصر من غير +)"""
    phone = "".join(ch for ch in (raw or "") if ch.isdigit())
    if phone.startswith("00"):
        phone = phone[2:]
    elif phone.startswith("0"):
        phone = "20" + phone[1:]       # مصر
    return phone


def whatsapp_url(request, invoice):
    """رابط واتساب جاهز برسالة فيها رابط الفاتورة (لرقم العميل لو موجود)"""
    from urllib.parse import quote
    phone = wa_phone(invoice.customer.phone if invoice.customer_id else "")
    text = (f"أهلاً بيك 👋\nفاتورتك من {SHOP_NAME} رقم #{invoice.pk}\n"
            f"الإجمالي: {invoice.total:,.2f} ج.م\nتقدر تشوفها وتنزّلها من هنا:\n{share_url(request, invoice)}")
    return f"https://wa.me/{phone}?text={quote(text)}"


@staff_member_required
def sale_print(request, pk):
    invoice = get_object_or_404(
        SaleInvoice.objects.select_related("customer", "seller"), pk=pk)
    return render(request, "trade/invoice_print.html",
                  {"invoice": invoice, **_invoice_context(invoice),
                   "whatsapp_url": whatsapp_url(request, invoice),
                   "has_phone": bool(invoice.customer_id and invoice.customer.phone)})


def invoice_public(request, token):
    """الفاتورة للعميل من الرابط اللي اتبعتله (من غير دخول)"""
    from django.core import signing
    from django.http import Http404
    try:
        pk = signing.loads(token, salt=SHARE_SALT, max_age=60 * 60 * 24 * 180)
    except signing.BadSignature:
        raise Http404
    invoice = get_object_or_404(SaleInvoice.objects.select_related("customer", "seller"),
                                pk=pk, is_confirmed=True)
    resp = render(request, "trade/invoice_print.html",
                  {"invoice": invoice, **_invoice_context(invoice), "public": True})
    resp["X-Robots-Tag"] = "noindex"
    return resp


@staff_member_required
def sale_pdf(request, pk):
    """تحميل الفاتورة كملف PDF"""
    if not request.user.has_perm("trade.view_saleinvoice"):
        raise PermissionDenied
    invoice = get_object_or_404(
        SaleInvoice.objects.select_related("customer", "seller"), pk=pk)
    ctx = _invoice_context(invoice)
    content = invoice_pdf(invoice, ctx)
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="invoice-{invoice.pk}.pdf"'
    return response


# ================= المرتجع من شاشة البيع =================
def _returnable(invoice):
    from django.db.models import Sum

    from .models import SaleReturnItem
    ratio = invoice.net_ratio
    rows = []
    for si in invoice.items.select_related("variant__model"):
        done = (SaleReturnItem.objects.filter(sale_item=si, return_doc__is_confirmed=True)
                .aggregate(s=Sum("quantity"))["s"] or 0)
        rows.append({"sale_item": si.pk, "label": str(si.variant), "code": si.variant.model.code,
                     "name": si.variant.model.name, "color": si.variant.color, "size": si.variant.size,
                     "category": si.variant.model.get_category_display(),
                     "sold": si.quantity, "returned": done, "returnable": si.quantity - done,
                     "unit_refund": float((si.unit_price * ratio).quantize(Decimal("0.01"),
                                                                           rounding=ROUND_HALF_UP))})
    return rows


@staff_member_required
def pos_return_lookup(request):
    """بيانات فاتورة البيع عشان المرتجع: الأصناف واللي لسه ينفع يرجع منها"""
    if not request.user.has_perm("trade.add_salereturn"):
        raise PermissionDenied
    num = request.GET.get("invoice", "").strip().lstrip("#")
    inv = (SaleInvoice.objects.filter(pk=num, is_confirmed=True).select_related("customer").first()
           if num.isdigit() else None)
    if inv is None:
        return JsonResponse({"ok": False, "error": "مفيش فاتورة بيع مؤكدة بالرقم ده"}, status=404)
    return JsonResponse({"ok": True, "invoice": {
        "id": inv.pk, "date": inv.date.strftime("%d/%m/%Y"),
        "customer": inv.customer.name if inv.customer_id else "",
        "type": inv.get_payment_type_display(), "can_account": inv.payment_type == "credit",
        "discount": float(inv.discount), "total": float(inv.total)},
        "items": _returnable(inv)})


def _sold_lots(customer, variant_ids=None):
    """بنود البيع المؤكدة للعميل (الأقدم الأول = FIFO) واللي لسه ينفع يرجع منها"""
    from django.db.models import Sum

    from .models import SaleReturnItem
    qs = (SaleItem.objects.filter(invoice__customer=customer, invoice__is_confirmed=True)
          .select_related("invoice", "variant__model").order_by("invoice__date", "invoice_id", "id"))
    if variant_ids is not None:
        qs = qs.filter(variant_id__in=variant_ids)
    done = dict(SaleReturnItem.objects.filter(sale_item__in=qs, return_doc__is_confirmed=True)
                .values_list("sale_item").annotate(s=Sum("quantity")))
    out = []
    for si in qs:
        left = si.quantity - (done.get(si.pk) or 0)
        if left > 0:
            out.append((si, left))
    return out


@staff_member_required
def pos_return_customer(request):
    """أصناف العميل اللي ينفع ترجع (مجمّعة بالصنف)، وسعرها بالـ FIFO من أقدم فاتورة"""
    if not request.user.has_perm("trade.add_salereturn"):
        raise PermissionDenied
    customer = Customer.objects.filter(pk=request.GET.get("customer") or 0).first()
    if customer is None:
        return JsonResponse({"ok": False, "error": "اختار العميل"}, status=404)
    rows = {}
    for si, left in _sold_lots(customer):
        v = si.variant
        r = rows.setdefault(v.pk, {"variant": v.pk, "code": v.model.code, "name": v.model.name, "size": v.size,
                                   "color": v.color, "category": v.model.get_category_display(),
                                   "returnable": 0, "lots": [], "channels": []})
        r["returnable"] += left
        r["lots"].append({"qty": left, "price": float((si.unit_price * si.invoice.net_ratio).quantize(Decimal("0.01"))),
                          "invoice": si.invoice_id})
        ch = si.invoice.get_channel_display()
        if ch not in r["channels"]:
            r["channels"].append(ch)
    cr = customer_credit(customer)
    return JsonResponse({"ok": True, "customer": {"id": customer.pk, "name": customer.name,
                                                  "balance": float(cr["balance"]), "type": customer.customer_type},
                         "items": list(rows.values())})


@staff_member_required
@require_POST
def pos_return(request):
    """المرتجع: برقم فاتورة (sale_items) أو بالعميل والصنف (variants، بيتوزع FIFO على فواتيره).
    كل فاتورة بيتعملها مرتجع بنفس رقم العملية، ورد الفلوس (خزن و/أو من الحساب) بيتوزع عليهم."""
    import uuid

    from .models import SaleReturn, SaleReturnItem
    if not request.user.has_perm("trade.add_salereturn"):
        raise PermissionDenied
    try:
        payload = json.loads(request.body)
        reason = (payload.get("reason") or "").strip()[:200]
        if not reason:
            raise ValidationError("اكتب سبب الارتجاع")
        ret_date = _parse_date(payload.get("date"))
        if ret_date > timezone.localdate():
            raise ValidationError("مينفعش التاريخ يبقى بعد النهارده")
        if ret_date != timezone.localdate() and not request.user.has_perm("trade.change_invoice_date"):
            raise ValidationError("تغيير تاريخ المرتجع لمدير الحسابات بس")
        delivery = payload.get("delivery") or "customer"
        if delivery not in dict(SaleInvoice.DELIVERY):
            raise ValidationError("طريقة الاستلام غير صحيحة")
        discount = Decimal(str(payload.get("discount") or 0))
        if discount < 0:
            raise ValidationError("الخصم غير صحيح")
        # الشاشة القديمة: mode + box
        if payload.get("refunds") is None:
            mode = payload.get("mode") or "box"
            payload["refunds"] = [{"box": "account" if mode == "account" else payload.get("box"), "amount": "rest"}]
        picks = []          # [(SaleItem, qty)]
        if payload.get("variants") is not None:
            customer = Customer.objects.filter(pk=payload.get("customer") or 0).first()
            if customer is None:
                raise ValidationError("اختار العميل")
            want = {int(i["variant"]): int(i["qty"]) for i in payload["variants"] if int(i.get("qty") or 0) > 0}
            lots = _sold_lots(customer, list(want))
            for vid, qty in want.items():
                for si, left in lots:
                    if si.variant_id != vid or qty <= 0:
                        continue
                    take = min(left, qty)
                    picks.append((si, take))
                    qty -= take
                if qty > 0:
                    raise ValidationError("الكمية أكبر من اللي العميل اشتراه ولسه ما رجعش")
        else:
            inv = SaleInvoice.objects.get(pk=int(payload.get("invoice")), is_confirmed=True)
            for i in payload.get("items") or []:
                qty = int(i.get("qty") or 0)
                if qty > 0:
                    si = inv.items.filter(pk=int(i["sale_item"])).first()
                    if si is None:
                        raise ValidationError("صنف مش من الفاتورة دي")
                    picks.append((si, qty))
        if not picks:
            raise ValidationError("اختار الأصناف والكميات اللي راجعة")

        with transaction.atomic():
            group = uuid.uuid4().hex[:12]
            by_inv = {}
            for si, qty in picks:
                by_inv.setdefault(si.invoice_id, []).append((si, qty))
            rets = []
            for inv_id, lines in by_inv.items():
                r = SaleReturn.objects.create(invoice_id=inv_id, date=ret_date, reason=reason, delivery=delivery,
                                              notes=(payload.get("notes") or "").strip()[:500], group=group,
                                              created_by=request.user)
                for si, qty in lines:
                    SaleReturnItem.objects.create(return_doc=r, sale_item=si, quantity=qty)
                rets.append(r)
            # خصم المرتجع بيتوزع على المرتجعات بنسبة قيمتها
            values = [r.priced_total() for r in rets]
            gross = sum(values, Decimal("0"))
            if discount > gross:
                raise ValidationError("الخصم أكبر من قيمة المرتجع")
            left_disc = discount
            for n, r in enumerate(rets):
                d = left_disc if n == len(rets) - 1 else (discount * values[n] / gross).quantize(Decimal("0.01")) if gross else Decimal("0")
                left_disc -= d
                r.discount = d
                r.save(update_fields=["discount"])
            net = [r.priced_total() for r in rets]
            total = sum(net, Decimal("0"))
            # رد الفلوس: "من الحساب" للفواتير الآجل الأول، والخزن على الباقي
            account, boxes = Decimal("0"), []
            for p in payload["refunds"]:
                amt = total if p.get("amount") == "rest" else Decimal(str(p.get("amount") or 0))
                if amt < 0:
                    raise ValidationError("مبلغ رد الفلوس غير صحيح")
                if p.get("box") == "account":
                    account += amt
                elif amt > 0:
                    box = CashBox.objects.filter(pk=p.get("box"), is_active=True).exclude(method="shipping").first()
                    if box is None:
                        raise ValidationError("اختار الخزنة اللي هترد منها الفلوس")
                    boxes.append([box, amt])
            if abs(account + sum((a for _b, a in boxes), Decimal("0")) - total) > Decimal("0.01"):
                raise ValidationError(f"مجموع رد الفلوس لازم يساوي قيمة المرتجع ({total:,.2f})")
            credit_room = sum((v for r, v in zip(rets, net) if r.invoice.payment_type == "credit"), Decimal("0"))
            if account > credit_room + Decimal("0.01"):
                raise ValidationError("الخصم من الحساب ينفع بس على مرتجع فواتير آجل "
                                      f"(أقصى حاجة {credit_room:,.2f})")
            for r, v in zip(rets, net):
                acc = min(account, v) if r.invoice.payment_type == "credit" else Decimal("0")
                account -= acc
                need, mine = v - acc, []
                for b in boxes:
                    if need <= 0:
                        break
                    take = min(b[1], need)
                    if take > 0:
                        mine.append((b[0], take))
                        b[1] -= take
                        need -= take
                r.confirm(refunds=mine, account=acc + need if need > Decimal("0.01") else acc)
    except ValidationError as e:
        return JsonResponse({"ok": False, "error": " ".join(e.messages)}, status=400)
    except (ValueError, TypeError, KeyError, InvalidOperation, SaleInvoice.DoesNotExist, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "بيانات غير صحيحة"}, status=400)
    nums = "، ".join(f"#{r.pk}" for r in rets)
    log_event(f"↩️ مرتجع بيع {nums} — {total:,.2f}\nالسبب: {reason}")
    return JsonResponse({"ok": True, "id": rets[0].pk, "total": float(total),
                         "print_url": reverse("return_print", args=[group]),
                         "message": f"اتسجل المرتجع ({nums}) بقيمة {total:,.2f} والأصناف رجعت للمخزن"})


@staff_member_required
def return_print(request, group):
    """إذن مرتجع البيع (ممكن يكون من كذا فاتورة بنفس رقم العملية)"""
    from .models import SaleReturn
    if not request.user.has_perm("trade.view_salereturn"):
        raise PermissionDenied
    rets = list(SaleReturn.objects.filter(group=group).order_by("id").select_related("invoice__customer", "invoice__seller",
                                                                       "created_by")
                .prefetch_related("items__sale_item__variant__model"))
    if not rets and group.isdigit():
        rets = list(SaleReturn.objects.filter(pk=int(group)).select_related("invoice__customer"))
    if not rets:
        from django.http import Http404
        raise Http404
    rows, plan = [], {}
    for r in rets:
        for it in r.items.all():
            v = it.sale_item.variant
            rows.append({"code": v.model.code, "name": v.model.name, "size": v.size, "color": v.color,
                         "category": v.model.get_category_display(), "qty": it.quantity, "price": it.unit_refund,
                         "total": it.quantity * it.unit_refund, "invoice": r.invoice_id})
        for p in r.refund_plan or [{"name": r.refund_box.name if r.refund_box_id else "خزنة", "amount": str(r.total)}]:
            plan[p["name"]] = plan.get(p["name"], Decimal("0")) + Decimal(p["amount"])
    first = rets[0]
    channels = sorted({r.invoice.get_channel_display() for r in rets})
    return render(request, "trade/return_print.html", {
        "rets": rets, "first": first, "rows": rows, "customer": first.invoice.customer,
        "gross": sum((r["total"] for r in rows), Decimal("0")) + sum((r.discount for r in rets), Decimal("0")),
        "discount": sum((r.discount for r in rets), Decimal("0")),
        "total": sum((r.total for r in rets), Decimal("0")), "pieces": sum(r["qty"] for r in rows),
        "plan": list(plan.items()), "channels": "، ".join(channels), "shop_name": SHOP_NAME})


# ---------------------------------------------------------------- موافقات مدير الحسابات
@staff_member_required
def sale_approvals(request):
    """فواتير البيع المستنية موافقة (حد الائتمان أو تغيير التاريخ)"""
    from django.contrib import messages
    from django.shortcuts import redirect
    if not request.user.has_perm("trade.approve_saleinvoice"):
        raise PermissionDenied
    if request.method == "POST":
        inv = get_object_or_404(SaleInvoice, pk=request.POST.get("invoice"), approval="pending")
        try:
            with transaction.atomic():
                if request.POST.get("decision") == "approve":
                    allocations = [(CashBox.objects.get(pk=p["box"]), Decimal(p["amount"]))
                                   for p in inv.payment_plan if p.get("box") != "account"]
                    inv.approval, inv.approved_by = "approved", request.user
                    inv.save()
                    inv.confirm()
                    record_sale_payments(inv, allocations)
                    messages.success(request, f"اتوافق على فاتورة #{inv.pk} واتخصمت من المخزن")
                    log_event(f"✅ اتوافق على فاتورة بيع #{inv.pk} ({inv.approval_reason})")
                else:
                    inv.approval, inv.approved_by = "rejected", request.user
                    inv.save()
                    messages.success(request, f"اترفضت فاتورة #{inv.pk}")
                    log_event(f"❌ اترفضت فاتورة بيع #{inv.pk} ({inv.approval_reason})")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect("sale_approvals")
    pending = (SaleInvoice.objects.filter(approval="pending").select_related("customer", "seller", "created_by")
               .prefetch_related("items__variant__model").order_by("id"))
    rows = []
    for inv in pending:
        rows.append({"inv": inv, "credit": customer_credit(inv.customer) if inv.customer_id else None})
    return render(request, "trade/sale_approvals.html", {
        **admin.site.each_context(request), "title": "فواتير مستنية موافقة", "rows": rows})
