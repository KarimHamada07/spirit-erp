import io
import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from trade.models import (CashBox, ClothingModel, PurchaseInvoice, PurchaseItem, SaleInvoice, SaleItem,
                          Seller, Supplier, Variant)

D = Decimal


class SaleOptionsTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss", "", "pw")
        self.c = Client()
        self.c.force_login(self.boss)
        self.box = CashBox.objects.get(name="كاش المحل")
        self.seller = Seller.objects.get(name="خالد فلكس")
        m = ClothingModel.objects.create(name="جاكيت", code="J1", category="jacket")
        self.v = Variant.objects.create(model=m, size="L", sale_price=D("333"))
        pi = PurchaseInvoice.objects.create(supplier=Supplier.objects.create(name="م"),
                                            date=timezone.localdate())
        PurchaseItem.objects.create(invoice=pi, variant=self.v, quantity=20, unit_cost=D("100"))
        pi.confirm()

    def checkout(self, discount, dtype, pay):
        body = {"items": [{"id": self.v.pk, "qty": 3, "price": "333"}], "discount": discount,
                "discount_type": dtype, "seller": self.seller.pk, "channel": "retail",
                "credit": False, "payments": [{"box": self.box.pk, "amount": pay}]}
        return self.c.post(reverse("pos_checkout"), json.dumps(body), content_type="application/json")

    def test_percent_discount(self):
        # 999 × 10% = 99.90 → الصافي 899.10
        r = self.checkout(10, "percent", 899.10)
        self.assertEqual(r.status_code, 200, r.content)
        inv = SaleInvoice.objects.get()
        self.assertEqual((inv.discount, inv.discount_percent, inv.total), (D("99.90"), D("10"), D("899.10")))
        self.assertContains(self.c.get(reverse("sale_print", args=[inv.pk])), "(10%)")

    def test_amount_discount_still_works(self):
        r = self.checkout(49, "amount", 950)
        self.assertEqual(r.status_code, 200, r.content)
        inv = SaleInvoice.objects.get()
        self.assertEqual((inv.discount, inv.discount_percent), (D("49"), None))

    def test_percent_over_100_rejected(self):
        self.assertEqual(self.checkout(150, "percent", 0).status_code, 400)

    def test_admin_percent_applies_on_confirm(self):
        inv = SaleInvoice.objects.create(seller=self.seller, discount_percent=D("12.5"))
        SaleItem.objects.create(invoice=inv, variant=self.v, quantity=2, unit_price=D("100"))
        inv.confirm()
        self.assertEqual(inv.discount, D("25.00"))
        self.assertEqual(inv.date, timezone.localdate())  # التاريخ لوحده

    def test_invoice_date_locked_for_sales(self):
        sales = User.objects.create_user("s", password="pw", is_staff=True)
        sales.groups.add(Group.objects.get(name="سيلز"))
        c = Client()
        c.force_login(sales)
        page = c.get(reverse("admin:trade_saleinvoice_add")).content.decode()
        self.assertNotIn('name="date"', page)
        page = self.c.get(reverse("admin:trade_saleinvoice_add")).content.decode()
        self.assertIn('name="date"', page)  # مدير الحسابات/المدير يقدر يعدّل

    def test_category_filter_and_import(self):
        ClothingModel.objects.create(name="بنطلون", code="P1", category="pants")
        page = self.c.get(reverse("inventory") + "?cat=jacket").content.decode()
        self.assertIn("جاكيت", page)
        self.assertNotIn("P1", page)
        wb = Workbook()
        ws = wb.active
        ws.title = "البيانات"
        ws.append(["كود الموديل *", "اسم الموديل *", "التصنيف", "المقاس *", "سعر البيع *"])
        ws.append(["T9", "تيشرت", "تيشيرت", "M", 150])
        ws.append(["S9", "شورت", "حاجة غلط", "M", 150])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = "x.xlsx"
        url = self.c.post(reverse("data_upload", args=["products"]), {"file": buf})["Location"]
        self.assertContains(self.c.get(url), "مش معروف")
        ws.delete_rows(3)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = "x.xlsx"
        url = self.c.post(reverse("data_upload", args=["products"]), {"file": buf})["Location"]
        self.c.post(reverse("data_confirm", args=[url.rstrip("/").split("/")[-1]]))
        self.assertEqual(ClothingModel.objects.get(code="T9").category, "tshirt")
