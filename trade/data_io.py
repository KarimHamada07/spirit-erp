"""استيراد وتصدير البيانات بـ Excel (العملاء، الموردين، الموديلات والمقاسات، الخامات، المصروفات).

الخطوات: تنزيل الفورم ← ملء البيانات ← رفع الملف ← معاينة (كل صف: جديد/تحديث/غلط) ← تأكيد.
- مفيش حاجة بتتحفظ إلا بعد التأكيد، ولو فيه صف واحد غلط الاستيراد مش بيكمل لحد ما يتصلح.
- إعادة رفع نفس الملف آمنة: الموجود بيتحدث بدل ما يتكرر، والكمية الافتتاحية بتتسجل مرة واحدة بس
  (للصنف اللي مالوش أي حركة قبل كده).
"""
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import (ClothingModel, Customer, Expense, MaterialPurchaseItem, OnlineOrderItem,
                     ProductionMaterial, ProductionOutput, PurchaseItem, RawMaterial, SaleItem, Seller,
                     Supplier, Variant)

ZERO = Decimal("0")
MAX_ROWS = 5000
UNITS = dict((label, key) for key, label in RawMaterial.UNITS)
CATEGORIES = dict((label, key) for key, label in ClothingModel.CATEGORIES)


class RowError(Exception):
    pass


# ---------------------------------------------------------------- تحويل القيم
def _text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _phone(v):
    s = _text(v).replace(" ", "").replace("-", "")
    if s.isdigit() and len(s) == 10 and s.startswith("1"):
        s = "0" + s  # Excel بيشيل الصفر اللي على الشمال لو الخانة رقم
    return s


def _dec(v, label, required=False, positive=False):
    s = _text(v).replace(",", "").replace("٫", ".")
    if not s:
        if required:
            raise RowError(f"{label} مطلوب")
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise RowError(f"{label} لازم يكون رقم ({s})")
    if d < 0 or (positive and d == 0):
        raise RowError(f"{label} لازم يكون أكبر من صفر")
    return d


def _int(v, label):
    d = _dec(v, label)
    if d is None:
        return None
    if d != d.to_integral_value():
        raise RowError(f"{label} لازم يكون رقم صحيح")
    return int(d)


def _date(v, label, required=False):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = _text(v)
    if not s:
        if required:
            raise RowError(f"{label} مطلوب")
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise RowError(f"{label} مش تاريخ صحيح ({s}). اكتبه كده: 2026-01-31")


def _req(v, label):
    s = _text(v)
    if not s:
        raise RowError(f"{label} مطلوب")
    return s


def _json(v):
    """قيمة تتحفظ في الـ session لحد التأكيد"""
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    if isinstance(v, Decimal):
        return str(v)
    if v is None:
        return ""
    return v if isinstance(v, (int, float, str)) else str(v)


# ---------------------------------------------------------------- الأنواع
class Spec:
    key = ""
    title = ""
    icon = ""
    perm_view = ""
    perm_add = ""
    columns = []      # [(key, header, required, help)]
    info_columns = []  # أعمدة للعلم في التصدير بس (الاستيراد بيتجاهلها)
    choices = {}       # key -> قايمة اختيارات للفورم

    def headers(self):
        return [h + (" *" if req else "") for _k, h, req, _help in self.columns]

    def export_rows(self):
        return []

    def check(self, raw, cache):
        """يرجع (action, label, clean) أو يرمي RowError"""
        raise NotImplementedError

    def apply(self, clean, user):
        raise NotImplementedError

    def get_choices(self):
        return {}


