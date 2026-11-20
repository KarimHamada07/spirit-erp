"""القايمة الجانبية وشريط الموبايل: مقسمة حسب الشغل، وكل رابط بيظهر بس لللي عنده صلاحيته.

بيتحقن من MobileSidebarMiddleware مكان قايمة Jazzmin الافتراضية (اللي كانت بتعرض كل الجداول
ورا بعض). عشان تضيف صفحة: زوّدها في MENU بالشكل ده:
    ("الاسم", "اسم الرابط أو admin:app.model", "fas fa-icon", "الصلاحية أو None")
والصفحات اللي نادراً ما بتتفتح تتحط في MORE، وبتظهر تحت "صفحات أكتر" (مقفولة لحد ما تدوس عليها).
الأرقام الحمرا جنب الصفحات (الحاجات المستنية) جاية من pending.py.
"""
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape

from .help_texts import MENU_TIPS

MENU = [
    ("", [
        ("لوحة التحكم", "dashboard", "fas fa-house", "dashboard"),
        ("بصمتي", "my_attendance", "fas fa-fingerprint", None),
        ("مأمورياتي", "my_missions", "fas fa-car-side", None),
        ("إجازاتي", "leaves", "fas fa-umbrella-beach", None),
        ("حسابي", "my_statement", "fas fa-id-card", "employee"),
        ("عهدتي", "my_custody", "fas fa-wallet", "custody_holder"),
    ]),
    ("التكويد", [
        ("تكويد عميل", "code_customer", "fas fa-user-plus", "trade.add_customer"),
        ("تكويد مورد", "code_supplier", "fas fa-truck-field", "trade.add_supplier"),
        ("تكويد صنف", "code_item", "fas fa-barcode", "trade.add_clothingmodel"),
        ("ملصقات الباركود", "barcode_labels", "fas fa-tags", "trade.view_clothingmodel"),
    ]),
    ("البيع", [
        ("نقطة البيع", "pos", "fas fa-cash-register", "trade.add_saleinvoice"),
        ("فواتير البيع", "admin:trade.saleinvoice", "fas fa-file-invoice-dollar", "trade.view_saleinvoice"),
        ("فواتير مستنية موافقة", "sale_approvals", "fas fa-stamp", "trade.approve_saleinvoice"),
        ("الأونلاين", "online_list", "fas fa-truck-fast", "trade.view_onlineorder"),
        ("مرتجعات العملاء", "admin:trade.salereturn", "fas fa-rotate-left", "trade.view_salereturn"),
    ]),
    ("العملاء", [
        ("العملاء والآجل", "customers", "fas fa-user-group", "trade.view_customer"),
        ("متابعة التحصيل", "debts", "fas fa-phone-volume", "trade.view_debtfollowup"),
    ]),
    ("المخزون", [
        ("المخزون", "inventory", "fas fa-boxes-stacked", "trade.view_variant"),
        ("الجرد", "stock_counts", "fas fa-clipboard-list", "trade.view_stockcount"),
        ("اقتراحات الطلب", "reorder", "fas fa-cart-flatbed", "trade.view_purchaseinvoice"),
        ("الموديلات", "admin:trade.clothingmodel", "fas fa-shirt", "trade.view_clothingmodel"),
        ("المقاسات والألوان", "admin:trade.variant", "fas fa-palette", "trade.view_variant"),
        ("فواتير التوريد", "admin:trade.purchaseinvoice", "fas fa-dolly", "trade.view_purchaseinvoice"),
        ("الموردين", "suppliers", "fas fa-industry", "trade.view_supplier"),
    ]),
    ("الموقع", [
        ("طلبات الموقع", "store_orders", "fas fa-globe", "trade.view_weborder"),
        ("منتجات الموقع", "store_products", "fas fa-images", "trade.view_productimage"),
        ("سلايدر الرئيسية", "admin:trade.heroslide", "fas fa-panorama", "trade.change_heroslide"),
        ("مصاريف الشحن", "admin:trade.shippingzone", "fas fa-map-location-dot", "trade.change_shippingzone"),
        ("إعدادات الموقع", "admin:trade.storesettings", "fas fa-sliders", "trade.change_storesettings"),
    ]),
    ("الإنتاج", [
        ("أوامر التشغيل", "admin:trade.productionorder", "fas fa-gears", "trade.view_productionorder"),
        ("الخامات", "admin:trade.rawmaterial", "fas fa-scroll", "trade.view_rawmaterial"),
    ]),
    ("الفلوس", [
        ("الخزينة", "treasury", "fas fa-vault", "trade.view_cashbox"),
        ("التقفيل اليومي", "daily_close", "fas fa-calendar-check", "trade.view_cashentry"),
        ("العهدة", "custody", "fas fa-hand-holding-usd", "trade.view_custodysettlement"),
        ("طلبات تصفية العهد", "custody_approvals", "fas fa-clipboard-check",
         "trade.approve_custodyclearance"),
        ("المصروفات", "admin:trade.expense", "fas fa-receipt", "trade.view_expense"),
        ("العمولات", "commissions", "fas fa-percent", "trade.view_cashentry"),
    ]),
    ("التقارير", [
        ("التحليلات", "analytics", "fas fa-chart-pie", "trade.view_expense"),
        ("تقرير الربحية", "profit_report", "fas fa-coins", "trade.view_expense"),
        ("ربحية التجار", "customer_profit", "fas fa-hand-holding-dollar", "trade.view_expense"),
        ("التقارير", "reports", "fas fa-file-lines", "trade.view_expense"),
    ]),
    ("الإدارة", [
        ("البائعين", "admin:trade.seller", "fas fa-id-badge", "trade.view_seller"),
        ("المستخدمين", "admin:auth.user", "fas fa-user-shield", "auth.view_user"),
        ("استيراد وتصدير Excel", "data_center", "fas fa-file-import", "trade.import_data"),
        ("الموظفين والمرتبات", "employees", "fas fa-people-group", "trade.view_employeetxn"),
        ("الحضور والانصراف", "attendance_report", "fas fa-user-clock", "trade.view_attendance"),
        ("المأموريات", "missions_report", "fas fa-route", "trade.view_mission"),
    ]),
]

