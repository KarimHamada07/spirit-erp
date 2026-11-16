import os
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.files.storage import FileSystemStorage

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import (DecimalField, ExpressionWrapper, F, OuterRef,
                              Subquery, Sum, Value)
from django.db.models.functions import Coalesce
from django.utils import timezone

from .notify import log_event, online_created_text, online_status_text

DEC = DecimalField(max_digits=14, decimal_places=2)
ZERO = Decimal("0")


class ClothingModel(models.Model):
    """موديل ملابس (يمكن إضافة أي عدد من الموديلات)"""
    name = models.CharField("اسم الموديل", max_length=100)
    code = models.CharField("الكود", max_length=30, unique=True)
    CATEGORIES = [("jacket", "جاكيت"), ("knitwear", "تريكو"), ("pants", "بنطلون"),
                  ("sweatshirt", "سويت شيرت"), ("tshirt", "تيشيرت"), ("suit", "بدلة"),
                  ("shorts", "شورت"), ("shirt", "قميص")]
    category = models.CharField("التصنيف", max_length=20, choices=CATEGORIES, blank=True,
                                db_index=True)
    description = models.TextField("وصف", blank=True)
    is_active = models.BooleanField("نشط", default=True)
    low_stock_level = models.PositiveIntegerField(
        "حد التنبيه", default=5,
        help_text="لما كمية أي مقاس/لون توصل للرقم ده أو أقل يجيلك تنبيه إن المخزون قرب يخلص")
    # الموقع (spiritegy.org)
    show_online = models.BooleanField("يظهر في الموقع", default=False, db_index=True)
    featured = models.BooleanField("مميز في الصفحة الرئيسية", default=False)
    old_price = models.DecimalField("السعر قبل الخصم (للموقع)", max_digits=12, decimal_places=2,
                                    null=True, blank=True,
                                    help_text="اختياري: بيظهر مشطوب جنب السعر عشان يبان إن عليه خصم")

    class Meta:
        verbose_name = "موديل"
        verbose_name_plural = "الموديلات"

    def __str__(self):
        return f"{self.name} ({self.code})"


class Variant(models.Model):
    """مقاس/لون لكل موديل، وعليه المخزون وسعر البيع ومتوسط التكلفة"""
    model = models.ForeignKey(ClothingModel, on_delete=models.CASCADE,
                              related_name="variants", verbose_name="الموديل")
    size = models.CharField("المقاس", max_length=20)
    color = models.CharField("اللون", max_length=30, blank=True)
    quantity = models.IntegerField("الكمية بالمخزن", default=0, editable=False)
    avg_cost = models.DecimalField("متوسط التكلفة", max_digits=12,
                                   decimal_places=2, default=0, editable=False)
    sale_price = models.DecimalField("سعر القطاعي", max_digits=12, decimal_places=2)
    wholesale_price = models.DecimalField("سعر الجملة", max_digits=12, decimal_places=2, null=True,
                                          blank=True, help_text="فاضي = نفس سعر القطاعي")

    class Meta:
        verbose_name = "مقاس/لون"
        verbose_name_plural = "المقاسات والألوان"
        unique_together = ("model", "size", "color")

    def __str__(self):
        return f"{self.model.name} - {self.size} - {self.color}".strip(" -")

    def price_for(self, channel):
        """سعر البيع حسب نوع البيع: الجملة ليها سعرها لو متحدد"""
        if channel == "wholesale" and self.wholesale_price:
            return self.wholesale_price
        return self.sale_price

    def add_stock(self, qty, unit_cost):
        """إضافة كمية للمخزن مع تحديث متوسط التكلفة (توريد أو مرتجع)"""
        new_qty = self.quantity + qty
        if new_qty > 0:
            self.avg_cost = ((self.quantity * self.avg_cost) +
                             (qty * unit_cost)) / new_qty
        self.quantity = new_qty


class Supplier(models.Model):
    name = models.CharField("اسم المورد", max_length=150)
    phone = models.CharField("الهاتف", max_length=30, blank=True)
    address = models.CharField("العنوان", max_length=250, blank=True)
    opening_balance = models.DecimalField(
        "رصيد افتتاحي", max_digits=14, decimal_places=2, default=0,
        help_text="قبل ما نبدأ النظام: موجب = ليه عندنا فلوس، سالب = لينا عنده فلوس (دفعنا مقدم تحت الحساب)")
    opening_date = models.DateField("تاريخ الرصيد الافتتاحي", null=True, blank=True)

    class Meta:
        verbose_name = "مورد"
        verbose_name_plural = "الموردين"

    def __str__(self):
        return self.name

    @property
    def balance(self):
        """رصيد المورد = افتتاحي + فواتير التوريد + مشتريات الخامات المؤكدة - مرتجعات مؤكدة - مدفوعات + مسترد منه.
        موجب = ليه عندنا (مستحق له)، سالب = لينا عنده (دفعنا تحت الحساب أكتر من اللي سحبناه)."""
        bought = self.opening_balance + sum((inv.total for inv in self.purchaseinvoice_set.filter(is_confirmed=True)), ZERO)
        bought += sum((p.total for p in self.material_purchases.filter(is_confirmed=True)), ZERO)
        returned = sum((r.total for r in self.purchase_returns.filter(is_confirmed=True)), ZERO)
        paid = sum((p.signed for p in self.supplier_payments.all()), ZERO)
        return bought - returned - paid

    @property
    def balance_abs(self):
        return abs(self.balance)

    @property
    def balance_label(self):
        b = self.balance
        return "ليه عندنا" if b > 0 else "لينا عنده" if b < 0 else "خالص"


class CustomerQuerySet(models.QuerySet):
    def with_balance(self):
        """يضيف لكل عميل: credit_total (إجمالي الآجل) و paid_total (المدفوع) و balance (المتبقي)"""
        gross = (SaleItem.objects
                 .filter(invoice__customer=OuterRef("pk"),
                         invoice__is_confirmed=True,
                         invoice__payment_type="credit")
                 .order_by().values("invoice__customer")
                 .annotate(s=Sum(F("quantity") * F("unit_price"), output_field=DEC))
                 .values("s"))
        disc = (SaleInvoice.objects
                .filter(customer=OuterRef("pk"), is_confirmed=True,
                        payment_type="credit")
                .order_by().values("customer")
                .annotate(s=Sum("discount")).values("s"))
        paid = (Payment.objects
                .filter(customer=OuterRef("pk"))
                .order_by().values("customer")
                .annotate(s=Sum("amount")).values("s"))
        returns = (SaleReturnItem.objects
                   .filter(return_doc__invoice__customer=OuterRef("pk"),
                           return_doc__is_confirmed=True,
                           return_doc__refund_mode="account",
                           return_doc__invoice__payment_type="credit")
                   .order_by().values("return_doc__invoice__customer")
                   .annotate(s=Sum(F("quantity") * F("unit_refund"), output_field=DEC))
                   .values("s"))
        return (self
                .annotate(
                    credit_gross=Coalesce(Subquery(gross, output_field=DEC),
                                          Value(ZERO), output_field=DEC),
                    credit_discount=Coalesce(Subquery(disc, output_field=DEC),
                                             Value(ZERO), output_field=DEC),
                    returned_total=Coalesce(Subquery(returns, output_field=DEC),
                                            Value(ZERO), output_field=DEC),
                    paid_total=Coalesce(Subquery(paid, output_field=DEC),
                                        Value(ZERO), output_field=DEC))
                .annotate(
                    credit_total=ExpressionWrapper(
                        F("opening_balance") + F("credit_gross") - F("credit_discount")
                        - F("returned_total"), output_field=DEC),
                    balance=ExpressionWrapper(
                        F("opening_balance") + F("credit_gross") - F("credit_discount")
                        - F("returned_total") - F("paid_total"), output_field=DEC)))


class Customer(models.Model):
    name = models.CharField("اسم العميل", max_length=150)
    phone = models.CharField("الهاتف", max_length=30, blank=True)
    seller = models.ForeignKey("Seller", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="customers", verbose_name="البائع المرتبط")
    opening_balance = models.DecimalField(
        "رصيد افتتاحي (عليه)", max_digits=14, decimal_places=2, default=0,
        help_text="المديونية اللي كانت على العميل قبل ما نبدأ النظام")
    opening_date = models.DateField("تاريخ الرصيد الافتتاحي", null=True, blank=True)

    objects = CustomerQuerySet.as_manager()

    class Meta:
        verbose_name = "عميل"
        verbose_name_plural = "العملاء"
        permissions = [("change_customer_seller", "تغيير بائع العميل (مدير الحسابات)"),
                       ("import_data", "استيراد البيانات والأرصدة الافتتاحية من Excel")]

    def __str__(self):
        return self.name


class PurchaseInvoice(models.Model):
    """فاتورة توريد من المورد"""
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, verbose_name="المورد")
    date = models.DateField("التاريخ")
    notes = models.TextField("ملاحظات", blank=True)
    is_confirmed = models.BooleanField("مؤكدة", default=False, editable=False)

    class Meta:
        verbose_name = "فاتورة توريد"
        verbose_name_plural = "فواتير التوريد"

    def __str__(self):
        return f"توريد #{self.pk} - {self.supplier}"

    @property
    def total(self):
        return sum((i.quantity * i.unit_cost for i in self.items.all()), ZERO)

    @transaction.atomic
    def confirm(self):
        if self.is_confirmed:
            raise ValidationError("الفاتورة مؤكدة بالفعل")
        if not self.items.exists():
            raise ValidationError("الفاتورة بدون أصناف")
        for item in self.items.select_related("variant"):
            v = Variant.objects.select_for_update().get(pk=item.variant_id)
            v.add_stock(item.quantity, item.unit_cost)
            v.save()
        self.is_confirmed = True
        self.save()