class CustomersSpec(Spec):
    key, title, icon = "customers", "العملاء", "fas fa-user-group"
    perm_view, perm_add = "trade.view_customer", "trade.add_customer"
    columns = [
        ("name", "اسم العميل", True, "زي ما هيظهر في الفواتير"),
        ("phone", "الموبايل", False, "لو موجود بيتعرف بيه العميل. اكتبه 01xxxxxxxxx"),
        ("seller", "البائع", False, "لازم للعميل الجديد. لازم يكون من البائعين المسجلين"),
        ("opening_balance", "رصيد افتتاحي (عليه)", False, "المديونية اللي عليه قبل النظام. فاضي = مايتغيرش"),
        ("opening_date", "تاريخ الرصيد", False, "مثلاً 2026-01-31. فاضي = النهارده"),
    ]
    info_columns = ["المتبقي عليه دلوقتي (للعلم)"]

    def get_choices(self):
        return {"seller": list(Seller.objects.filter(is_active=True).values_list("name", flat=True))}

    def export_rows(self):
        for c in Customer.objects.with_balance().select_related("seller").order_by("name"):
            yield [c.name, c.phone, c.seller.name if c.seller_id else "", c.opening_balance,
                   c.opening_date or "", c.balance]

    def check(self, raw, cache):
        name = _req(raw.get("name"), "اسم العميل")
        phone = _phone(raw.get("phone"))
        seller_name = _text(raw.get("seller"))
        opening = _dec(raw.get("opening_balance"), "الرصيد الافتتاحي")
        opening_date = _date(raw.get("opening_date"), "تاريخ الرصيد")
        sellers = cache.setdefault("sellers", {s.name.strip().lower(): s.pk
                                               for s in Seller.objects.filter(is_active=True)})
        seller_id = None
        if seller_name:
            seller_id = sellers.get(seller_name.lower())
            if seller_id is None:
                raise RowError(f"البائع '{seller_name}' مش موجود")
        key = ("p", phone) if phone else ("n", name.lower())
        seen = cache.setdefault("seen", set())
        if key in seen:
            raise RowError("العميل ده متكرر في الملف")
        seen.add(key)
        if phone:
            existing = list(Customer.objects.filter(phone=phone)[:2])
            if not existing:  # عميل متسجل بالاسم من غير موبايل
                existing = list(Customer.objects.filter(name__iexact=name, phone="")[:2])
        else:
            existing = list(Customer.objects.filter(name__iexact=name)[:2])
        if len(existing) > 1:
            raise RowError("فيه أكتر من عميل بنفس البيانات في النظام. اكتب الموبايل عشان نفرّق")
        obj = existing[0] if existing else None
        if obj is None and seller_id is None:
            raise RowError("اختار البائع للعميل الجديد")
        clean = {"pk": obj.pk if obj else None, "name": name, "phone": phone, "seller_id": seller_id,
                 "opening_balance": opening, "opening_date": opening_date}
        return ("update" if obj else "new"), name, clean

    def apply(self, c, user):
        obj = Customer.objects.get(pk=c["pk"]) if c["pk"] else Customer(name=c["name"])
        obj.name = c["name"]
        if c["phone"]:
            obj.phone = c["phone"]
        if c["seller_id"] and (not obj.pk or user.has_perm("trade.change_customer_seller")):
            obj.seller_id = c["seller_id"]
        if c["opening_balance"] is not None:
            obj.opening_balance = c["opening_balance"]
            obj.opening_date = c["opening_date"] or obj.opening_date or timezone.localdate()
        obj.save()


