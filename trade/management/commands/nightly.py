"""شغل آخر الليل (Scheduled task يومي الساعة 3 الفجر بتوقيت مصر):
  1) نسخة احتياطية كاملة (قاعدة البيانات + الصور الخاصة)
  2) ملخص اليوم اللي فات على تليجرام: المبيعات والربح والمصروفات والخزن والنواقص والحضور والطلبات المستنية
  3) لو BACKUP_CHAT_ID متحدد والإرسال المباشر شغال: النسخة الاحتياطية نفسها بتتبعت كملف للشات ده

    python manage.py nightly
    python manage.py nightly --dry-run      # يطبع الملخص من غير إرسال ولا نسخة
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from trade.models import (Attendance, CashBox, CustodyClearance, Customer, EmployeeProfile, Expense,
                          LeaveRequest, Variant)
from trade.notify import config, fmt, log_event, send_document


def summary(day):
    from django.db.models import F

    from trade.views import _period
    s = _period(day, day)
    expenses = Expense.objects.filter(date=day).aggregate(t=Sum("amount"))["t"] or 0
    lines = [f"🌙 ملخص يوم {day:%d/%m/%Y}",
             f"🧾 المبيعات: {fmt(s['revenue'])} — {s['invoices']} فاتورة، {s['pieces']} قطعة",
             f"💰 الربح: {fmt(s['profit'])}" + (f" — المصروفات {fmt(expenses)} — الصافي {fmt(s['profit'] - expenses)}"
                                                if expenses else "")]
    boxes = [(b.name, b.balance) for b in CashBox.objects.filter(is_active=True) if b.balance]
    if boxes:
        lines.append("🏦 الخزن: " + "، ".join(f"{n} {fmt(v)}" for n, v in boxes))
    low = Variant.objects.filter(model__is_active=True, quantity__lte=F("model__low_stock_level")).count()
    if low:
        lines.append(f"📦 {low} صنف قرب يخلص أو خلص")
    debtors = [c for c in Customer.objects.with_balance() if c.balance > 0]
    if debtors:
        lines.append(f"📒 مديونيات العملاء: {fmt(sum(c.balance for c in debtors))} على {len(debtors)} عميل")
    staff = list(EmployeeProfile.objects.filter(is_active=True).select_related("user"))
    if staff:
        came = set(Attendance.objects.filter(at__date=day, kind="in").values_list("user_id", flat=True))
        working = [p for p in staff if day.weekday() not in p.off_set]
        absent = [str(p) for p in working if p.user_id not in came]
        lines.append(f"👥 الحضور: {len([p for p in working if p.user_id in came])} من {len(working)}"
                     + (f" — غايب: {'، '.join(absent)}" if absent else ""))
    pending = (CustodyClearance.objects.filter(status="pending").count()
               + LeaveRequest.objects.filter(status="pending").count())
    if pending:
        lines.append(f"⏳ {pending} طلب مستني موافقة (عهد/إجازات)")
    return "\n".join(lines)


class Command(BaseCommand):
    help = "النسخة الاحتياطية + ملخص اليوم اللي فات على تليجرام (يتشغل كل ليلة)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--keep", type=int, default=30)

    def handle(self, *args, **opts):
        day = timezone.localdate() - timedelta(days=1)
        text = summary(day)
        if opts["dry_run"]:
            self.stdout.write(text)
            return
        from trade.management.commands.backup_db import default_dir, make_backup
        path = make_backup(default_dir(), keep=opts["keep"])
        size_kb = path.stat().st_size / 1024
        cfg = config()
        sent = ""
        if cfg["TOKEN"] and cfg["BACKUP_CHAT_ID"]:
            ok, detail = send_document(cfg["TOKEN"], cfg["BACKUP_CHAT_ID"], str(path),
                                       caption=f"💾 نسخة احتياطية {day:%d/%m/%Y}")
            sent = " واتبعتت لتليجرام" if ok else f" (ما اتبعتتش لتليجرام: {detail[:80]})"
        log_event(text + f"\n💾 النسخة الاحتياطية اتعملت ({size_kb:,.0f} KB){sent}")
        self.stdout.write(self.style.SUCCESS(f"تمام: {path}{sent}"))
