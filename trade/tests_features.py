import io
import tarfile
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from trade.debts import debt_rows, reminder_rows, reminder_text
from trade.models import (ActivityLog, BOMLine, CashBox, ClothingModel, Customer, DebtFollowUp,
                          MaterialPurchase, MaterialPurchaseItem, ProductionOrder, ProductionOutput,
                          PurchaseInvoice, PurchaseItem, RawMaterial, SaleInvoice, SaleItem,
                          SaleReturn, SaleReturnItem, Seller, Supplier, Variant)
from trade.notify import chat_for
from trade.profit import profitability
from trade.roles import ROLE_ACCOUNTANT
from trade.stock_alerts import low_stock_text
from trade.treasury import record_customer_payment

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.boss = User.objects.create_superuser("boss", "", "pw")
        self.client = Client()
        self.client.force_login(self.boss)
        self.seller = Seller.objects.get(name="خالد فلكس")
        self.box = CashBox.objects.get(name="كاش المحل")
        self.supplier = Supplier.objects.create(name="مصنع")
        self.model = ClothingModel.objects.create(name="تيشرت", code="T1", low_stock_level=3)
        self.v = Variant.objects.create(model=self.model, size="M", color="", sale_price=D("100"))
        pi = PurchaseInvoice.objects.create(supplier=self.supplier, date=self.today)
        PurchaseItem.objects.create(invoice=pi, variant=self.v, quantity=20, unit_cost=D("60"))
        pi.confirm()
        self.v.refresh_from_db()
        self.customer = Customer.objects.create(name="عميل", phone="010", seller=self.seller)

    def sale(self, qty, credit=True, discount=D("0"), days_ago=0, customer=None):
        inv = SaleInvoice.objects.create(
            customer=customer or self.customer, date=self.today - timedelta(days=days_ago),
            payment_type="credit" if credit else "cash", seller=self.seller, discount=discount)
        SaleItem.objects.create(invoice=inv, variant=self.v, quantity=qty, unit_price=D("100"))
        inv.confirm()
        return inv


class ReturnDiscountTests(Base):
    def test_return_refund_takes_invoice_discount(self):
        inv = self.sale(10, credit=True, discount=D("100"))  # 1000 - 100 = 900
        ret = SaleReturn.objects.create(invoice=inv, refund_mode="account")
        SaleReturnItem.objects.create(return_doc=ret, sale_item=inv.items.get(), quantity=10)
        self.assertEqual(ret.total, D("900.00"))  # قبل التأكيد: معاينة بعد الخصم
        ret.confirm()
        self.assertEqual(ret.total, D("900.00"))
        c = Customer.objects.with_balance().get(pk=self.customer.pk)
        self.assertEqual(c.balance, D("0"))  # مش -100 زي الأول
        inv.refresh_from_db()
        self.assertEqual(inv.remaining, D("0"))

    def test_cash_refund_does_not_exceed_paid(self):
        inv = self.sale(4, credit=False, discount=D("40"))  # 400 - 40
        ret = SaleReturn.objects.create(invoice=inv, refund_mode="box", refund_box=self.box)
        SaleReturnItem.objects.create(return_doc=ret, sale_item=inv.items.get(), quantity=4)
        ret.confirm()
        self.assertEqual(self.box.entries.get(kind="refund").amount, D("-360.00"))


