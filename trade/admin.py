from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .exports import xlsx_response
from .models import (AuditLog, CashBox, CashEntry, CashTransfer, ClothingModel,
                     CommissionRate, CustodyDeposit, CustodySettlement, Customer,
                     Expense, OnlineOrder, OnlineOrderItem, Payment,
                     PurchaseInvoice, PurchaseItem, PurchaseReturn, PurchaseReturnItem,
                     SaleInvoice, SaleItem, SaleReturn, SaleReturnItem, Seller,
                     SupplierPayment, Supplier, Variant)
from .treasury import create_transfer


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 3
    readonly_fields = ("quantity", "avg_cost")


def _print_labels(modeladmin, request, queryset):
    from django.http import HttpResponse
    from .labels import labels_pdf
    pdf = labels_pdf(list(queryset.order_by("code")))
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="labels.pdf"'
    return resp


_print_labels.short_description = "طباعة ملصقات الباركود للموديلات المحددة (50×30 مم)"


@admin.register(ClothingModel)
class ClothingModelAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "low_stock_level", "is_active")
    list_editable = ("low_stock_level",)
    search_fields = ("name", "code")
    inlines = [VariantInline]
    actions = [_print_labels]


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = ("model", "size", "color", "quantity", "avg_cost", "sale_price")
    list_filter = ("model",)
    readonly_fields = ("quantity", "avg_cost")


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 1


def _confirm(modeladmin, request, queryset):
    for inv in queryset:
        try:
            inv.confirm()
            messages.success(request, f"تم تأكيد {inv}")
        except ValidationError as e:
            messages.error(request, f"{inv}: {' '.join(e.messages)}")


_confirm.short_description = "تأكيد الفواتير المحددة (تحديث المخزون)"


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "date", "total", "is_confirmed")
    inlines = [PurchaseItemInline]
    actions = [_confirm]


def _export_sales(modeladmin, request, queryset):
    rows = [[i.pk, i.date, str(i.customer or ""), i.get_payment_type_display(),
             "مؤكدة" if i.is_confirmed else "مسودة", i.total, i.profit]
            for i in queryset.select_related("customer").prefetch_related("items")]
    return xlsx_response([("فواتير البيع", ["رقم", "التاريخ", "العميل", "النوع", "الحالة", "الإجمالي", "الربح"],
                           rows)], "sale_invoices")


_export_sales.short_description = "تصدير الفواتير المحددة إلى Excel"


@admin.register(SaleInvoice)
class SaleInvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "date", "payment_type", "total", "profit",
                    "is_confirmed")
    list_filter = ("payment_type", "is_confirmed")
    inlines = [SaleItemInline]
    actions = [_confirm, _export_sales]


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "seller")
    search_fields = ("name", "phone")

    def get_readonly_fields(self, request, obj=None):
        ro = []
        # البائع المرتبط بالعميل: مدير الحسابات بس يقدر يغيّره
        if not request.user.has_perm("trade.change_customer_seller"):
            ro.append("seller")
        if not request.user.has_perm("trade.import_data"):
            ro += ["opening_balance", "opening_date"]
        return ro


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("customer", "amount", "date", "method", "invoice")
    list_filter = ("method",)
    search_fields = ("customer__name", "customer__phone")


class OnlineOrderItemInline(admin.TabularInline):
    model = OnlineOrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("variant", "quantity", "unit_price", "unit_cost")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(OnlineOrder)
class OnlineOrderAdmin(admin.ModelAdmin):
    """للاطلاع والبحث فقط. الإنشاء وتغيير الحالة من صفحة الأونلاين لحماية المخزون"""
    list_display = ("id", "customer_name", "phone", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("customer_name", "phone")
    inlines = [OnlineOrderItemInline]
    readonly_fields = ("status", "discount", "shipping_fee", "shipping_cost",
                       "created_at", "updated_at")

    def has_add_permission(self, request):
        return False


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "address", "opening_balance")
    search_fields = ("name", "phone")

    def get_readonly_fields(self, request, obj=None):
        if request.user.has_perm("trade.import_data"):
            return []
        return ["opening_balance", "opening_date"]
@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("title", "amount", "date", "from_custody")
    list_filter = ("date",)
    search_fields = ("title",)

    @admin.display(boolean=True, description="من عهدة شخص")
    def from_custody(self, obj):
        return obj.custody_clearance_id is not None

    # المصروف اللي اتسجل من تصفية عهدة بيتعدل من هناك بس (عشان رصيد العهدة يفضل مظبوط)
    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.custody_clearance_id:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.custody_clearance_id:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(CustodySettlement)
