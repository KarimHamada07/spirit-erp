# SPIRIT — خطوات الرفع على PythonAnywhere

اسم الحساب: spiritegy
الدومين: erp.spiritegy.org

## 1) رفع الملف وفك الضغط
1. Files > Upload a file > ارفع `spirit_final.zip`
2. Consoles > Bash واكتب:
   unzip spirit_final.zip
   ده هيعمل مجلد `erp_spirit` في `/home/spiritegy/`

## 2) البيئة الافتراضية
   mkvirtualenv spirit --python=python3.12
   pip install -r ~/erp_spirit/requirements.txt

## 3) المفتاح السري
   python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
احفظ الناتج، وهتحطه في ملف WSGI في الخطوة 5.

## 4) قاعدة البيانات والملفات الثابتة
   cd ~/erp_spirit
   DJANGO_SECRET_KEY=المفتاح python manage.py migrate
   DJANGO_SECRET_KEY=المفتاح python manage.py collectstatic --noinput
   DJANGO_SECRET_KEY=المفتاح python manage.py createsuperuser

## 5) إعداد Web app
Web > Add a new web app > Manual configuration > Python 3.12
- Source code: `/home/spiritegy/erp_spirit`
- Virtualenv: `/home/spiritegy/.virtualenvs/spirit`
- WSGI configuration file: افتحه وامسح محتواه، وحط ده (وغيّر المفتاح):

```python
import os, sys
path = '/home/spiritegy/erp_spirit'
if path not in sys.path:
    sys.path.insert(0, path)
os.environ['DJANGO_SECRET_KEY'] = 'PUT_KEY_HERE'
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

- Static files: URL `/static/` → Directory `/home/spiritegy/erp_spirit/staticfiles`

## 6) الدومين
- في Web > Custom domain اكتب `erp.spiritegy.org`
- في Namecheap > Advanced DNS أضف record:
  - Type: CNAME
  - Host: erp
  - Value: spiritegy.pythonanywhere.com
  - (بدون Proxy، ده Namecheap مش Cloudflare)

## 7) التشغيل
Web > Reload. وبعد دقايق افتح https://erp.spiritegy.org/admin/ وسجل دخول بالسوبر يوزر.

## ملاحظات
- ملف `db.sqlite3` مش في الـ ZIP، فالقاعدة بتبدأ فاضية. البيانات والأرصدة الافتتاحية (العملاء، الموردين،
  المخزون، الخامات، المصروفات) تتدخل مرة واحدة من صفحة "استيراد وتصدير Excel".
- المستخدمين (البائعين والمحاسب والسيلز) بيتعملوا من لوحة الإدارة، وبيتضافوا للمجموعات.
- النسخ الاحتياطي والتذكيرات: الخطوة 8 تحت.

## 8) التحديث لنسخة جديدة من الكود
   cd ~/erp_spirit
   DJANGO_SECRET_KEY=المفتاح python manage.py backup_db      # نسخة قبل أي تحديث
   pip install -r requirements.txt     # لو اتضافت مكتبة جديدة (زي OpenCV للبصمة)
   DJANGO_SECRET_KEY=المفتاح python manage.py migrate
   DJANGO_SECRET_KEY=المفتاح python manage.py collectstatic --noinput
   وبعدين Web > Reload.

## 9-أ) المهمة الأساسية كل ليلة الساعة 3 الفجر (الملخص + النسخة الاحتياطية)
في Tasks > Scheduled tasks اعمل مهمة يومية الساعة **01:00 UTC** (= 3 الفجر في مصر شتاءً، و4 صيفاً):

    cd ~/erp_spirit && DJANGO_SECRET_KEY=$(grep -ohP "DJANGO_SECRET_KEY'\] = '\K[^']+" /var/www/*_wsgi.py | head -1) ~/.virtualenvs/spirit/bin/python manage.py nightly

بتعمل نسخة احتياطية، وبتبعت ملخص اليوم اللي فات على تليجرام (المبيعات، الربح، الخزن، النواقص، الحضور،
الطلبات المستنية). عشان النسخة نفسها تتبعتلك كملف: زوّد في `~/.telegram_bot` السطر
`BACKUP_CHAT_ID=رقم_الشات_الخاص_بيك` (شات خاص بيك بس، مش الجروب، لأن النسخة فيها كل البيانات).
المهمة دي بتغني عن مهمة backup_db اللي تحت.

## 9) المهام اليومية (Tasks > Scheduled tasks)
اعمل 3 مهام يومية (الوقت على PythonAnywhere بتوقيت UTC، يعني 22:00 UTC = 12 بالليل في مصر):

| الوقت (UTC) | الأمر |
|---|---|
| 22:00 | `cd ~/erp_spirit && DJANGO_SECRET_KEY=المفتاح ~/.virtualenvs/spirit/bin/python manage.py backup_db --keep 14` |
| 06:00 | `cd ~/erp_spirit && DJANGO_SECRET_KEY=المفتاح ~/.virtualenvs/spirit/bin/python manage.py debt_reminders --days 30` |
| 06:05 | `cd ~/erp_spirit && DJANGO_SECRET_KEY=المفتاح ~/.virtualenvs/spirit/bin/python manage.py stock_alerts` |

- **النسخ الاحتياطي**: بيتحفظ في `~/erp_spirit/backups/` (آخر 14 نسخة). كل نسخة فيها قاعدة البيانات
  وصور إثبات العهدة. نزّل نسخة على جهازك كل أسبوع من Files عشان لو الحساب نفسه حصله حاجة.
  - **الاسترجاع**: `tar xzf spirit-backup-XXXX.tar.gz` وبعدين انسخ `db.sqlite3` و `private_media` مكان
    القديمين (بعد ما تاخد نسخة من الحالي) واعمل Reload.
- **تذكير التحصيل**: بيروح للمحاسب بس (مش للعملاء). لو عايزه يروح لشات المحاسب لوحده، زوّد في
  `~/.telegram_bot` السطر `ACCOUNTANT_CHAT_ID=رقم_الشات` (أو متغير البيئة `TELEGRAM_ACCOUNTANT_CHAT_ID`).
  في وضع الناقل (الحساب المجاني) الرسالة بتوصل للناقل ومعاها `chat_id` الخاص بالمحاسب.
- **ملخص المخزون**: الأصناف اللي وصلت لحد التنبيه بتاع موديلها (بيتظبط من صفحة الموديل، الافتراضي 5)
  والخامات اللي قربت تخلص.

## 10) أمان
- HSTS متفعّل لمدة شهر (`DJANGO_HSTS_SECONDS` لو عايز تغيّرها). اتأكد إن الموقع شغال https قبل الرفع.

## 11) الموقع (المتجر) على spiritegy.org
الموقع نفس الكود ونفس قاعدة البيانات بتوع السيستم. الفرق إن أي طلب جاي على `spiritegy.org` أو
`www.spiritegy.org` بيفتح صفحات المتجر بس (من غير لوحة الإدارة)، والدومينات دي متحددة في
`STORE_HOSTS` في الإعدادات. قبل ما الدومين يشتغل، تقدر تعاين المتجر من السيستم على
`https://erp.spiritegy.org/shop/`.