class SuppliersSpec(Spec):
    key, title, icon = "suppliers", "الموردين", "fas fa-industry"
    perm_view, perm_add = "trade.view_supplier", "trade.add_supplier"
    columns = [
        ("name", "اسم المورد", True, "بيتعرف بيه المورد"),
        ("phone", "الموبايل", False, ""),
        ("address", "العنوان", False, ""),
        ("opening_balance", "رصيد افتتاحي (لينا عليه)", False, "المستحق للمورد قبل النظام"),
        ("opening_date", "تاريخ الرصيد", False, "مثلاً 2026-01-31"),
    ]
    info_columns = ["المستحق دلوقتي (للعلم)"]

    def export_rows(self):
        for s in Supplier.objects.order_by("name"):
            yield [s.name, s.phone, s.address, s.opening_balance, s.opening_date or "", s.balance]

    def check(self, raw, cache):
        name = _req(raw.get("name"), "اسم المورد")
        seen = cache.setdefault("seen", set())
        if name.lower() in seen:
            raise RowError("المورد ده متكرر في الملف")
        seen.add(name.lower())
        existing = list(Supplier.objects.filter(name__iexact=name)[:2])
        if len(existing) > 1:
            raise RowError("فيه أكتر من مورد بنفس الاسم في النظام")
        obj = existing[0] if existing else None
        clean = {"pk": obj.pk if obj else None, "name": name, "phone": _phone(raw.get("phone")),
                 "address": _text(raw.get("address"))[:250],
                 "opening_balance": _dec(raw.get("opening_balance"), "الرصيد الافتتاحي"),
                 "opening_date": _date(raw.get("opening_date"), "تاريخ الرصيد")}
        return ("update" if obj else "new"), name, clean

    def apply(self, c, user):
        obj = Supplier.objects.get(pk=c["pk"]) if c["pk"] else Supplier(name=c["name"])
        obj.name = c["name"]
        if c["phone"]:
            obj.phone = c["phone"]
        if c["address"]:
            obj.address = c["address"]
        if c["opening_balance"] is not None:
            obj.opening_balance = c["opening_balance"]
            obj.opening_date = c["opening_date"] or obj.opening_date or timezone.localdate()
        obj.save()


def _variant_has_moves(v):
    return (v.quantity != 0 or SaleItem.objects.filter(variant=v).exists()
            or PurchaseItem.objects.filter(variant=v).exists()
            or OnlineOrderItem.objects.filter(variant=v).exists()
            or ProductionOutput.objects.filter(variant=v, order__status="done").exists())


class ProductsSpec(Spec):
    key, title, icon = "products", "الموديلات والمقاسات والمخزون", "fas fa-shirt"
    perm_view, perm_add = "trade.view_variant", "trade.add_variant"
    columns = [
        ("code", "كود الموديل", True, "كل المقاسات بتاعة الموديل ليها نفس الكود"),
        ("model_name", "اسم الموديل", True, ""),
        ("category", "التصنيف", False, "جاكيت / تريكو / بنطلون / سويت شيرت / تيشيرت / بدلة / شورت / قميص"),
        ("size", "المقاس", True, "S / M / L / XL أو رقم"),
        ("color", "اللون", False, ""),
        ("sale_price", "سعر البيع", True, ""),
        ("low_stock_level", "حد التنبيه", False, "للموديل كله. فاضي = 5"),
        ("opening_qty", "كمية افتتاحية", False, "بتتضاف مرة واحدة بس لصنف مالوش حركة قبل كده"),
        ("unit_cost", "تكلفة القطعة", False, "مطلوبة لو فيه كمية افتتاحية"),
    ]
    info_columns = ["الكمية الحالية (للعلم)", "متوسط التكلفة (للعلم)"]

    def get_choices(self):
        return {"category": list(CATEGORIES)}

    def export_rows(self):
        for v in Variant.objects.select_related("model").order_by("model__code", "size", "color"):
            yield [v.model.code, v.model.name, v.model.get_category_display(), v.size, v.color,
                   v.sale_price,
                   v.model.low_stock_level, "", "", v.quantity, v.avg_cost]

    def check(self, raw, cache):
        code = _req(raw.get("code"), "كود الموديل")[:30]
        model_name = _req(raw.get("model_name"), "اسم الموديل")[:100]
        cat_label = _text(raw.get("category"))
        category = None
        if cat_label:
            category = CATEGORIES.get(cat_label)
            if category is None:
                raise RowError(f"التصنيف '{cat_label}' مش معروف. اختار من: {' / '.join(CATEGORIES)}")
        size = _req(raw.get("size"), "المقاس")[:20]
        color = _text(raw.get("color"))[:30]
        price = _dec(raw.get("sale_price"), "سعر البيع", required=True)
        low = _int(raw.get("low_stock_level"), "حد التنبيه")
        qty = _int(raw.get("opening_qty"), "الكمية الافتتاحية")
        cost = _dec(raw.get("unit_cost"), "تكلفة القطعة")
        if qty and cost is None:
            raise RowError("اكتب تكلفة القطعة عشان الكمية الافتتاحية")
        key = (code.lower(), size.lower(), color.lower())
        seen = cache.setdefault("seen", set())
        if key in seen:
            raise RowError("المقاس/اللون ده متكرر في الملف")
        seen.add(key)
        names = cache.setdefault("names", {})
        if names.setdefault(code.lower(), model_name) != model_name:
            raise RowError(f"الكود {code} مكتوب بأكتر من اسم موديل في الملف")
        model = ClothingModel.objects.filter(code__iexact=code).first()
        v = None
        if model:
            v = model.variants.filter(size__iexact=size, color__iexact=color).first()
        note = ""
        if qty and v is not None and _variant_has_moves(v):
            note = " (الكمية الافتتاحية اتجاهلت: الصنف ليه حركة قبل كده)"
            qty = None
        label = f"{model_name} — {size}{' — ' + color if color else ''}{note}"
        clean = {"code": code, "model_name": model_name, "category": category, "size": size, "color": color,
                 "sale_price": price, "low": low, "qty": qty, "cost": cost,
                 "variant": v.pk if v else None}
        return ("update" if v else "new"), label, clean

    def apply(self, c, user):
        model = ClothingModel.objects.filter(code__iexact=c["code"]).first()
        if model is None:
            model = ClothingModel(code=c["code"])
        model.name = c["model_name"]
        if c["category"]:
            model.category = c["category"]
        if c["low"] is not None:
            model.low_stock_level = c["low"]
        model.save()
        v = (Variant.objects.select_for_update().get(pk=c["variant"]) if c["variant"]
             else Variant(model=model, size=c["size"], color=c["color"]))
        v.sale_price = c["sale_price"]
        if c["qty"]:
            v.add_stock(c["qty"], c["cost"])
        v.save()


