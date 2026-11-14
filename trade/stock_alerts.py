"""الأصناف اللي وصلت لحد التنبيه بتاع موديلها"""
from django.db.models import F, Sum
from django.utils import timezone

from .models import RawMaterial, SaleItem, Variant


def low_stock_variants():
    return list(Variant.objects
                .filter(quantity__lte=F("model__low_stock_level"), model__is_active=True)
                .select_related("model").order_by("quantity", "model__name", "size"))


def sold_last_days(variant_ids, days=30):
    from datetime import timedelta
    since = timezone.localdate() - timedelta(days=days)
    return dict(SaleItem.objects
                .filter(variant_id__in=variant_ids, invoice__is_confirmed=True,
                        invoice__date__gte=since)
                .values_list("variant").annotate(s=Sum("quantity")))


def low_materials():
    return list(RawMaterial.objects.filter(is_active=True, low_stock_level__gt=0,
                                           quantity__lte=F("low_stock_level")))


def low_stock_text(limit=40):
    rows = low_stock_variants()
    mats = low_materials()
    if not rows and not mats:
        return "", 0
    sold = sold_last_days([v.pk for v in rows])
    out = [v for v in rows if v.quantity <= 0]
    low = [v for v in rows if v.quantity > 0]
    lines = [f"📦 ملخص المخزون: {len(out)} خلص، {len(low)} قرب يخلص"]
    for v in (out + low)[:limit]:
        icon = "⛔" if v.quantity <= 0 else "⚠️"
        s = sold.get(v.pk) or 0
        speed = f" — اتباع {s} في 30 يوم" if s else ""
        lines.append(f"{icon} {v}: {v.quantity} (الحد {v.model.low_stock_level}){speed}")
    if len(rows) > limit:
        lines.append(f"... و {len(rows) - limit} صنف كمان")
    if mats:
        lines.append(f"🧵 خامات قربت تخلص: {len(mats)}")
        for m in mats[:limit]:
            lines.append(f"• {m.name}: {m.quantity:g} {m.get_unit_display()} (الحد {m.low_stock_level:g})")
    return "\n".join(lines), len(rows) + len(mats)
