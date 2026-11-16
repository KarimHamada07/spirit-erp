"""الموقع (المتجر الأونلاين) على spiritegy.org.

نفس الكود ونفس قاعدة البيانات بتوع السيستم: الأسعار والمخزون بيتقروا مباشرة، فاللي بيتباع في المحل
بيختفي من الموقع على طول. الطلب بيتسجل WebOrder (محتاج تأكيد)، والتأكيد من السيستم بيعمل أوردر أونلاين.

StoreHostMiddleware: أي طلب على دومين من settings.STORE_HOSTS بيستخدم config/store_urls.py (صفحات
المتجر بس، من غير لوحة الإدارة). وعلى دومين السيستم المتجر متاح للمعاينة على /shop/.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Min, Prefetch, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (ClothingModel, ProductImage, ShippingZone, StoreSettings, Variant, WebOrder,
                     WebOrderItem)
from .notify import client_ip, fmt, log_event

ZERO = Decimal("0")
MAX_QTY = 10          # أقصى عدد من نفس الصنف في الطلب الواحد
PHONE_RE = re.compile(r"^01[0125]\d{8}$")


class StoreHostMiddleware:
    """دومين المتجر بيفتح صفحات المتجر بس"""

    def __init__(self, get_response):
        self.get_response = get_response
        self.hosts = {h.lower() for h in getattr(settings, "STORE_HOSTS", [])}

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower() if self.hosts else ""
        if host in self.hosts:
            request.urlconf = "config.store_urls"
            request.is_store_host = True
        return self.get_response(request)


# ---------------------------------------------------------------- مساعدات
def _visible_models():
    return (ClothingModel.objects.filter(show_online=True, is_active=True)
            .annotate(stock=Sum("variants__quantity"), min_price=Min("variants__sale_price"))
            .filter(stock__gt=0)
            .prefetch_related(Prefetch("images", queryset=ProductImage.objects.order_by("sort", "id")),
                              Prefetch("variants", queryset=Variant.objects.filter(quantity__gt=0),
                                       to_attr="in_stock")))


def _cover(m):
    imgs = list(m.images.all())
    return imgs[0] if imgs else None


def _cards(qs):
    """كروت المنتجات. لو الموديل ليه مقاس/لون واحد متاح، زرار + بيضيفه للسلة على طول"""
    out = []
    for m in qs:
        stock = getattr(m, "in_stock", None)
        out.append({"m": m, "cover": _cover(m), "second": (list(m.images.all())[1:2] or [None])[0],
                    "one": stock[0] if stock is not None and len(stock) == 1 else None,
                    "colors": [color_hex(c) for c in dict.fromkeys(v.color for v in (stock or []) if v.color)][:4]})
    return out


# ألوان الهدوم الشائعة (للدواير الصغيرة جنب اللون). أي لون مش هنا بيظهر بالاسم بس
COLOR_HEX = {
    "أسود": "#121214", "اسود": "#121214", "أبيض": "#f4f4f2", "ابيض": "#f4f4f2", "أوف وايت": "#ece7dc",
    "أحمر": "#d1121b", "احمر": "#d1121b", "نبيتي": "#6d1a2b", "كحلي": "#1f2a44", "أزرق": "#2f5fa8", "ازرق": "#2f5fa8",
    "لبني": "#9cc3e6", "رمادي": "#8a8d93", "رصاصي": "#8a8d93", "فحمي": "#3a3b3f", "بيج": "#d8c8a8",
    "جملي": "#b08350", "بني": "#6b4a2f", "زيتي": "#4a5a3a", "أخضر": "#2e7d4f", "اخضر": "#2e7d4f",
    "منت": "#a8dcc8", "موف": "#8a6aa8", "بمبي": "#f2a7c3", "وردي": "#f2a7c3", "أصفر": "#f2c230", "اصفر": "#f2c230",
    "برتقالي": "#ee7a22", "سيمون": "#f4a582", "دنيم": "#4f6f96",
}


def color_hex(name):
    return COLOR_HEX.get((name or "").strip(), "")


def _cart(request):
    raw = request.session.get("cart") or {}
    return {str(k): int(v) for k, v in raw.items() if str(k).isdigit() and int(v) > 0}


def _save_cart(request, cart):
    request.session["cart"] = cart
    request.session.modified = True


def cart_lines(cart):
    """سطور السلة من المخزون الحالي (الكمية بتتظبط على المتاح)"""
    variants = {str(v.pk): v for v in Variant.objects.filter(pk__in=[int(k) for k in cart])
                .select_related("model").prefetch_related("model__images")}
    lines, total = [], ZERO
    for vid, qty in cart.items():
        v = variants.get(vid)
        if v is None or not v.model.show_online or not v.model.is_active or v.quantity <= 0:
            continue
        qty = min(qty, v.quantity, MAX_QTY)
        img = None
        for i in v.model.images.all():
            if i.color and i.color == v.color:
                img = i
                break
        img = img or _cover(v.model)
        lines.append({"v": v, "qty": qty, "price": v.sale_price, "total": qty * v.sale_price, "img": img})
        total += qty * v.sale_price
    return lines, total


def _ctx(request, **extra):
    s = StoreSettings.get()
    cart = _cart(request)
    cats = (ClothingModel.objects.filter(show_online=True, is_active=True, variants__quantity__gt=0)
            .exclude(category="").values_list("category", flat=True).distinct())
    labels = dict(ClothingModel.CATEGORIES)
    from .middleware import _asset
    return {"s": s, "cart_count": sum(cart.values()), "css": _asset("trade/store/store.css"),
            "nav_cats": [(c, labels.get(c, c)) for c in sorted(set(cats), key=lambda c: list(labels).index(c)
                                                                   if c in labels else 99)],
            "wa": _wa(s.whatsapp), "year": timezone.localdate().year, **extra}


def _wa(phone):
    from .pos_views import wa_phone
    return f"https://wa.me/{wa_phone(phone)}" if phone else ""


# ---------------------------------------------------------------- الصفحات
def home(request):
    models = _visible_models()
    featured = list(models.filter(featured=True).order_by("-id")[:8])
    latest = list(models.order_by("-id")[:12])
    # البانر: صورة أحدث موديل مميز، وجنبه أكبر قسمين بصورة موديل من كل قسم وأقل سعر
    labels = dict(ClothingModel.CATEGORIES)
    tiles = []
    top = (ClothingModel.objects.filter(show_online=True, is_active=True, variants__quantity__gt=0)
           .exclude(category="").values("category").annotate(n=Count("id", distinct=True)).order_by("-n")[:2])
    for cat in [r["category"] for r in top]:
        m = models.filter(category=cat).order_by("-id").first()
        low = models.filter(category=cat).aggregate(p=Min("min_price"))["p"]
        tiles.append({"key": cat, "label": labels.get(cat, cat), "cover": _cover(m) if m else None, "low": low})
    hero_model = (featured or latest or [None])[0]
    return render(request, "trade/store/home.html", _ctx(
        request, featured=_cards(featured), latest=_cards(latest), tiles=tiles,
        hero_cover=_cover(hero_model) if hero_model else None))


SORTS = {"new": "-id", "low": "min_price", "high": "-min_price"}


def listing(request, category=None):
    labels = dict(ClothingModel.CATEGORIES)
    if category and category not in labels:
        raise Http404
    qs = _visible_models()
    if category:
        qs = qs.filter(category=category)
    q = request.GET.get("q", "").strip()[:60]
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__iexact=q) | Q(description__icontains=q))
    size = request.GET.get("size", "").strip()[:20]
    if size:
        qs = qs.filter(variants__size=size, variants__quantity__gt=0).distinct()
    sort = request.GET.get("sort", "new")
    qs = qs.order_by(SORTS.get(sort, "-id"))
    sizes = (Variant.objects.filter(model__show_online=True, model__is_active=True, quantity__gt=0,
                                    **({"model__category": category} if category else {}))
             .values_list("size", flat=True).distinct())
    from .stock_views import _size_key
    sizes = sorted(set(sizes), key=lambda s: _size_key(type("V", (), {"size": s, "color": ""})()))
    title = labels.get(category) if category else ("نتايج البحث" if q else "كل المنتجات")
    return render(request, "trade/store/listing.html", _ctx(
        request, cards=_cards(qs), title=title, category=category, q=q, sort=sort, size=size, sizes=sizes))


def product(request, code):
    m = get_object_or_404(ClothingModel, code=code, show_online=True, is_active=True)
    from .stock_views import _size_key
    variants = sorted(m.variants.all(), key=_size_key)
    if not any(v.quantity > 0 for v in variants):
        raise Http404
    images = list(m.images.all())
    colors = []
    for v in variants:
        if v.color not in colors:
            colors.append(v.color)
    data = [{"id": v.pk, "size": v.size, "color": v.color, "qty": min(v.quantity, MAX_QTY),
             "left": v.quantity if v.quantity <= 3 else None, "price": float(v.sale_price)} for v in variants]
    imgs = [{"src": i.image.url, "thumb": i.thumb_url, "color": i.color}
            for i in images]
    related = _cards(_visible_models().filter(category=m.category).exclude(pk=m.pk).order_by("-id")[:4]) \
        if m.category else []
    prices = [v.sale_price for v in variants if v.quantity > 0]
    return render(request, "trade/store/product.html", _ctx(
        request, m=m, images=images, colors=colors, variants_json=data, images_json=imgs,
        color_hex={c: color_hex(c) for c in colors},
        discount_pct=int(round((1 - min(prices) / m.old_price) * 100)) if m.old_price and m.old_price > min(prices) else 0,
        price=min(prices), max_price=max(prices), related=related,
        category_label=dict(ClothingModel.CATEGORIES).get(m.category, "")))


@require_POST
def cart_add(request):
    v = Variant.objects.filter(pk=request.POST.get("variant") or 0, model__show_online=True,
                               model__is_active=True).select_related("model").first()
    if v is None or v.quantity <= 0:
        return redirect(request.META.get("HTTP_REFERER") or reverse("store:home"))
    try:
        qty = max(1, min(int(request.POST.get("qty", 1)), MAX_QTY))
    except ValueError:
        qty = 1
    cart = _cart(request)
    cart[str(v.pk)] = min(cart.get(str(v.pk), 0) + qty, v.quantity, MAX_QTY)
    _save_cart(request, cart)
    if request.POST.get("buy_now"):
        return redirect("store:checkout")
    return redirect(f"{reverse('store:cart')}?added={v.pk}")


@require_POST
def cart_update(request):
    cart = _cart(request)
    for key, val in request.POST.items():
        if key.startswith("qty_") and key[4:] in cart:
            try:
                n = int(val)
            except ValueError:
                continue
            if n <= 0:
                cart.pop(key[4:])
            else:
                cart[key[4:]] = min(n, MAX_QTY)
    if request.POST.get("remove") in cart:
        cart.pop(request.POST["remove"])
    _save_cart(request, cart)
    return redirect("store:cart")


def cart(request):
    lines, total = cart_lines(_cart(request))
    added = request.GET.get("added")
    return render(request, "trade/store/cart.html", _ctx(
        request, lines=lines, total=total, added=added and any(str(l["v"].pk) == added for l in lines)))


def _shipping_fee(zone, total, s):
    if s.free_shipping_over and total >= s.free_shipping_over:
        return ZERO
    return zone.fee if zone else ZERO


def checkout(request):
    s = StoreSettings.get()
    cart_ = _cart(request)
    lines, total = cart_lines(cart_)
    if not lines:
        return redirect("store:cart")
    zones = list(ShippingZone.objects.filter(is_active=True))
    errors, data = [], {}
    if request.method == "POST":
        data = {k: request.POST.get(k, "").strip() for k in
                ("name", "phone", "phone2", "zone", "city", "address", "notes")}
        phone = re.sub(r"\D", "", data["phone"].translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
        phone2 = re.sub(r"\D", "", data["phone2"].translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
        zone = next((z for z in zones if str(z.pk) == data["zone"]), None)
        if request.POST.get("website"):           # فخ للبوتات (خانة مخفية)
            errors.append("حصلت مشكلة، جرّب تاني")
        if not s.is_open:
            errors.append("الطلبات واقفة مؤقتاً، كلمنا على واتساب")
        if len(data["name"]) < 3:
            errors.append("اكتب الاسم")
        if not PHONE_RE.match(phone):
            errors.append("رقم الموبايل لازم يكون 11 رقم ويبدأ بـ 01")
        if phone2 and not PHONE_RE.match(phone2):
            errors.append("رقم الموبايل التاني مش صحيح")
        if zones and zone is None:
            errors.append("اختار المحافظة")
        if len(data["city"]) < 2:
            errors.append("اكتب المدينة أو المنطقة")
        if len(data["address"]) < 6:
            errors.append("اكتب العنوان بالتفصيل (الشارع ورقم العمارة)")
        recent = WebOrder.objects.filter(phone=phone, status=WebOrder.NEW,
                                         created_at__gte=timezone.now() - timedelta(hours=6)).count()
        if recent >= 3:
            errors.append("عندك طلبات لسه بنأكدها، هنكلمك قريب. لو محتاج حاجة كلمنا على واتساب")
        if not errors:
            try:
                order = _place_order(request, lines, data, phone, phone2, zone, total, s)
            except ValidationError as e:
                errors.extend(e.messages)
            else:
                _save_cart(request, {})
                request.session["last_order"] = order.token
                return redirect("store:order_done", token=order.token)
    fee = _shipping_fee(None, total, s)
    return render(request, "trade/store/checkout.html", _ctx(
        request, lines=lines, total=total, zones=zones, errors=errors, data=data, base_fee=fee,
        zones_json=[{"id": z.pk, "fee": float(_shipping_fee(z, total, s)), "days": z.days} for z in zones]))


@transaction.atomic
def _place_order(request, lines, data, phone, phone2, zone, total, s):
    # اتأكد من المخزون تاني لحظة الطلب
    for l in lines:
        v = Variant.objects.select_for_update().get(pk=l["v"].pk)
        if v.quantity < l["qty"]:
            raise ValidationError(f"{v} فاضل منه {v.quantity} بس، عدّل الكمية في السلة")
    order = WebOrder.objects.create(
        customer_name=data["name"][:150], phone=phone, phone2=phone2, zone=zone,
        zone_name=zone.name if zone else "", city=data["city"][:80], address=data["address"][:300],
        notes=data["notes"][:300], shipping_fee=_shipping_fee(zone, total, s), ip=client_ip(request) or None)
    for l in lines:
        WebOrderItem.objects.create(order=order, variant=l["v"], quantity=l["qty"], unit_price=l["price"])
    text = [f"🛒 طلب جديد من الموقع #{order.pk} — محتاج تأكيد",
            f"العميل: {order.customer_name} — {order.phone}",
            f"العنوان: {order.zone_name} - {order.city} - {order.address}"]
    text += [f"• {l['v']} × {l['qty']} @ {fmt(l['price'])}" for l in lines]
    text.append(f"الإجمالي بالشحن: {fmt(order.total)}")
    transaction.on_commit(lambda: log_event("\n".join(text)))
    return order


def order_done(request, token):
    order = get_object_or_404(WebOrder.objects.prefetch_related("items__variant__model"), token=token)
    if request.session.get("last_order") != token:
        return redirect("store:track")
    return render(request, "trade/store/order_done.html", _ctx(request, order=order))


def _public_status(order):
    if order.status == WebOrder.CANCELLED:
        return "اتلغى", "cancelled"
    if order.status == WebOrder.NEW:
        return "مستني تأكيد (هنكلمك قريب)", "new"
    o = order.online_order
    if o is None:
        return "اتأكد", "confirmed"
    return {"prepared": ("اتأكد وبيتجهز", "confirmed"), "shipped": ("اتشحن ومع شركة الشحن", "shipped"),
            "collected": ("اتسلّم", "done"), "returned": ("رجع", "cancelled")}[o.status]


def track(request):
    result, error = None, ""
    if request.GET.get("phone") or request.GET.get("no"):
        phone = re.sub(r"\D", "", request.GET.get("phone", ""))
        no = re.sub(r"\D", "", request.GET.get("no", ""))
        order = WebOrder.objects.filter(pk=no or 0, phone=phone).select_related("online_order").first() \
            if phone and no else None
        if order is None:
            error = "مفيش طلب بالرقم والموبايل دول. اتأكد منهم، أو كلمنا على واتساب"
        else:
            label, key = _public_status(order)
            result = {"order": order, "label": label, "key": key}
    return render(request, "trade/store/track.html", _ctx(request, result=result, error=error,
                                                           phone=request.GET.get("phone", ""),
                                                           no=request.GET.get("no", "")))


def robots(request):
    lines = ["User-agent: *", "Disallow: /cart/", "Disallow: /checkout/", "Disallow: /order/",
             f"Sitemap: {request.build_absolute_uri(reverse('store:sitemap'))}"]
    if not getattr(request, "is_store_host", False):
        lines = ["User-agent: *", "Disallow: /"]      # السيستم نفسه ما يتأرشفش
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


def sitemap(request):
    urls = [request.build_absolute_uri(reverse("store:home"))]
    for c, _l in ClothingModel.CATEGORIES:
        urls.append(request.build_absolute_uri(reverse("store:category", args=[c])))
    for m in _visible_models():
        urls.append(request.build_absolute_uri(reverse("store:product", args=[m.code])))
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    body += [f"<url><loc>{u}</loc></url>" for u in urls]
    body.append("</urlset>")
    return HttpResponse("\n".join(body), content_type="application/xml")
