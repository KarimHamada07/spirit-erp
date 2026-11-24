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
- رصيد المورد (`Supplier.balance`) بالإشارة: موجب = ليه عندنا، سالب = لينا عنده (دفعة مقدمة تحت الحساب).
  `SupplierPayment.kind`: `pay` (سداد، ينفع أكتر من المستحق) أو `refund` (استرداد، مش أكتر من اللي لينا).
- التكويد: `coding_views.py` (عميل/مورد/صنف). العميل: محل، موبايلات (`phone` + `other_phones`)، محافظة ومنطقة،
  `customer_type` (نقدي/آجل/دفعات)، `credit_limit`. المورد: نوع وتصنيفات (`categories` أكواد + other).
  الصنف: مقاسات السيري × الألوان بتعمل الـ Variants، و`series_size` = عدد المقاسات. أسعار البيع بتيجي من
  `PurchaseItem.sale_price/wholesale_price` وقت تأكيد فاتورة التوريد (والصنف اللي سعره صفر لازم يتحددله سعر).
- `debts.py` + `debt_views.py` — أعمار المديونيات (FIFO) ومتابعة التحصيل للمحاسب (`DebtFollowUp`).
- `profit.py` (تقرير الربحية)، `analytics_views.py` (رسوم)، `report_views.py` (تقارير + Excel).
- `notify.py` — `log_event(text, audience="")` بيسجل `ActivityLog` وبيبعته تليجرام (مباشر أو عن طريق
  الناقل الخارجي اللي بيسحب `/activity/feed/`). `audience="accountant"` بيروح لشات المحاسب.
- `signals.py` — أحداث تليجرام (تأكيد فواتير، دفعات، مخزون منخفض حسب `ClothingModel.low_stock_level`).
- `audit.py` — سجل المراجعة (قبل/بعد ومين عملها باليوزر) للموديلات في `TRACKED`؛ `AUDIT_ONLY` بيتسجلوا من غير
  رسالة تليجرام إضافية. أي موديل جديد مهم ضيفه هنا. أكشن التأكيد في لوحة الإدارة لازم `allowed_permissions = ("change",)`.
- `roles.py` — مصفوفة صلاحيات الأدوار (مجموعات Django بتتحدث بعد كل migrate). أي موديل جديد لازم
  صلاحياته تتضاف هنا للدور المناسب.
- `middleware.py` — بيحقن الثيم (CSS/JS) ووسوم الـ PWA في صفحات القالب، وبيبدّل قايمة Jazzmin بقايمتنا.
- `menu.py` — القايمة الجانبية المقسمة (البيع، العملاء، المخزون...) وشريط الموبايل اللي تحت. أي صفحة جديدة
  تتضاف في `MENU` بصلاحيتها (والصفحات اللي بتتفتح قليل في `MORE` تحت "صفحات أكتر"). `QUICK` = زرار "+" العائم
  على الموبايل. التثبيت (النجمة) في `ui.js` ومتخزن في localStorage.
- `pending.py` — الحاجات المستنية حد يتصرف فيها حسب الصلاحية (إجازات، تصفية عهد، متابعة تحصيل، أونلاين،
  توريد، جرد): أرقام حمرا في القايمة + كارت "محتاج تتصرف فيه" في لوحة التحكم. المفتاح = target الصفحة في `MENU`.
- لوحة التحكم (`views.dashboard`) حسب الصلاحيات (`dashboard_sections`): الربح والمصروفات لـ `view_expense` بس،
  والبياع المربوط بحسابه (`Seller.user`) بيشوف مبيعاته وعمولته، ونقطة البيع بتختاره لوحدها.
- `quick_views.py` — فورم التحصيل السريع والمصروف (بدل لوحة الإدارة). منطق التحصيل في `customer_views.take_payment`.
  كشف حساب العميل ليه رابط موقّع `/s/<token>/` (30 يوم) بيتبعت واتساب من صفحة العميل.
