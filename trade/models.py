import os
from decimal import Decimal

from django.conf import settings
from django.core.files.storage import FileSystemStorage

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
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
    description = models.TextField("وصف", blank=True)
    is_active = models.BooleanField("نشط", default=True)
    low_stock_level = models.PositiveIntegerField(
        "حد التنبيه", default=5,
        help_text="لما كمية أي مقاس/لون توصل للرقم ده أو أقل يجيلك تنبيه إن المخزون قرب يخلص")

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
    sale_price = models.DecimalField("سعر البيع", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "مقاس/لون"
        verbose_name_plural = "المقاسات والألوان"
        unique_together = ("model", "size", "color")

    def __str__(self):
        return f"{self.model.name} - {self.size} - {self.color}".strip(" -")

    def add_stock(self, qty, unit_cost):
        """إضافة كمية للمخزن مع تحديث متوسط التكلفة (توريد أو مرتجع)"""
        new_qty = self.quantity + qty
        if new_qty > 0:
            self.avg_cost = ((self.quantity * self.avg_cost) +
                             (qty * unit_cost)) / new_qty
        self.quantity = new_qty


class Supplier(models.Model):
    name = models.CharField("اسم المصنع/المورد", max_length=150)
    phone = models.CharField("الهاتف", max_length=30, blank=True)
    address = models.CharField("العنوان", max_length=250, blank=True)

    class Meta:
        verbose_name = "مصنع/مورد"
        verbose_name_plural = "المصانع والموردين"

    def __str__(self):
        return self.name

    @property
    def balance(self):
        """المستحق للمورد = فواتير التوريد + مشتريات الخامات المؤكدة - مرتجعات مؤكدة - مدفوعات"""
        bought = sum((inv.total for inv in self.purchaseinvoice_set.filter(is_confirmed=True)), ZERO)
        bought += sum((p.total for p in self.material_purchases.filter(is_confirmed=True)), ZERO)
        returned = sum((r.total for r in self.purchase_returns.filter(is_confirmed=True)), ZERO)
        paid = sum((p.amount for p in self.supplier_payments.all()), ZERO)
        return bought - returned - paid


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
                        F("credit_gross") - F("credit_discount") - F("returned_total"),
                        output_field=DEC),
                    balance=ExpressionWrapper(
                        F("credit_gross") - F("credit_discount") - F("returned_total")
                        - F("paid_total"), output_field=DEC)))


class Customer(models.Model):
    name = models.CharField("اسم العميل", max_length=150)
    phone = models.CharField("الهاتف", max_length=30, blank=True)
    seller = models.ForeignKey("Seller", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="customers", verbose_name="البائع المرتبط")

    objects = CustomerQuerySet.as_manager()

    class Meta:
        verbose_name = "عميل"
        verbose_name_plural = "العملاء"
        permissions = [("change_customer_seller", "تغيير بائع العميل (مدير الحسابات)")]

    def __str__(self):
        return self.name


class PurchaseInvoice(models.Model):
    """فاتورة توريد من المصنع"""
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, verbose_name="المصنع")
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
    date = models.DateField("التاريخ")
    payment_type = models.CharField("نوع الفاتورة", max_length=10,
                                    choices=PAYMENT_TYPES, default="cash")
    channel = models.CharField("القناة", max_length=10, choices=CHANNELS,
                               default="retail")
    seller = models.ForeignKey("Seller", on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="invoices", verbose_name="البائع")
    discount = models.DecimalField("خصم", max_digits=12, decimal_places=2, default=0)
    notes = models.TextField("ملاحظات", blank=True)
    is_confirmed = models.BooleanField("مؤكدة", default=False, editable=False)

    @property
    def pieces(self):
        return sum((i.quantity for i in self.items.all()), 0)

    class Meta:
        verbose_name = "فاتورة بيع"
        verbose_name_plural = "فواتير البيع"
        permissions = [("change_customer_price",
                        "تعديل سعر صنف لعميل سبق له شراءه (مدير الحسابات)")]

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
        return f"{self.text}\n👤 {who}   🕒 {when}"


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
    created_at = models.DateTimeField(auto_now_add=True)

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
             ("transfer", "تحويل بين الخزن"), ("adjust", "رصيد افتتاحي / تسوية")]

    box = models.ForeignKey(CashBox, on_delete=models.PROTECT, related_name="entries",
                            verbose_name="الخزنة")
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2)
    date = models.DateField("التاريخ", default=timezone.localdate)
    kind = models.CharField("النوع", max_length=20, choices=KINDS)
    description = models.CharField("البيان", max_length=200, blank=True)
    invoice = models.ForeignKey(SaleInvoice, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="cash_entries")
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="cash_entries")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="cash_entries")
    online_order = models.ForeignKey(OnlineOrder, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name="cash_entries")
    transfer = models.ForeignKey("CashTransfer", on_delete=models.CASCADE, null=True,
                                 blank=True, related_name="entries")
    created_at = models.DateTimeField(auto_now_add=True)

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
                                   on_delete=models.SET_NULL, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

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
    """سداد لمورد/مصنع من خزنة"""
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT,
                                 related_name="supplier_payments", verbose_name="المورد")
    box = models.ForeignKey(CashBox, on_delete=models.PROTECT, related_name="supplier_payments",
                            verbose_name="الخزنة")
    amount = models.DecimalField("المبلغ", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    date = models.DateField("التاريخ", default=timezone.localdate)
    notes = models.CharField("ملاحظات", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سداد مورد"
        verbose_name_plural = "سدادات الموردين"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.supplier} - {self.amount}"


# ================= المرتجعات =================
class PurchaseReturn(models.Model):
    """مرتجع مورد: بضاعة بترجع للمصنع، بتتخصم من حسابه ومن المخزون"""
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
