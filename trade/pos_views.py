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
    return JsonResponse({
        "prices": {str(k): float(v) for k, v in prices.items()},
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
    } for v in variants]
    context = {
        **admin.site.each_context(request),
        "title": "بيع سريع",
        "variants": data,
        "customers": Customer.objects.select_related("seller").order_by("name"),
        "sellers": Seller.objects.filter(is_active=True).order_by("name"),
        "my_seller": getattr(getattr(request.user, "seller_profile", None), "pk", None),
        "boxes": CashBox.objects.filter(is_active=True),
        "channels": SaleInvoice.CHANNELS,
        "can_edit_price": request.user.has_perm("trade.change_sale_price"),
        "can_return": request.user.has_perm("trade.add_salereturn"),
        "return_boxes": CashBox.objects.filter(is_active=True).exclude(method="shipping"),
    }
    return render(request, "trade/pos.html", context)


def _parse_payments(raw):
    allocations = []
    for p in raw or []:
        box = CashBox.objects.filter(pk=p.get("box"), is_active=True).first()
        if box is None:
            raise ValidationError("طريقة الدفع غير صحيحة")
        amount = Decimal(str(p.get("amount") or 0))
        if amount < 0:
            raise ValidationError("المبلغ غير صحيح")
        if amount > 0:
            allocations.append((box, amount))
    return allocations


@staff_member_required
@require_POST
def pos_checkout(request):
    if not request.user.has_perm("trade.add_saleinvoice"):
        raise PermissionDenied
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
        credit = bool(payload.get("credit"))
        channel = payload.get("channel") or "retail"
        if channel not in dict(SaleInvoice.CHANNELS):
            raise ValidationError("نوع الفاتورة غير صحيح")
        allocations = _parse_payments(payload.get("payments"))

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
            if credit and customer is None:
                raise ValidationError("اختار العميل للبيع الآجل")
            last_prices = customer_last_prices(customer)
            can_change_price = request.user.has_perm("trade.change_customer_price")
            can_edit_price = request.user.has_perm("trade.change_sale_price")
            if not credit and not allocations:
                raise ValidationError("اختار طريقة الدفع")
            invoice = SaleInvoice.objects.create(
                customer=customer,
                date=timezone.localdate(),
                discount=discount,
                discount_percent=percent or None,
                payment_type="credit" if credit else "cash",
                channel=channel,
                seller=seller,
                notes="بيع سريع",
            )
            gross = Decimal("0")
            for it in items:
                qty = int(it["qty"])
                price = Decimal(str(it["price"]))
                if qty <= 0 or price < 0:
                    raise ValidationError("كمية أو سعر غير صحيح")
                variant = Variant.objects.get(pk=it["id"])
                last = last_prices.get(variant.pk)
                if last is not None and price != last and not can_change_price:
                    raise ValidationError(
                        f"سعر {variant.model.name} للعميل ده لازم يكون {last:.2f} "
                        "زي الفاتورة السابقة، والتعديل من مدير الحسابات")
                listed = variant.price_for(channel)
                # أول مرة العميل ياخد الصنف: البياع يحدد السعر وبعدها يتثبت. من غير عميل: سعر القايمة
                if last is None and customer is None and price != listed and not can_edit_price:
                    kind = "الجملة" if channel == "wholesale" else "القطاعي"
                    raise ValidationError(
                        f"سعر {variant} ({kind}) هو {listed:.2f}، وتعديل السعر لمدير الحسابات بس")
                SaleItem.objects.create(invoice=invoice, variant=variant,
                                        quantity=qty, unit_price=price)
                gross += qty * price
            if percent:
                discount = (gross * percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if discount > gross:
                raise ValidationError("الخصم أكبر من إجمالي الفاتورة")
            invoice.confirm()  # يخصم المخزون ويثبت التكلفة
            record_sale_payments(invoice, allocations)  # يسجل الدفع في الخزن

    except ValidationError as e:
        return JsonResponse({"ok": False, "error": " ".join(e.messages)}, status=400)
    except (ValueError, TypeError, KeyError, InvalidOperation,
            Variant.DoesNotExist, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "بيانات غير صحيحة"}, status=400)

    return JsonResponse({
        "ok": True,
        "id": invoice.id,
        "print_url": reverse("sale_print", args=[invoice.id]),
    })


def _invoice_context(invoice):
    rows = [{
        "code": i.variant.model.code,
        "name": str(i.variant),
        "qty": i.quantity,
        "price": i.unit_price,
        "total": i.quantity * i.unit_price,
    } for i in invoice.items.select_related("variant", "variant__model")]
    gross = sum((r["total"] for r in rows), Decimal("0"))
    payments = [{"box": e.box.name, "amount": e.amount}
                for e in CashEntry.objects.filter(
                    invoice=invoice, kind__in=["sale", "payment"]).select_related("box")]
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


@staff_member_required
@require_POST
def pos_return(request):
    from .models import SaleReturn, SaleReturnItem
    if not request.user.has_perm("trade.add_salereturn"):
        raise PermissionDenied
    try:
        payload = json.loads(request.body)
        inv = SaleInvoice.objects.get(pk=int(payload.get("invoice")), is_confirmed=True)
        mode = payload.get("mode") or "box"
        if mode not in ("box", "account"):
            raise ValidationError("طريقة رد الفلوس غير صحيحة")
        box = None
        if mode == "box":
            box = CashBox.objects.filter(pk=payload.get("box"), is_active=True).exclude(method="shipping").first()
            if box is None:
                raise ValidationError("اختار الخزنة اللي هترد منها الفلوس")
        lines = [(int(i["sale_item"]), int(i["qty"])) for i in payload.get("items") or [] if int(i.get("qty") or 0) > 0]
        if not lines:
            raise ValidationError("اختار الأصناف والكميات اللي راجعة")
        with transaction.atomic():
            ret = SaleReturn.objects.create(invoice=inv, refund_mode=mode, refund_box=box,
                                            reason=(payload.get("reason") or "").strip()[:200])
            for si_id, qty in lines:
                si = inv.items.filter(pk=si_id).first()
                if si is None:
                    raise ValidationError("صنف مش من الفاتورة دي")
                SaleReturnItem.objects.create(return_doc=ret, sale_item=si, quantity=qty)
            ret.confirm()
    except ValidationError as e:
        return JsonResponse({"ok": False, "error": " ".join(e.messages)}, status=400)
    except (ValueError, TypeError, KeyError, SaleInvoice.DoesNotExist, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "بيانات غير صحيحة"}, status=400)
    return JsonResponse({"ok": True, "id": ret.pk, "total": float(ret.total),
                         "message": f"اتسجل مرتجع #{ret.pk} بقيمة {ret.total:,.2f} والأصناف رجعت للمخزن"})
