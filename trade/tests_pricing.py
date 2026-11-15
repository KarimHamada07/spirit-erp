import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from trade.models import CashBox, ClothingModel, Customer, Seller, Variant
from trade.pos_views import customer_last_prices
from trade.roles import ROLE_ACCOUNTS_MANAGER, ROLE_SALES

D = Decimal


class RepeatCustomerPrices(TestCase):
    def setUp(self):
        self.seller = Seller.objects.get(name="خالد فلكس")
        self.box = CashBox.objects.get(name="كاش المحل")
        self.customer = Customer.objects.create(name="أحمد", seller=self.seller)

        self.sales = User.objects.create_user("sales2", password="pw", is_staff=True)
        self.sales.groups.add(Group.objects.get(name=ROLE_SALES))
        self.acct = User.objects.create_user("acct2", password="pw", is_staff=True)
        self.acct.groups.add(Group.objects.get(name=ROLE_ACCOUNTS_MANAGER))

        model = ClothingModel.objects.create(name="بنطلون", code="PT-1")
        self.v = Variant.objects.create(model=model, size="L", color="أسود",
                                        sale_price=D("200"), quantity=50, avg_cost=D("80"))

    def checkout(self, user, price, qty=1, channel="retail", customer=True):
        self.client.force_login(user)
        body = {"items": [{"id": self.v.pk, "qty": qty, "price": price}],
                "discount": 0, "customer": self.customer.pk if customer else None, "seller": self.seller.pk,
                "channel": channel, "credit": False,
                "payments": [{"box": self.box.pk, "amount": price * qty}]}
        return self.client.post(reverse("pos_checkout"), json.dumps(body),
                                content_type="application/json")

    def test_first_invoice_sets_the_price(self):
        self.assertEqual(self.checkout(self.acct, 180).status_code, 200)
        self.assertEqual(customer_last_prices(self.customer)[self.v.pk], D("180"))

    def test_second_invoice_must_keep_previous_price(self):
        self.checkout(self.acct, 180)
        r = self.checkout(self.sales, 200)
        self.assertEqual(r.status_code, 400)
        self.assertIn("التعديل من مدير الحسابات", r.json()["error"])
        self.assertEqual(self.v.__class__.objects.get(pk=self.v.pk).quantity, 49)

    def test_second_invoice_same_price_is_accepted(self):
        self.checkout(self.acct, 180)
        self.assertEqual(self.checkout(self.sales, 180).status_code, 200)

    def test_accounts_manager_can_change_price(self):
        self.checkout(self.sales, 180)
        self.assertEqual(self.checkout(self.acct, 150).status_code, 200)
        self.assertEqual(customer_last_prices(self.customer)[self.v.pk], D("150"))

    def test_only_accounts_manager_changes_list_price(self):
        r = self.checkout(self.sales, 180)   # السعر 200: البياع ميقدرش يغيّره
        self.assertEqual(r.status_code, 400)
        self.assertIn("لمدير الحسابات", r.json()["error"])
        self.assertEqual(self.checkout(self.sales, 200).status_code, 200)

    def test_wholesale_price_by_channel(self):
        self.v.wholesale_price = D("150")
        self.v.save()
        self.assertEqual(self.checkout(self.sales, 150, channel="wholesale", customer=False).status_code, 200)
        self.assertEqual(self.checkout(self.sales, 200, channel="wholesale", customer=False).status_code, 400)
        self.assertEqual(self.checkout(self.sales, 200, channel="retail", customer=False).status_code, 200)
        self.assertEqual(self.checkout(self.acct, 140, channel="wholesale", customer=False).status_code, 200)

    def test_last_price_follows_latest_invoice(self):
        self.checkout(self.acct, 180)
        self.checkout(self.acct, 170)
        self.assertEqual(customer_last_prices(self.customer)[self.v.pk], D("170"))

    def test_prices_endpoint_reports_permission(self):
        self.checkout(self.acct, 180)
        self.client.force_login(self.sales)
        d = self.client.get(reverse("pos_customer_prices"), {"customer": self.customer.pk}).json()
        self.assertEqual(d["prices"], {str(self.v.pk): 180.0})
        self.assertFalse(d["can_change"])
        self.client.force_login(self.acct)
        d = self.client.get(reverse("pos_customer_prices"), {"customer": self.customer.pk}).json()
        self.assertTrue(d["can_change"])

    def test_prices_endpoint_without_customer_is_empty(self):
        self.client.force_login(self.sales)
        d = self.client.get(reverse("pos_customer_prices"), {"customer": ""}).json()
        self.assertEqual(d["prices"], {})
