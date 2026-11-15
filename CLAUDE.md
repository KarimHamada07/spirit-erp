# SPIRIT ERP

نظام Django 5.2 لمحل/مصنع ملابس (بيع، مخزون، خزينة، أونلاين، إنتاج). الواجهة عربي (RTL) فوق
django-jazzmin، والمستخدمين بيكتبوا بالعامية المصري — الرسايل والتعليقات في الكود بالعامية كمان.
بيترفع على PythonAnywhere (SQLite) — شوف `DEPLOY.md`.

## التشغيل والاختبارات
```bash
pip install -r requirements.txt
DJANGO_DEBUG=1 python manage.py migrate
DJANGO_DEBUG=1 python manage.py runserver
DJANGO_SECRET_KEY=x python manage.py test trade      # الاختبارات بإعدادات الإنتاج (من غير تحويل https)
```
- من غير `DJANGO_DEBUG=1` لازم `DJANGO_SECRET_KEY`، وإلا الإعدادات بترفض تشتغل.
- `settings.TESTING` بيلغي `SECURE_SSL_REDIRECT` وقت `manage.py test` بس.

## خريطة الكود (`trade/`)
- `models.py` — كل الموديلات. القواعد المهمة جوه الموديل نفسه: `confirm()` للفواتير والمرتجعات
  ومشتريات الخامات، `ProductionOrder.complete()`، `OnlineOrder.set_status()`. كلها `transaction.atomic`
  وبتستخدم `select_for_update` على المخزون.
- المخزون: `Variant.quantity` و `avg_cost` (متوسط مرجّح عن طريق `add_stock`) — مش editable، بيتغيروا بس
  من العمليات. نفس الفكرة في `RawMaterial`.
- `treasury.py` — كل حركة فلوس بتعدي من هنا وبتتسجل `CashEntry` (موجب داخل / سالب خارج).
- مديونية العميل: `Customer.objects.with_balance()` (subqueries). المرتجع على الحساب بيتخصم بـ
  `SaleReturnItem.unit_refund` (سعر البيع بعد نصيبه من خصم الفاتورة، بيتثبت وقت التأكيد).
- `debts.py` + `debt_views.py` — أعمار المديونيات (FIFO) ومتابعة التحصيل للمحاسب (`DebtFollowUp`).
- `profit.py` (تقرير الربحية)، `analytics_views.py` (رسوم)، `report_views.py` (تقارير + Excel).
- `notify.py` — `log_event(text, audience="")` بيسجل `ActivityLog` وبيبعته تليجرام (مباشر أو عن طريق
  الناقل الخارجي اللي بيسحب `/activity/feed/`). `audience="accountant"` بيروح لشات المحاسب.
- `signals.py` — أحداث تليجرام (تأكيد فواتير، دفعات، مخزون منخفض حسب `ClothingModel.low_stock_level`).
- `audit.py` — سجل المراجعة (قبل/بعد) للموديلات في `TRACKED`.
- `roles.py` — مصفوفة صلاحيات الأدوار (مجموعات Django بتتحدث بعد كل migrate). أي موديل جديد لازم
  صلاحياته تتضاف هنا للدور المناسب.
- `middleware.py` — بيحقن الثيم (CSS/JS) ووسوم الـ PWA في صفحات القالب، وبيبدّل قايمة Jazzmin بقايمتنا.
- `menu.py` — القايمة الجانبية المقسمة (البيع، العملاء، المخزون...) وشريط الموبايل اللي تحت. أي صفحة جديدة
  تتضاف في `MENU` بصلاحيتها.
- `help_texts.py` — شرح كل صفحة (`PAGE_HELP` بالـ url name، و`MODEL_HELP` لجداول لوحة الإدارة) بيظهر في صندوق
  "الصفحة دي بتعمل إيه؟"، و`MENU_TIPS` تلميحات القايمة. أي صفحة أو جدول جديد لازم يتضافله شرح (فيه اختبار).
