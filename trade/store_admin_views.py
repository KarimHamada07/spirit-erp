"""إدارة الموقع من السيستم: طلبات الموقع (تأكيد ← أوردر أونلاين، أو إلغاء)، ومنتجات الموقع (يظهر ولا لأ،
الوصف، الصور، الأسعار)."""
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import ClothingModel, ProductImage, Seller, StoreSettings, Variant, WebOrder
from .store_media import delete_files

ZERO = Decimal("0")


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


def _money(raw, default=ZERO):
    try:
        v = Decimal(str(raw).strip() or default)
    except InvalidOperation:
        raise ValidationError("رقم غير صحيح")
    if v < 0:
        raise ValidationError("الأرقام لازم تكون موجبة")
    return v


# ---------------------------------------------------------------- الطلبات
@staff_member_required
def orders(request):
    _need(request, "trade.view_weborder")
    status = request.GET.get("status", WebOrder.NEW)
    qs = WebOrder.objects.prefetch_related("items").select_related("online_order")
    if status in dict(WebOrder.STATUSES):
        qs = qs.filter(status=status)
    counts = dict(WebOrder.objects.values_list("status").annotate(c=Count("id")))
    tabs = [(k, v, counts.get(k, 0)) for k, v in WebOrder.STATUSES]
    return render(request, "trade/store_orders.html", {
        **admin.site.each_context(request), "title": "طلبات الموقع",
        "orders": qs[:200], "status": status, "tabs": tabs,
    })


@staff_member_required
def order_detail(request, pk):
    _need(request, "trade.view_weborder")
    order = get_object_or_404(WebOrder.objects.prefetch_related("items__variant__model"), pk=pk)
    if request.method == "POST":
        _need(request, "trade.change_weborder")
        action = request.POST.get("action")
        try:
            if action == "confirm":
                _need(request, "trade.add_onlineorder")
                seller = Seller.objects.filter(pk=request.POST.get("seller"), is_active=True).first()
                online = order.confirm(request.user, seller,
                                       shipping_cost=_money(request.POST.get("shipping_cost")),
                                       discount=_money(request.POST.get("discount")))
                messages.success(request, f"اتأكد الطلب واتعمل أوردر أونلاين #{online.pk} واتخصم من المخزون")
                return redirect("online_detail", pk=online.pk)
            if action == "cancel":
                order.cancel(request.user, request.POST.get("reason", "").strip())
                messages.success(request, f"اتلغى طلب الموقع #{order.pk}")
                return redirect("store_orders")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect("store_order", pk=pk)
    from .pos_views import wa_phone
    rows = []
    for i in order.items.all():
        rows.append({"item": i, "total": i.quantity * i.unit_price, "stock": i.variant.quantity,
                     "short": i.variant.quantity < i.quantity})
    s = StoreSettings.get()
    wa_text = (f"أهلاً {order.customer_name} 👋 معاك SPIRIT بخصوص طلبك رقم #{order.pk} "
               f"بإجمالي {order.total:,.2f} ج.م. ممكن نأكد العنوان: {order.zone_name} - {order.city} - {order.address}؟")
    from urllib.parse import quote
    return render(request, "trade/store_order.html", {
        **admin.site.each_context(request), "title": f"طلب الموقع #{order.pk}",
        "order": order, "rows": rows, "short": any(r["short"] for r in rows),
        "sellers": Seller.objects.filter(is_active=True).order_by("name"),
        "default_seller": s.web_seller_id,
        "wa": f"https://wa.me/{wa_phone(order.phone)}?text={quote(wa_text)}",
        "can_act": request.user.has_perm("trade.change_weborder") and request.user.has_perm("trade.add_onlineorder"),
    })


