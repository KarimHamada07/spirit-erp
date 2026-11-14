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
- ملف `db.sqlite3` مش في الـ ZIP، فالقاعدة بتبدأ فاضية. الأرصدة الافتتاحية تتدخل من لوحة الإدارة.
- المستخدمين (البائعين والمحاسب والسيلز) بيتعملوا من لوحة الإدارة، وبيتضافوا للمجموعات.
- النسخ الاحتياطي: بعد ما يشتغل، هنضبط Scheduled task.