- `static/trade/js/ui.js` + `static/trade/css/ui.css` — التلميحات لكل زرار (data-tip ← title ← قاموس بالنص
  ← الأيقونة)، والحركات (ظهور، عدّاد الأرقام، ripple، شريط تحميل)، والشكل المودرن. بيتحمّلوا من الـ middleware
  برقم إصدار. لأي زرار جديد مهم: حط `data-tip="..."` بالشرح.
- `pwa_views.py` — manifest و service worker (الصفحات دايماً من السيرفر؛ الكاش للملفات الثابتة بس).
- `statements.py` — كشوف حساب العملاء والموردين (PDF و Excel، بفترة). الرصيد الافتتاحي
  `opening_balance` على العميل والمورد داخل في الأرصدة والكشوف وأعمار المديونيات.
- `data_io.py` + `data_views.py` — استيراد/تصدير Excel (فورم ← رفع ← معاينة ← تأكيد). كل نوع `Spec`؛
  الاستيراد ذري، وبيكتم رسايل تليجرام (`notify.muted`) ويبعت رسالة ملخص واحدة. محتاج صلاحية
  `trade.import_data`.
- العهدة: `custody_views.py`. العهدة العامة (`custody_summary`) = المقبوضات − المصروفات − اللي اتسلّم.
  التسليم بيتسجل على `CustodyHolder` (موظف بيتختار بحسابه، أو اسم بيتكتب من بره؛ `resolve_holder`
  بيعمله لوحده أول مرة)، ورصيده = اللي استلمه − تصفيات
  `CustodyClearance` المعتمدة. التصفية بتتطلب من المحاسب أو صاحب العهدة نفسه، وبتتحسب بس بموافقة
  `trade.approve_custodyclearance` (مدير الحسابات)، والصرف المعتمد بيعمل `Expense` مربوط بيها.
- البصمة: `attendance.py` + `attendance_views.py`. صورة لايف من الكاميرا (getUserMedia، مفيش رفع ملف)
  مع كود مؤقت في الـ session (مرة واحدة، 3 دقايق)، والسيرفر بيتأكد من وجود وش بـ OpenCV
  (`opencv-python-headless`)، واللوكيشن بيتقارن بأماكن الشغل `Workplace`. الموظف اللي مالوش صلاحية
  `view_saleinvoice` بيتحوّل من لوحة التحكم لـ "بصمتي". التقرير بصلاحية `view_attendance`.
- أوامر: `backup_db`، `debt_reminders`، `stock_alerts`، `telegram_test`.

## اتفاقيات
- اللغة عربي بس (`LANGUAGES = [("ar", ...)]`) مهما كانت لغة الموبايل. الكلام اللي Jazzmin/Django ما ترجموهوش
  في `locale/ar/LC_MESSAGES/django.po` و `djangojs.po`؛ بعد التعديل `python manage.py compile_ar` (بيعمل .mo من
  غير gettext) وارفع ملفات .mo. رسايل التحقق وزرار اختيار الملف بيتعرّبوا من JS في `middleware.py`.
- الصفحات المخصصة بتورث `admin/base.html` وبتمرر `admin.site.each_context(request)`، ومحمية بـ
  `staff_member_required` + `has_perm`. روابطها في القايمة من `trade/menu.py`.
- قوالب الصفحات بتحط `<div class="row">` جوه صف القالب على طول؛ `spirit.css` فيه إصلاح للحالة دي.
- التاريخ: استخدم `timezone.localdate()` مش `date.today()` (السيرفر UTC والنظام بتوقيت القاهرة).
- الألوان: أحمر `#fe0000` وأسود، ومتغيرات `--erp-*` للوضع الداكن. Chart.js محلي في
  `static/trade/vendor/` (مفيش CDN).
- أي تعديل في موديل → `makemigrations`، ولو بيغير أرقام قديمة فكّر في data migration تحافظ على التاريخ.