class PurchaseItem(models.Model):
    invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE,
                                related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, verbose_name="الصنف")
    quantity = models.PositiveIntegerField("الكمية")
    unit_cost = models.DecimalField("سعر الشراء للقطعة", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "بند توريد"
        verbose_name_plural = "بنود التوريد"

    def __str__(self):
        return f"{self.variant} × {self.quantity}"


class SaleInvoice(models.Model):
    """فاتورة بيع مباشر (نقدي أو آجل)"""
    PAYMENT_TYPES = [("cash", "نقدي"), ("credit", "آجل")]
    CHANNELS = [("retail", "قطاعي"), ("wholesale", "جملة")]

    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True,
                                 blank=True, verbose_name="العميل")
    # التاريخ بيتحط تلقائي بتاريخ النهارده (البياع مش بيغيّره)
    date = models.DateField("التاريخ", default=timezone.localdate)
    payment_type = models.CharField("نوع الفاتورة", max_length=10,
                                    choices=PAYMENT_TYPES, default="cash")
    channel = models.CharField("القناة", max_length=10, choices=CHANNELS,
                               default="retail")
    seller = models.ForeignKey("Seller", on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="invoices", verbose_name="البائع")
    discount = models.DecimalField("خصم (مبلغ)", max_digits=12, decimal_places=2, default=0,
                                   help_text="لو كتبت نسبة، المبلغ بيتحسب منها لوحده")
    discount_percent = models.DecimalField(
        "خصم (نسبة %)", max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))])
    notes = models.TextField("ملاحظات", blank=True)
    is_confirmed = models.BooleanField("مؤكدة", default=False, editable=False)

    @property
    def pieces(self):
        return sum((i.quantity for i in self.items.all()), 0)

    class Meta:
        verbose_name = "فاتورة بيع"
        verbose_name_plural = "فواتير البيع"
        permissions = [("change_customer_price",
                        "تعديل سعر صنف لعميل سبق له شراءه (مدير الحسابات)"),
                       ("change_invoice_date", "تعديل تاريخ فاتورة البيع (مدير الحسابات)"),
                       ("change_sale_price", "تعديل سعر البيع في نقطة البيع (مدير الحسابات)")]

    def apply_percent(self):
        """لو الخصم بالنسبة: المبلغ = نسبة من مجموع البنود"""
        if self.discount_percent:
            gross = sum((i.quantity * i.unit_price for i in self.items.all()), ZERO)
            self.discount = (gross * self.discount_percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def __str__(self):
        return f"بيع #{self.pk}"

    @property
    def total(self):
        gross = sum((i.quantity * i.unit_price for i in self.items.all()), ZERO)
        return gross - self.discount

    @property
    def profit(self):
        gross_profit = sum(((i.unit_price - i.unit_cost) * i.quantity
                            for i in self.items.all()), ZERO)
        return gross_profit - self.discount

    @property
    def paid_total(self):
        return sum((p.amount for p in self.payments.all()), ZERO)

    @property
    def net_ratio(self):
        """نسبة اللي بيفضل من سعر البند بعد خصم الفاتورة (1 = مفيش خصم)"""
        gross = sum((i.quantity * i.unit_price for i in self.items.all()), ZERO)
        if not gross:
            return Decimal("1")
        return (gross - self.discount) / gross

    @property
    def returned_on_account(self):
        """مرتجعات مؤكدة اتخصمت من مديونية الفاتورة دي"""
        return sum((r.total for r in self.returns.filter(is_confirmed=True, refund_mode="account")),
                   ZERO)

    @property
    def remaining(self):
        if self.payment_type != "credit":
            return ZERO
        return self.total - self.returned_on_account - self.paid_total

    @transaction.atomic
    def confirm(self):
        if self.is_confirmed:
            raise ValidationError("الفاتورة مؤكدة بالفعل")
        if self.payment_type == "credit" and not self.customer_id:
            raise ValidationError("البيع الآجل لازم يكون له عميل")
        if not self.items.exists():
            raise ValidationError("الفاتورة بدون أصناف")
        self.apply_percent()
        for item in self.items.select_related("variant"):
            v = Variant.objects.select_for_update().get(pk=item.variant_id)
            if v.quantity < item.quantity:
                raise ValidationError(f"الكمية غير كافية للصنف: {v} (المتاح {v.quantity})")
            item.unit_cost = v.avg_cost  # تثبيت التكلفة وقت البيع لحساب الربح
            item.save()
            v.quantity -= item.quantity
            v.save()
        self.is_confirmed = True
        self.save()


class SaleItem(models.Model):
    invoice = models.ForeignKey(SaleInvoice, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, verbose_name="الصنف")
    quantity = models.PositiveIntegerField("الكمية")
    unit_price = models.DecimalField("سعر البيع للقطعة", max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField("التكلفة وقت البيع", max_digits=12, decimal_places=2,
                                    default=0, editable=False)

    class Meta:
        verbose_name = "بند بيع"
        verbose_name_plural = "بنود البيع"

    def __str__(self):
        return f"{self.variant} × {self.quantity}"


class Payment(models.Model):
    """دفعة تسديد من عميل (على حساب الآجل)"""
    METHODS = [("cash", "كاش"), ("transfer", "تحويل بنكي"),
               ("wallet", "محفظة"), ("instapay", "إنستاباي"),
               ("shipping", "شركة شحن"), ("other", "أخرى")]

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT,
                                 related_name="payments", verbose_name="العميل")
    box = models.ForeignKey("CashBox", on_delete=models.SET_NULL, null=True, blank=True,
                            related_name="payments", verbose_name="الخزنة")
    invoice = models.ForeignKey(SaleInvoice, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="payments",
                                verbose_name="فاتورة محددة (اختياري)")
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    date = models.DateField("التاريخ", default=timezone.localdate)
    method = models.CharField("طريقة الدفع", max_length=10, choices=METHODS,
                              default="cash")
    notes = models.CharField("ملاحظات", max_length=200, blank=True)

    class Meta:
        verbose_name = "دفعة تسديد"
        verbose_name_plural = "دفعات التسديد"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.customer} - {self.amount}"

    def clean(self):
        if self.invoice_id and self.customer_id:
            if self.invoice.customer_id != self.customer_id:
                raise ValidationError("الفاتورة دي مش بتاعة نفس العميل")
            if self.invoice.payment_type != "credit":
                raise ValidationError("الفاتورة دي مش آجل")


