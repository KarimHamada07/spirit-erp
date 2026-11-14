import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import DecimalField, F, Q, Sum
from django.http import FileResponse, Http404
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (CustodyClearance, CustodyDeposit, CustodyHolder, CustodySettlement,
                     Expense, OnlineOrder, Payment, SaleInvoice, SaleItem)
from .notify import fmt, log_event

ZERO = Decimal("0")
DEC = DecimalField(max_digits=14, decimal_places=2)
MAX_UPLOAD = 8 * 1024 * 1024
# الأوردر الأونلاين المحصّل: المبلغ اللي بيوصل فعلاً = المطلوب تحصيله - تكلفة الشحن
# (شركات الشحن غالباً بتخصم أجرتها من التحصيل). غيّرها لـ False لو بتدفع الشحن بشكل منفصل.
ONLINE_NET_OF_SHIPPING_COST = True

IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}


def custody_summary():
    """المبالغ الموجودة (العهدة) = المقبوضات + الإضافات اليدوية - المصروفات - اللي اتصفّى"""
    gross = SaleItem.objects.filter(
        invoice__is_confirmed=True, invoice__payment_type="cash"
    ).aggregate(g=Sum(F("quantity") * F("unit_price"), output_field=DEC))["g"] or ZERO
    discount = SaleInvoice.objects.filter(
        is_confirmed=True, payment_type="cash"
    ).aggregate(d=Sum("discount"))["d"] or ZERO
    cash_sales = gross - discount

    online = ZERO
    for o in OnlineOrder.objects.filter(status=OnlineOrder.COLLECTED).prefetch_related("items"):
        online += o.total_to_collect
        if ONLINE_NET_OF_SHIPPING_COST:
            online -= o.shipping_cost

    payments = Payment.objects.aggregate(s=Sum("amount"))["s"] or ZERO
    deposits = CustodyDeposit.objects.aggregate(s=Sum("amount"))["s"] or ZERO
    # مصروفات اتصرفت من عهدة شخص اتخصمت من العهدة العامة وقت التسليم، فمش بتتخصم تاني
    expenses = (Expense.objects.filter(custody_clearance__isnull=True)
                .aggregate(s=Sum("amount"))["s"] or ZERO)
    settled = CustodySettlement.objects.aggregate(s=Sum("amount"))["s"] or ZERO
    returned = (CustodyClearance.objects.filter(status=CustodyClearance.APPROVED,
                                                kind=CustodyClearance.RETURNED)
                .aggregate(s=Sum("amount"))["s"] or ZERO)

    total_in = cash_sales + online + payments + deposits + returned
    return {
        "cash_sales": cash_sales, "online": online, "payments": payments,
        "deposits": deposits, "returned": returned, "total_in": total_in,
        "expenses": expenses, "settled": settled,
        "balance": total_in - expenses - settled,
    }


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


def _image_kind(head):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def _amount(raw):
    try:
        return Decimal((raw or "").strip().replace(",", "") or "0")
    except InvalidOperation:
        return ZERO


@staff_member_required
def custody(request):
    _need(request, "trade.view_custodysettlement")
    q = request.GET.get("q", "").strip()
    noproof = request.GET.get("noproof") == "1"

    qs = CustodySettlement.objects.select_related("created_by")
    if q:
        qs = qs.filter(Q(received_by__icontains=q) | Q(notes__icontains=q) | Q(holder__name__icontains=q))
    if noproof:
        qs = qs.filter(screenshot="")
    page = Paginator(qs, 15).get_page(request.GET.get("page"))

    context = {
        **admin.site.each_context(request),
        "title": "العهدة",
        "s": custody_summary(),
        "page": page,
        "q": q,
        "noproof": noproof,
        "methods": CustodySettlement.METHODS,
        "now": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "today": timezone.localdate().isoformat(),
        "deposits": CustodyDeposit.objects.all()[:5],
        "holders": holder_rows(),
        "holder_choices": CustodyHolder.objects.filter(is_active=True),
        "free_users": _free_users(),
        "pending_count": (CustodyClearance.objects.filter(status=CustodyClearance.PENDING).count()
                          if request.user.has_perm("trade.approve_custodyclearance") else 0),
        "can_add_holder": request.user.has_perm("trade.add_custodyholder"),
        "can_add": request.user.has_perm("trade.add_custodysettlement"),
        "can_deposit": request.user.has_perm("trade.add_custodydeposit"),
    }
    return render(request, "trade/custody.html", context)