class CustodySettlementAdmin(admin.ModelAdmin):
    """للاطلاع فقط. التسجيل من صفحة العهدة (فيها فحص الصورة والمبالغ)"""
    list_display = ("id", "amount", "received_by", "method", "settled_at", "proof")
    search_fields = ("received_by", "notes")
    list_filter = ("method",)
    exclude = ("screenshot",)
    readonly_fields = ("amount", "holder", "received_by", "method", "settled_at", "notes",
                       "created_by", "created_at")

    @admin.display(boolean=True, description="إثبات")
    def proof(self, obj):
        return obj.has_proof

    def has_add_permission(self, request):
        return False


@admin.register(CustodyDeposit)
class CustodyDepositAdmin(admin.ModelAdmin):
    list_display = ("id", "amount", "date", "note", "created_by")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """سجل المراجعة للاطلاع فقط: مفيش إضافة ولا تعديل ولا حذف من هنا"""
    list_display = ("created_at", "username", "action", "model", "object_repr", "ip")
    list_filter = ("action", "model")
    search_fields = ("username", "object_repr", "model")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ================= الخزينة والبائعين =================
admin.site.register(Seller)


@admin.register(CommissionRate)
class CommissionRateAdmin(admin.ModelAdmin):
    list_display = ("channel", "rate")


@admin.register(CashBox)
class CashBoxAdmin(admin.ModelAdmin):
    list_display = ("name", "method", "holder", "balance_display", "is_active")
    readonly_fields = ("balance_display",)

    @admin.display(description="الرصيد")
    def balance_display(self, obj):
        return obj.balance


@admin.register(CashEntry)
class CashEntryAdmin(admin.ModelAdmin):
    """حركات الخزن للاطلاع. الإضافة هنا للرصيد الافتتاحي / التسوية بس (كل الباقي بيتسجل من الشاشات)"""
    list_display = ("date", "box", "kind", "amount", "description")
    list_filter = ("box", "kind")
    search_fields = ("description",)
    fields = ("box", "amount", "date", "description")

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return [f.name for f in self.model._meta.fields]

    def get_fields(self, request, obj=None):
        return self.fields if obj is None else [f.name for f in self.model._meta.fields]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.kind = "adjust"
            obj.description = obj.description or "رصيد افتتاحي / تسوية"
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashTransfer)
class CashTransferAdmin(admin.ModelAdmin):
    list_display = ("date", "from_box", "to_box", "amount", "note", "created_by")
    fields = ("from_box", "to_box", "amount", "date", "note")

    def save_model(self, request, obj, form, change):
        if change:
            return  # التحويل المسجل ما بيتعدلش
        create_transfer(obj.from_box, obj.to_box, obj.amount, obj.date, obj.note, request.user)

    def has_change_permission(self, request, obj=None):
        return False if obj is not None else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SupplierPayment)
class SupplierPaymentAdmin(admin.ModelAdmin):
    list_display = ("date", "supplier", "amount", "box", "notes")
    list_filter = ("supplier",)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ================= المرتجعات (من لوحة الإدارة) =================
class PurchaseReturnItemInline(admin.TabularInline):
    model = PurchaseReturnItem
    extra = 1


class SaleReturnItemInline(admin.TabularInline):
    model = SaleReturnItem
    extra = 1


def _confirm_returns(modeladmin, request, queryset):
    for ret in queryset:
        try:
            ret.confirm()
            messages.success(request, f"تم تأكيد {ret}")
        except ValidationError as e:
            messages.error(request, f"{ret}: {' '.join(e.messages)}")


_confirm_returns.short_description = "تأكيد المرتجعات المحددة (تحديث المخزون والحسابات)"


@admin.register(PurchaseReturn)
class PurchaseReturnAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "date", "total", "is_confirmed")
    list_filter = ("is_confirmed",)
    inlines = [PurchaseReturnItemInline]
    actions = [_confirm_returns]
    readonly_fields = ("is_confirmed",)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_confirmed:
            return False
        return super().has_change_permission(request, obj)


@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ("id", "invoice", "date", "refund_mode", "refund_box", "total", "is_confirmed")
    list_filter = ("refund_mode", "is_confirmed")
    inlines = [SaleReturnItemInline]
    actions = [_confirm_returns]
    readonly_fields = ("is_confirmed",)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_confirmed:
            return False
        return super().has_change_permission(request, obj)


# ================= متابعة التحصيل =================
from .models import (BOMLine, DebtFollowUp, MaterialPurchase, MaterialPurchaseItem,  # noqa: E402
                     ProductionMaterial, ProductionOrder, ProductionOutput, RawMaterial)


@admin.register(DebtFollowUp)
class DebtFollowUpAdmin(admin.ModelAdmin):
    list_display = ("customer", "note", "next_date", "created_by", "created_at")
    list_filter = ("next_date",)
    search_fields = ("customer__name", "note")
    autocomplete_fields = ("customer",)

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# ================= الإنتاج =================
@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "quantity", "avg_cost", "low_stock_level", "is_active")
    list_filter = ("unit", "is_active")
    search_fields = ("name",)
    readonly_fields = ("quantity", "avg_cost")


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 1
    autocomplete_fields = ("material",)


