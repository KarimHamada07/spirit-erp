from decimal import Decimal

from django.test import TestCase

from trade.labels import labels_pdf
from trade.models import ActivityLog, CashBox, CashTransfer, ClothingModel, Variant

D = Decimal


class LabelsTest(TestCase):
    def test_labels_pdf_is_pdf(self):
        m = ClothingModel.objects.create(name="تيشرت قطن", code="TS-SHOT")
        v = Variant.objects.create(model=m, size="L", color="أسود", sale_price=D("250"))
        self.assertEqual(v.barcode, f"20{v.pk:06d}")
        for size in ("50x30", "38x25"):
            data = labels_pdf([(v, 2)], size=size)
            self.assertTrue(data.startswith(b"%PDF"))
            self.assertEqual(data.count(b"/Type /Page\n"), 2)


class TelegramNewEventsTest(TestCase):
    def test_transfer_is_logged(self):
        boxes = list(CashBox.objects.all().order_by("id"))
        self.assertGreaterEqual(len(boxes), 2)
        before = ActivityLog.objects.count()
        CashTransfer.objects.create(from_box=boxes[0], to_box=boxes[1], amount=D("50"))
        self.assertEqual(ActivityLog.objects.count(), before + 1)
        self.assertIn("تحويل", ActivityLog.objects.first().text)


class GenericTelegramTest(TestCase):
    def test_new_product_and_delete_are_logged(self):
        from trade.models import Supplier
        before = ActivityLog.objects.count()
        s = Supplier.objects.create(name="مورد تجريبي")
        self.assertGreaterEqual(ActivityLog.objects.count(), before + 1)
        self.assertIn("إضافة", ActivityLog.objects.first().text)
        s.delete()
        self.assertIn("حذف", ActivityLog.objects.first().text)
