"""مجموعة التكويد: فورم واحدة بسيطة لكل حاجة (عميل، مورد، صنف) للإضافة والتعديل، بدل لوحة الإدارة.

الصنف: بنكتب المقاسات (السيري) والألوان، والسيستم بيعمل كل لون × كل مقاس. عدد السيري = عدد المقاسات.
أسعار البيع (قطاعي وجملة) بتيجي من فاتورة التوريد وقت تأكيدها.
"""
import re
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from .models import PARTY_TYPES, ClothingModel, Customer, Seller, ShippingZone, Supplier, Variant
from .stock_views import _size_key

PHONE_RE = re.compile(r"^\+?[0-9]{8,15}$")


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


def _phones(request):
    """الموبايلات من الفورم (أكتر من خانة)، من غير مسافات ومن غير تكرار"""
    out = []
    for p in request.POST.getlist("phones"):
        p = re.sub(r"[\s\-]", "", p.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
        if p and p not in out:
            out.append(p)
    return out


def _dec(raw, label, errors, allow_negative=False):
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        v = Decimal(raw)
    except InvalidOperation:
        errors.append(f"{label} لازم يكون رقم")
        return None
    if v < 0 and not allow_negative:
        errors.append(f"{label} لازم يكون موجب")
    return v


def _ctx(request, title, **extra):
    return {**admin.site.each_context(request), "title": title, "party_types": PARTY_TYPES, **extra}


# ---------------------------------------------------------------- العميل
@staff_member_required
def code_customer(request, pk=None):
    obj = get_object_or_404(Customer, pk=pk) if pk else None
    _need(request, "trade.change_customer" if obj else "trade.add_customer")
    errors, data = [], {}
    if request.method == "POST":
        data = {k: request.POST.get(k, "").strip() for k in
                ("name", "shop_name", "governorate", "area", "customer_type", "credit_limit", "notes", "seller")}
        phones = _phones(request)
        if not data["name"]:
            errors.append("اسم العميل مطلوب")
        if not phones:
            errors.append("اكتب رقم موبايل واحد على الأقل")
        bad = [p for p in phones if not PHONE_RE.match(p)]
        if bad:
            errors.append(f"رقم الموبايل مش صحيح: {bad[0]}")
        if not data["governorate"]:
            errors.append("اختار المحافظة")
        if not data["area"]:
            errors.append("اكتب المنطقة")
        if data["customer_type"] not in dict(PARTY_TYPES):
            errors.append("اختار نوع العميل")
        limit = _dec(data["credit_limit"], "حد الائتمان", errors)
        dup = Customer.objects.filter(phone__in=phones).exclude(pk=getattr(obj, "pk", None)).first()
        if dup:
            errors.append(f"الرقم ده متسجل للعميل {dup.name}")
        if not errors:
            c = obj or Customer()
            c.name, c.shop_name = data["name"], data["shop_name"]
            c.phone, c.other_phones = phones[0], " / ".join(phones[1:])
            c.governorate, c.area = data["governorate"], data["area"]
            c.customer_type, c.credit_limit, c.notes = data["customer_type"], limit, data["notes"]
            seller = Seller.objects.filter(pk=data["seller"] or 0, is_active=True).first()
            if obj is None or request.user.has_perm("trade.change_customer_seller"):
                c.seller = seller if seller else (c.seller if obj else None)
            c.save()
            messages.success(request, f"اتسجل العميل {c.name}")
            return redirect("customer_detail", pk=c.pk)
    elif obj:
        data = {"name": obj.name, "shop_name": obj.shop_name, "governorate": obj.governorate, "area": obj.area,
                "customer_type": obj.customer_type, "credit_limit": obj.credit_limit if obj.credit_limit is not None else "",
                "notes": obj.notes, "seller": obj.seller_id or ""}
    phones = (request.POST.getlist("phones") if request.method == "POST" else (obj.phones if obj else [])) or [""]
    return render(request, "trade/coding_customer.html", _ctx(
        request, "تعديل عميل" if obj else "تكويد عميل", obj=obj, data=data, errors=errors, phones=phones,
        governorates=ShippingZone.objects.values_list("name", flat=True),
        sellers=Seller.objects.filter(is_active=True).order_by("name"),
        can_seller=obj is None or request.user.has_perm("trade.change_customer_seller")))


# ---------------------------------------------------------------- المورد
@staff_member_required
def code_supplier(request, pk=None):
    obj = get_object_or_404(Supplier, pk=pk) if pk else None
    _need(request, "trade.change_supplier" if obj else "trade.add_supplier")
    can_opening = obj is None or request.user.has_perm("trade.import_data")
    errors, data = [], {}
    if request.method == "POST":
        data = {k: request.POST.get(k, "").strip() for k in
                ("name", "supplier_type", "opening_balance", "categories_other", "notes", "address")}
        cats = [c for c in request.POST.getlist("categories")
                if c in dict(ClothingModel.CATEGORIES) or c == Supplier.OTHER]
        data["cats"] = cats
        phones = _phones(request)
        if not data["name"]:
            errors.append("اسم المورد مطلوب")
        if not phones:
            errors.append("اكتب رقم موبايل واحد على الأقل")
        bad = [p for p in phones if not PHONE_RE.match(p)]
        if bad:
            errors.append(f"رقم الموبايل مش صحيح: {bad[0]}")
        if data["supplier_type"] not in dict(PARTY_TYPES):
            errors.append("اختار نوع المورد")
        if not cats:
            errors.append("اختار تصنيف واحد على الأقل (بيورّد إيه)")
        if Supplier.OTHER in cats and not data["categories_other"]:
            errors.append("اكتب \"أخرى\" دي إيه")
        opening = _dec(data["opening_balance"], "الرصيد الافتتاحي", errors, allow_negative=True) if can_opening else None
        if not errors:
            s = obj or Supplier()
            s.name, s.address = data["name"], data["address"]
            s.phone, s.other_phones = phones[0], " / ".join(phones[1:])
            s.supplier_type, s.notes = data["supplier_type"], data["notes"]
            s.categories = ",".join(cats)
            s.categories_other = data["categories_other"] if Supplier.OTHER in cats else ""
            if can_opening and opening is not None:
                s.opening_balance = opening
            s.save()
            messages.success(request, f"اتسجل المورد {s.name}")
            return redirect("suppliers")
    elif obj:
        data = {"name": obj.name, "supplier_type": obj.supplier_type, "opening_balance": obj.opening_balance or "",
                "categories_other": obj.categories_other, "notes": obj.notes, "address": obj.address,
                "cats": obj.category_keys}
    phones = (request.POST.getlist("phones") if request.method == "POST" else (obj.phones if obj else [])) or [""]
    return render(request, "trade/coding_supplier.html", _ctx(
        request, "تعديل مورد" if obj else "تكويد مورد", obj=obj, data=data, errors=errors, phones=phones,
        categories=ClothingModel.CATEGORIES + [(Supplier.OTHER, "أخرى")], can_opening=can_opening))


# ---------------------------------------------------------------- الصنف
def _list(raw):
    return [x.strip() for x in re.split(r"[,،/\n]+", raw or "") if x.strip()]


@staff_member_required
def code_item(request, pk=None):
    obj = get_object_or_404(ClothingModel, pk=pk) if pk else None
    _need(request, "trade.change_clothingmodel" if obj else "trade.add_clothingmodel")
    errors, data = [], {}
    existing = list(obj.variants.all()) if obj else []
    if request.method == "POST":
        data = {k: request.POST.get(k, "").strip() for k in ("code", "name", "category", "notes")}
        sizes = list(dict.fromkeys(s.upper() for s in _list(request.POST.get("sizes"))))
        colors = list(dict.fromkeys(_list(request.POST.get("colors"))))
        data.update(sizes=", ".join(sizes), colors=", ".join(colors))
        if not data["code"]:
            errors.append("كود الصنف مطلوب")
        elif ClothingModel.objects.filter(code__iexact=data["code"]).exclude(pk=getattr(obj, "pk", None)).exists():
            errors.append(f"الكود {data['code']} متسجل لصنف تاني")
        if not data["name"]:
            errors.append("اسم الصنف مطلوب")
        if not sizes:
            errors.append("اكتب مقاسات السيري (مثلاً M, L, XL, XXL)")
        if not colors:
            errors.append("اكتب لون واحد على الأقل")
        if data["category"] not in dict(ClothingModel.CATEGORIES):
            errors.append("اختار التصنيف")
        if not errors:
            with transaction.atomic():
                m = obj or ClothingModel()
                m.code, m.name, m.category, m.notes = data["code"], data["name"], data["category"], data["notes"]
                m.series_size = len(sizes)
                m.save()
                have = {(v.size.upper(), v.color) for v in m.variants.all()}
                added = 0
                for color in colors:
                    for size in sizes:
                        if (size, color) not in have:
                            Variant.objects.create(model=m, size=size, color=color)
                            added += 1
            msg = f"اتسجل الصنف {m.name} (سيري {m.series_size} قطع)"
            if added:
                msg += f" — اتعمل {added} مقاس/لون جديد"
            messages.success(request, msg + ". الأسعار بتتحدد من فاتورة التوريد.")
            return redirect("code_item_edit", pk=m.pk)
    elif obj:
        sizes = []
        for v in sorted(existing, key=_size_key):
            if v.size not in sizes:
                sizes.append(v.size)
        data = {"code": obj.code, "name": obj.name, "category": obj.category, "notes": obj.notes,
                "sizes": ", ".join(sizes), "colors": ", ".join(dict.fromkeys(v.color for v in existing if v.color))}
    return render(request, "trade/coding_item.html", _ctx(
        request, "تعديل صنف" if obj else "تكويد صنف", obj=obj, data=data, errors=errors,
        categories=ClothingModel.CATEGORIES, variants=sorted(existing, key=_size_key)))
