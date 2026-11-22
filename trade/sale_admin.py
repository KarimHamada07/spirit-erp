"""فاتورة البيع في لوحة الإدارة.

- البنود: كود الصنف ← الاسم والتصنيف بيظهروا لوحدهم، واللون والمقاس اختيار، والكمية مش أكتر من الرصيد
  (بيظهر تحتها)، والسعر بيتحط لوحده من سعر البيع (قطاعي/جملة حسب القناة) وينفع يزيد بس، ما ينفعش يقل.
- البائع بيتاخد من العميل المكوّد عليه وما يتغيرش. لو العميل مالوش بائع (أو نقدي) تختاره.
- ربحية الفاتورة بتظهر لمدير الحسابات بس (صلاحية approve_saleinvoice).
- من غير أيقونات (قلم، +، x، عين) جنب الاختيارات.
"""
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .exports import xlsx_response
from .models import ClothingModel, Customer, SaleInvoice, SaleItem, Variant

PROFIT_PERM = "trade.approve_saleinvoice"


def can_see_profit(user):
    return user.is_superuser or user.has_perm(PROFIT_PERM)


def invoice_profit(inv):
    """ربح الفاتورة: المؤكدة بالتكلفة اللي اتثبتت، والمسودة تقديري بالتكلفة الحالية للصنف"""
    if inv.is_confirmed:
        return inv.profit
    items = inv.items.select_related("variant__model")
    gross = sum(((i.unit_price - i.variant.cost_basis) * i.quantity for i in items), Decimal("0"))
    return gross - inv.discount


def _no_icons(formfield):
    """يشيل أيقونات الإضافة/التعديل/الحذف/العرض جنب الاختيار"""
    w = getattr(formfield, "widget", None) if formfield else None
    if w is not None and hasattr(w, "can_add_related"):
        w.can_add_related = w.can_change_related = w.can_delete_related = w.can_view_related = False
    return formfield


def _ro(placeholder=""):
    return forms.CharField(required=False, disabled=True, widget=forms.TextInput(
        attrs={"readonly": "readonly", "class": "si-ro", "tabindex": "-1", "placeholder": placeholder}))