class OnlineOrder(models.Model):
    """أوردر أونلاين: يتجهز ثم يُشحن ثم يُحصَّل أو يرجع"""
    PREPARED, SHIPPED, COLLECTED, RETURNED = (
        "prepared", "shipped", "collected", "returned")
    STATUS_CHOICES = [
        (PREPARED, "تم التجهيز"),
        (SHIPPED, "تم الشحن"),
        (COLLECTED, "تم التحصيل"),
        (RETURNED, "مرتجع"),
    ]
    TRANSITIONS = {
        PREPARED: [SHIPPED, RETURNED],
        SHIPPED: [COLLECTED, RETURNED],
        COLLECTED: [RETURNED],
        RETURNED: [],
    }

    customer_name = models.CharField("اسم العميل", max_length=150)
    phone = models.CharField("رقم الموبايل", max_length=30)
    address = models.CharField("العنوان", max_length=300, blank=True)
    notes = models.TextField("ملاحظات", blank=True)
    status = models.CharField("الحالة", max_length=12, choices=STATUS_CHOICES,
                              default=PREPARED, db_index=True)
    discount = models.DecimalField("خصم", max_digits=12, decimal_places=2, default=0)
    shipping_fee = models.DecimalField("مصاريف شحن على العميل", max_digits=12,
                                       decimal_places=2, default=0)
    shipping_cost = models.DecimalField("تكلفة الشحن علينا", max_digits=12,
                                        decimal_places=2, default=0)
    seller = models.ForeignKey("Seller", on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="online_orders", verbose_name="البائع")
    payment_box = models.ForeignKey("CashBox", on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name="online_orders",
                                    verbose_name="الخزنة اللي اتحصل فيها")
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("آخر تحديث", auto_now=True)
    # تاريخ التحصيل (أو المرتجع): الربح بيتحسب في اليوم ده، مش في آخر تعديل على الأوردر
    status_at = models.DateTimeField("تاريخ التحصيل/المرتجع", null=True, blank=True, editable=False,
                                     db_index=True)

    class Meta:
        verbose_name = "أوردر أونلاين"
        verbose_name_plural = "الأوردرات الأونلاين"
        ordering = ["-id"]

    def __str__(self):
        return f"أوردر #{self.pk} - {self.customer_name}"

    # ---- حسابات ----
    @property
    def items_total(self):
        return sum((i.quantity * i.unit_price for i in self.items.all()), ZERO)

    @property
    def total_to_collect(self):
        return self.items_total - self.discount + self.shipping_fee

    @property
    def pieces(self):
        return sum((i.quantity for i in self.items.all()), 0)

    @property
    def items_profit(self):
        return sum(((i.unit_price - i.unit_cost) * i.quantity
                    for i in self.items.all()), ZERO)

    @property
    def profit(self):
        """الربح بيتحسب بعد التحصيل فقط. المرتجع بيخسر تكلفة الشحن."""
        if self.status == self.COLLECTED:
            return (self.items_profit - self.discount
                    + self.shipping_fee - self.shipping_cost)
        if self.status == self.RETURNED:
            return -self.shipping_cost
        return ZERO

    @property
    def allowed_next(self):
        labels = dict(self.STATUS_CHOICES)
        return [(s, labels[s]) for s in self.TRANSITIONS[self.status]]

    # ---- عمليات ----
    @classmethod
    @transaction.atomic
    def create_order(cls, *, customer_name, phone, items, address="", notes="",
                     discount=ZERO, shipping_fee=ZERO, shipping_cost=ZERO, seller=None):
        """ينشئ الأوردر (حالة: تم التجهيز) ويخصم المخزون ويثبت التكلفة"""
        customer_name = (customer_name or "").strip()
        phone = (phone or "").strip()
        if not customer_name or not phone:
            raise ValidationError("اسم العميل ورقم الموبايل مطلوبين")
        if seller is None:
            raise ValidationError("اختار البائع")
        if not items:
            raise ValidationError("الأوردر بدون أصناف")
        if min(discount, shipping_fee, shipping_cost) < 0:
            raise ValidationError("الأرقام لازم تكون موجبة")

        order = cls.objects.create(
            customer_name=customer_name, phone=phone, address=address.strip(),
            notes=notes.strip(), discount=discount, shipping_fee=shipping_fee,
            shipping_cost=shipping_cost, seller=seller)
        gross = ZERO
        for it in items:
            qty, price = int(it["qty"]), Decimal(str(it["price"]))
            if qty <= 0 or price < 0:
                raise ValidationError("كمية أو سعر غير صحيح")
            try:
                v = Variant.objects.select_for_update().get(pk=it["id"])
            except Variant.DoesNotExist:
                raise ValidationError("صنف غير موجود")
            if v.quantity < qty:
                raise ValidationError(
                    f"الكمية غير كافية للصنف: {v} (المتاح {v.quantity})")
            OnlineOrderItem.objects.create(
                order=order, variant=v, quantity=qty, unit_price=price,
                unit_cost=v.avg_cost)
            v.quantity -= qty
            v.save()
            gross += qty * price
        if discount > gross:
            raise ValidationError("الخصم أكبر من إجمالي الأوردر")
        log_event(online_created_text(order))
        return order

    @transaction.atomic
    def set_status(self, new_status, box=None):
        if new_status not in self.TRANSITIONS.get(self.status, []):
            raise ValidationError("مينفعش تنقل الأوردر للحالة دي")
        if new_status == self.RETURNED:  # المرتجع يرجع للمخزن
            for item in self.items.select_related("variant"):
                v = Variant.objects.select_for_update().get(pk=item.variant_id)
                v.add_stock(item.quantity, item.unit_cost)
                v.save()
            if self.status == self.COLLECTED and self.payment_box_id:
                # كان متحصّل: بنرجّع الفلوس من نفس الخزنة
                CashEntry.post(self.payment_box, -self.total_to_collect, "online_return",
                               f"مرتجع أوردر #{self.pk}", online_order=self)
        if new_status == self.COLLECTED:
            if box is None:
                raise ValidationError("اختار الخزنة اللي اتحصل فيها الفلوس")
            self.payment_box = box
            CashEntry.post(box, self.total_to_collect, "online",
                           f"تحصيل أوردر #{self.pk}", online_order=self)
        old_status = self.status
        self.status = new_status
        if new_status in (self.COLLECTED, self.RETURNED):
            self.status_at = timezone.now()
        self.save()
        log_event(online_status_text(self, old_status))


class OnlineOrderItem(models.Model):
    order = models.ForeignKey(OnlineOrder, on_delete=models.CASCADE,
                              related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, verbose_name="الصنف")
    quantity = models.PositiveIntegerField("الكمية")
    unit_price = models.DecimalField("سعر البيع للقطعة", max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField("التكلفة وقت البيع", max_digits=12,
                                    decimal_places=2, default=0, editable=False)

    class Meta:
        verbose_name = "بند أوردر"
        verbose_name_plural = "بنود الأوردرات"

    def __str__(self):
        return f"{self.variant} × {self.quantity}"


class Expense(models.Model):
    """مصروفات (إيجار، شحن، مرتبات...) تُخصم من صافي الربح"""
    title = models.CharField("البند", max_length=150)
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2)
    date = models.DateField("التاريخ")
    custody_clearance = models.OneToOneField(
        "CustodyClearance", on_delete=models.PROTECT, null=True, blank=True, editable=False,
        related_name="expense", verbose_name="من تصفية عهدة",
        help_text="مصروف اتصرف من عهدة شخص (اتخصم من رصيده هو مش من العهدة العامة)")

    class Meta:
        verbose_name = "مصروف"
        verbose_name_plural = "المصروفات"

    def __str__(self):
        return f"{self.title} - {self.amount}"


class ActivityLog(models.Model):
    """سجل بكل خطوة في النظام (وبيتبعت لتليجرام)"""
    created_at = models.DateTimeField("الوقت", auto_now_add=True)
    user_name = models.CharField("المستخدم", max_length=150, blank=True)
    text = models.TextField("الحدث")
    sent = models.BooleanField("أُرسل لتليجرام", default=False, db_index=True)
    AUDIENCES = [("", "الجروب العام"), ("accountant", "المحاسب")]
    audience = models.CharField("مرسل إلى", max_length=20, choices=AUDIENCES, blank=True,
                                default="")

    class Meta:
        verbose_name = "حدث"
        verbose_name_plural = "سجل النشاط"
        ordering = ["-id"]

    def __str__(self):
        return self.text.split("\n")[0]

    @property
    def message(self):
        when = timezone.localtime(self.created_at).strftime("%Y-%m-%d %H:%M:%S")
        who = self.user_name or "—"
        return f"{self.text}\n\n👤 عملها: {who}\n🕒 {when}"


def private_storage():
    """مجلد خاص لإثباتات العهدة: مش متاح بروابط مباشرة، بيتقدم بس عن طريق صفحة بتتأكد من الصلاحية"""
    return FileSystemStorage(location=os.path.join(settings.BASE_DIR, "private_media"))


class CustodyDeposit(models.Model):
    """مبلغ بيتضاف على العهدة يدوياً (رصيد موجود قبل النظام أو تمويل)"""
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    date = models.DateField("التاريخ", default=timezone.localdate)
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False,
                                   verbose_name="سجّلها")

    class Meta:
        verbose_name = "إضافة على العهدة"
        verbose_name_plural = "إضافات على العهدة"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"+{self.amount} ({self.note})"


class CustodySettlement(models.Model):
    """تصفية عهدة: مبلغ اتسلّم لشخص، مع صورة الإثبات (سكرين) واسم المستلم"""
    METHODS = [("cash", "نقدي"), ("transfer", "تحويل بنكي"), ("instapay", "إنستاباي"),
               ("wallet", "محفظة إلكترونية"), ("other", "أخرى")]

    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    holder = models.ForeignKey("CustodyHolder", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="handouts", verbose_name="صاحب العهدة")
    received_by = models.CharField("المستلم", max_length=120)
    method = models.CharField("طريقة التسليم", max_length=10, choices=METHODS,
                              default="cash")
    settled_at = models.DateTimeField("وقت التصفية", default=timezone.now)
    notes = models.CharField("ملاحظات", max_length=300, blank=True)
    screenshot = models.FileField("صورة الإثبات (سكرين)", upload_to="custody/",
                                  storage=private_storage, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False,
                                   verbose_name="سجّلها")
    created_at = models.DateTimeField("وقت التسجيل", auto_now_add=True)

    class Meta:
        verbose_name = "تصفية عهدة"
        verbose_name_plural = "تصفيات العهدة"
        ordering = ["-settled_at", "-id"]

    def __str__(self):
        return f"تصفية {self.amount} إلى {self.received_by}"

    @property
    def has_proof(self):
        return bool(self.screenshot)


class AuditLog(models.Model):
    """سجل مراجعة: مين عمل إيه ومتى (إضافة، تعديل، حذف، دخول) مع القيم قبل وبعد"""
    ACTIONS = [("create", "إضافة"), ("update", "تعديل"), ("delete", "حذف"),
               ("login", "دخول"), ("logout", "خروج"), ("login_failed", "دخول فاشل")]

    created_at = models.DateTimeField("الوقت", auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, editable=False, verbose_name="المستخدم")
    username = models.CharField("اسم المستخدم", max_length=150, blank=True)
    action = models.CharField("الإجراء", max_length=14, choices=ACTIONS, db_index=True)
    model = models.CharField("النوع", max_length=80, blank=True)
    object_id = models.CharField("رقم السجل", max_length=40, blank=True)
    object_repr = models.CharField("السجل", max_length=250, blank=True)
    changes = models.JSONField("التغييرات", default=dict, blank=True)
    ip = models.CharField("IP", max_length=64, blank=True)

    class Meta:
        verbose_name = "سجل مراجعة"
        verbose_name_plural = "سجل المراجعة"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.get_action_display()} {self.model} {self.object_repr}"


# ================= البائعين والعمولة =================
class Seller(models.Model):
    """البائع اللي بيعمل الفاتورة (للعمولة)"""
    name = models.CharField("الاسم", max_length=100, unique=True)
    is_commissionable = models.BooleanField("عليه عمولة", default=True,
                                            help_text="مباشر مش عليه عمولة")
    is_active = models.BooleanField("نشط", default=True)
    # حساب البائع على السيستم: لوحة التحكم بتوريله مبيعاته وعمولته، ونقطة البيع بتختاره لوحدها
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="seller_profile", verbose_name="حسابه على السيستم",
                                help_text="اختياري: لو البائع بيدخل السيستم بحساب، اختاره هنا")

    class Meta:
        verbose_name = "بائع"
        verbose_name_plural = "البائعين"
        ordering = ["name"]

    def __str__(self):
        return self.name


