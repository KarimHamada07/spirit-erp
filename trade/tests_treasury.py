import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from trade.models import (CashBox, CashEntry, ClothingModel, Customer, OnlineOrder, Payment,
                     PurchaseInvoice, PurchaseItem, PurchaseReturn, PurchaseReturnItem,
                     SaleInvoice, SaleReturn, SaleReturnItem, Seller, Supplier, Variant)
from trade.treasury import (commission_report, create_transfer, daily_close,
                       record_customer_payment, record_supplier_payment)

D = Decimal


class TreasuryFlows(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("boss", "b@x.com", "pw")
        self.client = Client()
        self.client.force_login(self.user)
        self.box = {b.name: b for b in CashBox.objects.all()}
        self.cash_shop = self.box["كاش المحل"]
        self.cash_dali = self.box["كاش دالي"]
        self.insta = self.box["إنستاباي دالي"]
        self.wallet = self.box["محفظة كريم"]
        self.shipping = self.box["شركة الشحن"]
        self.khaled = Seller.objects.get(name="خالد فلكس")
        self.direct = Seller.objects.get(name="مباشر")

        model = ClothingModel.objects.create(name="تيشرت كلاسيك", code="TS-01")
        self.v = Variant.objects.create(model=model, size="M", color="أحمر", sale_price=D("350"))
        sup = Supplier.objects.create(name="مصنع النور")
        self.supplier = sup
        pi = PurchaseInvoice.objects.create(supplier=sup, date=date.today())
        PurchaseItem.objects.create(invoice=pi, variant=self.v, quantity=10, unit_cost=D("200"))
        pi.confirm()
        self.v.refresh_from_db()
        self.customer = Customer.objects.create(name="أحمد")

    def checkout(self, items, payments, credit=False, seller=None, channel="retail",
                 customer=None, discount=0):
        body = {"items": items, "discount": discount, "customer": customer.pk if customer else None,
                "seller": (seller or self.khaled).pk, "channel": channel, "credit": credit,
                "payments": payments}
        return self.client.post(reverse("pos_checkout"), json.dumps(body),
                                content_type="application/json")

    # ---------- بيع كاش مجزأ ----------
    def test_cash_sale_split_payment(self):
        r = self.checkout([{"id": self.v.pk, "qty": 2, "price": "350"}],
                          [{"box": self.cash_dali.pk, "amount": 300},
                           {"box": self.wallet.pk, "amount": 400}])
        self.assertEqual(r.status_code, 200, r.content)
        inv = SaleInvoice.objects.get(pk=r.json()["id"])
        self.assertEqual(inv.total, D("700"))
        self.assertEqual(self.cash_dali.balance, D("300"))
        self.assertEqual(self.wallet.balance, D("400"))
        self.assertEqual(inv.seller, self.khaled)
        self.v.refresh_from_db()
        self.assertEqual(self.v.quantity, 8)

    def test_cash_sale_must_match_total(self):
        r = self.checkout([{"id": self.v.pk, "qty": 1, "price": "350"}],
                          [{"box": self.cash_shop.pk, "amount": 100}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(SaleInvoice.objects.count(), 0)
        self.v.refresh_from_db()
        self.assertEqual(self.v.quantity, 10)

    def test_seller_required(self):
        body = {"items": [{"id": self.v.pk, "qty": 1, "price": "350"}], "seller": None,
                "payments": [{"box": self.cash_shop.pk, "amount": 350}], "credit": False}
        r = self.client.post(reverse("pos_checkout"), json.dumps(body), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    # ---------- آجل مع مقدم ----------
    def test_credit_sale_with_prepayment(self):
        r = self.checkout([{"id": self.v.pk, "qty": 2, "price": "350"}],
                          [{"box": self.insta.pk, "amount": 200}],
                          credit=True, customer=self.customer, seller=self.khaled)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.insta.balance, D("200"))
        c = Customer.objects.with_balance().get(pk=self.customer.pk)
        self.assertEqual(c.balance, D("500"))
        self.assertEqual(Payment.objects.get(customer=self.customer).method, "instapay")

    # ---------- تحصيل من عميل وكشف ----------
    def test_customer_payment_drops_balance(self):
        self.checkout([{"id": self.v.pk, "qty": 2, "price": "350"}], [],
                      credit=True, customer=self.customer)
        record_customer_payment(self.customer, self.insta, D("300"))
        c = Customer.objects.with_balance().get(pk=self.customer.pk)
        self.assertEqual(c.balance, D("400"))
        self.assertEqual(self.insta.balance, D("300"))

    def test_customer_pay_cannot_exceed_balance(self):
        self.checkout([{"id": self.v.pk, "qty": 1, "price": "350"}], [],
                      credit=True, customer=self.customer)
        r = self.client.post(reverse("treasury_action"), {
            "action": "customer_pay", "customer": self.customer.pk, "box": self.wallet.pk,
            "amount": "999", "date": date.today().isoformat()}, follow=True)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertContains(r, "أكبر من المتبقي")

    # ---------- سداد مورد ----------
    def test_supplier_payment_reduces_payable(self):
        self.assertEqual(self.supplier.balance, D("2000"))
        record_supplier_payment(self.supplier, self.cash_shop, D("1500"))
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.balance, D("500"))
        self.assertEqual(self.cash_shop.balance, D("-1500"))

    def test_supplier_advance_and_refund(self):
        """دفعنا أكتر من المستحق (تحت الحساب) فرصيده بقى سالب = لينا عنده، وبعدين رجّعلنا جزء"""
        from trade.statements import supplier_statement
        from trade.treasury import record_supplier_refund
        record_supplier_payment(self.supplier, self.cash_shop, D("2600"))
        self.assertEqual(self.supplier.balance, D("-600"))
        self.assertEqual(self.supplier.balance_label, "لينا عنده")
        with self.assertRaises(ValidationError):
            record_supplier_refund(self.supplier, self.cash_shop, D("601"))
        record_supplier_refund(self.supplier, self.cash_shop, D("250"))
        self.assertEqual(self.supplier.balance, D("-350"))
        self.assertEqual(self.cash_shop.balance, D("-2350"))
        st = supplier_statement(self.supplier)
        self.assertEqual(st["totals"]["balance"], D("-350"))
        self.assertIn("لينا عنده", st["balance_label"])
        # الصفحة والخزينة
        page = self.client.get(reverse("suppliers")).content.decode()
        self.assertIn("لينا عندهم", page)
        self.assertIn("350.00", page)
        r = self.client.post(reverse("treasury_action"), {
            "action": "supplier_pay", "kind": "refund", "supplier": self.supplier.pk, "box": self.cash_shop.pk,
            "amount": "100", "date": date.today().isoformat()}, follow=True)
        self.assertContains(r, "لينا عنده 250.00")

    def test_supplier_negative_opening_balance(self):
        from trade.statements import supplier_statement
        self.supplier.opening_balance = D("-3000")
        self.supplier.save()
        self.assertEqual(self.supplier.balance, D("-1000"))
        first = supplier_statement(self.supplier)["entries"][0]
        self.assertEqual((first["debit"], first["credit"]), (D("0"), D("3000")))

    # ---------- مرتجع مورد ----------
    def test_purchase_return_reduces_stock_and_payable(self):
        pr = PurchaseReturn.objects.create(supplier=self.supplier, reason="عيب")
        PurchaseReturnItem.objects.create(return_doc=pr, variant=self.v, quantity=3, unit_cost=D("200"))
        pr.confirm()
        self.v.refresh_from_db()
        self.assertEqual(self.v.quantity, 7)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.balance, D("1400"))
        with self.assertRaises(ValidationError):
            pr2 = PurchaseReturn.objects.create(supplier=self.supplier)
            PurchaseReturnItem.objects.create(return_doc=pr2, variant=self.v, quantity=99, unit_cost=D("1"))
            pr2.confirm()

    # ---------- مرتجع عميل ----------
    def test_sale_return_cash_refund_restocks(self):
        r = self.checkout([{"id": self.v.pk, "qty": 2, "price": "350"}],
                          [{"box": self.cash_shop.pk, "amount": 700}])
        inv = SaleInvoice.objects.get(pk=r.json()["id"])
        si = inv.items.get()
        ret = SaleReturn.objects.create(invoice=inv, refund_mode="box", refund_box=self.cash_shop)
        SaleReturnItem.objects.create(return_doc=ret, sale_item=si, quantity=1)
        ret.confirm()
        self.v.refresh_from_db()
        self.assertEqual(self.v.quantity, 9)
        self.assertEqual(self.cash_shop.balance, D("700") - D("350"))
        # مش ممكن يرجّع أكتر من المباع
        ret2 = SaleReturn.objects.create(invoice=inv, refund_mode="box", refund_box=self.cash_shop)
        SaleReturnItem.objects.create(return_doc=ret2, sale_item=si, quantity=2)
        with self.assertRaises(ValidationError):
            ret2.confirm()

    def test_sale_return_account_reduces_debt(self):
        self.checkout([{"id": self.v.pk, "qty": 2, "price": "350"}], [],
                      credit=True, customer=self.customer)
        inv = SaleInvoice.objects.get(customer=self.customer)
        ret = SaleReturn.objects.create(invoice=inv, refund_mode="account")
        SaleReturnItem.objects.create(return_doc=ret, sale_item=inv.items.get(), quantity=1)
        ret.confirm()
        c = Customer.objects.with_balance().get(pk=self.customer.pk)
        self.assertEqual(c.balance, D("350"))

    def test_account_refund_needs_credit_invoice(self):
        r = self.checkout([{"id": self.v.pk, "qty": 1, "price": "350"}],
                          [{"box": self.cash_shop.pk, "amount": 350}])
        inv = SaleInvoice.objects.get(pk=r.json()["id"])
        ret = SaleReturn.objects.create(invoice=inv, refund_mode="account")
        SaleReturnItem.objects.create(return_doc=ret, sale_item=inv.items.get(), quantity=1)
        with self.assertRaises(ValidationError):
            ret.confirm()

    # ---------- تحويلات وشركة الشحن ----------
    def test_transfer_between_boxes(self):
        self.checkout([{"id": self.v.pk, "qty": 1, "price": "350"}],
                      [{"box": self.cash_dali.pk, "amount": 350}])
        create_transfer(self.cash_dali, self.cash_shop, D("350"), note="نقل عهدة")
        self.assertEqual(self.cash_dali.balance, D("0"))
        self.assertEqual(self.cash_shop.balance, D("350"))

    def test_online_collected_via_shipping_then_received(self):
        order = OnlineOrder.create_order(customer_name="ياسر", phone="0100",
                                         items=[{"id": self.v.pk, "qty": 1, "price": "350"}],
                                         shipping_fee=D("50"), seller=self.khaled)
        order.set_status("shipped")
        order.set_status("collected", box=self.shipping)
        self.assertEqual(self.shipping.balance, D("400"))
        create_transfer(self.shipping, self.cash_shop, D("400"), note="استلام من الشحن")
        self.assertEqual(self.shipping.balance, D("0"))
        self.assertEqual(self.cash_shop.balance, D("400"))

    def test_online_collect_requires_box(self):
        order = OnlineOrder.create_order(customer_name="ياسر", phone="0100",
                                         items=[{"id": self.v.pk, "qty": 1, "price": "350"}],
                                         seller=self.khaled)
        order.set_status("shipped")
        with self.assertRaises(ValidationError):
            order.set_status("collected")

    def test_online_return_after_collect_reverses_money(self):
        order = OnlineOrder.create_order(customer_name="ياسر", phone="0100",
                                         items=[{"id": self.v.pk, "qty": 1, "price": "350"}],
                                         seller=self.khaled)
        order.set_status("shipped")
        order.set_status("collected", box=self.insta)
        order.set_status("returned")
        self.assertEqual(self.insta.balance, D("0"))

    # ---------- تقفيل اليوم والعمولة ----------
    def test_daily_close_by_method_and_holder(self):
        self.checkout([{"id": self.v.pk, "qty": 2, "price": "350"}],
                      [{"box": self.cash_dali.pk, "amount": 200},
                       {"box": self.insta.pk, "amount": 300},
                       {"box": self.wallet.pk, "amount": 200}])
        rep = daily_close(date.today())
        methods = dict(rep["by_method"])
        self.assertEqual(methods["كاش"], D("200"))
        self.assertEqual(methods["إنستاباي"], D("300"))
        self.assertEqual(methods["محفظة"], D("200"))
        self.assertEqual(rep["net"], D("700"))

    def test_commission_rates_and_direct_seller(self):
        from trade.models import CommissionRate
        CommissionRate.objects.filter(channel="retail").update(rate=D("10"))
        self.checkout([{"id": self.v.pk, "qty": 2, "price": "350"}],
                      [{"box": self.cash_shop.pk, "amount": 700}])
        self.checkout([{"id": self.v.pk, "qty": 1, "price": "350"}],
                      [{"box": self.cash_shop.pk, "amount": 350}], seller=self.direct)
        rep = commission_report(date.today().year, date.today().month)
        rows = {r["seller"]: r for r in rep["rows"]}
        self.assertEqual(rows["خالد فلكس"]["commission"], D("70"))
        self.assertEqual(rows["مباشر"]["commission"], D("0"))
        self.assertEqual(rep["grand_commission"], D("70"))

    # ---------- العرض والـ PDF ----------
    def test_invoice_print_shows_new_fields(self):
        r = self.checkout([{"id": self.v.pk, "qty": 2, "price": "350"}],
                          [{"box": self.cash_dali.pk, "amount": 500},
                           {"box": self.insta.pk, "amount": 200}], discount=0)
        r = self.client.get(reverse("sale_print", args=[r.json()["id"]]))
        html = r.content.decode()
        self.assertIn("SPIRIT", html)
        self.assertNotIn("سبيريت", html)
        self.assertIn("TS-01", html)
        self.assertIn("خالد فلكس", html)
        self.assertIn("عدد القطع", html)
        self.assertIn("كاش دالي", html)
        self.assertIn("إنستاباي دالي", html)

    def test_invoice_pdf_downloads(self):
        r = self.checkout([{"id": self.v.pk, "qty": 1, "price": "350"}],
                          [{"box": self.cash_shop.pk, "amount": 350}])
        r = self.client.get(reverse("sale_pdf", args=[r.json()["id"]]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_treasury_pages_render(self):
        for name in ["treasury", "daily_close", "commissions", "pos", "customers"]:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
        self.assertContains(self.client.get(reverse("pos")), "إنستاباي دالي")

    def test_online_requires_seller(self):
        with self.assertRaises(ValidationError):
            OnlineOrder.create_order(customer_name="ياسر", phone="0100",
                                     items=[{"id": self.v.pk, "qty": 1, "price": "350"}])

    def test_online_pages_render(self):
        order = OnlineOrder.create_order(customer_name="ياسر", phone="0100",
                                         items=[{"id": self.v.pk, "qty": 1, "price": "350"}],
                                         seller=self.khaled)
        self.assertEqual(self.client.get(reverse("online_new")).status_code, 200)
        order.set_status("shipped")
        r = self.client.get(reverse("online_detail", args=[order.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "اختار الخزنة")