### أ) Web app تاني على PythonAnywhere
كل دومين على PythonAnywhere محتاج web app لوحده. لو باقتك فيها web app واحد بس، زوّد العدد من صفحة
**Account** (الباقة المخصصة بتسمح بأكتر من web app).
1. Web > Add a new web app > اكتب الدومين `www.spiritegy.org` > Manual configuration > Python 3.12
2. Source code: `/home/spiritegy/erp_spirit` — Virtualenv: `/home/spiritegy/.virtualenvs/spirit`
3. ملف WSGI: نفس محتوى ملف WSGI بتاع السيستم بالظبط (نفس المفتاح السري).
4. Static files (في الـ web app الجديد):
   - `/static/` → `/home/spiritegy/erp_spirit/staticfiles`
   - `/media/` → `/home/spiritegy/erp_spirit/media`
5. وزوّد كمان في web app السيستم (`erp.spiritegy.org`) نفس سطر `/media/` عشان الصور تظهر في السيستم.
6. في الـ web app الجديد: فعّل **HTTPS** (Let's Encrypt) و **Force HTTPS**.

### ب) الدومين على Namecheap (Advanced DNS)
- CNAME: Host `www` → القيمة اللي PythonAnywhere بيكتبها في تبويب Web للدومين الجديد
  (بتبقى زي `webapp-123456.pythonanywhere.com`).
- الدومين من غير www (`spiritegy.org`): من Namecheap اعمل **URL Redirect Record**: Host `@` →
  `https://www.spiritegy.org` (Permanent 301). PythonAnywhere ما بيشتغلش على الدومين من غير www مباشرة.

### مهم: سطر المفتاح السري بعد ما يبقى فيه web app تاني
بعد الـ web app التاني بيبقى فيه ملفين WSGI، فأي أمر بيجيب المفتاح من `/var/www/*_wsgi.py` لازم يبقى كده
(`-h` و `head -1`)، وإلا هيطلع اسم الملف مع المفتاح:

    export DJANGO_SECRET_KEY=$(grep -ohP "DJANGO_SECRET_KEY'\] = '\K[^']+" /var/www/*_wsgi.py | head -1)

ونفس الكلام جوه `~/nightly.sh`.

### ج) بعد كل تحديث
`git pull` و `migrate` و `collectstatic` مرة واحدة، وبعدين **Reload للاتنين** (السيستم والموقع).

### د) التشغيل أول مرة
- من السيستم: **إعدادات الموقع** (واتساب، الإعلان، الصورة الرئيسية، البائع اللي الطلبات بتتسجل عليه).
- **مصاريف الشحن**: المحافظات متسجلة بأسعار مبدئية، راجعها.
- **منتجات الموقع**: لكل موديل ارفع الصور واكتب الوصف وعلّم "يظهر في الموقع".

