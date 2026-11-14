from django.test import Client, TestCase
from django.contrib.auth.models import Group, Permission, User
from trade.roles import ROLE_PERMISSIONS, ROLE_ACCOUNTANT, ROLE_WAREHOUSE_MANAGER, ROLE_SALES, ROLE_WAREHOUSE_EMPLOYEE, ROLE_ACCOUNTS_MANAGER

class RoleTests(TestCase):
    def setUp(self):
        self.users = {}
        for role in ROLE_PERMISSIONS:
            u = User.objects.create_user(username=role, password="x-Pass-123", is_staff=True)
            u.groups.add(Group.objects.get(name=role))
            self.users[role] = u

    def test_all_codenames_exist(self):
        existing = set(Permission.objects.filter(content_type__app_label="trade").values_list("codename", flat=True))
        for role, codes in ROLE_PERMISSIONS.items():
            if codes is None: continue
            missing = set(codes) - existing
            self.assertFalse(missing, f"{role} missing {missing}")

    def _login(self, role):
        c = Client(); c.force_login(self.users[role]); return c

    def test_pos_access(self):
        self.assertEqual(self._login(ROLE_SALES).get("/pos/").status_code, 200)
        self.assertEqual(self._login(ROLE_ACCOUNTANT).get("/pos/").status_code, 200)
        self.assertEqual(self._login(ROLE_WAREHOUSE_EMPLOYEE).get("/pos/").status_code, 403)
        self.assertEqual(self._login(ROLE_WAREHOUSE_MANAGER).get("/pos/").status_code, 403)

    def test_custody_and_reports(self):
        self.assertEqual(self._login(ROLE_ACCOUNTS_MANAGER).get("/custody/").status_code, 200)
        self.assertEqual(self._login(ROLE_ACCOUNTANT).get("/custody/").status_code, 200)
        self.assertEqual(self._login(ROLE_SALES).get("/custody/").status_code, 403)
        self.assertEqual(self._login(ROLE_WAREHOUSE_EMPLOYEE).get("/custody/").status_code, 403)
        self.assertEqual(self._login(ROLE_WAREHOUSE_EMPLOYEE).get("/reports/").status_code, 403)
        self.assertEqual(self._login(ROLE_ACCOUNTS_MANAGER).get("/reports/").status_code, 200)

    def test_customer_add_only_sales_and_accounts(self):
        self.assertEqual(self._login(ROLE_WAREHOUSE_MANAGER).post("/customers/add/", {"name":"t"}).status_code, 403)
        self.assertEqual(self._login(ROLE_WAREHOUSE_EMPLOYEE).post("/customers/add/", {"name":"t"}).status_code, 403)
        self.assertEqual(self._login(ROLE_SALES).post("/customers/add/", {"name":"t"}).status_code, 302)

    def test_inventory_write(self):
        self.assertEqual(self._login(ROLE_WAREHOUSE_EMPLOYEE).get("/inventory/").status_code, 200)
        self.assertEqual(self._login(ROLE_SALES).get("/inventory/").status_code, 200)

    def test_audit_only_accounts_manager(self):
        self.assertEqual(self._login(ROLE_ACCOUNTS_MANAGER).get("/audit/").status_code, 200)
        self.assertEqual(self._login(ROLE_ACCOUNTANT).get("/audit/").status_code, 403)
