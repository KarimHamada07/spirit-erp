"""المبيعات الجديدة: نوع الفاتورة والتسليم وطرق السداد (ومنها من الحساب)، حد الائتمان وتغيير التاريخ بموافقة
مدير الحسابات، المرتجع بالعميل والصنف (FIFO على الفواتير)، وكشف الحساب بالإجماليات."""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from trade.models import (CashBox, ClothingModel, Customer, SaleInvoice, SaleReturn, Seller, Variant)
from trade.roles import ROLE_ACCOUNTS_MANAGER, ROLE_SALES
from trade.statements import customer_statement
from trade.tests_hr import login

D = Decimal


class SalesBase(TestCase):
    def setUp(self):
        self.seller = Seller.objects.get(name="خالد فلكس")
        self.box = CashBox.objects.get(name="كاش المحل")
        self.sales_u, self.sales = login("sales", ROLE_SALES)
        self.boss_u, self.boss = login("boss", ROLE_ACCOUNTS_MANAGER)
        m = ClothingModel.objects.create(name="هودي", code="H-1", category="sweatshirt", series_size=3,
                                         series_sizes="L, L, L")
        self.v = Variant.objects.create(model=m, size="L", color="أسود", sale_price=D("500"), wholesale_price=D("400"))
        Variant.objects.filter(pk=self.v.pk).update(quantity=50, avg_cost=D("200"))
        self.cust = Customer.objects.create(name="تاجر", phone="01011111111", customer_type="installments",
                                            credit_limit=D("1000"), seller=self.seller)

    def sell(self, client, qty, payments, channel="retail", price=None, **extra):
        body = {"items": [{"id": self.v.pk, "qty": qty, "price": price or (400 if channel == "wholesale" else 500)}],
                "customer": self.cust.pk, "seller": self.seller.pk, "channel": channel, "delivery": "shipping",
                "notes": "تتسلم الخميس", "payments": payments, **extra}
        return client.post(reverse("pos_checkout"), json.dumps(body), content_type="application/json")

    def balance(self):
        return Customer.objects.with_balance().get(pk=self.cust.pk).balance


class InvoiceTests(SalesBase):
    def test_mixed_payment_and_print_layout(self):
        r = self.sell(self.sales, 2, [{"box": self.box.pk, "amount": 300}, {"box": "account", "amount": 700}])
        self.assertTrue(r.json()["ok"], r.content)
        inv = SaleInvoice.objects.get()
        self.assertEqual((inv.payment_type, inv.account_amount, inv.delivery), ("credit", D("700"), "shipping"))
        self.assertEqual(self.balance(), D("700"))
        page = self.sales.get(reverse("sale_print", args=[inv.pk])).content.decode()
        for text in ("فاتورة قطاعي", "من الحساب", "شركة الشحن", "تتسلم الخميس", "إجمالي القطع", "صافي الفاتورة", "هودي"):
            self.assertIn(text, page)

    def test_payments_must_match_and_cash_customer_cannot_use_account(self):
        r = self.sell(self.sales, 1, [{"box": self.box.pk, "amount": 100}])
        self.assertIn("لازم يساوي صافي الفاتورة", r.json()["error"])
        self.cust.customer_type = "cash"
        self.cust.save()
        r = self.sell(self.sales, 1, [{"box": "account", "amount": 500}])
        self.assertIn("عميل نقدي", r.json()["error"])

    def test_credit_limit_needs_approval(self):
        r = self.sell(self.sales, 3, [{"box": "account", "amount": 1500}])          # الحد 1000
        d = r.json()
        self.assertTrue(d["pending"])
        inv = SaleInvoice.objects.get()
        self.assertEqual((inv.is_confirmed, inv.approval), (False, "pending"))
        self.assertEqual(Variant.objects.get(pk=self.v.pk).quantity, 50)       # لسه ما اتخصمش
        self.assertEqual(self.sales.get(reverse("sale_approvals")).status_code, 403)
        self.assertContains(self.boss.get(reverse("dashboard")), "فواتير بيع مستنية موافقتك")
        self.assertContains(self.boss.get(reverse("sale_approvals")), "حد الائتمان")
        self.boss.post(reverse("sale_approvals"), {"invoice": inv.pk, "decision": "approve"})
        inv.refresh_from_db()
        self.assertEqual((inv.is_confirmed, inv.approval, inv.approved_by), (True, "approved", self.boss_u))
        self.assertEqual(Variant.objects.get(pk=self.v.pk).quantity, 47)
        self.assertEqual(self.balance(), D("1500"))
        # مدير الحسابات نفسه: من غير انتظار
        r = self.sell(self.boss, 1, [{"box": "account", "amount": 500}])
        self.assertFalse(r.json()["pending"])

    def test_date_change_needs_approval_and_reject(self):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self.sell(self.sales, 1, [{"box": self.box.pk, "amount": 500}], date=yesterday)
        self.assertTrue(r.json()["pending"])
        inv = SaleInvoice.objects.get()
        self.assertIn("مش النهارده", inv.approval_reason)
        self.boss.post(reverse("sale_approvals"), {"invoice": inv.pk, "decision": "reject"})
        inv.refresh_from_db()
        self.assertEqual((inv.approval, inv.is_confirmed), ("rejected", False))
        self.assertEqual(self.box.balance, D("0"))
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.sell(self.boss, 1, [{"box": self.box.pk, "amount": 500}], date=tomorrow).status_code, 400)

    def test_channels_and_prices(self):
        self.assertTrue(self.sell(self.sales, 1, [{"box": self.box.pk, "amount": 400}], channel="wholesale").json()["ok"])
        # نفس العميل: آخر سعر ليه (400) بيمشي على أي نوع فاتورة
        self.assertTrue(self.sell(self.sales, 1, [{"box": self.box.pk, "amount": 400}], channel="marketing", price=400).json()["ok"])
        self.assertEqual(sorted(SaleInvoice.objects.values_list("channel", flat=True)), ["marketing", "wholesale"])
        page = self.sales.get(reverse("pos")).content.decode()
        self.assertIn("تسويق", page)
        self.assertIn('"series": ["L", "L", "L"]', page)


