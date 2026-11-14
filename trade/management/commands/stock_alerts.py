"""ملخص يومي بالأصناف اللي قربت تخلص أو خلصت (يتحط كـ Scheduled task يومي).

    python manage.py stock_alerts
    python manage.py stock_alerts --dry-run

التنبيه اللحظي (أول ما صنف يوصل للحد) شغال لوحده من غير الأمر ده،
والأمر ده بيفكّرك كل يوم باللي لسه ما اتطلبش.
"""
from django.core.management.base import BaseCommand

from trade.stock_alerts import low_stock_text


class Command(BaseCommand):
    help = "يبعت لتليجرام ملخص الأصناف اللي وصلت لحد التنبيه"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        from trade.notify import log_event
        text, count = low_stock_text()
        if not count:
            self.stdout.write("كل الأصناف فوق حد التنبيه.")
            return
        if opts["dry_run"]:
            self.stdout.write(text)
            return
        log_event(text)
        self.stdout.write(self.style.SUCCESS(f"اتبعت ملخص بـ {count} صنف."))