class MaterialsSpec(Spec):
    key, title, icon = "materials", "الخامات", "fas fa-scroll"
    perm_view, perm_add = "trade.view_rawmaterial", "trade.add_rawmaterial"
    columns = [
        ("name", "اسم الخامة", True, ""),
        ("unit", "الوحدة", True, "متر / كيلو / قطعة / طوب/رول / علبة"),
        ("low_stock_level", "حد التنبيه", False, ""),
        ("opening_qty", "كمية افتتاحية", False, "مرة واحدة بس لخامة مالهاش حركة"),
        ("unit_cost", "تكلفة الوحدة", False, "مطلوبة لو فيه كمية افتتاحية"),
    ]
    info_columns = ["الكمية الحالية (للعلم)", "متوسط التكلفة (للعلم)"]

    def get_choices(self):
        return {"unit": list(UNITS)}

    def export_rows(self):
        for m in RawMaterial.objects.order_by("name"):
            yield [m.name, m.get_unit_display(), m.low_stock_level, "", "", m.quantity, m.avg_cost]

    def check(self, raw, cache):
        name = _req(raw.get("name"), "اسم الخامة")[:120]
        unit_label = _req(raw.get("unit"), "الوحدة")
        unit = UNITS.get(unit_label)
        if unit is None:
            raise RowError(f"الوحدة '{unit_label}' مش معروفة. اختار من: {' / '.join(UNITS)}")
        low = _dec(raw.get("low_stock_level"), "حد التنبيه")
        qty = _dec(raw.get("opening_qty"), "الكمية الافتتاحية")
        cost = _dec(raw.get("unit_cost"), "تكلفة الوحدة")
        if qty and cost is None:
            raise RowError("اكتب تكلفة الوحدة عشان الكمية الافتتاحية")
        seen = cache.setdefault("seen", set())
        if name.lower() in seen:
            raise RowError("الخامة دي متكررة في الملف")
        seen.add(name.lower())
        m = RawMaterial.objects.filter(name__iexact=name).first()
        note = ""
        if qty and m is not None and (m.quantity != 0
                                      or MaterialPurchaseItem.objects.filter(material=m).exists()
                                      or ProductionMaterial.objects.filter(material=m).exists()):
            note = " (الكمية الافتتاحية اتجاهلت: الخامة ليها حركة قبل كده)"
            qty = None
        clean = {"pk": m.pk if m else None, "name": name, "unit": unit, "low": low, "qty": qty,
                 "cost": cost}
        return ("update" if m else "new"), name + note, clean

    def apply(self, c, user):
        m = (RawMaterial.objects.select_for_update().get(pk=c["pk"]) if c["pk"]
             else RawMaterial(name=c["name"]))
        m.name, m.unit = c["name"], c["unit"]
        if c["low"] is not None:
            m.low_stock_level = c["low"]
        if c["qty"]:
            m.add_stock(c["qty"], c["cost"])
        m.save()


