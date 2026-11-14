"""نسخة احتياطية لقاعدة البيانات وصور إثبات العهدة.

الاستخدام (يتحط كـ Scheduled task يومي على PythonAnywhere):
    python manage.py backup_db
    python manage.py backup_db --keep 30 --dir ~/backups

بياخد نسخة سليمة من SQLite وهي شغالة (backup API، مش نسخ ملف عادي)،
ويضغطها مع مجلد private_media في ملف واحد .tar.gz، ويمسح النسخ الأقدم من --keep.
"""
import os
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

PREFIX = "spirit-backup-"


def default_dir():
    return Path(os.environ.get("SPIRIT_BACKUP_DIR", settings.BASE_DIR / "backups"))


def make_backup(target_dir, keep=14):
    """يعمل النسخة ويرجع مسارها. keep = عدد النسخ اللي تفضل (الأقدم بيتمسح)."""
    db = connection.settings_dict
    if db["ENGINE"] != "django.db.backends.sqlite3":
        raise CommandError("الأمر ده للـ SQLite بس")
    name = str(db["NAME"])
    uri = name.startswith("file:")
    if not uri and not Path(name).exists():
        raise CommandError(f"قاعدة البيانات مش موجودة: {name}")
    target_dir = Path(target_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    out = target_dir / f"{PREFIX}{stamp}.tar.gz"

    connection.close()  # منبدأش النسخة واحنا فاتحين معاملة
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "db.sqlite3"
        src = sqlite3.connect(name, uri=uri)
        dst = sqlite3.connect(str(snap))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        check = sqlite3.connect(str(snap))
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if ok != "ok":
            raise CommandError(f"النسخة فيها مشكلة: {ok}")
        partial = out.with_suffix(".part")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(snap, arcname="db.sqlite3")
            media = Path(settings.BASE_DIR) / "private_media"
            if media.is_dir():
                tar.add(media, arcname="private_media")
        partial.rename(out)
    os.chmod(out, 0o600)  # النسخة فيها كل البيانات: لصاحب الحساب بس

    old = sorted(target_dir.glob(f"{PREFIX}*.tar.gz"))
    for f in old[:-keep] if keep > 0 else []:
        f.unlink()
    return out


class Command(BaseCommand):
    help = "نسخة احتياطية لقاعدة البيانات وإثباتات العهدة (tar.gz) مع مسح النسخ القديمة"

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=None, help="مجلد النسخ (افتراضي: backups جنب المشروع)")
        parser.add_argument("--keep", type=int, default=14, help="عدد النسخ اللي تفضل (افتراضي 14)")

    def handle(self, *args, **opts):
        out = make_backup(opts["dir"] or default_dir(), keep=opts["keep"])
        size = out.stat().st_size / 1024
        self.stdout.write(self.style.SUCCESS(f"تمام: {out} ({size:,.0f} KB)"))
