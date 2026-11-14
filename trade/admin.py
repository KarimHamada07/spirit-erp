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
    list_display = ("name", "code", "is_active")
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
        # البائع المرتبط بالعميل: مدير الحسابات بس يقدر يغيّره
        if request.user.has_perm("trade.change_customer_seller"):
            return []
        return ["seller"]


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


admin.site.register(Supplier)
admin.site.register(Expense)


@admin.register(CustodySettlement)
class CustodySettlementAdmin(admin.ModelAdmin):
    """للاطلاع فقط. التسجيل من صفحة العهدة (فيها فحص الصورة والمبالغ)"""
    list_display = ("id", "amount", "received_by", "method", "settled_at", "proof")
    search_fields = ("received_by", "notes")
    list_filter = ("method",)
    exclude = ("screenshot",)
    readonly_fields = ("amount", "received_by", "method", "settled_at", "notes",
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