# مكونات القطعة بتتعدل من صفحة الموديل نفسها
ClothingModelAdmin.inlines = [VariantInline, BOMLineInline]


class MaterialPurchaseItemInline(admin.TabularInline):
    model = MaterialPurchaseItem
    extra = 3
    autocomplete_fields = ("material",)


def _confirm_material_purchases(modeladmin, request, queryset):
    for p in queryset:
        try:
            p.confirm()
            messages.success(request, f"تم تأكيد {p} وإضافة الخامات للمخزن")
        except ValidationError as e:
            messages.error(request, f"{p}: {' '.join(e.messages)}")


_confirm_material_purchases.short_description = "تأكيد الاستلام (إضافة الخامات للمخزن)"


@admin.register(MaterialPurchase)
class MaterialPurchaseAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "date", "total", "is_confirmed")
    list_filter = ("is_confirmed",)
    inlines = [MaterialPurchaseItemInline]
    actions = [_confirm_material_purchases]

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_confirmed:
            return False
        return super().has_change_permission(request, obj)


class ProductionOutputInline(admin.TabularInline):
    model = ProductionOutput
    extra = 3

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # المقاسات بتاعة موديل الأمر بس
        if db_field.name == "variant":
            obj_id = request.resolver_match.kwargs.get("object_id")
            if obj_id:
                order = ProductionOrder.objects.filter(pk=obj_id).first()
                if order:
                    kwargs["queryset"] = Variant.objects.filter(model=order.model_id)
            kwargs.setdefault("queryset", Variant.objects.select_related("model"))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ProductionMaterialInline(admin.TabularInline):
    model = ProductionMaterial
    extra = 1
    readonly_fields = ("unit_cost",)
    autocomplete_fields = ("material",)


def _po_action(method, done_msg):
    def action(modeladmin, request, queryset):
        for o in queryset:
            try:
                getattr(o, method)()
                messages.success(request, f"{o}: {done_msg}")
            except ValidationError as e:
                messages.error(request, f"{o}: {' '.join(e.messages)}")
    action.__name__ = f"po_{method}"
    return action


_po_bom = _po_action("fill_from_bom", "اتحسبت الخامات من مكونات الموديل")
_po_bom.short_description = "احسب الخامات من مكونات الموديل (BOM)"
_po_complete = _po_action("complete", "اتسلّم للمخزن واتحسبت تكلفة القطعة")
_po_complete.short_description = "تسليم للمخزن (صرف الخامات وإضافة القطع)"
_po_cancel = _po_action("cancel", "اتلغى")
_po_cancel.short_description = "إلغاء الأوامر المحددة"


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "model", "date", "workshop", "pieces_display", "status",
                    "unit_cost_display")
    list_filter = ("status", "model")
    search_fields = ("model__name", "model__code", "workshop")
    inlines = [ProductionOutputInline, ProductionMaterialInline]
    actions = [_po_bom, _po_complete, _po_cancel]
    readonly_fields = ("status", "material_cost", "unit_cost", "completed_at", "estimate_display")

    @admin.display(description="القطع")
    def pieces_display(self, obj):
        return obj.pieces

    @admin.display(description="تكلفة القطعة")
    def unit_cost_display(self, obj):
        if obj.status == obj.DONE:
            return f"{obj.unit_cost:,.2f}"
        est = obj.estimate()
        return f"~{est['unit_cost']:,.2f} (متوقع)" if est["pieces"] else "—"

    @admin.display(description="التكلفة المتوقعة")
    def estimate_display(self, obj):
        if not obj.pk:
            return "احفظ الأمر الأول"
        e = obj.estimate()
        return (f"{e['pieces']} قطعة — خامات {e['material_cost']:,.2f} + مصنعية "
                f"{obj.labor_cost_per_piece * e['pieces']:,.2f} + مصاريف {obj.other_costs:,.2f} "
                f"= {e['total']:,.2f} ← القطعة {e['unit_cost']:,.2f}")

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.status != obj.DRAFT:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status == obj.DONE:
            return False
        return super().has_delete_permission(request, obj)


# ================= عهد الأشخاص =================
from .models import CustodyClearance, CustodyHolder  # noqa: E402


@admin.register(CustodyHolder)
class CustodyHolderAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "phone", "is_active", "balance_display")
    search_fields = ("name", "phone", "user__username")
    list_filter = ("is_active",)

    @admin.display(description="رصيد العهدة")
    def balance_display(self, obj):
        return f"{obj.totals()['balance']:,.2f}"


@admin.register(CustodyClearance)
class CustodyClearanceAdmin(admin.ModelAdmin):
    """للاطلاع. الطلب والموافقة من صفحة عهدة الشخص (فيها فحص الرصيد والصلاحية)"""
    list_display = ("holder", "kind", "amount", "date", "status", "requested_by", "reviewed_by")
    list_filter = ("status", "kind", "holder")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
