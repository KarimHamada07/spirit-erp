"""الأدوار والصلاحيات لنظام SPIRIT.

الأدوار تتكون تلقائياً بعد كل migrate (مجموعات Django)، وتُحدَّث صلاحياتها
عند كل تشغيل، فلو عدّلت المصفوفة هنا تتطبق على المستخدمين تلقائياً.
المستخدم يتضاف لمجموعة الدور من لوحة الإدارة (المستخدمين > المجموعات).
"""
from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission

ROLE_ACCOUNTANT = "محاسب"
ROLE_WAREHOUSE_MANAGER = "مدير مخزن"
ROLE_SALES = "سيلز"
ROLE_WAREHOUSE_EMPLOYEE = "موظف مخزن"
ROLE_ACCOUNTS_MANAGER = "مدير حسابات"

_STOCK_READ = ["view_clothingmodel", "view_variant", "view_supplier"]
_STOCK_WRITE = [
    "add_clothingmodel", "change_clothingmodel",
    "add_variant", "change_variant",
    "add_supplier", "change_supplier",
]
_PURCHASE = [
    "view_purchaseinvoice", "add_purchaseinvoice", "change_purchaseinvoice",
    "view_purchaseitem", "add_purchaseitem", "change_purchaseitem",
]
_ONLINE = ["view_onlineorder", "add_onlineorder", "change_onlineorder",
           "view_onlineorderitem", "add_onlineorderitem"]
_SALES = [
    "view_saleinvoice", "add_saleinvoice", "change_saleinvoice",
    "view_saleitem", "add_saleitem",
]
_CUSTOMERS = ["view_customer", "add_customer", "change_customer"]
_PAYMENTS = ["view_payment", "add_payment"]
_DEBT_FOLLOWUP = ["view_debtfollowup", "add_debtfollowup"]
_CUSTODY = ["view_custodydeposit", "add_custodydeposit",
            "view_custodysettlement", "add_custodysettlement",
            "view_custodyholder", "add_custodyholder",
            "view_custodyclearance", "add_custodyclearance"]
# الموافقة على تصفية العهد: مدير الحسابات بس (عنده كل الصلاحيات)
_EXPENSES = ["view_expense", "add_expense", "change_expense"]
_TREASURY_READ = ["view_cashbox", "view_cashentry", "view_cashtransfer", "view_supplierpayment",
                  "view_seller", "view_commissionrate"]
_TREASURY_ACT = ["add_cashtransfer", "add_supplierpayment"]
_RETURNS_SALE = ["view_salereturn", "add_salereturn", "change_salereturn",
                 "view_salereturnitem", "add_salereturnitem", "change_salereturnitem"]
_RETURNS_PURCHASE = ["view_purchasereturn", "add_purchasereturn", "change_purchasereturn",
                     "view_purchasereturnitem", "add_purchasereturnitem",
                     "change_purchasereturnitem"]

_PRODUCTION = [f"{a}_{m}" for m in ("rawmaterial", "bomline", "materialpurchase",
                                    "materialpurchaseitem", "productionorder",
                                    "productionoutput", "productionmaterial")
               for a in ("view", "add", "change")]
_PRODUCTION_READ = [p for p in _PRODUCTION if p.startswith("view_")]

_STOCKCOUNT = ["view_stockcount", "add_stockcount", "change_stockcount",
               "view_stockcountline", "add_stockcountline", "change_stockcountline"]
_HR_READ = ["view_employeeprofile", "view_employeetxn", "view_leaverequest", "view_mission",
            "view_missionpoint", "view_attendance"]

# مصفوفة الصلاحيات: كل دور وقائمة أكواد الصلاحيات (app trade)
ROLE_PERMISSIONS = {
    ROLE_ACCOUNTANT: _CUSTOMERS + _PAYMENTS + _DEBT_FOLLOWUP + _CUSTODY + _EXPENSES + _SALES
    + _PURCHASE + _ONLINE + ["view_activitylog"]
    + ["view_clothingmodel", "view_variant"]
    + _TREASURY_READ + _TREASURY_ACT + _RETURNS_SALE + _RETURNS_PURCHASE + _PRODUCTION_READ
    + _HR_READ + ["view_stockcount", "view_stockcountline"],

    ROLE_WAREHOUSE_MANAGER: _STOCK_READ + _STOCK_WRITE + _PURCHASE + _ONLINE
    + ["view_activitylog"] + _RETURNS_PURCHASE + _PRODUCTION + _STOCKCOUNT,

    ROLE_SALES: _STOCK_READ + _CUSTOMERS + _PAYMENTS + _SALES + _ONLINE
    + ["add_onlineorder", "view_onlineorder", "change_onlineorder"]
    + ["view_cashbox", "view_seller"],

    ROLE_WAREHOUSE_EMPLOYEE: _STOCKCOUNT + ["view_clothingmodel", "view_variant",
                              "view_purchaseinvoice", "view_onlineorder",
                              "change_onlineorder", "view_onlineorderitem"],

    # مدير الحسابات: كل صلاحيات التطبيق (بما فيها الخزينة الأساسية وسجل المراجعة)
    ROLE_ACCOUNTS_MANAGER: None,  # None = كل صلاحيات trade
}


def ensure_roles(app_config=None, using="default", **kwargs):
    """ينشئ/يحدّث مجموعات الأدوار. يتنفذ بعد كل migrate."""
    app_config = global_apps.get_app_config("trade")
    # لازم الصلاحيات تتعمل الأول (create_permissions بيتنفذ بعد الـ migrate لكل app)
    create_permissions(app_config, verbosity=0, using=using)

    all_perms = Permission.objects.filter(content_type__app_label="trade")
    for role, codenames in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)
        if codenames is None:
            perms = all_perms
        else:
            perms = all_perms.filter(codename__in=codenames)
        group.permissions.set(perms)