class ExpensesSpec(Spec):
    key, title, icon = "expenses", "المصروفات", "fas fa-receipt"
    perm_view, perm_add = "trade.view_expense", "trade.add_expense"
    columns = [
        ("date", "التاريخ", True, "مثلاً 2026-01-31"),
        ("title", "البند", True, "إيجار، كهربا، مرتبات..."),
        ("amount", "المبلغ", True, ""),
    ]

    def export_rows(self):
        for e in Expense.objects.order_by("date", "id"):
            yield [e.date, e.title, e.amount]

    def check(self, raw, cache):
        d = _date(raw.get("date"), "التاريخ", required=True)
        title = _req(raw.get("title"), "البند")[:150]
        amount = _dec(raw.get("amount"), "المبلغ", required=True, positive=True)
        if Expense.objects.filter(date=d, title=title, amount=amount).exists():
            return "skip", f"{title} — {amount} (موجود قبل كده)", None
        return "new", f"{title} — {amount}", {"date": d, "title": title, "amount": amount}

    def apply(self, c, user):
        Expense.objects.create(**c)


SPECS = {s.key: s for s in (CustomersSpec(), SuppliersSpec(), ProductsSpec(), MaterialsSpec(),
                            ExpensesSpec())}


# ---------------------------------------------------------------- Excel
def template_xlsx(spec, with_data=False):
    """الفورم (فاضي) أو تصدير البيانات الحالية بنفس الشكل"""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "البيانات"
    ws.sheet_view.rightToLeft = True
    headers = spec.headers() + (spec.info_columns if with_data else [])
    ws.append(headers)
    req_fill, opt_fill, info_fill = (PatternFill("solid", fgColor="FE0000"),
                                     PatternFill("solid", fgColor="111111"),
                                     PatternFill("solid", fgColor="8A8A93"))
    for i, cell in enumerate(ws[1]):
        if i < len(spec.columns):
            _k, _h, req, help_ = spec.columns[i]
            cell.fill = req_fill if req else opt_fill
            if help_:
                cell.comment = Comment(help_, "SPIRIT")
        else:
            cell.fill = info_fill
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i + 1)].width = max(16, len(str(cell.value)) + 6)
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"
    if with_data:
        for row in spec.export_rows():
            ws.append([float(v) if isinstance(v, Decimal) else v for v in row])
    # الموبايل نص عشان الصفر اللي على الشمال
    for idx, (k, *_r) in enumerate(spec.columns, start=1):
        letter = get_column_letter(idx)
        if k == "phone":
            for r in range(2, max(ws.max_row, 2) + 500):
                ws[f"{letter}{r}"].number_format = "@"
        if "date" in k:
            for r in range(2, max(ws.max_row, 2) + 500):
                ws[f"{letter}{r}"].number_format = "yyyy-mm-dd"
    # قوايم اختيار
    choices = spec.get_choices()
    if choices:
        lists = wb.create_sheet("قوايم")
        for col_i, (k, values) in enumerate(choices.items(), start=1):
            letter = get_column_letter(col_i)
            for r, v in enumerate(values, start=1):
                lists[f"{letter}{r}"] = v
            idx = [c[0] for c in spec.columns].index(k) + 1
            target = get_column_letter(idx)
            if values:
                dv = DataValidation(type="list", allow_blank=True,
                                    formula1=f"='قوايم'!${letter}$1:${letter}${len(values)}")
                dv.error = "اختار من القايمة"
                ws.add_data_validation(dv)
                dv.add(f"{target}2:{target}5000")
        lists.sheet_state = "hidden"
    # التعليمات
    help_ws = wb.create_sheet("التعليمات")
    help_ws.sheet_view.rightToLeft = True
    help_ws.append([f"فورم {spec.title} — SPIRIT"])
    help_ws["A1"].font = Font(bold=True, size=14)
    help_ws.append(["املا البيانات في ورقة 'البيانات' (من الصف التاني). العمود الأحمر لازم، والأسود اختياري."])
    help_ws.append(["لو العنصر موجود في النظام هيتحدث، ولو جديد هيتضاف. هتشوف معاينة قبل ما أي حاجة تتحفظ."])
    help_ws.append(["الأعمدة الرمادي (للعلم) بتظهر في التصدير بس وبتتجاهل في الاستيراد."])
    help_ws.append([])
    help_ws.append(["العمود", "مطلوب؟", "ملاحظات"])
    for c in help_ws[6]:
        c.font = Font(bold=True)
    for _k, h, req, help_ in spec.columns:
        help_ws.append([h, "أيوه" if req else "لأ", help_])
    help_ws.column_dimensions["A"].width = 28
    help_ws.column_dimensions["B"].width = 10
    help_ws.column_dimensions["C"].width = 70
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read_rows(spec, fileobj):
    """يقرا الملف ويرجع [(رقم الصف، {key: value})]"""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(fileobj, read_only=True, data_only=True)
    except Exception:
        raise RowError("الملف مش Excel صحيح (لازم يكون .xlsx)")
    ws = wb["البيانات"] if "البيانات" in wb.sheetnames else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration:
        raise RowError("الملف فاضي")
    by_header = {h: k for k, h, _r, _h in spec.columns}
    index = {}
    for i, h in enumerate(header):
        name = _text(h).replace("*", "").strip()
        if name in by_header:
            index[by_header[name]] = i
    missing = [h for k, h, req, _h in spec.columns if req and k not in index]
    if missing:
        raise RowError("الأعمدة دي ناقصة في الملف: " + "، ".join(missing)
                       + ". نزّل الفورم من جديد واستخدمه.")
    out = []
    for n, row in enumerate(rows, start=2):
        if row is None or all(v is None or _text(v) == "" for v in row):
            continue
        out.append((n, {k: _json(row[i]) if i < len(row) else "" for k, i in index.items()}))
        if len(out) > MAX_ROWS:
            raise RowError(f"الملف أكبر من {MAX_ROWS} صف. قسّمه على أكتر من ملف.")
    wb.close()
    if not out:
        raise RowError("مفيش بيانات في الملف")
    return out