# ---------------------------------------------------------------- المنتجات
@staff_member_required
def products(request):
    _need(request, "trade.view_productimage")
    q = request.GET.get("q", "").strip()
    show = request.GET.get("show", "")
    qs = (ClothingModel.objects.filter(is_active=True)
          .annotate(stock=Sum("variants__quantity"), n_images=Count("images", distinct=True))
          .order_by("-show_online", "name"))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    if show == "1":
        qs = qs.filter(show_online=True)
    elif show == "0":
        qs = qs.filter(show_online=False)
    rows = list(qs.prefetch_related("images"))
    for m in rows:
        imgs = list(m.images.all())
        m.cover = imgs[0] if imgs else None
        m.problem = ("مفيش صور" if not imgs else "المخزون خلص" if not m.stock else "") if m.show_online else ""
    return render(request, "trade/store_products.html", {
        **admin.site.each_context(request), "title": "منتجات الموقع",
        "rows": rows, "q": q, "show": show,
        "online_count": ClothingModel.objects.filter(show_online=True, is_active=True).count(),
    })


@staff_member_required
def product_edit(request, pk):
    _need(request, "trade.view_productimage")
    m = get_object_or_404(ClothingModel, pk=pk)
    if request.method == "POST":
        what = request.POST.get("what")
        try:
            if what == "info":
                _need(request, "trade.change_clothingmodel")
                m.show_online = request.POST.get("show_online") == "1"
                m.featured = request.POST.get("featured") == "1"
                m.description = request.POST.get("description", "").strip()
                cat = request.POST.get("category", "")
                m.category = cat if cat in dict(ClothingModel.CATEGORIES) else m.category
                old = request.POST.get("old_price", "").strip()
                m.old_price = _money(old) if old else None
                m.save()
                messages.success(request, "اتحفظت بيانات المنتج")
            elif what == "prices":
                _need(request, "trade.change_variant")
                changed = 0
                for v in m.variants.all():
                    raw = request.POST.get(f"price_{v.pk}")
                    if raw is None or not raw.strip():
                        continue
                    price = _money(raw)
                    if price <= 0:
                        raise ValidationError("السعر لازم يكون أكبر من صفر")
                    if price != v.sale_price:
                        v.sale_price = price
                        v.save(update_fields=["sale_price"])
                        changed += 1
                messages.success(request, f"اتعدل سعر {changed} صنف" if changed else "مفيش أسعار اتغيرت")
            elif what == "images":
                _need(request, "trade.add_productimage")
                files = request.FILES.getlist("images")
                color = request.POST.get("color", "").strip()[:30]
                start = (m.images.order_by("-sort").values_list("sort", flat=True).first() or 0) + 1
                added = 0
                for n, f in enumerate(files[:20]):
                    ProductImage(model=m, image=f, color=color, sort=start + n).save()
                    added += 1
                messages.success(request, f"اتضافت {added} صورة" if added else "اختار صور الأول")
            elif what == "image_meta":
                _need(request, "trade.change_productimage")
                for img in m.images.all():
                    c = request.POST.get(f"color_{img.pk}")
                    srt = request.POST.get(f"sort_{img.pk}")
                    if c is not None:
                        img.color = c.strip()[:30]
                    if srt is not None and srt.strip().isdigit():
                        img.sort = int(srt)
                    img.save(update_fields=["color", "sort"])
                messages.success(request, "اتحفظ ترتيب الصور وألوانها")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect("store_product", pk=pk)
    from .stock_views import _size_key
    variants = sorted(m.variants.all(), key=_size_key)
    colors = sorted({v.color for v in variants if v.color})
    return render(request, "trade/store_product.html", {
        **admin.site.each_context(request), "title": f"منتج الموقع: {m.name}",
        "m": m, "variants": variants, "colors": colors, "images": m.images.all(),
        "categories": ClothingModel.CATEGORIES,
        "preview": reverse("store:product", args=[m.code]),
    })


@staff_member_required
@require_POST
def image_delete(request, pk):
    _need(request, "trade.delete_productimage")
    img = get_object_or_404(ProductImage, pk=pk)
    model_pk = img.model_id
    delete_files(img.image, img.thumb)
    img.delete()
    messages.success(request, "اتمسحت الصورة")
    return redirect("store_product", pk=model_pk)