class SaleItemForm(forms.ModelForm):
    model_code = forms.ChoiceField(label="كود الصنف", required=True)
    item_name = _ro()
    color = forms.CharField(label="اللون", required=False, widget=forms.Select(attrs={"class": "si-color"}))
    size = forms.CharField(label="المقاس", required=False, widget=forms.Select(attrs={"class": "si-size"}))
    category = _ro()

    class Meta:
        model = SaleItem
        fields = ("quantity", "unit_price")
        labels = {"quantity": "الكمية", "unit_price": "سعر القطعة"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item_name"].label = "اسم الصنف"
        self.fields["category"].label = "التصنيف"
        codes = ClothingModel.objects.filter(is_active=True).order_by("code").values_list("code", "name")
        self.fields["model_code"].choices = [("", "— اختار الكود —")] + [(c, f"{c} — {n}") for c, n in codes]
        self.fields["model_code"].widget.attrs["class"] = "si-code"
        self.fields["quantity"].widget.attrs.update({"class": "si-qty", "min": "1"})
        self.fields["unit_price"].required = False
        self.fields["unit_price"].widget.attrs.update({"class": "si-price", "step": "0.01"})
        v = self.instance.variant if self.instance.pk and self.instance.variant_id else None
        if v:
            m = v.model
            if m.code not in dict(self.fields["model_code"].choices):
                self.fields["model_code"].choices.append((m.code, f"{m.code} — {m.name}"))
            self.initial.update(model_code=m.code, item_name=m.name, color=v.color, size=v.size,
                                category=m.get_category_display())
            # القيمة الحالية لازم تبقى في القايمة عشان تظهر مختارة قبل ما الـ JS يشتغل
            self.fields["color"].widget.choices = [(v.color, v.color or "—")]
            self.fields["size"].widget.choices = [(v.size, v.size)]

    def clean(self):
        data = super().clean()
        code = data.get("model_code")
        if not code:
            return data
        qs = Variant.objects.filter(model__code=code, color=data.get("color") or "")
        if data.get("size"):
            qs = qs.filter(size=data["size"])
        variants = list(qs.select_related("model")[:2])
        if len(variants) != 1:
            raise ValidationError("اختار اللون والمقاس للصنف ده")
        self.instance.variant = variants[0]
        return data


class SaleItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        inv = self.instance
        if inv.pk and inv.is_confirmed:
            return
        channel = getattr(inv, "channel", "") or "retail"
        need = {}
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            v = form.instance.variant if form.instance.variant_id else None
            if v is None:
                continue
            floor = v.price_for(channel) or Decimal("0")
            price = form.cleaned_data.get("unit_price")
            if price is None:
                form.instance.unit_price = floor
                form.cleaned_data["unit_price"] = floor
            elif price < floor:
                form.add_error("unit_price", f"أقل سعر للصنف ده {floor:,.2f} (سعر فاتورة التوريد). ينفع تزوّد بس")
            qty = form.cleaned_data.get("quantity") or 0
            need[v.pk] = need.get(v.pk, 0) + qty
            if need[v.pk] > v.quantity:
                form.add_error("quantity", f"الرصيد {v.quantity} بس")


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    form = SaleItemForm
    formset = SaleItemFormSet
    fields = ("model_code", "item_name", "color", "size", "category", "quantity", "unit_price")
    extra = 1

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        return _no_icons(super().formfield_for_dbfield(db_field, request, **kwargs))


class SaleInvoiceForm(forms.ModelForm):
    class Meta:
        model = SaleInvoice
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "discount" in self.fields:
            self.fields["discount"].required = False      # فاضي = صفر (من غير نجمة)

    def clean_discount(self):
        return self.cleaned_data.get("discount") or Decimal("0")

    def clean(self):
        data = super().clean()
        customer = data.get("customer")
        if customer and customer.seller_id:
            data["seller"] = customer.seller                # البائع المكوّد على العميل
        return data


def _export_sales(modeladmin, request, queryset):
    profit = can_see_profit(request.user)
    head = ["رقم", "التاريخ", "العميل", "النوع", "الحالة", "الإجمالي"] + (["الربح"] if profit else [])
    rows = [[i.pk, i.date, str(i.customer or ""), i.get_payment_type_display(),
             "مؤكدة" if i.is_confirmed else "مسودة", i.total] + ([i.profit] if profit else [])
            for i in queryset.select_related("customer").prefetch_related("items")]
    return xlsx_response([("فواتير البيع", head, rows)], "sale_invoices")


_export_sales.short_description = "تصدير الفواتير المحددة إلى Excel"


class StatusFilter(admin.SimpleListFilter):
    title = "الحالة"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [("draft", "مسودة"), ("pending", "مستنية موافقة"), ("done", "مؤكدة")]

    def queryset(self, request, queryset):
        v = self.value()
        if v == "draft":
            return queryset.filter(is_confirmed=False).exclude(approval="pending")
        if v == "pending":
            return queryset.filter(approval="pending")
        if v == "done":
            return queryset.filter(is_confirmed=True)
        return queryset


def make_admin(confirm_action):
    class SaleInvoiceAdmin(admin.ModelAdmin):
        form = SaleInvoiceForm
        change_form_template = "admin/trade/saleinvoice/change_form.html"
        list_filter = (StatusFilter, "channel", "payment_type", "seller")
        search_fields = ("customer__name", "customer__phone", "=id")
        date_hierarchy = "date"
        inlines = [SaleItemInline]
        actions = [confirm_action, _export_sales]

        def get_list_display(self, request):
            cols = ["id", "customer", "date", "channel", "payment_type", "total"]
            if can_see_profit(request.user):
                cols.append("profit_col")
            return cols + ["is_confirmed"]

        @admin.display(description="الربح")
        def profit_col(self, obj):
            return f"{invoice_profit(obj):,.2f}" + ("" if obj.is_confirmed else " (تقديري)")

        @admin.display(description="ربحية الفاتورة")
        def profit_box(self, obj):
            if obj is None or not obj.pk:
                return "بتظهر بعد الحفظ"
            total, profit = obj.total, invoice_profit(obj)
            pct = f" ({profit * 100 / total:.1f}%)" if total else ""
            return f"{profit:,.2f}{pct}" + ("" if obj.is_confirmed else " — تقديري لحد ما الفاتورة تتأكد")

        def get_fields(self, request, obj=None):
            fields = [f for f in super().get_fields(request, obj) if f != "profit_box"]
            if can_see_profit(request.user):
                fields.append("profit_box")
            return fields

        def get_readonly_fields(self, request, obj=None):
            ro = [] if request.user.has_perm("trade.change_invoice_date") else ["date"]
            if can_see_profit(request.user):
                ro.append("profit_box")
            return ro

        def formfield_for_dbfield(self, db_field, request, **kwargs):
            return _no_icons(super().formfield_for_dbfield(db_field, request, **kwargs))

        def save_model(self, request, obj, form, change):
            if obj.customer_id and obj.customer.seller_id:
                obj.seller_id = obj.customer.seller_id
            super().save_model(request, obj, form, change)

        def render_change_form(self, request, context, *args, **kwargs):
            data = {}
            for v in (Variant.objects.filter(model__is_active=True).select_related("model")
                      .order_by("model__code", "size", "color")):
                m = data.setdefault(v.model.code, {"name": v.model.name, "category": v.model.get_category_display(),
                                                   "variants": []})
                m["variants"].append({"size": v.size, "color": v.color, "qty": v.quantity,
                                      "price": float(v.sale_price),
                                      "wprice": float(v.wholesale_price) if v.wholesale_price else None})
            sellers = {c.pk: [c.seller_id, c.seller.name] for c in
                       Customer.objects.filter(seller__isnull=False).select_related("seller")}
            context["sale_admin_data"] = {"models": data, "sellers": sellers}
            return super().render_change_form(request, context, *args, **kwargs)

    return SaleInvoiceAdmin


__all__ = ["make_admin", "SaleItemInline", "can_see_profit", "messages"]