def preview(spec, rows):
    """كل صف: {row, action, label, error, clean}"""
    cache, out = {}, []
    for n, raw in rows:
        try:
            action, label, clean = spec.check(raw, cache)
            out.append({"row": n, "action": action, "label": label, "error": "", "clean": clean})
        except RowError as e:
            out.append({"row": n, "action": "error", "label": _text(next(iter(raw.values()), "")),
                        "error": str(e), "clean": None})
    return out


@transaction.atomic
def run_import(spec, rows, user):
    """يعيد الفحص ويحفظ. لو فيه غلط مش بيحفظ حاجة."""
    from .notify import log_event, muted
    result = preview(spec, rows)
    if any(r["action"] == "error" for r in result):
        raise RowError("فيه صفوف غلط. صلّحها وارفع الملف تاني.")
    counts = {"new": 0, "update": 0, "skip": 0}
    with muted():  # من غير رسالة تليجرام لكل صف
        for r in result:
            counts[r["action"]] += 1
            if r["action"] != "skip":
                spec.apply(r["clean"], user)
    log_event(f"📥 استيراد {spec.title} من Excel: {counts['new']} جديد، "
              f"{counts['update']} تحديث" + (f"، {counts['skip']} موجود قبل كده" if counts["skip"] else ""),
              user=user)
    return counts
