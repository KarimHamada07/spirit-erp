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
   DJANGO_SECRET_KEY=المفتاح python manage.py migrate
   DJANGO_SECRET_KEY=المفتاح python manage.py collectstatic --noinput
   وبعدين Web > Reload.

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