class CommissionRate(models.Model):
    """نسبة العمولة لكل نوع فاتورة (نفس النسبة لكل البائعين)"""
    CHANNELS = [("retail", "قطاعي"), ("wholesale", "جملة"), ("online", "أونلاين")]

    channel = models.CharField("نوع الفاتورة", max_length=10, choices=CHANNELS, unique=True)
    rate = models.DecimalField("النسبة %", max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "نسبة عمولة"
        verbose_name_plural = "نسب العمولة"

    def __str__(self):
        return f"{self.get_channel_display()}: {self.rate}%"


# ================= الخزن =================
class CashBox(models.Model):
    """خزنة: كاش لكل شخص، إنستاباي، محفظة، تحويل بنكي، أو مستحقات شركة الشحن"""
    METHODS = [("cash", "كاش"), ("instapay", "إنستاباي"), ("wallet", "محفظة"),
               ("bank", "تحويل بنكي"), ("shipping", "شركة شحن")]

    name = models.CharField("اسم الخزنة", max_length=100, unique=True)
    method = models.CharField("النوع", max_length=10, choices=METHODS)
    holder = models.CharField("الشخص المسؤول", max_length=60, blank=True)
    is_active = models.BooleanField("نشطة", default=True)

    class Meta:
        verbose_name = "خزنة"
        verbose_name_plural = "الخزن"
        ordering = ["id"]

    def __str__(self):
        return self.name

    @property
    def balance(self):
        return self.entries.aggregate(s=Sum("amount"))["s"] or ZERO


class CashEntry(models.Model):
    """حركة في خزنة: موجب = داخل، سالب = خارج. كل الحركات بتتسجل هنا"""
    KINDS = [("sale", "بيع"), ("payment", "تحصيل عميل"),
             ("supplier_payment", "سداد مورد"), ("refund", "استرداد مرتجع عميل"),
             ("online", "تحصيل أونلاين"), ("online_return", "مرتجع أونلاين"),
             ("transfer", "تحويل بين الخزن"), ("adjust", "رصيد افتتاحي / تسوية"),
             ("salary", "مرتبات وسلف"), ("supplier_refund", "استرداد من مورد")]

    box = models.ForeignKey(CashBox, on_delete=models.PROTECT, related_name="entries",
                            verbose_name="الخزنة")
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2)
    date = models.DateField("التاريخ", default=timezone.localdate)
    kind = models.CharField("النوع", max_length=20, choices=KINDS)
    description = models.CharField("البيان", max_length=200, blank=True)
    invoice = models.ForeignKey(SaleInvoice, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="cash_entries", verbose_name="الفاتورة")
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="cash_entries", verbose_name="العميل")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="cash_entries", verbose_name="المورد")
    online_order = models.ForeignKey(OnlineOrder, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name="cash_entries", verbose_name="الأوردر الأونلاين")
    transfer = models.ForeignKey("CashTransfer", on_delete=models.CASCADE, null=True,
                                 blank=True, related_name="entries", verbose_name="التحويل")
    created_at = models.DateTimeField("وقت التسجيل", auto_now_add=True)

    class Meta:
        verbose_name = "حركة خزنة"
        verbose_name_plural = "حركات الخزن"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.box} {self.amount}"

    @classmethod
    def post(cls, box, amount, kind, description="", **refs):
        return cls.objects.create(box=box, amount=Decimal(amount), kind=kind,
                                  description=description[:200], **refs)


class CashTransfer(models.Model):
    """تحويل بين خزنتين (نقل العهد). بيكوّن حركتين: خارج من الأولى وداخل للتانية"""
    from_box = models.ForeignKey(CashBox, on_delete=models.PROTECT, related_name="transfers_out",
                                 verbose_name="من خزنة")
    to_box = models.ForeignKey(CashBox, on_delete=models.PROTECT, related_name="transfers_in",
                               verbose_name="إلى خزنة")
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    date = models.DateField("التاريخ", default=timezone.localdate)
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False, verbose_name="سجّله")
    created_at = models.DateTimeField("وقت التسجيل", auto_now_add=True)

    class Meta:
        verbose_name = "تحويل بين خزن"
        verbose_name_plural = "التحويلات بين الخزن"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.from_box} → {self.to_box}: {self.amount}"

    def clean(self):
        if self.from_box_id and self.from_box_id == self.to_box_id:
            raise ValidationError("مينفعش التحويل لنفس الخزنة")


class SupplierPayment(models.Model):
    """فلوس بينا وبين المورد: سداد/دفعة تحت الحساب (خارجة من الخزنة) أو استرداد منه (داخلة الخزنة)"""
    PAY, REFUND = "pay", "refund"
    KINDS = [(PAY, "سداد / دفعة تحت الحساب"), (REFUND, "استرداد من المورد")]
    kind = models.CharField("النوع", max_length=10, choices=KINDS, default=PAY)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT,
                                 related_name="supplier_payments", verbose_name="المورد")
    box = models.ForeignKey(CashBox, on_delete=models.PROTECT, related_name="supplier_payments",
                            verbose_name="الخزنة")
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    date = models.DateField("التاريخ", default=timezone.localdate)
    notes = models.CharField("ملاحظات", max_length=200, blank=True)
    created_at = models.DateTimeField("وقت التسجيل", auto_now_add=True)

    class Meta:
        verbose_name = "سداد مورد"
        verbose_name_plural = "سدادات الموردين"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.supplier} - {self.amount}"

    @property
    def signed(self):
        """اللي اتدفع للمورد (الاسترداد بالسالب)"""
        return -self.amount if self.kind == self.REFUND else self.amount


# ================= المرتجعات =================
class PurchaseReturn(models.Model):
    """مرتجع مورد: بضاعة بترجع للمورد، بتتخصم من حسابه ومن المخزون"""
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT,
                                 related_name="purchase_returns", verbose_name="المورد")
    date = models.DateField("التاريخ", default=timezone.localdate)
    reason = models.CharField("السبب", max_length=200, blank=True)
    is_confirmed = models.BooleanField("مؤكد", default=False, editable=False)

    class Meta:
        verbose_name = "مرتجع مورد"
        verbose_name_plural = "مرتجعات الموردين"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"مرتجع مورد #{self.pk} - {self.supplier}"

    @property
    def total(self):
        return sum((i.quantity * i.unit_cost for i in self.items.all()), ZERO)

    @transaction.atomic
    def confirm(self):
        if self.is_confirmed:
            raise ValidationError("المرتجع مؤكد بالفعل")
        if not self.items.exists():
            raise ValidationError("المرتجع بدون أصناف")
        for item in self.items.select_related("variant"):
            v = Variant.objects.select_for_update().get(pk=item.variant_id)
            if v.quantity < item.quantity:
                raise ValidationError(f"الكمية بالمخزن أقل من المرتجع للصنف: {v} (المتاح {v.quantity})")
            v.quantity -= item.quantity
            v.save()
        self.is_confirmed = True
        self.save()


class PurchaseReturnItem(models.Model):
    return_doc = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, verbose_name="الصنف")
    quantity = models.PositiveIntegerField("الكمية")
    unit_cost = models.DecimalField("سعر الشراء للقطعة", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "بند مرتجع مورد"
        verbose_name_plural = "بنود مرتجع المورد"

    def __str__(self):
        return f"{self.variant} × {self.quantity}"


class SaleReturn(models.Model):
    """مرتجع عميل: البضاعة بترجع للمخزن، والفلوس بتترد من خزنة أو تتخصم من مديونية العميل"""
    REFUND_MODES = [("box", "استرداد نقدي من خزنة"), ("account", "خصم من مديونية العميل (آجل)")]

    invoice = models.ForeignKey(SaleInvoice, on_delete=models.PROTECT, related_name="returns",
                                verbose_name="الفاتورة الأصلية")
    date = models.DateField("التاريخ", default=timezone.localdate)
    reason = models.CharField("السبب", max_length=200, blank=True)
    refund_mode = models.CharField("طريقة رد الفلوس", max_length=10, choices=REFUND_MODES,
                                   default="box")
    refund_box = models.ForeignKey(CashBox, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="sale_returns", verbose_name="الخزنة اللي بترد منها")
    is_confirmed = models.BooleanField("مؤكد", default=False, editable=False)

    class Meta:
        verbose_name = "مرتجع عميل"
        verbose_name_plural = "مرتجعات العملاء"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"مرتجع بيع #{self.pk} (فاتورة #{self.invoice_id})"

    @property
    def total(self):
        """قيمة المرتجع: بسعر البيع بعد نصيبه من خصم الفاتورة (بعد التأكيد بتتثبت)"""
        items = list(self.items.select_related("sale_item"))
        if self.is_confirmed:
            return sum((i.quantity * i.unit_refund for i in items), ZERO)
        ratio = self.invoice.net_ratio
        return sum((i.quantity * (i.sale_item.unit_price * ratio).quantize(Decimal("0.01"))
                    for i in items), ZERO)

    @transaction.atomic
    def confirm(self):
        if self.is_confirmed:
            raise ValidationError("المرتجع مؤكد بالفعل")
        if not self.invoice.is_confirmed:
            raise ValidationError("الفاتورة الأصلية لازم تكون مؤكدة")
        if not self.items.exists():
            raise ValidationError("المرتجع بدون أصناف")
        if self.refund_mode == "account" and self.invoice.payment_type != "credit":
            raise ValidationError("الخصم من المديونية للفواتير الآجل بس")
        if self.refund_mode == "box" and not self.refund_box_id:
            raise ValidationError("اختار الخزنة اللي هترد منها الفلوس")
        ratio = self.invoice.net_ratio
        for item in self.items.select_related("sale_item__variant"):
            si = item.sale_item
            item.unit_refund = (si.unit_price * ratio).quantize(Decimal("0.01"))
            item.save(update_fields=["unit_refund"])
            already = (SaleReturnItem.objects
                       .filter(sale_item=si, return_doc__is_confirmed=True)
                       .aggregate(s=Sum("quantity"))["s"] or 0)
            if already + item.quantity > si.quantity:
                raise ValidationError(f"الكمية أكبر من المباع للصنف: {si.variant}")
            v = Variant.objects.select_for_update().get(pk=si.variant_id)
            v.add_stock(item.quantity, si.unit_cost)
            v.save()
        self.is_confirmed = True  # عشان total يقرا السعر المتثبت
        if self.refund_mode == "box":
            CashEntry.post(self.refund_box, -self.total, "refund",
                           f"مرتجع بيع #{self.pk}", invoice=self.invoice,
                           customer=self.invoice.customer)
        self.is_confirmed = True
        self.save()


class SaleReturnItem(models.Model):
    return_doc = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="items")
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="return_items",
                                  verbose_name="البند الأصلي")
    quantity = models.PositiveIntegerField("الكمية")
    unit_refund = models.DecimalField("قيمة القطعة المرتجعة", max_digits=12, decimal_places=2,
                                      default=0, editable=False,
                                      help_text="سعر البيع بعد نصيب القطعة من خصم الفاتورة")

    class Meta:
        verbose_name = "بند مرتجع عميل"
        verbose_name_plural = "بنود مرتجع العميل"

    def __str__(self):
        return f"{self.sale_item.variant} × {self.quantity}"


