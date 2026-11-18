"""صفحات الباركود: طباعة الملصقات، وفتح أي باركود (فاتورة أو صنف) بعد الإسكان."""
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from . import barcodes
from .security import safe_back
from .labels import SIZES, labels_pdf
from .models import ClothingModel, OnlineOrder, PurchaseItem, SaleInvoice, Variant

MAX_LABELS = 2000


def _ids(raw):
    return [int(x) for x in (raw or "").split(",") if x.strip().isdigit()]


@staff_member_required
def barcode_labels(request):
    """اختار الموديلات (أو فاتورة توريد) وعدد الملصقات لكل مقاس/لون، وبيطلع PDF للطابعة"""
    if not request.user.has_perm("trade.view_clothingmodel"):
        raise PermissionDenied
    if request.method == "POST":
        entries, total = [], 0
        variants = Variant.objects.select_related("model").in_bulk(_ids(",".join(request.POST.getlist("v"))))
        for pk, v in sorted(variants.items(), key=lambda kv: (kv[1].model.code, kv[1].size, kv[1].color)):
            try:
                n = max(0, min(int(request.POST.get(f"n_{pk}") or 0), 500))
            except ValueError:
                n = 0
            if n:
                entries.append((v, n))
                total += n
        if not entries:
            messages.error(request, "اكتب عدد ملصقات لصنف واحد على الأقل")
            return redirect(request.get_full_path())
        if total > MAX_LABELS:
            messages.error(request, f"أقصى عدد في المرة {MAX_LABELS} ملصق")
            return redirect(request.get_full_path())
        pdf = labels_pdf(entries, size=request.POST.get("size", "50x30"),
                         show_price=bool(request.POST.get("price")))
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = 'inline; filename="barcode-labels.pdf"'
        return resp

    counts, source = {}, ""
    purchase = _ids(request.GET.get("purchase"))
    model_ids = _ids(request.GET.get("models")) or _ids(request.GET.get("model"))
    if purchase:
        for it in PurchaseItem.objects.filter(invoice_id__in=purchase):
            counts[it.variant_id] = counts.get(it.variant_id, 0) + it.quantity
        source = "فاتورة توريد " + "، ".join(f"#{p}" for p in purchase) + " (عدد الملصقات = الكمية اللي اتوردت)"
    q = request.GET.get("q", "").strip()
    qs = Variant.objects.select_related("model").order_by("model__code", "size", "color")
    if counts:
        qs = qs.filter(pk__in=counts)
    elif model_ids:
        qs = qs.filter(model_id__in=model_ids)
    elif q:
        qs = qs.filter(model__code__icontains=q) | qs.filter(model__name__icontains=q) | qs.filter(barcode=barcodes.clean(q))
    else:
        qs = qs.none()
    rows = [{"v": v, "n": counts.get(v.pk, max(v.quantity, 0) if not counts else 0)} for v in qs[:400]]
    return render(request, "trade/barcode_labels.html", {
        **admin.site.each_context(request), "title": "ملصقات الباركود", "rows": rows, "q": q,
        "source": source, "sizes": SIZES,
        "models": ClothingModel.objects.filter(is_active=True).order_by("code").values_list("pk", "code", "name"),
        "model_ids": model_ids,
    })


@staff_member_required
def scan_open(request):
    """الإسكانر في أي صفحة: باركود الفاتورة بيفتحها، وباركود الصنف بيفتح تكويده"""
    code = barcodes.clean(request.GET.get("c"))
    doc = barcodes.parse_doc(code)
    if doc:
        kind, pk = doc
        if kind == "sale" and SaleInvoice.objects.filter(pk=pk).exists():
            if not request.user.has_perm("trade.view_saleinvoice"):
                raise PermissionDenied
            return redirect("sale_print", pk=pk)
        if kind == "online" and OnlineOrder.objects.filter(pk=pk).exists():
            if not request.user.has_perm("trade.view_onlineorder"):
                raise PermissionDenied
            return redirect("online_detail", pk=pk)
    v = Variant.objects.filter(barcode=code).select_related("model").first()
    m = v.model if v else ClothingModel.objects.filter(code__iexact=code).first() if code else None
    if m and not request.user.has_perm("trade.view_variant"):
        raise PermissionDenied
    if m:
        if request.user.has_perm("trade.change_clothingmodel"):
            return redirect("code_item_edit", pk=m.pk)
        return redirect(reverse("admin:trade_variant_changelist") + "?q=" + (v.barcode if v else m.code))
    messages.error(request, f"مفيش فاتورة أو صنف بالباركود {code or '—'}")
    return redirect(safe_back(request, reverse("dashboard")))
