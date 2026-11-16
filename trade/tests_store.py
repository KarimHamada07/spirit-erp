"""الموقع: الدومين بيفتح المتجر بس، الطلب بيتسجل محتاج تأكيد، والتأكيد بيعمل أوردر أونلاين ويخصم المخزون."""
import io
import os
import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from trade.models import (ActivityLog, ClothingModel, OnlineOrder, ProductImage, Seller, ShippingZone,
                          StoreSettings, Variant, WebOrder)
from trade.roles import ROLE_ACCOUNTS_MANAGER, ROLE_SALES, ROLE_WAREHOUSE_MANAGER
from trade.tests_hr import login

D = Decimal
STORE = {"HTTP_HOST": "spiritegy.org"}
MEDIA = tempfile.mkdtemp(prefix="spirit-media-")


def su(name, *args):
    """رابط صفحة في المتجر على دومين المتجر"""
    return reverse(name, urlconf="config.store_urls", args=args)


def jpeg(w=1200, h=1500, color=(200, 0, 0)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return SimpleUploadedFile("p.jpg", buf.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=MEDIA)
class StoreBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.m = ClothingModel.objects.create(name="تيشرت أسود", code="TS-9", category="tshirt", show_online=True)
        self.v = Variant.objects.create(model=self.m, size="L", color="أسود", sale_price=D("350"),
                                        quantity=5, avg_cost=D("120"))
        self.v2 = Variant.objects.create(model=self.m, size="XL", color="أسود", sale_price=D("370"),
                                         quantity=0, avg_cost=D("120"))
        self.zone = ShippingZone.objects.get(name="القاهرة")

    def order(self, client=None, **over):
        c = client or self.client
        c.post(su("store:cart_add"), {"variant": self.v.pk, "qty": 2}, **STORE)
        data = {"name": "أحمد محمد", "phone": "01012345678", "zone": self.zone.pk, "city": "مدينة نصر",
                "address": "شارع عباس العقاد عمارة 5", "notes": ""}
        data.update(over)
        return c.post(su("store:checkout"), data, **STORE)


class StorePublic(StoreBase):
    def test_store_domain_serves_store_only(self):
        page = self.client.get("/", **STORE)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "تيشرت أسود")
        self.assertEqual(self.client.get("/admin/", **STORE).status_code, 404)
        self.assertEqual(self.client.get("/dashboard/", **STORE).status_code, 404)
        self.assertIn("Sitemap:", self.client.get("/robots.txt", **STORE).content.decode())
        # على دومين السيستم: المتجر للمعاينة بس، والسيستم ما يتأرشفش
        self.assertEqual(self.client.get("/robots.txt").content.decode().strip().splitlines()[-1], "Disallow: /")
        self.assertEqual(self.client.get("/shop/").status_code, 200)

    def test_hidden_or_sold_out_models_are_not_shown(self):
        hidden = ClothingModel.objects.create(name="مخفي", code="HD-1")
        Variant.objects.create(model=hidden, size="M", sale_price=D("100"), quantity=3)
        self.assertEqual(self.client.get(f"/p/{hidden.code}/", **STORE).status_code, 404)
        self.assertNotContains(self.client.get("/shop/", **STORE), "مخفي")
        page = self.client.get(f"/p/{self.m.code}/", **STORE).content.decode()
        self.assertIn('"qty": 0', page)          # XL خلص: بيبان مشطوب
        self.v.quantity = 0
        self.v.save()
        self.assertEqual(self.client.get(f"/p/{self.m.code}/", **STORE).status_code, 404)

    def test_checkout_creates_web_order_without_touching_stock(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.order()
        o = WebOrder.objects.get()
        self.assertRedirects(r, su("store:order_done", o.token), fetch_redirect_response=False)
        self.assertEqual((o.status, o.phone, o.zone_name, o.shipping_fee), (WebOrder.NEW, "01012345678", "القاهرة", D("60")))
        self.assertEqual(o.items.get().quantity, 2)
        self.assertEqual(o.total, D("760"))
        self.assertEqual(Variant.objects.get(pk=self.v.pk).quantity, 5)
        self.assertTrue(ActivityLog.objects.filter(text__contains="طلب جديد من الموقع").exists())
        self.assertEqual(self.client.session.get("cart"), {})
        self.assertContains(self.client.get(r.url, **STORE), "طلبك وصلنا")

    def test_free_shipping_and_validation(self):
        s = StoreSettings.get()
        s.free_shipping_over = D("500")
        s.save()
        self.order()
        self.assertEqual(WebOrder.objects.get().shipping_fee, D("0"))
        r = self.order(phone="12345")
        self.assertContains(r, "11 رقم", status_code=200)
        r = self.order(website="spam")                      # بوت
        self.assertEqual(WebOrder.objects.count(), 1)

    def test_closed_store_rejects_orders(self):
        s = StoreSettings.get()
        s.is_open = False
        s.save()
        self.order()
        self.assertFalse(WebOrder.objects.exists())

    def test_track(self):
        self.order()
        o = WebOrder.objects.get()
        page = self.client.get(su("store:track"), {"no": o.pk, "phone": o.phone}, **STORE)
        self.assertContains(page, "مستني تأكيد")
        page = self.client.get(su("store:track"), {"no": o.pk, "phone": "01099999999"}, **STORE)
        self.assertContains(page, "مفيش طلب")


class StoreAdmin(StoreBase):
    def test_confirm_turns_into_online_order_and_deducts_stock(self):
        self.order()
        o = WebOrder.objects.get()
        _u, sales = login("sales", ROLE_SALES)
        self.assertContains(sales.get(reverse("dashboard")), "طلبات جديدة من الموقع")
        seller = Seller.objects.get(name="خالد فلكس")
        r = sales.post(reverse("store_order", args=[o.pk]),
                       {"action": "confirm", "seller": seller.pk, "shipping_cost": "45", "discount": "0"})
        o.refresh_from_db()
        self.assertEqual(o.status, WebOrder.CONFIRMED)
        online = o.online_order
        self.assertRedirects(r, reverse("online_detail", args=[online.pk]), fetch_redirect_response=False)
        self.assertEqual((online.shipping_fee, online.shipping_cost, online.status), (D("60"), D("45"), OnlineOrder.PREPARED))
        self.assertEqual(online.total_to_collect, D("760"))
        self.assertEqual(Variant.objects.get(pk=self.v.pk).quantity, 3)
        # مرة تانية: مرفوض
        sales.post(reverse("store_order", args=[o.pk]), {"action": "confirm", "seller": seller.pk})
        self.assertEqual(OnlineOrder.objects.count(), 1)
        # حالة الطلب للعميل
        page = self.client.get(su("store:track"), {"no": o.pk, "phone": o.phone}, **STORE)
        self.assertContains(page, "بيتجهز")

    def test_cancel(self):
        self.order()
        o = WebOrder.objects.get()
        _u, mgr = login("boss", ROLE_ACCOUNTS_MANAGER)
        mgr.post(reverse("store_order", args=[o.pk]), {"action": "cancel", "reason": "الرقم مش بيرد"})
        o.refresh_from_db()
        self.assertEqual((o.status, o.cancel_reason), (WebOrder.CANCELLED, "الرقم مش بيرد"))
        self.assertEqual(Variant.objects.get(pk=self.v.pk).quantity, 5)

    def test_product_page_uploads_resized_images_and_edits_prices(self):
        _u, wh = login("store", ROLE_WAREHOUSE_MANAGER)
        url = reverse("store_product", args=[self.m.pk])
        self.assertEqual(wh.get(url).status_code, 200)
        wh.post(url, {"what": "images", "color": "أسود", "images": [jpeg(), jpeg(800, 600)]})
        imgs = list(ProductImage.objects.all())
        self.assertEqual(len(imgs), 2)
        from PIL import Image
        with Image.open(imgs[0].image.path) as big, Image.open(imgs[0].thumb.path) as small:
            self.assertEqual(max(big.size), 1500 if big.size[1] > big.size[0] else 1200)
            self.assertLessEqual(max(small.size), 600)
        wh.post(url, {"what": "prices", f"price_{self.v.pk}": "399"})
        self.assertEqual(Variant.objects.get(pk=self.v.pk).sale_price, D("399"))
        path = imgs[0].image.path
        wh.post(reverse("store_image_delete", args=[imgs[0].pk]))
        self.assertFalse(os.path.exists(path))
        page = self.client.get(f"/p/{self.m.code}/", **STORE).content.decode()
        self.assertIn("/media/products/", page)

    def test_sales_cannot_manage_products(self):
        _u, sales = login("sales", ROLE_SALES)
        self.assertEqual(sales.get(reverse("store_products")).status_code, 403)


class StoreV2(StoreBase):
    """السلة من غير تحميل صفحة، المعاينة السريعة، كروت المفضلة، والسلايدر"""

    def test_ajax_cart_drawer_quick_and_cards(self):
        r = self.client.post(su("store:cart_add"), {"variant": self.v.pk, "qty": 1},
                             HTTP_X_REQUESTED_WITH="fetch", **STORE)
        self.assertEqual(r.json()["count"], 1)
        r = self.client.post(su("store:cart_add"), {"variant": self.v2.pk}, HTTP_X_REQUESTED_WITH="fetch", **STORE)
        self.assertEqual(r.status_code, 400)                       # المقاس خلص
        self.assertContains(self.client.get(su("store:cart_drawer"), **STORE), "تيشرت أسود")
        quick = self.client.get(su("store:quick", self.m.code), **STORE)
        self.assertContains(quick, 'data-ajax-cart')
        self.assertNotContains(quick, ">XL<")                       # الخلصان مش بيظهر في المعاينة
        cards = self.client.get(su("store:cards") + f"?codes={self.m.code},NOPE", **STORE)
        self.assertContains(cards, self.m.name)
        self.assertEqual(self.client.get(su("store:wishlist"), **STORE).status_code, 200)

    def test_home_slides_and_size_guide(self):
        from trade.models import HeroSlide
        self.assertContains(self.client.get("/", **STORE), "class=\"slide on\"")
        HeroSlide(image=jpeg(2000, 1000), title="كولكشن الشتا").save()
        self.assertContains(self.client.get("/", **STORE), "كولكشن الشتا")
        s = StoreSettings.get()
        s.size_guide = "M | 104 | 72 | 65-75"
        s.save()
        self.assertContains(self.client.get(f"/p/{self.m.code}/", **STORE), "65-75")
