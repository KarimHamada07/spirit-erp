from decimal import Decimal

from django.test import TestCase

from trade.labels import labels_pdf
from trade.models import ActivityLog, CashBox, CashTransfer, ClothingModel

D = Decimal


class LabelsTest(TestCase):
    def test_labels_pdf_is_pdf(self):
        m = ClothingModel.objects.create(name="تيشرت قطن", code="TS-SHOT")
        data = labels_pdf([m])
        self.assertTrue(data.startswith(b"%PDF"))


class TelegramNewEventsTest(TestCase):
    def test_transfer_is_logged(self):
        boxes = list(CashBox.objects.all().order_by("id"))
        self.assertGreaterEqual(len(boxes), 2)
        before = ActivityLog.objects.count()
        CashTransfer.objects.create(from_box=boxes[0], to_box=boxes[1], amount=D("50"))
        self.assertEqual(ActivityLog.objects.count(), before + 1)
        self.assertIn("تحويل", ActivityLog.objects.first().text)