- الموقع (المتجر) على `spiritegy.org`: نفس المشروع ونفس القاعدة. `store_views.StoreHostMiddleware` بيخلي أي
  دومين في `settings.STORE_HOSTS` يستخدم `config/store_urls.py` (المتجر بس). على دومين السيستم المتجر للمعاينة على
  `/shop/` (namespace `store`). القوالب في `templates/trade/store/` (مش admin) والشكل `static/trade/store/store.css`.
  الطلب `WebOrder` محتاج تأكيد من `store_admin_views` (طلبات الموقع) ← `WebOrder.confirm()` بيعمل `OnlineOrder`
  ويخصم المخزون. الموديل بيظهر لو `show_online` وليه مخزون. الصور `ProductImage` بتتصغر وقت الرفع (`store_media.py`)
  وبتتحفظ في `MEDIA_ROOT` (`media/`). مصاريف الشحن `ShippingZone`، والإعدادات `StoreSettings.get()`.
  في الاختبارات استخدم `reverse(..., urlconf="config.store_urls")` مع `HTTP_HOST="spiritegy.org"`.
  الشكل داكن مودرن بحركة: `static/trade/store/store.js` (ظهور العناصر `.rv`، سلايدر `HeroSlide`، سلة جانبية
  بـ fetch على `cart_add` مع هيدر `X-Requested-With: fetch` و`cart_drawer`، معاينة سريعة `quick`، المفضلة وآخر
  المشاهدات في localStorage وبيتعرضوا من `cards?codes=`). دليل المقاسات من `StoreSettings.size_guide`.
- `help_texts.py` — شرح كل صفحة (`PAGE_HELP` بالـ url name، و`MODEL_HELP` لجداول لوحة الإدارة) بيظهر في صندوق
  "الصفحة دي بتعمل إيه؟"، و`MENU_TIPS` تلميحات القايمة. أي صفحة أو جدول جديد لازم يتضافله شرح (فيه اختبار).
- `static/trade/js/ui.js` + `static/trade/css/ui.css` — التلميحات لكل زرار (data-tip ← title ← قاموس بالنص
  ← الأيقونة)، والحركات (ظهور، عدّاد الأرقام، ripple، شريط تحميل)، والشكل المودرن. بيتحمّلوا من الـ middleware
  برقم إصدار. لأي زرار جديد مهم: حط `data-tip="..."` بالشرح.
- `security.py` — قفل الدخول (5 غلط لنفس اليوزر/الجهاز أو 20 للجهاز = 15 دقيقة، `admin.site.login_form`)، و`safe_back`
  للرجوع بالـ Referer (نفس الموقع بس)، و`hit()` لتحديد عدد الطلبات من الموقع. الكاش ملفات في `cache/`.
  كل صفحة فيها بيانات لازم `has_perm` (مش `staff_member_required` لوحده، لأن كل الموظفين staff عشان البصمة).
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
  `trade.approve_custodyclearance` (المحاسب ومدير الحسابات، ومحدش يوافق على طلبه)، والصرف المعتمد بيعمل `Expense` مربوط بيها.
- البصمة: `attendance.py` + `attendance_views.py`. صورة لايف من الكاميرا (getUserMedia، مفيش رفع ملف)
  مع كود مؤقت في الـ session (مرة واحدة، 3 دقايق)، والسيرفر بيتأكد من وجود وش بـ OpenCV
  (`opencv-python-headless`)، واللوكيشن بيتقارن بأماكن الشغل `Workplace`. الموظف اللي مالوش صلاحية
  `view_saleinvoice` بيتحوّل من لوحة التحكم لـ "بصمتي". التقرير بصلاحية `view_attendance`.
- الموظفين: `payroll.py` (المرتب من البصمة والإجازات: اليوم = المرتب ÷ 30، والتأخير/الأوفر تايم بالدقيقة) +
  `employee_views.py` (كشف حساب `EmployeeTxn`، الإجازات `LeaveRequest` بموافقة المدير المباشر `EmployeeProfile.manager` أو
  `approve_leaverequest`؛ `employee_views.leave_approver_qs`). اعتماد المرتبات `add_employeetxn` (مدير الحسابات/الأدمن).
  `EmployeeProfile.start_date`: اللي قبله مش غياب.
- المأموريات: `mission_views.py` (بداية/وصول/نهاية باللوكيشن، وخط السير على خريطة جوجل).
- إذن الصرف: `dispatch_views.py` (فواتير البيع المؤكدة في يوم ← ورقة للمخزن بالأصناف من غير أسعار؛ مش بيحرّك المخزون).
  مرتجع العميل في لوحة الإدارة بينبّه لو فاتورة البيع عليها خصم (`admin.invoice_discount_note`).
