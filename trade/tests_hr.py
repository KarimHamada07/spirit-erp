import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from trade.models import (ActivityLog, Attendance, CashBox, CashEntry, ClothingModel, EmployeeProfile,
                          EmployeeTxn, Expense, LeaveRequest, Mission, OnlineOrder, PurchaseInvoice,
                          PurchaseItem, SaleInvoice, SaleItem, Seller, StockCount, Supplier, Variant)
from trade.payroll import approve_payroll, balance, month_stats
from trade.roles import ROLE_ACCOUNTS_MANAGER

D = Decimal
TZ = timezone.get_current_timezone()


def at(d, hh, mm):
    return timezone.make_aware(datetime.combine(d, time(hh, mm)), TZ)


def login(username, role=None, superuser=False):
    u = (User.objects.create_superuser(username, "", "pw") if superuser
         else User.objects.create_user(username, password="pw", is_staff=True, first_name=username))
    if role:
        u.groups.add(Group.objects.get(name=role))
    c = Client()
    c.force_login(u)
    return u, c


class PayrollTests(TestCase):
    def setUp(self):
        self.emp, self.emp_c = login("ahmed")
        self.mgr, self.mgr_c = login("mgr", ROLE_ACCOUNTS_MANAGER)
        self.p = EmployeeProfile.objects.create(user=self.emp, monthly_salary=D("6000"), shift_start=time(10),
                                                shift_end=time(18), grace_minutes=15, off_days="4",
                                                start_date=date(2026, 1, 1))
        # أكتوبر 2026: الخميس 1 لحد السبت 31، الجمعة إجازة
        self.period = "2026-10"

    def punch(self, d, h_in, m_in, h_out, m_out):
        Attendance.objects.create(user=self.emp, kind="in", at=at(d, h_in, m_in), latitude=0, longitude=0, inside=True)
        Attendance.objects.create(user=self.emp, kind="out", at=at(d, h_out, m_out), latitude=0, longitude=0, inside=True)

    def test_month_calculation(self):
        d = date(2026, 10, 1)
        while d.month == 10:
            if d.weekday() != 4:
                self.punch(d, 10, 0, 18, 0)
            d += timedelta(days=1)
        # يوم 3 تأخير 45 دقيقة (30 بعد السماح)، يوم 4 غياب، يوم 5 إجازة متوافق عليها، يوم 6 أوفر تايم ساعة
        Attendance.objects.filter(at__date=date(2026, 10, 3), kind="in").update(at=at(date(2026, 10, 3), 10, 45))
        Attendance.objects.filter(at__date=date(2026, 10, 4)).delete()
        Attendance.objects.filter(at__date=date(2026, 10, 5)).delete()
        LeaveRequest.objects.create(user=self.emp, kind="annual", start_date=date(2026, 10, 5),
                                    end_date=date(2026, 10, 5), status="approved")
        Attendance.objects.filter(at__date=date(2026, 10, 6), kind="out").update(at=at(date(2026, 10, 6), 19, 0))
        st = month_stats(self.p, self.period, today=date(2026, 11, 1))
        self.assertEqual((st["work_days"], st["absent"], st["paid_leave"]), (26, 1, 1))
        self.assertEqual((st["late_minutes"], st["overtime"]), (30, 60))
        self.assertEqual(st["absence_deduction"], D("200.00"))            # 6000/30
        self.assertEqual(st["late_deduction"], D("12.50"))                # 200/480 × 30
        self.assertEqual(st["overtime_pay"], D("25.00"))
        self.assertEqual(st["net"], D("5812.50"))

        n = approve_payroll(self.period, self.mgr, today=date(2026, 11, 1))
        self.assertEqual(n, 1)
        self.assertEqual(approve_payroll(self.period, self.mgr, today=date(2026, 11, 1)), 0)  # مرة واحدة
        self.assertTrue(Expense.objects.filter(amount=D("5812.50")).exists())
        self.assertEqual(balance(self.emp), D("5812.50"))

    def test_days_before_start_date_are_not_absent(self):
        self.p.start_date = date(2026, 10, 20)
        self.p.save()
        st = month_stats(self.p, self.period, today=date(2026, 10, 22))
        self.assertEqual((st["work_days"], st["absent"]), (3, 2))   # 20 و 21 غياب، و22 لسه

    def test_permission_leave_excuses_lateness(self):
        d = date(2026, 10, 3)
        self.punch(d, 11, 15, 18, 0)   # 75 دقيقة − 15 سماح = 60
        LeaveRequest.objects.create(user=self.emp, kind="permission", start_date=d, end_date=d,
                                    hours=D("1"), status="approved")
        st = month_stats(self.p, self.period, today=date(2026, 10, 4))
        self.assertEqual(st["late_minutes"], 0)

    def test_statement_advance_and_pages(self):
        box = CashBox.objects.get(name="كاش المحل")
        r = self.mgr_c.post(reverse("employee_txn_add", args=[self.emp.pk]),
                            {"kind": "advance", "amount": "500", "box": box.pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(balance(self.emp), D("-500"))
        self.assertEqual(CashEntry.objects.get(kind="salary").amount, D("-500"))
        self.assertEqual(self.emp_c.get(reverse("my_statement")).status_code, 302)
        self.assertContains(self.emp_c.get(reverse("employee_detail", args=[self.emp.pk])), "سلفة")
        self.assertEqual(self.emp_c.get(reverse("employee_detail", args=[self.mgr.pk])).status_code, 403)
        self.assertEqual(self.emp_c.post(reverse("employee_txn_add", args=[self.emp.pk]),
                                         {"kind": "bonus", "amount": "9"}).status_code, 403)
        for fmt in ("pdf", "xlsx"):
            self.assertEqual(self.emp_c.get(reverse("employee_statement_file", args=[self.emp.pk, fmt])).status_code, 200)
        self.assertContains(self.mgr_c.get(reverse("payroll") + "?month=2026-10"), "ahmed")
        self.assertEqual(self.mgr_c.get(reverse("employees")).status_code, 200)

    def test_leave_flow(self):
        self.emp_c.post(reverse("leave_request"), {"kind": "sick", "start_date": "2026-10-10", "end_date": "2026-10-11"})
        lv = LeaveRequest.objects.get()
        self.assertEqual((lv.status, lv.days), ("pending", 2))
        self.assertEqual(self.emp_c.post(reverse("leave_review", args=[lv.pk]), {"decision": "approve"}).status_code, 403)
        self.assertContains(self.mgr_c.get(reverse("leaves")), "إجازة مرضي")
        self.mgr_c.post(reverse("leave_review", args=[lv.pk]), {"decision": "approve"})
        lv.refresh_from_db()
        self.assertEqual((lv.status, lv.reviewed_by), ("approved", self.mgr))


class MissionTests(TestCase):
    def test_mission_flow(self):
        emp, c = login("driver")
        geo = {"lat": "30.0444", "lng": "31.2357", "accuracy": "15"}
        c.post(reverse("mission_start"), {"purpose": "تحصيل من محل النجمة", **geo})
        m = Mission.objects.get()
        self.assertEqual((m.status, m.points.count()), ("open", 1))
        c.post(reverse("mission_point", args=[m.pk]), {"kind": "arrive", "note": "محل النجمة", **geo, "lat": "30.05"})
        c.post(reverse("mission_point", args=[m.pk]), {"kind": "end", **geo})
        m.refresh_from_db()
        self.assertEqual((m.status, m.points.count()), ("done", 3))
        self.assertIn("google.com/maps/dir/", m.route_url)
        self.assertTrue(ActivityLog.objects.filter(text__contains="خلّص مأمورية").exists())
        # من غير لوكيشن مش بيبدأ
        c.post(reverse("mission_start"), {"purpose": "x"})
        self.assertEqual(Mission.objects.count(), 1)
        self.assertEqual(c.get(reverse("missions_report")).status_code, 403)
        _mgr, mc = login("m2", ROLE_ACCOUNTS_MANAGER)
        self.assertContains(mc.get(reverse("missions_report")), "تحصيل من محل النجمة")


class StockAndReorderTests(TestCase):
    def setUp(self):
        self.boss, self.c = login("boss", superuser=True)
        self.sup = Supplier.objects.create(name="مورد")
        m = ClothingModel.objects.create(name="تيشرت", code="T1", low_stock_level=3)
        self.v = Variant.objects.create(model=m, size="M", sale_price=D("100"))
        pi = PurchaseInvoice.objects.create(supplier=self.sup, date=timezone.localdate())
        PurchaseItem.objects.create(invoice=pi, variant=self.v, quantity=12, unit_cost=D("50"))
        pi.confirm()

    def test_stock_count_apply(self):
        self.c.post(reverse("stock_counts"), {"name": "جرد"})
        sc = StockCount.objects.get()
        import json
        r = self.c.post(reverse("stock_count_set", args=[sc.pk]), json.dumps({"variant": self.v.pk, "counted": 10}),
                        content_type="application/json")
        self.assertTrue(r.json()["ok"])
        self.c.post(reverse("stock_count_action", args=[sc.pk]), {"action": "apply"})
        sc.refresh_from_db()
        self.v.refresh_from_db()
        self.assertEqual((sc.status, self.v.quantity), ("applied", 10))
        line = sc.lines.get()
        self.assertEqual((line.expected, line.counted), (12, 10))
        self.assertContains(self.c.get(reverse("stock_count_detail", args=[sc.pk])), "-2")

    def test_reorder_and_draft_invoice(self):
        seller = Seller.objects.get(name="خالد فلكس")
        inv = SaleInvoice.objects.create(seller=seller)
        SaleItem.objects.create(invoice=inv, variant=self.v, quantity=10, unit_price=D("100"))
        inv.confirm()   # فضل 2، اتباع 10 في الشهر
        page = self.c.get(reverse("reorder")).content.decode()
        self.assertIn("مورد", page)
        self.c.post(reverse("reorder_create"), {"supplier": self.sup.pk, "days": 30, "cover": 30})
        draft = PurchaseInvoice.objects.filter(is_confirmed=False).get()
        self.assertEqual(draft.items.get().quantity, 11)   # 10 + 3 حد − 2 موجود
        self.assertEqual(self.c.get(reverse("reorder") + "?export=xlsx").status_code, 200)


class NightlyAndOnlineTests(TestCase):
    def test_nightly_summary_and_backup(self):
        out = io.StringIO()
        call_command("nightly", "--dry-run", stdout=out)
        self.assertIn("ملخص يوم", out.getvalue())
        with mock.patch("trade.management.commands.backup_db.make_backup") as mb, \
                mock.patch("trade.management.commands.nightly.config",
                           return_value={"TOKEN": "t", "BACKUP_CHAT_ID": "1", "CHAT_ID": "", "FEED_KEY": "",
                                         "ACCOUNTANT_CHAT_ID": ""}), \
                mock.patch("trade.management.commands.nightly.send_document", return_value=(True, "")) as sd:
            mb.return_value = mock.Mock(stat=lambda: mock.Mock(st_size=2048), __str__=lambda s: "/tmp/b.tar.gz")
            call_command("nightly", stdout=io.StringIO())
            self.assertTrue(sd.called)
        self.assertTrue(ActivityLog.objects.filter(text__contains="واتبعتت لتليجرام").exists())

    def test_online_profit_on_collection_day(self):
        seller = Seller.objects.get(name="خالد فلكس")
        m = ClothingModel.objects.create(name="ت", code="X")
        v = Variant.objects.create(model=m, size="M", sale_price=D("100"), quantity=5, avg_cost=D("40"))
        o = OnlineOrder.create_order(customer_name="ع", phone="010", items=[{"id": v.pk, "qty": 1, "price": 100}],
                                     seller=seller)
        o.set_status(OnlineOrder.SHIPPED)
        o.set_status(OnlineOrder.COLLECTED, box=CashBox.objects.get(name="شركة الشحن"))
        o.refresh_from_db()
        self.assertIsNotNone(o.status_at)
        old = o.status_at
        o.notes = "تعديل بعدين"
        o.save()
        o.refresh_from_db()
        self.assertEqual(o.status_at, old)   # التعديل ما بيغيّرش يوم الربح