# ================= متابعة تحصيل المديونيات (للمحاسب) =================
class DebtFollowUp(models.Model):
    """ملاحظة متابعة من المحاسب على مديونية عميل: اتكلم معاه، وعده يدفع إمتى"""
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="followups",
                                 verbose_name="العميل")
    note = models.CharField("الملاحظة", max_length=300)
    next_date = models.DateField("ميعاد المتابعة الجاي", null=True, blank=True,
                                 help_text="التذكير هيرجع للمحاسب في اليوم ده")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False,
                                   verbose_name="سجّلها")
    created_at = models.DateTimeField("الوقت", auto_now_add=True)

    class Meta:
        verbose_name = "متابعة تحصيل"
        verbose_name_plural = "متابعات التحصيل"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.customer}: {self.note}"


# ================= الإنتاج: الخامات وأوامر التشغيل =================
QTY = dict(max_digits=12, decimal_places=3)


class RawMaterial(models.Model):
    """خامة (قماش، خيط، زراير، تيكيت...) ليها مخزون ومتوسط تكلفة زي الأصناف"""
    UNITS = [("m", "متر"), ("kg", "كيلو"), ("pc", "قطعة"), ("roll", "طوب/رول"), ("box", "علبة")]
    name = models.CharField("اسم الخامة", max_length=120, unique=True)
    unit = models.CharField("الوحدة", max_length=5, choices=UNITS, default="m")
    quantity = models.DecimalField("الكمية بالمخزن", default=0, editable=False, **QTY)
    avg_cost = models.DecimalField("متوسط التكلفة للوحدة", max_digits=12, decimal_places=4,
                                   default=0, editable=False)
    low_stock_level = models.DecimalField("حد التنبيه", default=0, **QTY)
    is_active = models.BooleanField("نشطة", default=True)

    class Meta:
        verbose_name = "خامة"
        verbose_name_plural = "الخامات"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_unit_display()})"

    def add_stock(self, qty, unit_cost):
        qty, unit_cost = Decimal(qty), Decimal(unit_cost)
        new_qty = self.quantity + qty
        if new_qty > 0:
            self.avg_cost = ((self.quantity * self.avg_cost) + (qty * unit_cost)) / new_qty
        self.quantity = new_qty


class BOMLine(models.Model):
    """مكونات القطعة الواحدة من الموديل (للحساب التلقائي لأوامر التشغيل)"""
    model = models.ForeignKey(ClothingModel, on_delete=models.CASCADE, related_name="bom",
                              verbose_name="الموديل")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, verbose_name="الخامة")
    qty_per_piece = models.DecimalField("الكمية للقطعة", validators=[MinValueValidator(Decimal("0.001"))],
                                        **QTY)

    class Meta:
        verbose_name = "مكوّن"
        verbose_name_plural = "مكونات القطعة (BOM)"
        unique_together = ("model", "material")

    def __str__(self):
        return f"{self.material} × {self.qty_per_piece}"


class MaterialPurchase(models.Model):
    """شراء خامات من مورد: بتزود مخزون الخامات وبتتحسب على حساب المورد"""
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="material_purchases",
                                 verbose_name="المورد")
    date = models.DateField("التاريخ", default=timezone.localdate)
    notes = models.CharField("ملاحظات", max_length=200, blank=True)
    is_confirmed = models.BooleanField("مؤكدة", default=False, editable=False)

    class Meta:
        verbose_name = "شراء خامات"
        verbose_name_plural = "مشتريات الخامات"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"خامات #{self.pk} - {self.supplier}"

    @property
    def total(self):
        return sum((i.quantity * i.unit_cost for i in self.items.all()), ZERO)

    @transaction.atomic
    def confirm(self):
        if self.is_confirmed:
            raise ValidationError("الفاتورة مؤكدة بالفعل")
        if not self.items.exists():
            raise ValidationError("الفاتورة بدون خامات")
        for item in self.items.all():
            m = RawMaterial.objects.select_for_update().get(pk=item.material_id)
            m.add_stock(item.quantity, item.unit_cost)
            m.save()
        self.is_confirmed = True
        self.save()


class MaterialPurchaseItem(models.Model):
    purchase = models.ForeignKey(MaterialPurchase, on_delete=models.CASCADE, related_name="items")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, verbose_name="الخامة")
    quantity = models.DecimalField("الكمية", validators=[MinValueValidator(Decimal("0.001"))], **QTY)
    unit_cost = models.DecimalField("سعر الوحدة", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "بند خامات"
        verbose_name_plural = "بنود الخامات"

    def __str__(self):
        return f"{self.material} × {self.quantity}"


class ProductionOrder(models.Model):
    """أمر تشغيل: بيصرف خامات ويطلّع قطع جاهزة على المخزون بتكلفة محسوبة"""
    DRAFT, DONE, CANCELLED = "draft", "done", "cancelled"
    STATUSES = [(DRAFT, "تحت التشغيل"), (DONE, "اتسلّم للمخزن"), (CANCELLED, "ملغي")]

    model = models.ForeignKey(ClothingModel, on_delete=models.PROTECT, related_name="production_orders",
                              verbose_name="الموديل")
    date = models.DateField("تاريخ الأمر", default=timezone.localdate)
    status = models.CharField("الحالة", max_length=10, choices=STATUSES, default=DRAFT,
                              editable=False, db_index=True)
    workshop = models.CharField("الورشة / المشغل", max_length=120, blank=True)
    labor_cost_per_piece = models.DecimalField("مصنعية القطعة", max_digits=12, decimal_places=2,
                                               default=0)
    other_costs = models.DecimalField("مصاريف تانية (إجمالي)", max_digits=12, decimal_places=2,
                                      default=0, help_text="نقل، طباعة، تغليف... بتتوزع على القطع")
    notes = models.TextField("ملاحظات", blank=True)
    material_cost = models.DecimalField("تكلفة الخامات", max_digits=14, decimal_places=2, default=0,
                                        editable=False)
    unit_cost = models.DecimalField("تكلفة القطعة", max_digits=12, decimal_places=2, default=0,
                                    editable=False)
    completed_at = models.DateTimeField("وقت التسليم", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "أمر تشغيل"
        verbose_name_plural = "أوامر التشغيل"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"تشغيل #{self.pk} - {self.model.name}"

    @property
    def pieces(self):
        return sum((o.quantity for o in self.outputs.all()), 0)

    def estimate(self):
        """تكلفة متوقعة (بأسعار الخامات الحالية) قبل التسليم"""
        pieces = self.pieces
        mat = sum((u.quantity * u.material.avg_cost for u in self.materials.select_related("material")),
                  ZERO)
        total = mat + self.labor_cost_per_piece * pieces + self.other_costs
        return {"pieces": pieces, "material_cost": mat, "total": total,
                "unit_cost": (total / pieces) if pieces else ZERO}

    @transaction.atomic
    def fill_from_bom(self):
        """يحسب الخامات المطلوبة من مكونات الموديل × عدد القطع (بيمسح الخامات الحالية)"""
        if self.status != self.DRAFT:
            raise ValidationError("الأمر مقفول")
        pieces = self.pieces
        if not pieces:
            raise ValidationError("حدد المقاسات والكميات الأول")
        bom = list(self.model.bom.all())
        if not bom:
            raise ValidationError("الموديل مالوش مكونات (BOM) متسجلة")
        self.materials.all().delete()
        for line in bom:
            ProductionMaterial.objects.create(order=self, material_id=line.material_id,
                                              quantity=line.qty_per_piece * pieces)

    @transaction.atomic
    def complete(self):
        """تسليم للمخزن: يصرف الخامات ويضيف القطع بتكلفتها"""
        order = ProductionOrder.objects.select_for_update().get(pk=self.pk)
        if order.status != self.DRAFT:
            raise ValidationError("الأمر ده اتقفل قبل كده")
        outputs = list(self.outputs.select_related("variant"))
        pieces = sum(o.quantity for o in outputs)
        if not pieces:
            raise ValidationError("الأمر بدون قطع")
        for o in outputs:
            if o.variant.model_id != self.model_id:
                raise ValidationError(f"الصنف {o.variant} مش من نفس الموديل")
        material_cost = ZERO
        for use in self.materials.all():
            m = RawMaterial.objects.select_for_update().get(pk=use.material_id)
            if m.quantity < use.quantity:
                raise ValidationError(f"الخامة {m} مش كفاية (المتاح {m.quantity:g})")
            use.unit_cost = m.avg_cost
            use.save(update_fields=["unit_cost"])
            material_cost += use.quantity * m.avg_cost
            m.quantity -= use.quantity
            m.save()
        total = material_cost + self.labor_cost_per_piece * pieces + self.other_costs
        unit_cost = (total / pieces).quantize(Decimal("0.01"))
        for o in outputs:
            v = Variant.objects.select_for_update().get(pk=o.variant_id)
            v.add_stock(o.quantity, unit_cost)
            v.save()
        self.material_cost = material_cost.quantize(Decimal("0.01"))
        self.unit_cost = unit_cost
        self.status = self.DONE
        self.completed_at = timezone.now()
        self.save()
        log_event(f"🏭 أمر تشغيل #{self.pk} اتسلّم للمخزن: {self.model.name} — {pieces} قطعة"
                  f"\nتكلفة القطعة: {unit_cost:,.2f} (خامات {self.material_cost:,.2f})")

    def cancel(self):
        if self.status != self.DRAFT:
            raise ValidationError("مينفعش تلغي أمر اتسلّم")
        self.status = self.CANCELLED
        self.save(update_fields=["status"])


class ProductionOutput(models.Model):
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="outputs")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, verbose_name="المقاس/اللون")
    quantity = models.PositiveIntegerField("عدد القطع", validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "قطع الأمر"
        verbose_name_plural = "القطع المطلوبة"

    def __str__(self):
        return f"{self.variant} × {self.quantity}"


class ProductionMaterial(models.Model):
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="materials")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, verbose_name="الخامة")
    quantity = models.DecimalField("الكمية المصروفة", validators=[MinValueValidator(Decimal("0.001"))],
                                   **QTY)
    unit_cost = models.DecimalField("تكلفة الوحدة وقت الصرف", max_digits=12, decimal_places=4,
                                    default=0, editable=False)

    class Meta:
        verbose_name = "خامة مصروفة"
        verbose_name_plural = "الخامات المصروفة"

    def __str__(self):
        return f"{self.material} × {self.quantity}"