class CustomerReturnFifoTests(SalesBase):
    def test_return_by_customer_spans_invoices_fifo(self):
        # فاتورتين آجل لنفس الصنف بسعرين: 450 (القديمة) و 500
        self.sell(self.boss, 2, [{"box": "account", "amount": 900}], price=450)
        self.sell(self.boss, 2, [{"box": "account", "amount": 1000}])
        first, second = SaleInvoice.objects.order_by("id")
        self.assertEqual(self.balance(), D("1900"))
        d = self.sales.get(reverse("pos_return_customer"), {"customer": self.cust.pk}).json()
        item = d["items"][0]
        self.assertEqual((item["returnable"], item["lots"][0]["price"]), (4, 450.0))
        # 3 قطع: 2 من الأولى (450) + 1 من التانية (500) = 1400؛ 1000 من الحساب و 400 كاش
        body = {"customer": self.cust.pk, "variants": [{"variant": self.v.pk, "qty": 3}], "reason": "عيب",
                "delivery": "shipping", "notes": "رجعت مع المندوب",
                "refunds": [{"box": "account", "amount": 1000}, {"box": self.box.pk, "amount": 400}]}
        r = self.sales.post(reverse("pos_return"), json.dumps(body), content_type="application/json")
        self.assertTrue(r.json()["ok"], r.content)
        rets = list(SaleReturn.objects.order_by("id"))
        self.assertEqual([r_.invoice_id for r_ in rets], [first.pk, second.pk])
        self.assertEqual(len({r_.group for r_ in rets}), 1)
        self.assertEqual(sum((r_.total for r_ in rets), D("0")), D("1400"))
        self.assertEqual(self.balance(), D("900"))                       # 1900 − 1000
        self.assertEqual(self.box.balance, D("-400"))
        self.assertEqual(Variant.objects.get(pk=self.v.pk).quantity, 50 - 4 + 3)
        page = self.sales.get(r.json()["print_url"]).content.decode()
        for text in ("عيب", "رجعت مع المندوب", "شركة الشحن", "1,400.00"):
            self.assertIn(text, page)
        # مفيش سبب = مرفوض، وأكتر من المتاح = مرفوض
        body.update(reason="")
        self.assertIn("سبب", self.sales.post(reverse("pos_return"), json.dumps(body), content_type="application/json").json()["error"])
        body.update(reason="x", variants=[{"variant": self.v.pk, "qty": 5}])
        self.assertEqual(self.sales.post(reverse("pos_return"), json.dumps(body), content_type="application/json").status_code, 400)

    def test_return_discount_and_statement_totals(self):
        self.sell(self.boss, 2, [{"box": self.box.pk, "amount": 400}, {"box": "account", "amount": 600}])
        body = {"customer": self.cust.pk, "variants": [{"variant": self.v.pk, "qty": 1}], "reason": "مقاس",
                "discount": 50, "refunds": [{"box": "account", "amount": 450}]}
        self.assertTrue(self.sales.post(reverse("pos_return"), json.dumps(body), content_type="application/json").json()["ok"])
        self.assertEqual(self.balance(), D("150"))                  # 600 − 450
        st = customer_statement(Customer.objects.get(pk=self.cust.pk))
        summary = dict(st["summary"])
        self.assertEqual((summary["إجمالي الفواتير"], summary["إجمالي المرتجعات"], summary["إجمالي الدفعات"]),
                         (D("1000"), D("450"), D("400")))
        self.assertEqual(st["totals"]["balance"], D("150"))


