from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from trade.custody_views import custody_summary
from trade.models import CustodyClearance, CustodyDeposit, CustodyHolder, CustodySettlement, Expense
from trade.roles import ROLE_ACCOUNTANT, ROLE_ACCOUNTS_MANAGER

D = Decimal


def staff(name, role=None):
    u = User.objects.create_user(name, password="pw", is_staff=True)
    if role:
        u.groups.add(Group.objects.get(name=role))
    c = Client()
    c.force_login(u)
    return u, c


class CustodyPerPersonTests(TestCase):
    def setUp(self):
        self.acc, self.acc_c = staff("acc", ROLE_ACCOUNTANT)
        self.mgr, self.mgr_c = staff("mgr", ROLE_ACCOUNTS_MANAGER)
        self.driver_user, self.driver_c = staff("sayed")
        CustodyDeposit.objects.create(amount=D("10000"))

    def hand(self, holder, amount):
        data = {"amount": amount, "method": "cash"}
        if holder.user_id:
            data.update(receiver_kind="staff", user=holder.user_id)
        else:
            data.update(receiver_kind="outside", received_by=holder.name)
        r = self.acc_c.post(reverse("custody_settle"), data)
        self.assertEqual(r.status_code, 302)

    def test_full_flow(self):
        # التسليم: موظف بيتختار بحسابه، واللي من بره بيتكتب اسمه. صاحب العهدة بيتعمل لوحده
        self.driver_user.first_name = "سيد"
        self.driver_user.save()
        self.acc_c.post(reverse("custody_settle"), {"amount": "1000", "method": "cash",
                                                    "receiver_kind": "staff", "user": self.driver_user.pk})
        self.acc_c.post(reverse("custody_settle"), {"amount": "50", "method": "cash",
                                                    "receiver_kind": "outside", "received_by": "عم حسن السواق"})
        self.acc_c.post(reverse("custody_settle"), {"amount": "50", "method": "cash",
                                                    "receiver_kind": "outside", "received_by": "عم حسن السواق "})
        sayed = CustodyHolder.objects.get(name="سيد")
        hassan = CustodyHolder.objects.get(name="عم حسن السواق")
        self.assertEqual(sayed.user, self.driver_user)
        self.assertIsNone(hassan.user)
        self.assertEqual(CustodyHolder.objects.count(), 2)  # نفس الاسم مش بيتكرر
        self.assertEqual(hassan.totals()["balance"], D("100"))
        CustodySettlement.objects.filter(holder=hassan).delete()
        self.assertEqual(CustodySettlement.objects.get().holder, sayed)
        self.assertEqual(sayed.totals()["balance"], D("1000"))
        self.assertEqual(custody_summary()["balance"], D("9000"))

        # سيد يفتح عهدتي ويطلب تصفية بنفسه
        r = self.driver_c.get(reverse("my_custody"))
        self.assertRedirects(r, reverse("custody_holder", args=[sayed.pk]))
        self.assertContains(self.driver_c.get(reverse("custody_holder", args=[sayed.pk])), "تصفية من عهدتي")
        self.driver_c.post(reverse("custody_clearance_request", args=[sayed.pk]),
                           {"kind": "spent", "amount": "300", "description": "بنزين"})
        req = CustodyClearance.objects.get()
        self.assertEqual(req.status, "pending")
        self.assertEqual(sayed.totals()["balance"], D("1000"))  # لسه مستني موافقة

        # سيد ميقدرش يوافق، ولا يشوف عهدة حد تاني
        self.assertEqual(self.driver_c.post(reverse("custody_clearance_review", args=[req.pk]),
                                            {"decision": "approve"}).status_code, 403)
        self.assertEqual(self.driver_c.get(reverse("custody_holder", args=[hassan.pk])).status_code, 403)
        # المحاسب كمان بيشوف الطلبات ويقدر يراجعها (هنا مدير الحسابات هو اللي هيوافق)
        self.assertContains(self.acc_c.get(reverse("custody_approvals")), "بنزين")

        # مدير الحسابات يوافق: يتخصم من عهدة سيد ويتسجل مصروف مش بيتخصم من العامة تاني
        self.assertContains(self.mgr_c.get(reverse("custody_approvals")), "بنزين")
        self.mgr_c.post(reverse("custody_clearance_review", args=[req.pk]), {"decision": "approve"})
        req.refresh_from_db()
        self.assertEqual((req.status, req.reviewed_by), ("approved", self.mgr))
        self.assertEqual(sayed.totals()["balance"], D("700"))
        self.assertEqual(Expense.objects.get().amount, D("300"))
        self.assertEqual(custody_summary()["balance"], D("9000"))

        # المحاسب يطلب "رجّع فلوس" لعهدة سيد، والمدير يرفض واحد ويوافق على التاني
        self.acc_c.post(reverse("custody_clearance_request", args=[sayed.pk]),
                        {"kind": "returned", "amount": "200"})
        own = CustodyClearance.objects.latest("id")
        self.acc_c.post(reverse("custody_clearance_review", args=[own.pk]), {"decision": "approve"})
        own.refresh_from_db()
        self.assertEqual(own.status, "pending")   # المحاسب ميوافقش على طلب هو اللي عامله
        self.acc_c.post(reverse("custody_clearance_request", args=[sayed.pk]),
                        {"kind": "returned", "amount": "100"})
        r1, r2 = CustodyClearance.objects.filter(status="pending").order_by("id")
        self.mgr_c.post(reverse("custody_clearance_review", args=[r1.pk]), {"decision": "approve"})
        self.mgr_c.post(reverse("custody_clearance_review", args=[r2.pk]), {"decision": "reject", "note": "مش مظبوط"})
        self.assertEqual(sayed.totals()["balance"], D("500"))
        self.assertEqual(custody_summary()["balance"], D("9200"))  # الفلوس رجعت للعامة

    def test_cannot_clear_more_than_balance(self):
        h = CustodyHolder.objects.create(name="ح")
        self.hand(h, "100")
        self.acc_c.post(reverse("custody_clearance_request", args=[h.pk]),
                        {"kind": "spent", "amount": "150", "description": "x"})
        self.assertFalse(CustodyClearance.objects.exists())

    def test_requester_cannot_approve_own(self):
        h = CustodyHolder.objects.create(name="ح")
        self.hand(h, "100")
        c = CustodyClearance.objects.create(holder=h, amount=D("50"), description="x",
                                            requested_by=self.mgr)
        with self.assertRaises(ValidationError):
            c.review(self.mgr, True)

    def test_handout_requires_holder(self):
        r = self.acc_c.post(reverse("custody_settle"), {"amount": "50", "method": "cash"}, follow=True)
        self.assertContains(r, "اختار الموظف اللي استلم")
        self.assertFalse(CustodySettlement.objects.exists())

    def test_menu_shows_my_custody_only_for_holders(self):
        page = self.driver_c.get(reverse("my_attendance")).content.decode()
        self.assertNotIn("عهدتي", page)
        CustodyHolder.objects.create(name="سيد", user=self.driver_user)
        page = self.driver_c.get(reverse("my_attendance")).content.decode()
        self.assertIn("عهدتي", page)
        self.assertIn("طلبات تصفية العهد", self.mgr_c.get(reverse("dashboard")).content.decode())