# ================= العهدة على الأشخاص =================
class CustodyHolder(models.Model):
    """الشخص اللي بيستلم عهدة: مستخدم في النظام، أو اسم من بره (سواق، مندوب...)"""
    name = models.CharField("الاسم", max_length=120, unique=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="custody_holder",
                                verbose_name="حسابه في النظام",
                                help_text="لو مربوط بحساب، يقدر يفتح 'عهدتي' ويطلب تصفية بنفسه")
    phone = models.CharField("الموبايل", max_length=30, blank=True)
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("وقت التسجيل", auto_now_add=True)

    class Meta:
        verbose_name = "صاحب عهدة"
        verbose_name_plural = "أصحاب العهد"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def totals(self):
        received = self.handouts.aggregate(s=Sum("amount"))["s"] or ZERO
        approved = self.clearances.filter(status=CustodyClearance.APPROVED)
        cleared = approved.aggregate(s=Sum("amount"))["s"] or ZERO
        pending = (self.clearances.filter(status=CustodyClearance.PENDING)
                   .aggregate(s=Sum("amount"))["s"] or ZERO)
        return {"received": received, "cleared": cleared, "pending": pending,
                "balance": received - cleared}


class CustodyClearance(models.Model):
    """تصفية عهدة شخص: صرف منها (مصروف بإيصال) أو رجّع فلوس. مش بتتحسب غير بموافقة مدير الحسابات."""
    SPENT, RETURNED = "spent", "returned"
    KINDS = [(SPENT, "صرف (مصروف)"), (RETURNED, "رجّع فلوس للعهدة العامة")]
    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
    STATUSES = [(PENDING, "مستني الموافقة"), (APPROVED, "اتوافق عليها"), (REJECTED, "اترفضت")]

    holder = models.ForeignKey(CustodyHolder, on_delete=models.PROTECT, related_name="clearances",
                               verbose_name="صاحب العهدة")
    kind = models.CharField("النوع", max_length=10, choices=KINDS, default=SPENT)
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    date = models.DateField("التاريخ", default=timezone.localdate)
    description = models.CharField("البيان", max_length=200,
                                   help_text="اتصرفت في إيه؟ (بنزين، نقل، خامات...)")
    screenshot = models.FileField("صورة الإيصال", upload_to="custody/clear/",
                                  storage=private_storage, blank=True)
    status = models.CharField("الحالة", max_length=10, choices=STATUSES, default=PENDING,
                              db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                     related_name="custody_requests", verbose_name="طلبها")
    requested_at = models.DateTimeField("وقت الطلب", auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="custody_reviews",
                                    verbose_name="راجعها")
    reviewed_at = models.DateTimeField("وقت المراجعة", null=True, blank=True)
    review_note = models.CharField("ملاحظة المراجعة", max_length=200, blank=True)

    class Meta:
        verbose_name = "تصفية عهدة شخص"
        verbose_name_plural = "تصفيات عهد الأشخاص"
        ordering = ["-requested_at", "-id"]
        permissions = [("approve_custodyclearance", "الموافقة على تصفية العهد (مدير الحسابات)")]

    def __str__(self):
        return f"{self.holder}: {self.get_kind_display()} {self.amount}"

    @property
    def has_proof(self):
        return bool(self.screenshot)

    @transaction.atomic
    def review(self, user, approve, note=""):
        obj = CustodyClearance.objects.select_for_update().get(pk=self.pk)
        if obj.status != self.PENDING:
            raise ValidationError("الطلب ده اتراجع قبل كده")
        if not user.has_perm("trade.approve_custodyclearance"):
            raise ValidationError("الموافقة للمحاسب ومدير الحسابات بس")
        if obj.requested_by_id == user.pk and not user.is_superuser:
            raise ValidationError("مينفعش توافق على طلب انت اللي عامله")
        if approve:
            balance = self.holder.totals()["balance"]
            if self.amount > balance:
                raise ValidationError(f"المبلغ أكبر من رصيد عهدة {self.holder} ({balance:,.2f})")
        self.status = self.APPROVED if approve else self.REJECTED
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.review_note = note[:200]
        self.save()
        if approve and self.kind == self.SPENT:
            # المصروف بيظهر في التقارير وصافي الربح، ومش بيتخصم من العهدة العامة تاني
            Expense.objects.create(title=f"{self.description} (عهدة {self.holder})"[:150],
                                   amount=self.amount, date=self.date, custody_clearance=self)


# ================= الحضور والانصراف (بصمة بصورة لايف + لوكيشن) =================
class Workplace(models.Model):
    """مكان شغل (المحل، المخزن...): البصمة جوه دايرة نصف قطرها radius_m بتتحسب 'في المكان'"""
    name = models.CharField("الاسم", max_length=100)
    latitude = models.DecimalField("خط العرض", max_digits=9, decimal_places=6)
    longitude = models.DecimalField("خط الطول", max_digits=9, decimal_places=6)
    radius_m = models.PositiveIntegerField("المسافة المسموحة (متر)", default=150)
    is_active = models.BooleanField("نشط", default=True)

    class Meta:
        verbose_name = "مكان شغل"
        verbose_name_plural = "أماكن الشغل (للبصمة)"

    def __str__(self):
        return self.name


def _attendance_photo_path(instance, filename):
    day = timezone.localdate()
    return f"attendance/{day:%Y/%m}/{filename}"


class Attendance(models.Model):
    IN, OUT = "in", "out"
    KINDS = [(IN, "حضور"), (OUT, "انصراف")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="attendance", verbose_name="الموظف")
    kind = models.CharField("النوع", max_length=3, choices=KINDS)
    at = models.DateTimeField("الوقت", default=timezone.now, db_index=True)
    latitude = models.DecimalField("خط العرض", max_digits=9, decimal_places=6)
    longitude = models.DecimalField("خط الطول", max_digits=9, decimal_places=6)
    accuracy_m = models.PositiveIntegerField("دقة اللوكيشن (متر)", default=0)
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True,
                                  verbose_name="أقرب مكان شغل")
    distance_m = models.PositiveIntegerField("البعد عن المكان (متر)", null=True, blank=True)
    inside = models.BooleanField("في مكان الشغل", default=False)
    photo = models.FileField("الصورة", upload_to=_attendance_photo_path, storage=private_storage)
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    ip = models.CharField("IP", max_length=64, blank=True)
    device = models.CharField("الجهاز", max_length=200, blank=True)

    class Meta:
        verbose_name = "بصمة"
        verbose_name_plural = "الحضور والانصراف"
        ordering = ["-at", "-id"]

    def __str__(self):
        return f"{self.user} — {self.get_kind_display()} {timezone.localtime(self.at):%Y-%m-%d %H:%M}"

    @property
    def map_url(self):
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"


