import io
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import numpy as np
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from trade.attendance import PunchError, check_photo, day_summary, distance_m
from trade.models import ActivityLog, Attendance, Workplace
from trade.roles import ROLE_ACCOUNTS_MANAGER

SHOP = (Decimal("30.044420"), Decimal("31.235712"))


def jpeg(w=480, h=640):
    import cv2
    img = np.full((h, w, 3), 180, np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


class FakeFace:
    """بدل كاشف الوجوه في الاختبارات: بيلاقي وش واحد كبير"""
    def detectMultiScale(self, gray, **kw):
        h, w = gray.shape
        return [(w // 4, h // 4, w // 2, w // 2)]


class AttendanceTests(TestCase):
    def setUp(self):
        self.emp = User.objects.create_user("ahmed", password="pw", is_staff=True, first_name="أحمد")
        self.c = Client()
        self.c.force_login(self.emp)
        Workplace.objects.create(name="المحل", latitude=SHOP[0], longitude=SHOP[1], radius_m=150)

    def token(self):
        self.c.get(reverse("my_attendance"))
        return self.c.session["attendance_token"]["t"]

    def punch(self, lat=SHOP[0], lng=SHOP[1], acc=20, token=None, photo=None, face=True):
        data = {"token": token if token is not None else self.token(), "lat": lat, "lng": lng,
                "accuracy": acc, "photo": SimpleUploadedFile("live.jpg", photo or jpeg(), "image/jpeg")}
        det = FakeFace() if face else mock.Mock(detectMultiScale=lambda g, **k: [])
        with mock.patch("trade.attendance._detector", return_value=det):
            return self.c.post(reverse("attendance_punch"), data)

    def test_in_then_out_inside_shop(self):
        r = self.punch()
        self.assertEqual(r.status_code, 200, r.content)
        a = Attendance.objects.get()
        self.assertEqual((a.kind, a.inside, a.workplace.name), ("in", True, "المحل"))
        self.assertTrue(a.photo.name.endswith(".jpg"))
        self.assertTrue(ActivityLog.objects.filter(text__contains="حضور: أحمد").exists())
        Attendance.objects.update(at=timezone.now() - timedelta(minutes=5))  # عدّى وقت
        r = self.punch()
        self.assertEqual(r.json()["kind"], "انصراف")

    def test_outside_is_recorded_and_flagged(self):
        r = self.punch(lat=Decimal("30.054420"))  # حوالي 1.1 كم
        self.assertEqual(r.status_code, 200)
        a = Attendance.objects.get()
        self.assertFalse(a.inside)
        self.assertGreater(a.distance_m, 1000)
        self.assertIn("برا مكان الشغل", r.json()["where"])

    def test_no_face_rejected(self):
        r = self.punch(face=False)
        self.assertEqual(r.status_code, 400)
        self.assertIn("وشك مش ظاهر", r.json()["error"])
        self.assertFalse(Attendance.objects.exists())

    def test_real_detector_rejects_blank_photo(self):
        with self.assertRaises(PunchError):
            check_photo(jpeg())
        with self.assertRaises(PunchError):
            check_photo(b"not an image")

    def test_token_required_and_single_use(self):
        self.assertEqual(self.punch(token="fake").status_code, 400)
        t = self.token()
        self.assertEqual(self.punch(token=t).status_code, 200)
        Attendance.objects.update(at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.punch(token=t).status_code, 400)  # نفس الكود تاني

    def test_token_expires(self):
        t = self.token()
        s = self.c.session
        s["attendance_token"]["at"] -= 600
        s.save()
        r = self.punch(token=t)
        self.assertIn("الوقت خلص", r.json()["error"])

    def test_bad_location_accuracy(self):
        r = self.punch(acc=900)
        self.assertIn("مش دقيق", r.json()["error"])

    def test_no_double_punch(self):
        self.punch()
        r = self.punch()
        self.assertIn("لسه بصمت", r.json()["error"])

    def test_page_has_no_file_picker(self):
        page = self.c.get(reverse("my_attendance")).content.decode()
        self.assertIn("getUserMedia", page)
        self.assertNotIn('type="file"', page)

    def test_employee_without_sales_perm_lands_on_attendance(self):
        self.assertRedirects(self.c.get(reverse("dashboard")), reverse("my_attendance"))

    def test_report_permissions_and_photo_privacy(self):
        self.punch()
        a = Attendance.objects.get()
        self.assertEqual(self.c.get(reverse("attendance_report")).status_code, 403)
        self.assertEqual(self.c.get(reverse("attendance_photo", args=[a.pk])).status_code, 200)  # صورته هو
        other = User.objects.create_user("x", password="pw", is_staff=True)
        oc = Client()
        oc.force_login(other)
        self.assertEqual(oc.get(reverse("attendance_photo", args=[a.pk])).status_code, 404)
        mgr = User.objects.create_user("m", password="pw", is_staff=True)
        mgr.groups.add(Group.objects.get(name=ROLE_ACCOUNTS_MANAGER))
        mc = Client()
        mc.force_login(mgr)
        r = mc.get(reverse("attendance_report"))
        self.assertContains(r, "أحمد")
        self.assertEqual(mc.get(reverse("attendance_report") + "?export=xlsx").status_code, 200)
        mc.post(reverse("workplace_add"), {"name": "المخزن", "lat": "30.1", "lng": "31.2", "radius": "200"})
        self.assertTrue(Workplace.objects.filter(name="المخزن", radius_m=200).exists())

    def test_helpers(self):
        self.assertAlmostEqual(distance_m(30.0444, 31.2357, 30.0544, 31.2357), 1112, delta=5)
        t = timezone.now()
        punches = [mock.Mock(kind="in", at=t, inside=True), mock.Mock(kind="out", at=t + timedelta(hours=4), inside=True),
                   mock.Mock(kind="in", at=t + timedelta(hours=5), inside=False)]
        s = day_summary(punches)
        self.assertEqual((s["hours"], s["open"], s["outside"]), (4.0, True, 1))


class AuditWordingTests(TestCase):
    def test_user_change_message_is_clear(self):
        boss = User.objects.create_superuser("boss", "", "pw")
        c = Client()
        c.force_login(boss)
        u = User.objects.create_user("ahmed2", password="pw")
        ActivityLog.objects.all().delete()
        r = c.post(reverse("admin:auth_user_change", args=[u.pk]),
                   {"username": "ahmed2", "is_active": "on", "is_staff": "on",
                    "date_joined_0": "2026-01-01", "date_joined_1": "10:00:00", "initial-date_joined_0": "2026-01-01",
                    "initial-date_joined_1": "10:00:00"})
        self.assertIn(r.status_code, (200, 302))
        text = "\n".join(ActivityLog.objects.values_list("text", flat=True))
        self.assertIn("يقدر يدخل السيستم: من «لأ» إلى «أيوه»", text)
        self.assertNotIn("False", text)


class ArabicEverywhereTests(TestCase):
    def test_admin_is_arabic_even_for_english_phone(self):
        boss = User.objects.create_superuser("boss", "", "pw")
        c = Client(HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
        c.force_login(boss)
        page = c.get(reverse("admin:auth_user_change", args=[boss.pk])).content.decode()
        for english in ("Save and continue editing", "Log out", "Staff status", "See Profile", ">General<"):
            self.assertNotIn(english, page)
        self.assertIn("احفظ", page)
        js = c.get("/admin/jsi18n/").content.decode()
        self.assertIn('"Choose all %s": "' + "اختار كل".encode("unicode_escape").decode()[:12], js)

    def test_error_pages_are_arabic(self):
        u = User.objects.create_user("x", password="pw", is_staff=True)
        c = Client()
        c.force_login(u)
        r = c.get(reverse("attendance_report"))
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "مش مسموحلك", status_code=403)
        self.assertContains(c.get("/no-such-page/"), "الصفحة مش موجودة", status_code=404)


class UiHelpTests(TestCase):
    def test_help_box_tooltips_and_assets(self):
        boss = User.objects.create_superuser("boss", "", "pw")
        c = Client()
        c.force_login(boss)
        page = c.get(reverse("customers")).content.decode()
        self.assertIn('class="erp-help"', page)
        self.assertIn("الصفحة دي بتعمل إيه؟", page)
        self.assertIn("trade/js/ui.js?v=", page)
        self.assertIn("trade/css/ui.css?v=", page)
        self.assertIn('data-tip="العملاء المتأخرين في الدفع ومواعيد متابعتهم"', page)
        # صفحات لوحة الإدارة: شرح الجدول في القايمة، ونصيحة في الإضافة
        page = c.get(reverse("admin:trade_clothingmodel_changelist")).content.decode()
        self.assertIn("الموديلات (المنتجات)", page)
        page = c.get(reverse("admin:trade_clothingmodel_add")).content.decode()
        self.assertIn("حد التنبيه", page.split('class="erp-help"')[1][:600])

    def test_every_menu_item_has_help_or_tip(self):
        from trade.help_texts import MENU_TIPS, PAGE_HELP, MODEL_HELP
        from trade.menu import MENU
        for _title, items in MENU:
            for name, target, _icon, _perm in items:
                self.assertIn(name, MENU_TIPS, name)
                if target.startswith("admin:"):
                    self.assertIn(target[6:], MODEL_HELP, target)
                elif target != "my_custody":   # بتحوّل لصفحة عهدة الشخص (ليها شرح)
                    self.assertIn(target, PAGE_HELP, target)


class PermissionLabelsTests(TestCase):
    def test_permissions_are_arabic_and_technical_hidden(self):
        boss = User.objects.create_superuser("boss", "", "pw")
        c = Client()
        c.force_login(boss)
        page = c.get(reverse("admin:auth_group_change", args=[Group.objects.get(name="محاسب").pk])).content.decode()
        self.assertIn("عميل — إضافة", page)
        self.assertNotIn("Can add", page)
        self.assertNotIn("log entry", page)
        self.assertNotIn("content type", page.lower())
        page = c.get(reverse("admin:auth_user_change", args=[boss.pk])).content.decode()
        self.assertIn("يقدر يدخل السيستم", page)
        self.assertNotIn("Can add", page)
