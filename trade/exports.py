"""تصدير Excel (.xlsx). لو مكتبة openpyxl مش متثبتة بيرجع CSV بدالها (بيفتح في Excel بالعربي)."""
import csv
import io
import re
from datetime import datetime
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    HAVE_XLSX = True
except ImportError:  # pragma: no cover
    HAVE_XLSX = False

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _cell(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return timezone.localtime(v).replace(tzinfo=None) if timezone.is_aware(v) else v
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v          # يمنع تنفيذ صيغ لو النص بيبدأ بـ = أو +
    return v


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def xlsx_response(sheets, filename):
    """sheets = [(عنوان الورقة، [العناوين]، [[صف], ...])]"""
    filename = safe_name(filename)
    if HAVE_XLSX:
        wb = Workbook()
        wb.remove(wb.active)
        head_fill = PatternFill("solid", fgColor="0F0F10")
        for title, headers, rows in sheets:
            ws = wb.create_sheet(re.sub(r"[\[\]:*?/\\]", " ", title)[:31])
            ws.sheet_view.rightToLeft = True
            ws.append(headers)
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = head_fill
                c.alignment = Alignment(horizontal="center", vertical="center")
            for row in rows:
                ws.append([_cell(v) for v in row])
            for col in range(1, len(headers) + 1):
                letter = get_column_letter(col)
                width = max([len(str(headers[col - 1]))] +
                            [len(str(r[col - 1])) for r in rows[:200] if col - 1 < len(r)]) + 3
                ws.column_dimensions[letter].width = min(max(width, 10), 60)
                for cell in ws[letter][1:]:
                    if isinstance(cell.value, float):
                        cell.number_format = "#,##0.00"
            ws.freeze_panes = "A2"
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type=XLSX_TYPE)
        resp["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return resp

    out = io.StringIO()
    out.write("\ufeff")
    w = csv.writer(out)
    for title, headers, rows in sheets:
        w.writerow([title])
        w.writerow(headers)
        for r in rows:
            w.writerow([_cell(v) for v in r])
        w.writerow([])
    resp = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    return resp