# عناوين أعمدة الحسابات في لوحة الإدارة (بدل Total و Profit)
for _model, _prop, _label in [(PurchaseInvoice, "total", "الإجمالي"), (SaleInvoice, "total", "الإجمالي"),
                              (SaleInvoice, "profit", "الربح"), (PurchaseReturn, "total", "الإجمالي"),
                              (SaleReturn, "total", "الإجمالي"), (MaterialPurchase, "total", "الإجمالي"),
                              (SaleInvoice, "pieces", "القطع"), (OnlineOrder, "pieces", "القطع")]:
    getattr(_model, _prop).fget.short_description = _label


# ================= المأموريات =================
class Mission(models.Model):
    OPEN, DONE = "open", "done"
    STATUSES = [(OPEN, "شغالة"), (DONE, "خلصت")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="missions",
                             verbose_name="الموظف")
    purpose = models.CharField("الغرض", max_length=200)
    destination = models.CharField("رايح فين", max_length=200, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 verbose_name="العميل")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True,
                                 verbose_name="المورد")
    status = models.CharField("الحالة", max_length=5, choices=STATUSES, default=OPEN, db_index=True)
    started_at = models.DateTimeField("البداية", default=timezone.now, db_index=True)
    ended_at = models.DateTimeField("النهاية", null=True, blank=True)

    class Meta:
        verbose_name = "مأمورية"
        verbose_name_plural = "المأموريات"
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"{self.user} — {self.purpose}"

    @property
    def duration_minutes(self):
        end = self.ended_at or timezone.now()
        return int((end - self.started_at).total_seconds() // 60)

    @property
    def route_url(self):
        pts = [f"{p.latitude},{p.longitude}" for p in self.points.all()]
        if not pts:
            return ""
        if len(pts) == 1:
            return f"https://www.google.com/maps?q={pts[0]}"
        return "https://www.google.com/maps/dir/" + "/".join(pts[:25])


class MissionPoint(models.Model):
    KINDS = [("start", "بدأ"), ("arrive", "وصل"), ("note", "ملاحظة"), ("end", "خلّص")]
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="points")
    kind = models.CharField("النوع", max_length=6, choices=KINDS)
    at = models.DateTimeField("الوقت", default=timezone.now)
    latitude = models.DecimalField("خط العرض", max_digits=9, decimal_places=6)
    longitude = models.DecimalField("خط الطول", max_digits=9, decimal_places=6)
    accuracy_m = models.PositiveIntegerField("الدقة (متر)", default=0)
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    photo = models.FileField("صورة", upload_to="missions/", storage=private_storage, blank=True)

    class Meta:
        verbose_name = "نقطة مأمورية"
        verbose_name_plural = "نقاط المأموريات"
        ordering = ["at", "id"]

    @property
    def map_url(self):
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"


# ================= الموظفين: المرتبات والإجازات وكشف الحساب =================
class EmployeeProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="employee", verbose_name="الموظف")
    job_title = models.CharField("الوظيفة", max_length=100, blank=True)
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="team", verbose_name="المدير المباشر",
                                help_text="بيوافق على إجازاته وأذوناته (ومدير الحسابات كمان يقدر)")
    monthly_salary = models.DecimalField("المرتب الشهري", max_digits=12, decimal_places=2, default=0)
    shift_start = models.TimeField("ميعاد الحضور", default="10:00")
    shift_end = models.TimeField("ميعاد الانصراف", default="18:00")
    grace_minutes = models.PositiveIntegerField("سماح التأخير (دقيقة)", default=15)
    off_days = models.CharField("أيام الإجازة الأسبوعية", max_length=20, default="4",
                                help_text="أرقام الأيام مفصولة بفاصلة: 0 الاتنين ... 4 الجمعة، 5 السبت، 6 الحد")
    overtime_rate = models.DecimalField("معامل الأوفر تايم", max_digits=4, decimal_places=2, default=1,
                                        help_text="1 = نفس سعر الساعة، 1.5 = ساعة ونص")
    count_overtime = models.BooleanField("يتحسب أوفر تايم", default=True)
    start_date = models.DateField("بداية حساب المرتب", default=timezone.localdate,
                                  help_text="الأيام اللي قبل التاريخ ده مش بتتحسب غياب")
    is_active = models.BooleanField("بيشتغل حالياً", default=True)

    class Meta:
        verbose_name = "بيانات موظف"
        verbose_name_plural = "بيانات الموظفين (المرتبات)"

    def __str__(self):
        return self.user.get_full_name() or self.user.get_username()

    @property
    def off_set(self):
        return {int(x) for x in self.off_days.replace(" ", "").split(",") if x.isdigit()}


class LeaveRequest(models.Model):
    KINDS = [("annual", "إجازة اعتيادية"), ("sick", "إجازة مرضي"), ("unpaid", "إجازة من غير مرتب"),
             ("permission", "إذن (ساعات)")]
    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
    STATUSES = [(PENDING, "مستني الموافقة"), (APPROVED, "اتوافق عليها"), (REJECTED, "اترفضت")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leaves",
                             verbose_name="الموظف")
    kind = models.CharField("النوع", max_length=10, choices=KINDS, default="annual")
    start_date = models.DateField("من يوم")
    end_date = models.DateField("لحد يوم")
    hours = models.DecimalField("عدد الساعات (للإذن)", max_digits=4, decimal_places=1, null=True, blank=True)
    reason = models.CharField("السبب", max_length=200, blank=True)
    status = models.CharField("الحالة", max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    requested_at = models.DateTimeField("وقت الطلب", auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="leave_reviews", verbose_name="راجعها")
    review_note = models.CharField("ملاحظة المراجعة", max_length=200, blank=True)

    class Meta:
        verbose_name = "طلب إجازة/إذن"
        verbose_name_plural = "طلبات الإجازات والأذونات"
        ordering = ["-requested_at", "-id"]
        permissions = [("approve_leaverequest", "الموافقة على الإجازات والأذونات (مدير الحسابات)")]

    def __str__(self):
        return f"{self.user} — {self.get_kind_display()} {self.start_date}"

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class EmployeeTxn(models.Model):
    """حركة في كشف حساب الموظف. الرصيد = اللي ليه عند المحل."""
    KINDS = [("salary", "مرتب الشهر"), ("bonus", "مكافأة"), ("deduction", "خصم/جزاء"),
             ("advance", "سلفة"), ("payment", "صرف مرتب")]
    PLUS = {"salary", "bonus"}
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee_txns",
                             verbose_name="الموظف")
    date = models.DateField("التاريخ", default=timezone.localdate)
    kind = models.CharField("النوع", max_length=10, choices=KINDS)
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    description = models.CharField("البيان", max_length=200, blank=True)
    period = models.CharField("الشهر", max_length=7, blank=True, db_index=True, help_text="للمرتب: 2026-11")
    details = models.JSONField("تفاصيل الحساب", default=dict, blank=True)
    box = models.ForeignKey(CashBox, on_delete=models.SET_NULL, null=True, blank=True,
                            verbose_name="الخزنة (للسلفة والصرف)")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="+", editable=False, verbose_name="سجّلها")
    created_at = models.DateTimeField("وقت التسجيل", auto_now_add=True)

    class Meta:
        verbose_name = "حركة موظف"
        verbose_name_plural = "كشوف حساب الموظفين"
        ordering = ["date", "id"]
        constraints = [models.UniqueConstraint(fields=["user", "period"], condition=models.Q(kind="salary"),
                                               name="one_salary_per_month")]

    def __str__(self):
        return f"{self.user} — {self.get_kind_display()} {self.amount}"

    @property
    def signed(self):
        return self.amount if self.kind in self.PLUS else -self.amount


# ================= الجرد =================
class StockCount(models.Model):
    DRAFT, APPLIED, CANCELLED = "draft", "applied", "cancelled"
    STATUSES = [(DRAFT, "شغال"), (APPLIED, "اتطبق على المخزون"), (CANCELLED, "ملغي")]
    name = models.CharField("اسم الجرد", max_length=120)
    status = models.CharField("الحالة", max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name="+", verbose_name="بدأه")
    created_at = models.DateTimeField("البداية", auto_now_add=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+", verbose_name="طبّقه")
    applied_at = models.DateTimeField("وقت التطبيق", null=True, blank=True)

    class Meta:
        verbose_name = "جرد"
        verbose_name_plural = "الجرد"
        ordering = ["-created_at", "-id"]
        permissions = [("apply_stockcount", "تطبيق الجرد على المخزون (تعديل الكميات)")]

    def __str__(self):
        return self.name

    @transaction.atomic
    def apply(self, user):
        """الكمية في السيستم بتبقى اللي اتعد. الأصناف اللي ما اتعدتش ما بتتغيرش."""
        count = StockCount.objects.select_for_update().get(pk=self.pk)
        if count.status != self.DRAFT:
            raise ValidationError("الجرد ده اتقفل قبل كده")
        lines = list(self.lines.select_related("variant"))
        if not lines:
            raise ValidationError("مفيش أصناف اتعدت")
        diff_qty, diff_value = 0, ZERO
        for line in lines:
            v = Variant.objects.select_for_update().get(pk=line.variant_id)
            line.expected = v.quantity
            line.save(update_fields=["expected"])
            delta = line.counted - v.quantity
            diff_qty += delta
            diff_value += delta * v.avg_cost
            v.quantity = line.counted
            v.save()
        self.status, self.applied_by, self.applied_at = self.APPLIED, user, timezone.now()
        self.save()
        return diff_qty, diff_value


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, verbose_name="الصنف")
    counted = models.PositiveIntegerField("الكمية المعدودة", default=0)
    expected = models.IntegerField("كانت في السيستم", null=True, blank=True)

    class Meta:
        verbose_name = "صنف في الجرد"
        verbose_name_plural = "أصناف الجرد"
        unique_together = ("count", "variant")



