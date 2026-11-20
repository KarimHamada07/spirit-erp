"""الهولد: القطع اللي في أوردرات أونلاين لسه مفتوحة (اتجهزت أو اتشحنت).

الكمية دي بتتشال من "المتاح" (Variant.quantity) أول ما الأوردر يتسجل، عشان السيلز ما يبيعهاش،
وبتفضل ظاهرة في المخزون كـ "هولد في الشحن":
  - لو الأوردر اتحصّل: بتخرج من الهولد (اتباعت خلاص).
  - لو رجع: بترجع للمتاح.
فالمخزون الفعلي اللي بتملكه = المتاح + الهولد.
"""
from django.db.models import Sum

from .models import OnlineOrder, OnlineOrderItem

OPEN = (OnlineOrder.PREPARED, OnlineOrder.SHIPPED)


def held_by_variant(variant_ids=None):
    """{variant_id: {"prepared": n, "shipped": n, "total": n}}"""
    qs = OnlineOrderItem.objects.filter(order__status__in=OPEN)
    if variant_ids is not None:
        qs = qs.filter(variant_id__in=list(variant_ids))
    out = {}
    for row in qs.values("variant_id", "order__status").annotate(q=Sum("quantity")):
        d = out.setdefault(row["variant_id"], {"prepared": 0, "shipped": 0, "total": 0})
        d["prepared" if row["order__status"] == OnlineOrder.PREPARED else "shipped"] += row["q"]
        d["total"] += row["q"]
    return out


def held_by_model():
    """{model_id: عدد القطع الهولد}"""
    rows = (OnlineOrderItem.objects.filter(order__status__in=OPEN)
            .values("variant__model_id").annotate(q=Sum("quantity")))
    return {r["variant__model_id"]: r["q"] for r in rows}
