"""الأمان: الموظف من غير صلاحيات ما يشوفش بيانات العملاء والمخزون والأونلاين، وقفل الدخول بعد المحاولات الغلط."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from trade.models import ClothingModel, Customer, SaleInvoice, Variant
from trade.roles import ROLE_SALES
from trade.tests_hr import login


class PlainEmployeeTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("plain", password="pw-12345!", is_staff=True)
        self.client.force_login(self.u)
        self.m = ClothingModel.objects.create(name="سري", code="S-1")
        Variant.objects.create(model=self.m, size="L")
        self.c = Customer.objects.create(name="عميل سري", phone="01011111111")
        self.inv = SaleInvoice.objects.create(date="2026-11-18", customer=self.c)

    def test_no_data_without_permission(self):
        for url in (reverse("customers"), reverse("customer_detail", args=[self.c.pk]), reverse("inventory"),
                    reverse("inventory_detail", args=[self.m.pk]), reverse("online_list"), reverse("online_new"),
                    reverse("sale_print", args=[self.inv.pk])):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        r = self.client.get(reverse("search_api"), {"q": "سري"}).json()
        self.assertEqual(r["groups"], [])
        self.assertEqual(self.client.get(reverse("scan_open"), {"c": "S-1"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("my_attendance")).status_code, 200)       # بصمته عادي

    def test_sales_still_works(self):
        _u, sales = login("sales", ROLE_SALES)
        for url in (reverse("customers"), reverse("inventory"), reverse("online_list"), reverse("sale_print", args=[self.inv.pk])):
            self.assertEqual(sales.get(url).status_code, 200, url)
        self.assertTrue(sales.get(reverse("search_api"), {"q": "سري"}).json()["groups"])

    def test_back_redirect_stays_on_site(self):
        r = self.client.get(reverse("scan_open"), {"c": "nothing"}, HTTP_REFERER="https://evil.example/x")
        self.assertEqual(r["Location"], reverse("dashboard"))


class LoginLockoutTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user("boss2", password="right-pass-1", is_staff=True)

    def attempt(self, pw):
        return self.client.post("/admin/login/", {"username": "boss2", "password": pw}, REMOTE_ADDR="10.0.0.5")

    def test_locks_after_five_wrong(self):
        for _ in range(5):
            self.assertEqual(self.attempt("wrong").status_code, 200)
        r = self.attempt("right-pass-1")                      # حتى الباسورد الصح مقفول
        self.assertContains(r, "اتقفل 15 دقيقة")
        cache.clear()
        self.assertEqual(self.attempt("right-pass-1").status_code, 302)


class SupplierMenuTests(TestCase):
    def test_supplier_group_statement_and_filter(self):
        from trade.models import PurchaseInvoice, Supplier
        from trade.roles import ROLE_ACCOUNTS_MANAGER
        _u, boss = login("boss", ROLE_ACCOUNTS_MANAGER)
        s = Supplier.objects.create(name="مصنع التجربة", phone="01000000001")
        PurchaseInvoice.objects.create(supplier=s, date="2026-11-01")
        page = boss.get(reverse("dashboard")).content.decode()
        nav = page.split('id="jazzy-navigation"')[1]
        # جروب الموردين تحت جروب العملاء وفيه الأربع صفحات
        self.assertLess(nav.index("كشف حساب عميل"), nav.index("مرتجع مورد"))
        for name in ("فواتير التوريد", "مرتجع مورد", "كشف حساب مورد"):
            self.assertIn(name, nav)
        r = boss.get(reverse("supplier_statement_page"), {"supplier": s.pk})
        self.assertContains(r, "كشف حساب مورد: مصنع التجربة")
        self.assertContains(r, f"/suppliers/{s.pk}/statement/pdf/")
        r = boss.get(reverse("admin:trade_purchaseinvoice_changelist"), {"status": "pending"})
        self.assertContains(r, "مستنية تأكيد الاستلام")
        self.assertContains(r, "مصنع التجربة")