# ================= الموقع (المتجر الأونلاين) =================
class ProductImage(models.Model):
    """صورة موديل في الموقع. الصورة بتتصغر لوحدها وقت الرفع (كبيرة + مصغرة) عشان الموقع يبقى سريع."""
    model = models.ForeignKey(ClothingModel, on_delete=models.CASCADE, related_name="images",
                              verbose_name="الموديل")
    image = models.ImageField("الصورة", upload_to="products/")
    thumb = models.ImageField("مصغرة", upload_to="products/thumbs/", blank=True, editable=False)
    color = models.CharField("اللون", max_length=30, blank=True,
                             help_text="اختياري: لو الصورة للون معين، بتظهر لما العميل يختاره")
    sort = models.PositiveSmallIntegerField("الترتيب", default=0)

    class Meta:
        verbose_name = "صورة منتج"
        verbose_name_plural = "صور المنتجات"
        ordering = ["sort", "id"]

    def __str__(self):
        return f"صورة {self.model.name}"

    @property
    def thumb_url(self):
        return self.thumb.url if self.thumb else self.image.url

    def save(self, *args, **kwargs):
        if self.image and not getattr(self.image, "_committed", True):
            from .store_media import process_image
            big, small = process_image(self.image)
            self.image.save(big.name, big, save=False)
            self.thumb.save(small.name, small, save=False)
        super().save(*args, **kwargs)


class ShippingZone(models.Model):
    """مصاريف الشحن على العميل حسب المحافظة"""
    name = models.CharField("المحافظة / المنطقة", max_length=60, unique=True)
    fee = models.DecimalField("مصاريف الشحن", max_digits=10, decimal_places=2, default=0)
    days = models.CharField("مدة التوصيل", max_length=40, blank=True, help_text="مثلاً: 2-3 أيام")
    is_active = models.BooleanField("متاح", default=True)
    sort = models.PositiveSmallIntegerField("الترتيب", default=0)

    class Meta:
        verbose_name = "منطقة شحن"
        verbose_name_plural = "مصاريف الشحن (الموقع)"
        ordering = ["sort", "name"]

    def __str__(self):
        return self.name


class StoreSettings(models.Model):
    """إعدادات الموقع (سجل واحد بس)"""
    announcement = models.CharField("شريط الإعلان فوق", max_length=160, blank=True,
                                    help_text="مثلاً: شحن مجاني فوق 1500 ج.م")
    hero_title = models.CharField("العنوان الكبير في الرئيسية", max_length=80, default="SPIRIT")
    hero_subtitle = models.CharField("الكلام تحت العنوان", max_length=200, blank=True)
    hero_image = models.ImageField("صورة الرئيسية", upload_to="store/", blank=True)
    whatsapp = models.CharField("رقم واتساب خدمة العملاء", max_length=20, blank=True)
    facebook = models.URLField("فيسبوك", blank=True)
    instagram = models.URLField("إنستجرام", blank=True)
    tiktok = models.URLField("تيك توك", blank=True)
    free_shipping_over = models.DecimalField("شحن مجاني لو الطلب فوق", max_digits=10, decimal_places=2,
                                             null=True, blank=True, help_text="فاضي = مفيش شحن مجاني")
    web_seller = models.ForeignKey("Seller", on_delete=models.SET_NULL, null=True, blank=True,
                                   verbose_name="البائع اللي طلبات الموقع بتتسجل عليه")
    is_open = models.BooleanField("الموقع بياخد طلبات", default=True,
                                  help_text="اقفلها لو عايز توقف الطلبات مؤقتاً (الموقع بيفضل ظاهر)")
    size_guide = models.TextField(
        "دليل المقاسات", blank=True,
        help_text="كل سطر: المقاس | الصدر | الطول | الوزن المناسب. مثال: M | 104 | 72 | 65-75 كجم. "
                  "فاضي = جدول افتراضي")

    class Meta:
        verbose_name = "إعدادات الموقع"
        verbose_name_plural = "إعدادات الموقع"

    def __str__(self):
        return "إعدادات الموقع"

    def save(self, *args, **kwargs):
        self.pk = 1
        if self.hero_image and not getattr(self.hero_image, "_committed", True):
            from .store_media import process_image
            big, _small = process_image(self.hero_image, max_side=2000)
            self.hero_image.save(big.name, big, save=False)
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class HeroSlide(models.Model):
    """شريحة في السلايدر الكبير أول الموقع (صورة عريضة + عنوان + زرار)"""
    image = models.ImageField("الصورة (عريضة)", upload_to="store/slides/")
    title = models.CharField("العنوان", max_length=80)
    subtitle = models.CharField("الكلام تحت العنوان", max_length=160, blank=True)
    button = models.CharField("كلام الزرار", max_length=30, default="تسوّق دلوقتي")
    link = models.CharField("الزرار بيودّي على", max_length=200, blank=True,
                            help_text="فاضي = كل المنتجات. أو رابط قسم زي /c/pants/ أو منتج زي /p/M-02/")
    sort = models.PositiveSmallIntegerField("الترتيب", default=0)
    is_active = models.BooleanField("ظاهرة", default=True)

    class Meta:
        verbose_name = "شريحة في الرئيسية"
        verbose_name_plural = "سلايدر الرئيسية (الموقع)"
        ordering = ["sort", "id"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.image and not getattr(self.image, "_committed", True):
            from .store_media import process_image
            big, _small = process_image(self.image, max_side=2200)
            self.image.save(big.name, big, save=False)
        super().save(*args, **kwargs)


def _order_token():
    import secrets
    return secrets.token_urlsafe(12)


class WebOrder(models.Model):
    """طلب من الموقع: بيستنى تأكيد (مكالمة/واتساب)، والتأكيد بيعمل أوردر أونلاين ويخصم المخزون"""
    NEW, CONFIRMED, CANCELLED = "new", "confirmed", "cancelled"
    STATUSES = [(NEW, "جديد - محتاج تأكيد"), (CONFIRMED, "اتأكد واتحول أوردر"), (CANCELLED, "اتلغى")]

    token = models.CharField(max_length=32, unique=True, default=_order_token, editable=False)
    status = models.CharField("الحالة", max_length=10, choices=STATUSES, default=NEW, db_index=True)
    customer_name = models.CharField("الاسم", max_length=150)
    phone = models.CharField("الموبايل", max_length=20, db_index=True)
    phone2 = models.CharField("موبايل تاني", max_length=20, blank=True)
    zone = models.ForeignKey(ShippingZone, on_delete=models.SET_NULL, null=True, verbose_name="المحافظة")
    zone_name = models.CharField("المحافظة", max_length=60)
    city = models.CharField("المدينة / المنطقة", max_length=80)
    address = models.CharField("العنوان بالتفصيل", max_length=300)
    notes = models.CharField("ملاحظات العميل", max_length=300, blank=True)
    shipping_fee = models.DecimalField("مصاريف الشحن", max_digits=10, decimal_places=2, default=0)
    online_order = models.OneToOneField(OnlineOrder, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="web_order", verbose_name="الأوردر الأونلاين")
    cancel_reason = models.CharField("سبب الإلغاء", max_length=200, blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   editable=False, verbose_name="اتعامل معاه")
    handled_at = models.DateTimeField("وقت التأكيد/الإلغاء", null=True, blank=True, editable=False)
    ip = models.GenericIPAddressField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField("وقت الطلب", auto_now_add=True)

    class Meta:
        verbose_name = "طلب من الموقع"
        verbose_name_plural = "طلبات الموقع"
        ordering = ["-id"]

    def __str__(self):
        return f"طلب موقع #{self.pk} - {self.customer_name}"

    @property
    def items_total(self):
        return sum((i.quantity * i.unit_price for i in self.items.all()), ZERO)

    @property
    def total(self):
        return self.items_total + self.shipping_fee

    @property
    def pieces(self):
        return sum((i.quantity for i in self.items.all()), 0)

    @transaction.atomic
    def confirm(self, user, seller, shipping_cost=ZERO, discount=ZERO):
        """يحوّل الطلب لأوردر أونلاين (حالة: تم التجهيز) ويخصم المخزون"""
        order = WebOrder.objects.select_for_update().get(pk=self.pk)
        if order.status != self.NEW:
            raise ValidationError("الطلب ده اتعامل معاه قبل كده")
        items = [{"id": i.variant_id, "qty": i.quantity, "price": i.unit_price} for i in self.items.all()]
        phones = self.phone + (f" / {self.phone2}" if self.phone2 else "")
        online = OnlineOrder.create_order(
            customer_name=self.customer_name, phone=phones,
            address=f"{self.zone_name} - {self.city} - {self.address}",
            notes=f"طلب الموقع #{self.pk}" + (f" — {self.notes}" if self.notes else ""),
            items=items, shipping_fee=self.shipping_fee, shipping_cost=shipping_cost,
            discount=discount, seller=seller)
        self.status, self.online_order = self.CONFIRMED, online
        self.handled_by, self.handled_at = user, timezone.now()
        self.save()
        return online

    def cancel(self, user, reason=""):
        if self.status != self.NEW:
            raise ValidationError("الطلب ده اتعامل معاه قبل كده")
        self.status, self.cancel_reason = self.CANCELLED, reason[:200]
        self.handled_by, self.handled_at = user, timezone.now()
        self.save()


class WebOrderItem(models.Model):
    order = models.ForeignKey(WebOrder, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, verbose_name="الصنف")
    quantity = models.PositiveIntegerField("الكمية")
    unit_price = models.DecimalField("السعر", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "صنف في طلب الموقع"
        verbose_name_plural = "أصناف طلبات الموقع"
