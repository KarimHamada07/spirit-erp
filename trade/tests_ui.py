"""لوحة التحكم حسب الدور، الأرقام المستنية في القايمة، الفورم السريعة، وصفحة العميل."""
from datetime import date
from decimal import Decimal

from django.core import signing
from django.urls import reverse

from django.test import TestCase

from trade.customer_views import STATEMENT_SALT
from trade.models import (CashBox, CashEntry, ClothingModel, Customer, Expense, LeaveRequest, Payment,
                          SaleInvoice, SaleItem, Seller, Variant)
from trade.roles import ROLE_ACCOUNTS_MANAGER, ROLE_SALES, ROLE_WAREHOUSE_EMPLOYEE
from trade.tests_hr import login

D = Decimal


def credit_sale(customer, variant, price, qty=1):
    inv = SaleInvoice.objects.create(customer=customer, payment_type="credit", seller=customer.seller)
    SaleItem.objects.create(invoice=inv, variant=variant, quantity=qty, unit_price=price)
    inv.confirm()
    return inv


class DashboardByRole(TestCase):
    def setUp(self):
        self.seller = Seller.objects.get(name="خالد فلكس")
        self.customer = Customer.objects.create(name="تاجر", phone="01001234567", seller=self.seller)
        m = ClothingModel.objects.create(name="قميص", code="SH-1")
        self.v = Variant.objects.create(model=m, size="L", color="أبيض", sale_price=D("300"),
                                        quantity=20, avg_cost=D("100"))

    def test_sales_role_does_not_see_profit(self):
        _u, c = login("sales", ROLE_SALES)
        page = c.get(reverse("dashboard")).content.decode()
        self.assertIn("مبيعات اليوم", page)
        self.assertNotIn("صافي ربح الشهر", page)
        self.assertNotIn("قيمة المخزون", page)

    def test_accounts_manager_sees_everything(self):
        _u, c = login("boss", ROLE_ACCOUNTS_MANAGER)
        page = c.get(reverse("dashboard")).content.decode()
        for text in ("صافي ربح الشهر", "قيمة المخزون", "مديونيات العملاء", "المخزون بالنوع والمقاسات"):
            self.assertIn(text, page)

    def test_warehouse_employee_gets_stock_dashboard(self):
        _u, c = login("store", ROLE_WAREHOUSE_EMPLOYEE)
        r = c.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)
        page = r.content.decode()
        self.assertIn("تنبيهات المخزون", page)
        self.assertNotIn("مبيعات اليوم", page)

    def test_linked_seller_sees_own_work_and_is_preselected_in_pos(self):
        u, c = login("khaled", ROLE_SALES)
        self.seller.user = u
        self.seller.save()
        credit_sale(self.customer, self.v, D("300"), qty=2)
        page = c.get(reverse("dashboard")).content.decode()
        self.assertIn("عمولتك الشهر ده", page)
        self.assertIn("600.00", page)
        pos = c.get(reverse("pos")).content.decode()
        self.assertIn(f'<option value="{self.seller.pk}" selected>', pos)


class PendingBadges(TestCase):
    def test_pending_leave_shows_for_approver_only(self):
        emp, _c = login("emp")
        LeaveRequest.objects.create(user=emp, start_date=date(2026, 12, 1), end_date=date(2026, 12, 1))
        _m, mgr = login("boss", ROLE_ACCOUNTS_MANAGER)
        page = mgr.get(reverse("dashboard")).content.decode()
        self.assertIn("طلبات إجازة مستنية موافقتك", page)
        self.assertIn('class="erp-count"', page)
        _s, sales = login("sales", ROLE_SALES)
        self.assertNotIn("طلبات إجازة مستنية موافقتك", sales.get(reverse("dashboard")).content.decode())

    def test_rare_pages_go_under_more(self):
        _m, mgr = login("boss", ROLE_ACCOUNTS_MANAGER)
        page = mgr.get(reverse("dashboard")).content.decode()
        self.assertIn("صفحات أكتر", page)
        more = page[page.index("erp-more-list"):]
        self.assertIn("حركات الخزن", more)
        self.assertIn('id="erp-fab"', page)


class QuickForms(TestCase):
    def setUp(self):
        self.seller = Seller.objects.get(name="خالد فلكس")
        self.customer = Customer.objects.create(name="تاجر", seller=self.seller)
        m = ClothingModel.objects.create(name="قميص", code="SH-1")
        self.v = Variant.objects.create(model=m, size="L", color="أبيض", sale_price=D("300"),
                                        quantity=20, avg_cost=D("100"))
        credit_sale(self.customer, self.v, D("300"))
        self.box = CashBox.objects.get(name="كاش المحل")

    def test_quick_payment_records_and_validates(self):
        _u, c = login("sales", ROLE_SALES)
        page = c.get(reverse("quick_payment")).content.decode()
        self.assertIn("تاجر", page)
        r = c.post(reverse("quick_payment"), {"customer": self.customer.pk, "amount": "500", "box": self.box.pk})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Payment.objects.exists())   # أكبر من المتبقي
        r = c.post(reverse("quick_payment"), {"customer": self.customer.pk, "amount": "120", "box": self.box.pk})
        self.assertRedirects(r, reverse("quick_payment"))
        self.assertEqual(Payment.objects.get().amount, D("120"))
        self.assertTrue(CashEntry.objects.filter(box=self.box, amount=D("120")).exists())
        self.assertEqual(Customer.objects.with_balance().get(pk=self.customer.pk).balance, D("180"))

    def test_quick_expense(self):
        _u, store = login("store", ROLE_WAREHOUSE_EMPLOYEE)
        self.assertEqual(store.get(reverse("quick_expense")).status_code, 403)
        _m, mgr = login("boss", ROLE_ACCOUNTS_MANAGER)
        r = mgr.post(reverse("quick_expense"), {"title": "كهربا", "amount": "350.5", "date": "2026-11-10"})
        self.assertRedirects(r, reverse("quick_expense"))
        e = Expense.objects.get()
        self.assertEqual((e.title, e.amount, e.date), ("كهربا", D("350.5"), date(2026, 11, 10)))
        self.assertIn("كهربا", mgr.get(reverse("quick_expense")).content.decode())   # اقتراح المرة الجاية
        mgr.post(reverse("quick_expense"), {"title": "", "amount": "10"})
        self.assertEqual(Expense.objects.count(), 1)


class CustomerPage(TestCase):
    def setUp(self):
        seller = Seller.objects.get(name="خالد فلكس")
        self.customer = Customer.objects.create(name="تاجر", phone="01001234567", seller=seller)
        m = ClothingModel.objects.create(name="قميص", code="SH-1")
        self.v = Variant.objects.create(model=m, size="L", color="أبيض", sale_price=D("300"),
                                        quantity=20, avg_cost=D("100"))
        credit_sale(self.customer, self.v, D("275"))

    def test_page_shows_fixed_prices_and_whatsapp(self):
        _m, mgr = login("boss", ROLE_ACCOUNTS_MANAGER)
        page = mgr.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        self.assertIn("أسعاره الثابتة", page)
        self.assertIn("275.00", page)
        self.assertIn("https://wa.me/201001234567?text=", page)
        self.assertIn(f"{reverse('pos')}?customer={self.customer.pk}", page)

    def test_public_statement_link(self):
        token = signing.dumps(self.customer.pk, salt=STATEMENT_SALT, compress=True)
        r = self.client.get(reverse("statement_public", args=[token]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(self.client.get(reverse("statement_public", args=["bad"])).status_code, 404)