@staff_member_required
@require_POST
def custody_settle(request):
    _need(request, "trade.add_custodysettlement")
    amount = _amount(request.POST.get("amount"))
    holder = CustodyHolder.objects.filter(pk=request.POST.get("holder") or 0, is_active=True).first()
    receiver = holder.name if holder else ""
    method = request.POST.get("method", "cash")
    if method not in dict(CustodySettlement.METHODS):
        method = "cash"

    if amount <= 0:
        messages.error(request, "اكتب مبلغ صحيح أكبر من صفر")
        return redirect("custody")
    if holder is None:
        messages.error(request, "اختار الشخص اللي استلم العهدة (أو ضيفه الأول)")
        return redirect("custody")

    try:
        when = timezone.make_aware(datetime.fromisoformat(request.POST.get("settled_at", "")))
    except (ValueError, TypeError):
        when = timezone.now()

    upload = request.FILES.get("screenshot")
    data = kind = None
    if upload:
        try:
            data, kind = _read_image(upload)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("custody")

    balance_before = custody_summary()["balance"]
    st = CustodySettlement(
        amount=amount, holder=holder, received_by=receiver, method=method, settled_at=when,
        notes=request.POST.get("notes", "").strip()[:300], created_by=request.user)
    if data:
        st.screenshot.save(f"{uuid.uuid4().hex}.{kind}", ContentFile(data), save=False)
    st.save()

    balance = balance_before - amount
    proof = "📎 بإثبات" if st.has_proof else "بدون إثبات"
    log_event(f"🏦 تسليم عهدة {fmt(amount)} على حساب: {receiver} — "
              f"{st.get_method_display()} — {proof}\nرصيد عهدته: {fmt(holder.totals()['balance'])}"
              f"\nالمتبقي في العهدة العامة: {fmt(balance)}")

    messages.success(request, f"اتسجل {fmt(amount)} على عهدة {receiver}")
    if amount > balance_before:
        messages.warning(
            request, f"المبلغ أكبر من رصيد العهدة المسجل ({fmt(balance_before)}). "
                     "لو في مبلغ موجود قبل النظام، سجّله من 'إضافة على العهدة'.")
    return redirect("custody_detail", pk=st.pk)


@staff_member_required
@require_POST
def custody_deposit(request):
    _need(request, "trade.add_custodydeposit")
    amount = _amount(request.POST.get("amount"))
    if amount <= 0:
        messages.error(request, "اكتب مبلغ صحيح أكبر من صفر")
        return redirect("custody")
    try:
        when = date.fromisoformat(request.POST.get("date", ""))
    except ValueError:
        when = timezone.localdate()
    note = request.POST.get("note", "").strip()[:200]
    CustodyDeposit.objects.create(amount=amount, date=when, note=note,
                                  created_by=request.user)
    log_event(f"➕ إضافة على العهدة {fmt(amount)}" + (f" — {note}" if note else ""))
    messages.success(request, f"اتضاف {fmt(amount)} على العهدة")
    return redirect("custody")


@staff_member_required
def custody_detail(request, pk):
    _need(request, "trade.view_custodysettlement")
    st = get_object_or_404(CustodySettlement.objects.select_related("created_by"), pk=pk)
    return render(request, "trade/custody_detail.html", {
        **admin.site.each_context(request), "title": f"تصفية عهدة #{st.pk}", "st": st})