class DebtReminderTests(Base):
    def test_aging_is_fifo_and_reminds_accountant_only(self):
        self.sale(3, days_ago=50)   # 300 قديمة
        self.sale(2, days_ago=5)    # 200 جديدة
        record_customer_payment(self.customer, self.box, D("300"))  # بتقفل القديمة
        rows = debt_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["balance"], D("200"))
        self.assertEqual(rows[0]["days"], 5)
        self.assertEqual(reminder_rows(rows, 30), [])

        record_customer_payment(self.customer, self.box, D("50"))
        self.sale(1, days_ago=40, customer=Customer.objects.create(name="قديم", seller=self.seller))
        rows = debt_rows()
        due = reminder_rows(rows, 30)
        self.assertEqual([r["customer"].name for r in due], ["قديم"])
        self.assertIn("قديم", reminder_text(due))

        ActivityLog.objects.all().delete()
        call_command("debt_reminders", stdout=io.StringIO())
        ev = ActivityLog.objects.get()
        self.assertEqual(ev.audience, "accountant")
        self.assertIn("للمحاسب", ev.text)

    def test_followup_date_snoozes_then_reminds(self):
        self.sale(1, days_ago=60)
        DebtFollowUp.objects.create(customer=self.customer, note="هيدفع الخميس",
                                    next_date=self.today + timedelta(days=3))
        self.assertEqual(reminder_rows(debt_rows(), 30), [])  # مأجّل
        DebtFollowUp.objects.create(customer=self.customer, note="كلمته تاني",
                                    next_date=self.today)
        rows = reminder_rows(debt_rows(), 30)
        self.assertTrue(rows and rows[0]["due"])

    def test_accountant_chat_routing(self):
        cfg = {"CHAT_ID": "main", "ACCOUNTANT_CHAT_ID": "acc", "TOKEN": "", "FEED_KEY": ""}
        self.assertEqual(chat_for("accountant", cfg), "acc")
        self.assertEqual(chat_for("", cfg), "main")
        cfg["ACCOUNTANT_CHAT_ID"] = ""
        self.assertEqual(chat_for("accountant", cfg), "main")

    def test_debts_page_and_followup(self):
        self.sale(2, days_ago=45)
        r = self.client.get(reverse("debts"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.customer.name)
        r = self.client.post(reverse("debt_followup_add", args=[self.customer.pk]),
                             {"note": "اتصلت", "next_date": str(self.today), "next": "https://evil.com/"})
        self.assertRedirects(r, reverse("debts"), fetch_redirect_response=False)
        self.assertEqual(DebtFollowUp.objects.get().created_by, self.boss)
        self.assertEqual(self.client.get(reverse("debts") + "?export=xlsx").status_code, 200)

    def test_accountant_role_can_open_page_sales_cannot(self):
        acc = User.objects.create_user("acc", password="pw", is_staff=True)
        acc.groups.add(Group.objects.get(name=ROLE_ACCOUNTANT))
        c = Client()
        c.force_login(acc)
        self.assertEqual(c.get(reverse("debts")).status_code, 200)
        sales = User.objects.create_user("s", password="pw", is_staff=True)
        sales.groups.add(Group.objects.get(name="سيلز"))
        c.force_login(sales)
        self.assertEqual(c.get(reverse("debts")).status_code, 403)


class StockAlertTests(Base):
    def test_model_level_used_for_alert(self):
        self.assertEqual(low_stock_text()[1], 0)
        self.sale(17, credit=False)  # يفضل 3 = الحد
        text, n = low_stock_text()
        self.assertEqual(n, 1)
        self.assertIn("الحد 3", text)
        self.assertTrue(ActivityLog.objects.filter(text__contains="مخزون منخفض").exists())


class ProfitTests(Base):
    def test_profit_by_model_with_discount_and_return(self):
        inv = self.sale(10, credit=False, discount=D("100"))  # إيراد 900، تكلفة 600
        ret = SaleReturn.objects.create(invoice=inv, refund_mode="box", refund_box=self.box)
        SaleReturnItem.objects.create(return_doc=ret, sale_item=inv.items.get(), quantity=2)
        ret.confirm()  # -180 إيراد، -120 تكلفة
        data = profitability(self.today, self.today)
        m = data["models"][0]
        self.assertEqual((m["sold"], m["returned"]), (10, 2))
        self.assertEqual(m["revenue"], D("720.00"))
        self.assertEqual(m["cost"], D("480.00"))
        self.assertEqual(m["profit"], D("240.00"))
        r = self.client.get(reverse("profit_report"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(reverse("profit_report") + "?export=xlsx").status_code, 200)

    def test_analytics_page(self):
        self.sale(2, credit=False)
        r = self.client.get(reverse("analytics"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "أداء البائعين")


class ProductionTests(Base):
    def test_material_purchase_and_production_cost(self):
        fabric = RawMaterial.objects.create(name="قماش", unit="m")
        mp = MaterialPurchase.objects.create(supplier=self.supplier)
        MaterialPurchaseItem.objects.create(purchase=mp, material=fabric, quantity=D("50"),
                                            unit_cost=D("40"))
        mp.confirm()
        self.assertEqual(self.supplier.balance, D("1200") + D("2000"))  # توريد + خامات
        BOMLine.objects.create(model=self.model, material=fabric, qty_per_piece=D("2"))
        po = ProductionOrder.objects.create(model=self.model, labor_cost_per_piece=D("10"),
                                            other_costs=D("100"))
        ProductionOutput.objects.create(order=po, variant=self.v, quantity=10)
        po.fill_from_bom()
        self.assertEqual(po.materials.get().quantity, D("20"))
        self.assertEqual(po.estimate()["unit_cost"], D("100"))  # (800 + 100 + 100) / 10
        po.complete()
        po.refresh_from_db()
        fabric.refresh_from_db()
        self.v.refresh_from_db()
        self.assertEqual(po.unit_cost, D("100.00"))
        self.assertEqual(fabric.quantity, D("30"))
        self.assertEqual(self.v.quantity, 30)
        self.assertEqual(self.v.avg_cost, D("73.33"))  # (20×60 + 10×100) / 30
        with self.assertRaises(ValidationError):
            po.complete()

    def test_not_enough_material_rolls_back(self):
        fabric = RawMaterial.objects.create(name="قماش", unit="m")
        po = ProductionOrder.objects.create(model=self.model)
        ProductionOutput.objects.create(order=po, variant=self.v, quantity=5)
        po.materials.create(material=fabric, quantity=D("3"))
        with self.assertRaises(ValidationError):
            po.complete()
        self.v.refresh_from_db()
        self.assertEqual(self.v.quantity, 20)
        self.assertEqual(ProductionOrder.objects.get().status, "draft")


class BackupAndPwaTests(Base):
    def test_backup_command_rotates(self):
        import sqlite3
        from trade.management.commands.backup_db import make_backup
        with tempfile.TemporaryDirectory() as d:
            # قاعدة منفصلة: قاعدة الاختبار نفسها مقفولة جوه معاملة الاختبار
            src = Path(d) / "src.sqlite3"
            con = sqlite3.connect(src)
            con.execute("create table t (x)")
            con.commit()
            con.close()
            fake = mock.Mock(settings_dict={"ENGINE": "django.db.backends.sqlite3", "NAME": str(src)})
            with mock.patch("trade.management.commands.backup_db.timezone") as tz, \
                    mock.patch("trade.management.commands.backup_db.connection", fake):
                for i in range(3):
                    tz.localtime.return_value = timezone.localtime() + timedelta(seconds=i)
                    make_backup(d, keep=2)
            files = sorted(Path(d).glob("*.tar.gz"))
            self.assertEqual(len(files), 2)
            with tarfile.open(files[-1]) as t:
                self.assertIn("db.sqlite3", t.getnames())

    def test_pwa_endpoints(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.json()["short_name"], "SPIRIT")
        self.assertContains(self.client.get("/sw.js"), "caches")
        self.assertEqual(self.client.get("/offline/").status_code, 200)
        page = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn('rel="manifest"', page.split("</head>")[0])


class MenuTests(Base):
    def test_menu_is_grouped_and_follows_permissions(self):
        page = self.client.get(reverse("customers")).content.decode()
        self.assertIn("erp-nav-header", page)
        self.assertIn("متابعة التحصيل", page)
        self.assertIn('id="erp-bottom"', page)
        self.assertNotIn("<p>Dashboard</p>", page)  # قايمة Jazzmin القديمة اتشالت
        # الرابط المفتوح بس هو اللي متعلّم (مش "العملاء" لما تكون في متابعة التحصيل)
        page = self.client.get(reverse("debts")).content.decode()
        self.assertEqual(page.count('class="nav-link active"'), 1)
        self.assertIn('href="/customers/debts/" class="nav-link active"', page)

        sales = User.objects.create_user("s", password="pw", is_staff=True)
        sales.groups.add(Group.objects.get(name="سيلز"))
        c = Client()
        c.force_login(sales)
        page = c.get(reverse("dashboard")).content.decode()
        self.assertIn("نقطة البيع", page)
        nav = page.split('id="jazzy-navigation"')[1].split("</ul>")[0]
        for hidden in ("التقارير", "الإنتاج", "سجل المراجعة", "متابعة التحصيل"):
            self.assertNotIn(hidden, nav)

    def test_pos_has_no_bottom_bar(self):
        self.assertNotContains(self.client.get(reverse("pos")), 'id="erp-bottom"')
