import io
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from trade.models import (ActivityLog, ClothingModel, Customer, Expense, PurchaseInvoice, PurchaseItem,
                          RawMaterial, SaleInvoice, SaleItem, Seller, Supplier, SupplierPayment, Variant,
                          CashBox)
from trade.statements import customer_statement, supplier_statement
from trade.treasury import record_customer_payment, record_supplier_payment

D = Decimal


def xlsx(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "البيانات"
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = "data.xlsx"
    return buf


class ImportTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss", "", "pw")
        self.c = Client()
        self.c.force_login(self.boss)
        self.seller = Seller.objects.get(name="خالد فلكس")

    def upload(self, key, f):
        r = self.c.post(reverse("data_upload", args=[key]), {"file": f})
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        return r["Location"]

    def confirm(self, preview_url):
        token = preview_url.rstrip("/").split("/")[-1]
        return self.c.post(reverse("data_confirm", args=[token]))

    def test_template_download_has_headers(self):
        r = self.c.get(reverse("data_file", args=["customers", "template"]))
        self.assertEqual(r.status_code, 200)
        wb = load_workbook(io.BytesIO(r.content))
        self.assertIn("اسم العميل *", [c.value for c in wb["البيانات"][1]])
        self.assertIn("التعليمات", wb.sheetnames)

    def test_customers_import_preview_confirm_and_reimport(self):
        Customer.objects.create(name="قديم", phone="01000000001", seller=self.seller)
        f = xlsx(["اسم العميل *", "الموبايل", "البائع", "رصيد افتتاحي (عليه)", "تاريخ الرصيد"], [
            ["أحمد", 1012345678, "خالد فلكس", 1500, date(2026, 1, 1)],   # الموبايل رقم: الصفر بيرجع
            ["قديم معدّل", "01000000001", "", 200, None],
        ])
        url = self.upload("customers", f)
        page = self.c.get(url)
        self.assertContains(page, "جديد: 1")
        self.assertContains(page, "تحديث: 1")
        self.assertEqual(Customer.objects.count(), 1)  # لسه مفيش حاجة اتحفظت
        ActivityLog.objects.all().delete()
        self.confirm(url)
        a = Customer.objects.with_balance().get(phone="01012345678")
        self.assertEqual((a.name, a.seller, a.balance), ("أحمد", self.seller, D("1500")))
        self.assertEqual(a.opening_date, date(2026, 1, 1))
        self.assertEqual(Customer.objects.get(phone="01000000001").name, "قديم معدّل")
        self.assertEqual(ActivityLog.objects.count(), 1)  # رسالة واحدة مش رسالة لكل عميل
        # نفس الملف تاني: مفيش تكرار
        f.seek(0)
        self.confirm(self.upload("customers", f))
        self.assertEqual(Customer.objects.count(), 2)

    def test_errors_block_import(self):
        f = xlsx(["اسم العميل *", "الموبايل", "البائع"], [
            ["تمام", "0111", "خالد فلكس"], ["", "0122", "خالد فلكس"], ["بائع غلط", "", "مش موجود"]])
        url = self.upload("customers", f)
        page = self.c.get(url)
        self.assertContains(page, "غلط: 2")
        self.assertContains(page, "اسم العميل مطلوب")
        self.assertContains(page, "مش موجود")
        self.confirm(url)
        self.assertEqual(Customer.objects.count(), 0)

    def test_missing_required_column(self):
        r = self.c.post(reverse("data_upload", args=["customers"]),
                        {"file": xlsx(["الموبايل"], [["0100"]])}, follow=True)
        self.assertContains(r, "الأعمدة دي ناقصة")

    def test_products_opening_stock_once(self):
        headers = ["كود الموديل *", "اسم الموديل *", "المقاس *", "اللون", "سعر البيع *", "حد التنبيه",
                   "كمية افتتاحية", "تكلفة القطعة"]
        rows = [["T1", "تيشرت", "M", "أسود", 250, 3, 10, 120],
                ["T1", "تيشرت", "L", "أسود", 250, None, 4, 130],
                ["P1", "بنطلون", "32", "", 400, None, None, None]]
        self.confirm(self.upload("products", xlsx(headers, rows)))
        m = ClothingModel.objects.get(code="T1")
        self.assertEqual((m.name, m.low_stock_level, m.variants.count()), ("تيشرت", 3, 2))
        v = Variant.objects.get(model=m, size="M")
        self.assertEqual((v.quantity, v.avg_cost, v.sale_price), (10, D("120"), D("250")))
        # تاني مرة: الكمية مش بتتضاف تاني، والسعر بيتحدث
        rows[0][4] = 270
        self.confirm(self.upload("products", xlsx(headers, rows)))
        v.refresh_from_db()
        self.assertEqual((v.quantity, v.sale_price), (10, D("270")))
        self.assertEqual(Variant.objects.count(), 3)

    def test_products_opening_qty_needs_cost(self):
        url = self.upload("products", xlsx(["كود الموديل *", "اسم الموديل *", "المقاس *", "سعر البيع *",
                                            "كمية افتتاحية"], [["X", "س", "M", 100, 5]]))
        self.assertContains(self.c.get(url), "تكلفة القطعة")

    def test_suppliers_materials_expenses(self):
        self.confirm(self.upload("suppliers", xlsx(["اسم المورد *", "رصيد افتتاحي (لينا عليه)"],
                                                   [["مصنع النور", 5000]])))
        self.assertEqual(Supplier.objects.get().balance, D("5000"))
        self.confirm(self.upload("materials", xlsx(["اسم الخامة *", "الوحدة *", "كمية افتتاحية", "تكلفة الوحدة"],
                                                   [["قماش", "متر", 100, 50]])))
        self.assertEqual(RawMaterial.objects.get().quantity, D("100"))
        exp = xlsx(["التاريخ *", "البند *", "المبلغ *"], [["2026-01-05", "إيجار", 3000], ["05/01/2026", "كهربا", 400]])
        self.confirm(self.upload("expenses", exp))
        exp.seek(0)
        url = self.upload("expenses", exp)
        self.assertContains(self.c.get(url), "موجود قبل كده: 2")
        self.confirm(url)
        self.assertEqual(Expense.objects.count(), 2)

    def test_export_round_trip(self):
        Customer.objects.create(name="ع", phone="0100", seller=self.seller, opening_balance=D("10"))
        r = self.c.get(reverse("data_file", args=["customers", "export"]))
        wb = load_workbook(io.BytesIO(r.content))
        row = [c.value for c in wb["البيانات"][2]]
        self.assertEqual(row[:3], ["ع", "0100", "خالد فلكس"])

    def test_permissions(self):
        sales = User.objects.create_user("s", password="pw", is_staff=True)
        sales.groups.add(Group.objects.get(name="سيلز"))
        c = Client()
        c.force_login(sales)
        self.assertEqual(c.get(reverse("data_center")).status_code, 200)  # يقدر يصدّر اللي يشوفه
        r = c.post(reverse("data_upload", args=["customers"]), {"file": xlsx(["اسم العميل *"], [["x"]])})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(c.get(reverse("data_file", args=["expenses", "export"])).status_code, 403)


class StatementTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss", "", "pw")
        self.c = Client()
        self.c.force_login(self.boss)
        self.box = CashBox.objects.get(name="كاش المحل")
        self.seller = Seller.objects.get(name="خالد فلكس")

    def test_customer_statement_with_opening_and_period(self):
        cust = Customer.objects.create(name="ع", seller=self.seller, opening_balance=D("500"),
                                       opening_date=date(2026, 1, 1))
        m = ClothingModel.objects.create(name="ت", code="T")
        v = Variant.objects.create(model=m, size="M", sale_price=D("100"))
        sup = Supplier.objects.create(name="م")
        pi = PurchaseInvoice.objects.create(supplier=sup, date=date(2026, 1, 1))
        PurchaseItem.objects.create(invoice=pi, variant=v, quantity=10, unit_cost=D("50"))
        pi.confirm()
        inv = SaleInvoice.objects.create(customer=cust, date=date(2026, 2, 1), payment_type="credit",
                                         seller=self.seller)
        SaleItem.objects.create(invoice=inv, variant=v, quantity=3, unit_price=D("100"))
        inv.confirm()
        record_customer_payment(cust, self.box, D("200"), date(2026, 3, 1))
        st = customer_statement(cust)
        self.assertEqual([e["balance"] for e in st["entries"]], [D("500"), D("800"), D("600")])
        st = customer_statement(cust, d1=date(2026, 2, 15))
        self.assertEqual(st["entries"][0]["kind"], "رصيد سابق")
        self.assertEqual(st["entries"][0]["balance"], D("800"))
        self.assertEqual(st["totals"]["balance"], D("600"))
        self.assertEqual(Customer.objects.with_balance().get(pk=cust.pk).balance, D("600"))
        for fmt, ctype in (("pdf", "application/pdf"), ("xlsx", "spreadsheet")):
            r = self.c.get(reverse("customer_statement", args=[cust.pk, fmt]) + "?from=2026-02-01")
            self.assertEqual(r.status_code, 200)
            self.assertIn(ctype, r["Content-Type"])
        self.assertEqual(self.c.get(reverse("customer_statement_pdf", args=[cust.pk])).status_code, 200)

        sst = supplier_statement(sup)
        self.assertEqual(sst["totals"]["balance"], D("500"))
        record_supplier_payment(sup, self.box, D("300"), date(2026, 3, 2))
        self.assertEqual(supplier_statement(sup)["totals"]["balance"], sup.balance)
        self.assertEqual(SupplierPayment.objects.count(), 1)
        r = self.c.get(reverse("supplier_statement", args=[sup.pk, "xlsx"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(self.c.get(reverse("suppliers")), "م")
