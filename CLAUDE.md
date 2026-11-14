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
- `middleware.py` — بيحقن الثيم (CSS/JS) ووسوم الـ PWA في صفحات القالب.
- `pwa_views.py` — manifest و service worker (الصفحات دايماً من السيرفر؛ الكاش للملفات الثابتة بس).
- أوامر: `backup_db`، `debt_reminders`، `stock_alerts`، `telegram_test`.

## اتفاقيات
- الصفحات المخصصة بتورث `admin/base.html` وبتمرر `admin.site.each_context(request)`، ومحمية بـ
  `staff_member_required` + `has_perm`. روابطها في القايمة من `JAZZMIN_SETTINGS["custom_links"]`.
- التاريخ: استخدم `timezone.localdate()` مش `date.today()` (السيرفر UTC والنظام بتوقيت القاهرة).
- الألوان: أحمر `#fe0000` وأسود، ومتغيرات `--erp-*` للوضع الداكن. Chart.js محلي في
  `static/trade/vendor/` (مفيش CDN).
- أي تعديل في موديل → `makemigrations`، ولو بيغير أرقام قديمة فكّر في data migration تحافظ على التاريخ.