@staff_member_required
def custody_screenshot(request, pk):
    """الصورة بتتقدم من هنا بس (مع فحص الصلاحية). مفيش رابط مباشر ليها."""
    _need(request, "trade.view_custodysettlement")
    st = get_object_or_404(CustodySettlement, pk=pk)
    if not st.screenshot:
        raise Http404
    ext = st.screenshot.name.rsplit(".", 1)[-1].lower()
    try:
        f = st.screenshot.open("rb")
    except FileNotFoundError:
        raise Http404
    resp = FileResponse(f, content_type=IMAGE_TYPES.get(ext, "application/octet-stream"))
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


@staff_member_required
def custody_receipt(request, pk):
    _need(request, "trade.view_custodysettlement")
    st = get_object_or_404(CustodySettlement.objects.select_related("created_by"), pk=pk)
    return render(request, "trade/custody_receipt.html",
                  {"st": st, "shop_name": "SPIRIT | سبيريت"})


# ================= العهدة على الأشخاص =================
def holder_rows():
    rows = []
    for h in CustodyHolder.objects.select_related("user"):
        t = h.totals()
        if h.is_active or t["balance"] or t["pending"]:
            rows.append({"holder": h, **t})
    rows.sort(key=lambda r: (-r["balance"], r["holder"].name))
    return rows


def _free_users():
    """المستخدمين اللي لسه مش مربوطين بصاحب عهدة"""
    return (get_user_model().objects.filter(is_active=True, custody_holder__isnull=True)
            .order_by("username"))


def _can_see_holder(user, holder):
    return user.has_perm("trade.view_custodysettlement") or (holder.user_id == user.pk)


def _can_request(user, holder):
    return user.has_perm("trade.add_custodyclearance") or (holder.user_id == user.pk)


def _read_image(upload):
    """(data, kind) أو ValueError برسالة"""
    if upload.size > MAX_UPLOAD:
        raise ValueError("الصورة أكبر من 8 ميجا")
    data = upload.read()
    kind = _image_kind(data[:16])
    if kind is None:
        raise ValueError("الملف لازم يكون صورة (PNG أو JPG أو WEBP)")
    return data, kind


@staff_member_required
@require_POST
def holder_add(request):
    _need(request, "trade.add_custodyholder")
    name = request.POST.get("name", "").strip()[:120]
    if not name:
        messages.error(request, "اكتب الاسم")
        return redirect("custody")
    if CustodyHolder.objects.filter(name__iexact=name).exists():
        messages.error(request, f"'{name}' موجود قبل كده")
        return redirect("custody")
    user = None
    if request.POST.get("user"):
        user = _free_users().filter(pk=request.POST["user"]).first()
        if user is None:
            messages.error(request, "الحساب ده مربوط بشخص تاني")
            return redirect("custody")
    CustodyHolder.objects.create(name=name, user=user, phone=request.POST.get("phone", "").strip()[:30])
    linked = f" (مربوط بحساب {user.get_username()})" if user else " (من بره النظام)"
    messages.success(request, f"اتضاف {name}{linked}")
    return redirect("custody")


@staff_member_required
def my_custody(request):
    holder = getattr(request.user, "custody_holder", None)
    if holder is None:
        messages.info(request, "مفيش عهدة مسجلة على حسابك.")
        return redirect("dashboard")
    return redirect("custody_holder", pk=holder.pk)


@staff_member_required
def holder_detail(request, pk):
    holder = get_object_or_404(CustodyHolder.objects.select_related("user"), pk=pk)
    if not _can_see_holder(request.user, holder):
        raise PermissionDenied
    approver = request.user.has_perm("trade.approve_custodyclearance")
    return render(request, "trade/custody_holder.html", {
        **admin.site.each_context(request), "title": f"عهدة {holder.name}",
        "holder": holder, "t": holder.totals(),
        "handouts": holder.handouts.select_related("created_by")[:100],
        "clearances": holder.clearances.select_related("requested_by", "reviewed_by")[:100],
        "can_request": _can_request(request.user, holder),
        "approver": approver, "me": request.user,
        "kinds": CustodyClearance.KINDS, "today": timezone.localdate().isoformat(),
        "is_mine": holder.user_id == request.user.pk,
    })


