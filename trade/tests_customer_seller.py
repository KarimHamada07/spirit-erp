import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from trade.models import (ClothingModel, Customer, CashBox, SaleInvoice, Seller, Variant)
from trade.roles import ROLE_ACCOUNTS_MANAGER, ROLE_SALES

D = Decimal


class CustomerSellerRules(TestCase):
    def setUp(self):
        self.khaled = Seller.objects.get(name="خالد فلكس")
        self.amr = Seller.objects.get(name="عمرو عصام")
        self.cash_shop = CashBox.objects.get(name="كاش المحل")

        self.sales = User.objects.create_user("sales1", password="pw", is_staff=True)
        self.sales.groups.add(Group.objects.get(name=ROLE_SALES))
        self.acct = User.objects.create_user("acct1", password="pw", is_staff=True)
        self.acct.groups.add(Group.objects.get(name=ROLE_ACCOUNTS_MANAGER))

        model = ClothingModel.objects.create(name="تيشرت", code="TS-9")
        self.v = Variant.objects.create(model=model, size="M", color="أحمر",
                                        sale_price=D("100"), quantity=5, avg_cost=D("50"))

    def test_customer_creation_requires_seller(self):
        c = Client()
        c.force_login(self.sales)
        c.post(reverse("customer_add"), {"name": "ياسر", "phone": "01"})
        self.assertFalse(Customer.objects.filter(name="ياسر").exists())

    def test_customer_created_with_seller(self):
        c = Client()
        c.force_login(self.sales)
        c.post(reverse("customer_add"), {"name": "ياسر", "phone": "01", "seller": self.khaled.pk})
        cust = Customer.objects.get(name="ياسر")
        self.assertEqual(cust.seller, self.khaled)

    def test_only_accounts_manager_changes_seller(self):
        cust = Customer.objects.create(name="ياسر", seller=self.khaled)
        c = Client()
        c.force_login(self.sales)
        r = c.post(reverse("customer_set_seller", args=[cust.pk]), {"seller": self.amr.pk})
        self.assertEqual(r.status_code, 403)
        cust.refresh_from_db()
        self.assertEqual(cust.seller, self.khaled)

        c.force_login(self.acct)
        c.post(reverse("customer_set_seller", args=[cust.pk]), {"seller": self.amr.pk})
        cust.refresh_from_db()
        self.assertEqual(cust.seller, self.amr)

    def test_pos_uses_customer_seller(self):
        cust = Customer.objects.create(name="ياسر", seller=self.amr)
        c = Client()
        c.force_login(self.sales)
        body = {"items": [{"id": self.v.pk, "qty": 1, "price": "100"}],
                "customer": cust.pk, "seller": self.khaled.pk, "channel": "retail",
                "credit": False, "payments": [{"box": self.cash_shop.pk, "amount": 100}]}
        r = c.post(reverse("pos_checkout"), json.dumps(body), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(SaleInvoice.objects.get(pk=r.json()["id"]).seller, self.amr)

    def test_customer_detail_shows_seller_change_only_for_accounts(self):
        cust = Customer.objects.create(name="ياسر", seller=self.khaled)
        c = Client()
        c.force_login(self.sales)
        self.assertNotContains(c.get(reverse("customer_detail", args=[cust.pk])), "غيّر البائع")
        c.force_login(self.acct)
        self.assertContains(c.get(reverse("customer_detail", args=[cust.pk])), "غيّر البائع")