# صفحات بتتفتح قليل: تحت "صفحات أكتر" عشان القايمة تبقى قصيرة
MORE = [
        ("دفعات التسديد", "admin:trade.payment", "fas fa-hand-holding-dollar", "trade.view_payment"),
        ("مرتجعات الموردين", "admin:trade.purchasereturn", "fas fa-truck-arrow-right", "trade.view_purchasereturn"),
        ("مشتريات الخامات", "admin:trade.materialpurchase", "fas fa-truck-ramp-box", "trade.view_materialpurchase"),
        ("حركات الخزن", "admin:trade.cashentry", "fas fa-list-ul", "trade.view_cashentry"),
        ("التحويلات بين الخزن", "admin:trade.cashtransfer", "fas fa-right-left", "trade.view_cashtransfer"),
        ("سدادات الموردين", "admin:trade.supplierpayment", "fas fa-money-check-dollar", "trade.view_supplierpayment"),
        ("نسب العمولة", "admin:trade.commissionrate", "fas fa-sliders", "trade.view_commissionrate"),
        ("الخزن", "admin:trade.cashbox", "fas fa-wallet", "trade.view_cashbox"),
        ("الأدوار والصلاحيات", "admin:auth.group", "fas fa-users-gear", "auth.view_group"),
        ("سجل المراجعة", "audit", "fas fa-clock-rotate-left", "trade.view_auditlog"),
        ("سجل النشاط وتليجرام", "activity", "fas fa-bell", "superuser"),
]

# شريط الموبايل اللي تحت: أهم 4 صفحات + زرار القايمة
BOTTOM = [
    ("الرئيسية", "dashboard", "fas fa-house", "dashboard"),
    ("بصمتي", "my_attendance", "fas fa-fingerprint", None),
    ("بيع", "pos", "fas fa-cash-register", "trade.add_saleinvoice"),
    ("العملاء", "customers", "fas fa-user-group", "trade.view_customer"),
    ("المخزون", "inventory", "fas fa-boxes-stacked", "trade.view_variant"),
]


def _url(target):
    try:
        if target.startswith("admin:"):
            app, model = target[6:].split(".")
            return reverse(f"admin:{app}_{model}_changelist")
        return reverse(target)
    except NoReverseMatch:
        return None


def _allowed(user, perm):
    if perm is None:
        return True
    if perm == "superuser":
        return user.is_superuser
    if perm == "dashboard":  # لوحة التحكم لأي حد ليه شغل يتعرض فيها (مش البصمة بس)
        from .views import can_see_dashboard
        return can_see_dashboard(user)
    if perm == "employee":  # الموظف اللي ليه بيانات مرتب يشوف كشف حسابه
        return getattr(user, "employee", None) is not None
    if perm == "custody_holder":  # الشخص اللي عليه عهدة مربوطة بحسابه
        return getattr(user, "custody_holder", None) is not None
    return user.has_perm(perm)


def _items(user, items):
    out = []
    for name, target, icon, perm in items:
        if not _allowed(user, perm):
            continue
        url = _url(target)
        if url:
            out.append((name, url, icon))
    return out


def _active_url(path, urls):
    """أطول رابط بيبدأ بيه المسار الحالي (عشان /customers/debts/ ما يعلّمش على /customers/)"""
    best = None
    for u in urls:
        if u != "/" and path.startswith(u) and (best is None or len(u) > len(best)):
            best = u
    return best