- الجرد واقتراحات الطلب: `stock_count_views.py` (`StockCount.apply` بصلاحية `apply_stockcount`).
- المبيعات: `SaleInvoice.channel` (قطاعي/جملة/تسويق/أون لاين؛ الجملة بسعر الجملة والباقي قطاعي)، `delivery`،
  `payment_plan` (خزن + "account" = من الحساب، و`account_amount`). حد ائتمان العميل أو تاريخ غير النهارده (من غير
  `change_invoice_date`) = `approval="pending"` ومش بتتأكد غير من `sale_approvals` (`approve_saleinvoice`).
  المرتجع `pos_return`: بالعميل والصنف (FIFO على بنود البيع، مرتجع لكل فاتورة بنفس `group`) أو برقم الفاتورة،
  ورد الفلوس سطور خزن و/أو حساب (`SaleReturn.account_amount` هو اللي بيقلل المديونية). السيري للجملة بس
  (`ClothingModel.series_sizes`، المقاس ممكن يتكرر). كشف العميل فيه `summary` (فواتير/مرتجعات/دفعات).
- فاتورة البيع في لوحة الإدارة: `sale_admin.py` (البنود بكود الصنف ← الاسم/التصنيف لوحدهم، اللون والمقاس اختيار، الكمية
  مش أكتر من الرصيد، والسعر مش أقل من `price_for(channel)`؛ البائع من العميل؛ الربحية لـ `approve_saleinvoice` بس؛
  الجافاسكربت في `templates/admin/trade/saleinvoice/change_form.html`، والـ inline prefix = `items`).
  "نوع الفاتورة" (`pay_method`: نقدي/محفظة/إنستاباي/تحويل/آجل/تسوية) + "العهدة" (`pay_box` = خزنة من نفس النوع،
  مش بتظهر في الطباعة) بيتحفظوا في `payment_plan`؛ التسوية = `account` من رصيد العميل. أكشن `confirm_sales` بيخصم
  المخزن ويسجل الفلوس في الخزنة (`record_sale_payments`). العهد الافتراضية `treasury.CUSTODY_BOXES`.
- نقطة البيع: سعر الجملة `Variant.wholesale_price` حسب القناة، وتعديل السعر بصلاحية `change_sale_price` بس
  (السيرفر بيتحقق). المرتجع من نفس الشاشة برقم الفاتورة (`pos_return_lookup` / `pos_return`).
  الفاتورة ليها رابط موقّع للعميل `/i/<token>/` بيتبعت واتساب.
- الباركود: `barcodes.py`. كل `Variant.barcode` (فاضي = `20`+رقمه 6 خانات، أو باركود المصنع من تكويد الصنف)، وفاتورة
  البيع `31`+رقمها، وأوردر الأونلاين `32`+رقمه (أرقام بس عشان الكيبورد العربي). الملصقات `labels.py` + `barcode_views.barcode_labels`
  (من الموديل أو فاتورة التوريد). `ui.js` بيلقط الإسكانر بره الخانات ويبعت `erp-scan` (نقطة البيع بتمسكه: قطعة في القطاعي،
  سيري في الجملة) وإلا بيفتح `/scan/?c=`. الوسم `{% barcode %}` في `templatetags/barcode_tags.py`.
  بوالص الأونلاين `online_views.online_print` (3 في ورقة A4).
- تكلفة القطعة `ClothingModel.unit_cost` من تكويد الصنف: `Variant.cost_basis` = `avg_cost` ولو صفر = `unit_cost` (بتتثبت
  وقت البيع). صفحة الصنف بتعرض المكسب قطاعي/جملة/سيري (`coding_views.item_margins`). ربحية التجار
  `profit.customer_profitability` + `report_views.customer_profit` (`view_expense`).
- الهولد (`holds.py`): أوردر الأونلاين بيخصم من `Variant.quantity` (المتاح) أول ما يتسجل، والقطع اللي في أوردرات
  متجهزة/اتشحنت بتظهر "هولد في الشحن" في المخزون ونقطة البيع وتكويد الصنف؛ التحصيل بيخرّجها، والمرتجع بيرجعها للمتاح.
- الأونلاين: الربح بيتحسب بـ `OnlineOrder.status_at` (يوم التحصيل/المرتجع)، مش `updated_at`.
- أوامر: `nightly` (3 الفجر: نسخة + ملخص + النسخة لتليجرام)، `backup_db`، `debt_reminders`، `stock_alerts`،
  `telegram_test`.

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