@staff_member_required
@require_POST
def clearance_request(request, pk):
    holder = get_object_or_404(CustodyHolder, pk=pk)
    if not _can_request(request.user, holder):
        raise PermissionDenied
    amount = _amount(request.POST.get("amount"))
    kind = request.POST.get("kind")
    desc = request.POST.get("description", "").strip()[:200]
    if amount <= 0:
        messages.error(request, "اكتب مبلغ صحيح أكبر من صفر")
        return redirect("custody_holder", pk=pk)
    if kind not in dict(CustodyClearance.KINDS):
        kind = CustodyClearance.SPENT
    if not desc:
        desc = "رجّع فلوس" if kind == CustodyClearance.RETURNED else ""
    if not desc:
        messages.error(request, "اكتب اتصرفت في إيه")
        return redirect("custody_holder", pk=pk)
    t = holder.totals()
    if amount > t["balance"] - t["pending"]:
        messages.error(request, f"المبلغ أكبر من رصيد العهدة اللي مستني تصفية "
                                f"({fmt(t['balance'] - t['pending'])})")
        return redirect("custody_holder", pk=pk)
    try:
        d = date.fromisoformat(request.POST.get("date", ""))
    except ValueError:
        d = timezone.localdate()
    c = CustodyClearance(holder=holder, kind=kind, amount=amount, description=desc, date=d,
                         requested_by=request.user)
    if request.FILES.get("screenshot"):
        try:
            data, ext = _read_image(request.FILES["screenshot"])
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("custody_holder", pk=pk)
        c.screenshot.save(f"{uuid.uuid4().hex}.{ext}", ContentFile(data), save=False)
    c.save()
    log_event(f"🧾 طلب تصفية عهدة: {holder} — {c.get_kind_display()} {fmt(amount)}\n{desc}"
              f"\nمستني موافقة مدير الحسابات")
    messages.success(request, "اتبعت الطلب. هيتخصم من العهدة بعد موافقة مدير الحسابات.")
    return redirect("custody_holder", pk=pk)


@staff_member_required
@require_POST
def clearance_review(request, pk):
    _need(request, "trade.approve_custodyclearance")
    c = get_object_or_404(CustodyClearance.objects.select_related("holder"), pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        c.review(request.user, approve, request.POST.get("note", "").strip())
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    else:
        word = "اتوافق على" if approve else "اترفض"
        log_event(f"{'✅' if approve else '❌'} {word} تصفية عهدة {c.holder}: "
                  f"{c.get_kind_display()} {fmt(c.amount)}"
                  f"\nرصيد عهدته: {fmt(c.holder.totals()['balance'])}")
        messages.success(request, f"{word} الطلب")
    nxt = request.POST.get("next") or ""
    if nxt == "approvals":
        return redirect("custody_approvals")
    return redirect("custody_holder", pk=c.holder_id)


@staff_member_required
def custody_approvals(request):
    _need(request, "trade.approve_custodyclearance")
    pending = (CustodyClearance.objects.filter(status=CustodyClearance.PENDING)
               .select_related("holder", "requested_by").order_by("requested_at"))
    return render(request, "trade/custody_approvals.html", {
        **admin.site.each_context(request), "title": "طلبات تصفية العهد",
        "pending": pending, "me": request.user})


@staff_member_required
def clearance_screenshot(request, pk):
    c = get_object_or_404(CustodyClearance.objects.select_related("holder"), pk=pk)
    if not _can_see_holder(request.user, c.holder) or not c.screenshot:
        raise Http404
    ext = c.screenshot.name.rsplit(".", 1)[-1].lower()
    try:
        f = c.screenshot.open("rb")
    except FileNotFoundError:
        raise Http404
    resp = FileResponse(f, content_type=IMAGE_TYPES.get(ext, "application/octet-stream"))
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, max-age=3600"
    return resp