def _link(name, url, icon, active, count=0):
    on = " active" if url == active else ""
    cur = ' aria-current="page"' if on else ""
    tip = MENU_TIPS.get(name, "")
    badge = (f' <span class="erp-count" aria-label="{count} مستني">{count}</span>' if count else "")
    return (f'<li class="nav-item"><a href="{escape(url)}" class="nav-link{on}"{cur}'
            f' data-tip="{escape(tip)}" data-tip-side="left">'
            f'<i class="nav-icon {icon}" aria-hidden="true"></i> <p>{escape(name)}{badge}</p></a></li>')


def render_sidebar(request):
    from .pending import badge_counts
    user = request.user
    counts = badge_counts(user)
    groups = []
    for title, items in MENU:
        rows = [(n, u, i, counts.get(t, 0)) for n, t, i, p in items
                if _allowed(user, p) and (u := _url(t))]
        if rows:
            groups.append((title, rows))
    more = [(n, u, i, counts.get(t, 0)) for n, t, i, p in MORE if _allowed(user, p) and (u := _url(t))]
    active = _active_url(request.path, [r[1] for _t, rows in groups for r in rows] + [r[1] for r in more])
    html = ['<ul class="nav sidebar-menu flex-column nav-flat" role="navigation" '
            'aria-label="القايمة الرئيسية" id="jazzy-navigation">',
            # المثبتة: بيملاها ui.js من اختيار المستخدم (النجمة جنب كل صفحة)
            '<li class="nav-header erp-nav-header erp-pins-head" hidden>المثبتة عندك</li>'
            '<li class="erp-pins" hidden><ul class="nav flex-column"></ul></li>']
    for title, rows in groups:
        if title:
            html.append(f'<li class="nav-header erp-nav-header">{escape(title)}</li>')
        html.extend(_link(n, u, i, active, c) for n, u, i, c in rows)
    if more:
        is_open = any(u == active for _n, u, _i, _c in more)
        html.append(f'<li class="nav-item erp-more{" open" if is_open else ""}">'
                    f'<button type="button" class="nav-link erp-more-btn" aria-expanded="{"true" if is_open else "false"}"'
                    ' data-tip="صفحات بتتفتح قليل: حركات الخزن، الصلاحيات، السجلات..." data-tip-side="left">'
                    '<i class="nav-icon fas fa-ellipsis" aria-hidden="true"></i> <p>صفحات أكتر'
                    '<i class="fas fa-chevron-down erp-more-arrow" aria-hidden="true"></i></p></button>'
                    '<ul class="nav flex-column erp-more-list">')
        html.extend(_link(n, u, i, active, c) for n, u, i, c in more)
        html.append("</ul></li>")
    html.append("</ul>")
    return "".join(html)


# زرار "+" العائم على الموبايل: أكتر حاجات بتتعمل في اليوم، بضغطة من أي صفحة
QUICK = [
    ("بيع جديد", "pos", "fas fa-cash-register", "trade.add_saleinvoice"),
    ("تحصيل من عميل", "quick_payment", "fas fa-hand-holding-dollar", "trade.add_payment"),
    ("مصروف", "quick_expense", "fas fa-receipt", "trade.add_expense"),
    ("أوردر أونلاين", "online_new", "fas fa-truck-fast", "trade.add_onlineorder"),
    ("بصمة", "my_attendance", "fas fa-fingerprint", None),
]


def render_fab(request):
    items = _items(request.user, QUICK)
    if len(items) < 2:
        return ""
    html = ['<button type="button" id="erp-fab" aria-label="إضافة سريعة" aria-expanded="false" '
            'data-tip="إضافة سريعة: بيع، تحصيل، مصروف، بصمة"><i class="fas fa-plus" aria-hidden="true"></i></button>'
            '<div id="erp-fab-sheet" role="menu" aria-label="إضافة سريعة">']
    for name, url, icon in items:
        html.append(f'<a role="menuitem" href="{escape(url)}"><i class="{icon}" aria-hidden="true"></i>'
                    f'<span>{escape(name)}</span></a>')
    html.append("</div>")
    return "".join(html)


def render_bottom_bar(request):
    if request.path.startswith("/pos/"):  # شاشة البيع ليها شريطها الخاص
        return ""
    items = _items(request.user, BOTTOM)
    active = _active_url(request.path, [u for _n, u, _i in items])
    html = ['<nav id="erp-bottom" aria-label="تنقل سريع">']
    for name, url, icon in items:
        on = ' class="on" aria-current="page"' if url == active else ""
        html.append(f'<a href="{escape(url)}"{on} data-tip="{escape(MENU_TIPS.get(name, name))}">'
                    f'<i class="{icon}" aria-hidden="true"></i>'
                    f'<span>{escape(name)}</span></a>')
    from .pending import pending_items
    total = sum(i["count"] for i in pending_items(request.user))
    dot = f'<span class="erp-count">{total}</span>' if total else ""
    html.append('<button type="button" id="erp-bottom-menu" aria-label="القايمة" data-tip="كل صفحات السيستم">'
                f'<i class="fas fa-bars" aria-hidden="true"></i>{dot}<span>القايمة</span></button></nav>')
    html.append(render_fab(request))
    return "".join(html)
