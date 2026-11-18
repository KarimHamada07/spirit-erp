"""مجموعة التكويد: عميل، مورد، صنف (السيري والألوان)، وأسعار البيع من فاتورة التوريد."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from trade.models import ClothingModel, Customer, PurchaseInvoice, PurchaseItem, Supplier, Variant
from trade.roles import ROLE_ACCOUNTS_MANAGER, ROLE_SALES, ROLE_WAREHOUSE_MANAGER
from trade.tests_hr import login

D = Decimal


class CodingTests(TestCase):
    def test_customer_coding(self):
        _u, c = login("sales", ROLE_SALES)
        url = reverse("code_customer")
        r = c.post(url, {"name": "", "phones": [""]})
        self.assertContains(r, "اسم العميل مطلوب")
        self.assertContains(r, "رقم موبايل واحد على الأقل")
        r = c.post(url, {"name": "أحمد", "shop_name": "محل النور", "phones": ["0100 123 4567", "01112223334", ""],
                         "governorate": "القاهرة", "area": "المعادي", "customer_type": "installments",
                         "credit_limit": "5000", "notes": "عميل جملة"})
        cu = Customer.objects.get()
        self.assertRedirects(r, reverse("customer_detail", args=[cu.pk]))
        self.assertEqual((cu.phone, cu.other_phones, cu.customer_type, cu.credit_limit, cu.place),
                         ("01001234567", "01112223334", "installments", D("5000"), "القاهرة - المعادي"))
        r = c.post(url, {"name": "تاني", "phones": ["01001234567"], "governorate": "الجيزة", "area": "الهرم",
                         "customer_type": "cash"})
        self.assertContains(r, "متسجل للعميل أحمد")
        page = c.get(reverse("customer_detail", args=[cu.pk])).content.decode()
        self.assertIn("محل النور", page)
        self.assertIn("دفعات", page)

    def test_supplier_coding(self):
        _u, c = login("boss", ROLE_ACCOUNTS_MANAGER)
        r = c.post(reverse("code_supplier"), {"name": "مصنع النور", "phones": ["01000000000", "01222222222"],
                                              "supplier_type": "installments", "categories": ["tshirt", "pants", "other"],
                                              "categories_other": "إكسسوارات", "opening_balance": "-2000"})
        self.assertRedirects(r, reverse("suppliers"))
        s = Supplier.objects.get()
        self.assertEqual(s.phones, ["01000000000", "01222222222"])
        self.assertEqual(s.categories_display, "تيشيرت، بنطلون، إكسسوارات")
        self.assertEqual(s.balance, D("-2000"))
        r = c.post(reverse("code_supplier"), {"name": "x", "phones": ["01000000001"], "supplier_type": "cash"})
        self.assertContains(r, "اختار تصنيف واحد على الأقل")

    def test_item_coding_series_and_colors(self):
        _u, c = login("wm", ROLE_WAREHOUSE_MANAGER)
        r = c.post(reverse("code_item"), {"code": "TS-50", "name": "تيشرت بيسك", "category": "tshirt",
                                          "sizes": "m, L, XL, XXL, XXXL", "colors": "أبيض، أحمر، أسود، أزرق"})
        m = ClothingModel.objects.get(code="TS-50")
        self.assertRedirects(r, reverse("code_item_edit", args=[m.pk]))
        self.assertEqual((m.series_size, m.series_list), (5, ["M", "L", "XL", "XXL", "XXXL"]))
        self.assertEqual(m.variants.count(), 20)
        self.assertTrue(m.variants.filter(size="M", color="أحمر").exists())
        # تعديل: لون جديد بيتعمل بكل المقاسات، والقديم ما بيتكررش
        c.post(reverse("code_item_edit", args=[m.pk]), {"code": "TS-50", "name": "تيشرت بيسك", "category": "tshirt",
                                                         "sizes": "M, L, XL, XXL, XXXL", "colors": "أبيض، أحمر، أسود، أزرق، كحلي"})
        self.assertEqual(m.variants.count(), 25)
        # سيري الجملة: مقاس متكرر، ومقاس مش من مقاسات الصنف مرفوض
        c.post(reverse("code_item_edit", args=[m.pk]), {"code": "TS-50", "name": "تيشرت بيسك", "category": "tshirt",
                                                         "sizes": "M, L, XL, XXL, XXXL", "colors": "أبيض",
                                                         "series": "M, L, L, XL, XL, XXL"})
        m.refresh_from_db()
        self.assertEqual(m.series_size, 6)
        r = c.post(reverse("code_item_edit", args=[m.pk]), {"code": "TS-50", "name": "x", "category": "tshirt",
                                                             "sizes": "M, L", "colors": "أبيض", "series": "M, S"})
        self.assertContains(r, "مش من مقاسات الصنف")
        r = c.post(reverse("code_item"), {"code": "ts-50", "name": "x", "category": "tshirt", "sizes": "M", "colors": "أسود"})
        self.assertContains(r, "متسجل لصنف تاني")
        _s, sales = login("sales", ROLE_SALES)
        self.assertEqual(sales.get(reverse("code_item")).status_code, 403)

    def test_purchase_sets_sale_prices(self):
        m = ClothingModel.objects.create(code="J-1", name="جاكيت")
        v = Variant.objects.create(model=m, size="L", color="أسود")
        inv = PurchaseInvoice.objects.create(supplier=Supplier.objects.create(name="م"), date="2026-11-01")
        it = PurchaseItem.objects.create(invoice=inv, variant=v, quantity=10, unit_cost=D("400"))
        with self.assertRaises(ValidationError):           # صنف جديد من غير سعر بيع
            inv.confirm()
        it.sale_price, it.wholesale_price = D("900"), D("700")
        it.save()
        inv.confirm()
        v.refresh_from_db()
        self.assertEqual((v.quantity, v.sale_price, v.wholesale_price), (10, D("900"), D("700")))
