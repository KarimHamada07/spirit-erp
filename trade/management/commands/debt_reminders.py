"""تذكير المحاسب بالمديونيات المتأخرة (يتحط كـ Scheduled task يومي).

    python manage.py debt_reminders            # العملاء اللي عدّى عليهم 30 يوم أو ميعاد متابعتهم جه
    python manage.py debt_reminders --days 15
    python manage.py debt_reminders --dry-run  # يطبع الرسالة بس من غير ما يبعتها

الرسالة بتروح للمحاسب (TELEGRAM_ACCOUNTANT_CHAT_ID لو متحدد، وإلا الشات العام)،
ومش بتتبعت للعملاء نهائياً.
"""
from django.core.management.base import BaseCommand

from trade.debts import debt_rows, reminder_rows, reminder_text
from trade.notify import log_event


class Command(BaseCommand):
    help = "يبعت للمحاسب قايمة العملاء اللي محتاجين متابعة تحصيل"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="عمر المديونية اللي يستاهل تذكير")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        rows = reminder_rows(debt_rows(), min_days=opts["days"])
        text = reminder_text(rows, min_days=opts["days"])
        if not text:
            self.stdout.write("مفيش مديونيات محتاجة تذكير النهارده.")
            return
        if opts["dry_run"]:
            self.stdout.write(text)
            return
        log_event(text, audience="accountant")
        self.stdout.write(self.style.SUCCESS(f"اتبعت تذكير بـ {len(rows)} عميل للمحاسب."))
