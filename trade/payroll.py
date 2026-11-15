"""حساب المرتبات من البصمة والإجازات، وكشف حساب الموظف.

لكل يوم في الشهر (لحد النهارده):
  - يوم إجازة أسبوعية: مش بيتحسب.
  - إجازة متوافق عليها: مدفوعة، إلا "من غير مرتب" بتتخصم يوم.
  - مفيش بصمة: غياب = خصم يوم (المرتب ÷ 30).
  - فيه بصمة: التأخير بعد السماح + الخروج بدري بيتخصموا بالدقيقة، والإذن المتوافق عليه بيغطي ساعاته.
    والقعاد بعد ميعاد الانصراف = أوفر تايم بالدقيقة × المعامل.
"""
import calendar
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Attendance, EmployeeProfile, EmployeeTxn, Expense, LeaveRequest

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _minutes(t1, t2):
    return int((datetime.combine(date.min, t2) - datetime.combine(date.min, t1)).total_seconds() // 60)


def month_bounds(period):
    y, m = (int(x) for x in period.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def month_stats(profile, period, today=None):
    today = today or timezone.localdate()
    first, last = month_bounds(period)
    last = min(last, today)
    first = max(first, profile.start_date)   # قبل ما يتسجل في السيستم مش غياب
    user = profile.user
    shift_min = max(1, _minutes(profile.shift_start, profile.shift_end))
    leaves = list(LeaveRequest.objects.filter(user=user, status=LeaveRequest.APPROVED,
                                              start_date__lte=last, end_date__gte=first))
    punches = {}
    for p in Attendance.objects.filter(user=user, at__date__range=(first, last)).order_by("at", "id"):
        punches.setdefault(timezone.localtime(p.at).date(), []).append(p)
    st = {"work_days": 0, "present": 0, "absent": 0, "paid_leave": 0, "unpaid_leave": 0,
          "late": 0, "early": 0, "excused": 0, "overtime": 0, "days": []}
    d = first
    while d <= last:
        if d.weekday() in profile.off_set:
            d += timedelta(days=1)
            continue
        st["work_days"] += 1
        day_leaves = [lv for lv in leaves if lv.start_date <= d <= lv.end_date]
        full = next((lv for lv in day_leaves if lv.kind != "permission"), None)
        ps = punches.get(d, [])
        row = {"date": d, "status": "", "late": 0, "early": 0, "overtime": 0}
        if full:
            key = "unpaid_leave" if full.kind == "unpaid" else "paid_leave"
            st[key] += 1
            row["status"] = full.get_kind_display()
        elif not ps:
            if d < today:   # النهارده لسه ما خلصش
                st["absent"] += 1
                row["status"] = "غياب"
            else:
                row["status"] = "لسه"
        else:
            st["present"] += 1
            row["status"] = "حضور"
            first_in = next((p for p in ps if p.kind == "in"), None)
            last_out = next((p for p in reversed(ps) if p.kind == "out"), None)
            if first_in:
                t = timezone.localtime(first_in.at).time()
                late = _minutes(profile.shift_start, t) - profile.grace_minutes
                row["late"] = max(0, late) if late > 0 else 0
            if last_out:
                t = timezone.localtime(last_out.at).time()
                diff = _minutes(t, profile.shift_end)
                if diff > 0:
                    row["early"] = diff
                elif profile.count_overtime:
                    row["overtime"] = -diff
            excuse = sum(int((lv.hours or 0) * 60) for lv in day_leaves if lv.kind == "permission")
            used = min(excuse, row["late"] + row["early"])
            st["excused"] += used
            st["late"] += row["late"]
            st["early"] += row["early"]
            st["overtime"] += row["overtime"]
        st["days"].append(row)
        d += timedelta(days=1)

    salary = profile.monthly_salary
    day_rate = salary / 30
    minute_rate = day_rate / shift_min
    late_minutes = max(0, st["late"] + st["early"] - st["excused"])
    st.update({
        "salary": salary,
        "day_rate": day_rate.quantize(CENT),
        "absence_deduction": (day_rate * (st["absent"] + st["unpaid_leave"])).quantize(CENT, ROUND_HALF_UP),
        "late_minutes": late_minutes,
        "late_deduction": (minute_rate * late_minutes).quantize(CENT, ROUND_HALF_UP),
        "overtime_pay": (minute_rate * st["overtime"] * profile.overtime_rate).quantize(CENT, ROUND_HALF_UP),
    })
    st["net"] = max(ZERO, salary - st["absence_deduction"] - st["late_deduction"] + st["overtime_pay"])
    return st


def payroll_rows(period, today=None):
    rows = []
    for prof in EmployeeProfile.objects.filter(is_active=True).select_related("user"):
        st = month_stats(prof, period, today)
        done = EmployeeTxn.objects.filter(user=prof.user, kind="salary", period=period).first()
        rows.append({"profile": prof, "st": st, "done": done})
    return rows


@transaction.atomic
def approve_payroll(period, user, today=None):
    """يسجل مرتب الشهر في كشف حساب كل موظف (مرة واحدة للشهر) ومصروف بنفس القيمة"""
    created = 0
    for r in payroll_rows(period, today):
        if r["done"] or r["st"]["net"] <= 0:
            continue
        st, prof = r["st"], r["profile"]
        details = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in st.items() if k != "days"}
        EmployeeTxn.objects.create(user=prof.user, kind="salary", amount=st["net"], period=period,
                                   date=month_bounds(period)[1], details=details, created_by=user,
                                   description=f"مرتب {period}: أساسي {st['salary']:,.2f} − غياب "
                                               f"{st['absence_deduction']:,.2f} − تأخير {st['late_deduction']:,.2f}"
                                               f" + أوفر تايم {st['overtime_pay']:,.2f}")
        Expense.objects.create(title=f"مرتب {prof} — {period}", amount=st["net"], date=month_bounds(period)[1])
        created += 1
    return created


def balance(user):
    total = ZERO
    for t in EmployeeTxn.objects.filter(user=user).only("kind", "amount"):
        total += t.signed
    return total


def employee_statement(user, d1=None, d2=None):
    """كشف حساب الموظف بنفس شكل كشوف العملاء والموردين (PDF/Excel)"""
    from .statements import _finish
    rows = []
    for t in EmployeeTxn.objects.filter(user=user):
        debit = t.amount if t.kind in EmployeeTxn.PLUS else ZERO
        credit = ZERO if t.kind in EmployeeTxn.PLUS else t.amount
        rows.append((t.date, 0, t.pk, {"kind": t.get_kind_display(), "ref": t.description[:60],
                                       "debit": debit, "credit": credit}))
    entries, totals = _finish(rows, d1, d2)
    name = user.get_full_name() or user.get_username()
    return {"kind": "employee", "title": f"كشف حساب موظف: {name}", "name": name, "phone": "",
            "entries": entries, "totals": totals, "debit_label": "مستحق (مرتب ومكافآت)",
            "credit_label": "اتصرف (سلف وخصومات ومرتب)", "balance_label": "الباقي له"}


def advances_total(user, period):
    first, last = month_bounds(period)
    return (EmployeeTxn.objects.filter(user=user, kind="advance", date__range=(first, last))
            .aggregate(s=Sum("amount"))["s"] or ZERO)