class WholesaleSeriesTests(SalesBase):
    def test_wholesale_invoice_shows_series(self):
        m = ClothingModel.objects.create(name="تيشرت", code="T-7", category="tshirt", series_sizes="M, L, L, XL", series_size=4)
        vs = {}
        for sz in ("M", "L", "XL"):
            v = Variant.objects.create(model=m, size=sz, color="أبيض", sale_price=D("200"), wholesale_price=D("120"))
            Variant.objects.filter(pk=v.pk).update(quantity=30)
            vs[sz] = v
        # 2 سيري (M×2, L×4, XL×2) + قطعة M زيادة
        items = [{"id": vs["M"].pk, "qty": 3, "price": 120}, {"id": vs["L"].pk, "qty": 4, "price": 120},
                 {"id": vs["XL"].pk, "qty": 2, "price": 120}]
        body = {"items": items, "customer": None, "seller": self.seller.pk, "channel": "wholesale",
                "payments": [{"box": self.box.pk, "amount": 1080}]}
        r = self.boss.post(reverse("pos_checkout"), json.dumps(body), content_type="application/json")
        self.assertTrue(r.json()["ok"], r.content)
        page = self.boss.get(r.json()["print_url"]).content.decode()
        self.assertIn("مقاسات السيري", page)
        self.assertIn("<b>2</b> سيري (4 قطع)", page)
        self.assertIn("<b>1</b> قطعة", page)
        self.assertIn("إجمالي السيريهات", page)
        self.assertIn("L×2", page)
        pdf = self.boss.get(reverse("sale_pdf", args=[r.json()["id"]]))
        self.assertEqual(pdf.status_code, 200)


class OnlineSlipsTests(SalesBase):
    def test_three_slips_per_a4_page(self):
        from trade.models import OnlineOrder
        orders = [OnlineOrder.create_order(customer_name=f"عميل {n}", phone=f"0100000000{n}", address="القاهرة - مدينة نصر",
                                           notes="", discount=D("0"), shipping_fee=D("60"), shipping_cost=D("45"),
                                           items=[{"id": self.v.pk, "qty": 1, "price": 500}], seller=self.seller)
                  for n in range(4)]
        orders[3].set_status(OnlineOrder.SHIPPED)
        page = self.sales.get(reverse("online_print")).content.decode()      # المتجهز بس
        self.assertEqual((page.count('class="page"'), page.count('class="slip"')), (1, 3))
        self.assertIn("560.00", page)
        self.assertNotIn("عميل 3", page)
        page = self.sales.get(reverse("online_print"), {"ids": ",".join(str(o.pk) for o in orders)}).content.decode()
        self.assertEqual((page.count('class="page"'), page.count('class="slip"')), (2, 4))
        self.assertContains(self.sales.get(reverse("online_list")), "اطبع بوالص المتجهز (3)")
