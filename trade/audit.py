"""سجل المراجعة: بيسجّل تلقائياً مين أضاف أو عدّل أو حذف إيه ومتى (من أي مكان: صفحات النظام أو لوحة الإدارة)."""
import logging
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import (user_logged_in, user_logged_out,
                                         user_login_failed)
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from .models import (AuditLog, ClothingModel, CustodyDeposit, CustodySettlement,
                     Customer, Expense, OnlineOrder, Payment, PurchaseInvoice,
                     SaleInvoice, Supplier, Variant)
from .notify import client_ip, current_actor, log_event, model_ar

logger = logging.getLogger(__name__)
User = get_user_model()

TRACKED = [ClothingModel, Variant, Customer, Supplier, PurchaseInvoice, SaleInvoice,
           Payment, OnlineOrder, Expense, CustodyDeposit, CustodySettlement, User]
# حقول مش بتتسجل: كلمات السر، أوقات تلقائية، وكميات المخزن (بتتغير مع كل بيع، وبيتسجل سببها في الفاتورة)
SKIP = {"id", "password", "last_login", "date_joined", "created_at", "updated_at", "created_by"}
SKIP_BY_MODEL = {"Variant": {"quantity", "avg_cost"}}


def _value(field, instance):
    v = getattr(instance, field.attname, None)
    if isinstance(field, models.FileField):
        return "مرفق" if v else ""
    if v is None:
        return ""
    if isinstance(field, models.DecimalField):   # 250 و 250.00 نفس القيمة: نوحّد الشكل عشان ما يظهرش "تغيير" وهمي
        try:
            return f"{Decimal(v):.{field.decimal_places}f}"
        except (ArithmeticError, ValueError):
            pass
    return str(v)[:200]


def snapshot(instance):
    skip = SKIP | SKIP_BY_MODEL.get(instance.__class__.__name__, set())
    out = {}
    for f in instance._meta.concrete_fields:
        if f.name in skip or f.attname in skip:
            continue
        out[str(f.verbose_name)] = _value(f, instance)
    return out


def _repr(instance):
    try:
        return str(instance)[:250]
    except Exception:
        return f"#{instance.pk}"


def write(action, *, model="", object_id="", object_repr="", changes=None,
          user=None, ip=None, username=""):
    """يكتب سطر في السجل. عمره ما يكسر العملية الأصلية."""
    try:
        if user is None and ip is None:
            user, ip = current_actor()
        with transaction.atomic():
            AuditLog.objects.create(
                user=user if getattr(user, "pk", None) else None,
                username=(user.get_username() if user is not None else username) or "",
                action=action, model=model[:80], object_id=str(object_id)[:40],
                object_repr=object_repr[:250], changes=changes or {}, ip=ip or "")
    except Exception:
        logger.exception("audit write failed")


# حاجات ليها رسالة تليجرام مخصصة في مكانها (البيع، الدفع، المصروف...): مش هنكررها هنا
NOTIFY_CREATE_SKIP = {"SaleInvoice", "PurchaseInvoice", "Payment", "Expense", "Customer",
                      "OnlineOrder", "CustodyDeposit", "CustodySettlement"}
NOTIFY_UPDATE_SKIP = {"SaleInvoice", "PurchaseInvoice", "OnlineOrder"}  # المسودات بتتعدل كتير


def _notify(action, sender, instance=None, label="", repr_="", changes=None):
    """رسالة تليجرام للإضافة والتعديل والحذف لكل الحاجات المتتبعة"""
    try:
        user, _ip = current_actor()
        who = user.get_username() if user is not None and getattr(user, "is_authenticated", False) else "نظام"
        if action == "create" and sender.__name__ in NOTIFY_CREATE_SKIP:
            return
        if action == "update" and sender.__name__ in NOTIFY_UPDATE_SKIP:
            return
        head = {"create": "🆕 إضافة", "update": "✏️ تعديل", "delete": "🗑 حذف"}[action]
        lines = [f"{head} {label}: {repr_}", f"بواسطة: {who}"]
        for k, v in list((changes or {}).items())[:6]:
            if isinstance(v, (list, tuple)) and len(v) == 2:
                lines.append(f"• {k}: {v[0] or '—'} ← {v[1] or '—'}")
            else:
                lines.append(f"• {k}: {v}")
        log_event("\n".join(lines), user=user)
    except Exception:
        logger.exception("audit telegram failed")


def _connect():
    for model in TRACKED:
        uid = f"audit-{model._meta.label_lower}"
        pre_save.connect(_before_save, sender=model, dispatch_uid=uid + "-pre")
        post_save.connect(_after_save, sender=model, dispatch_uid=uid + "-post")
        pre_delete.connect(_before_delete, sender=model, dispatch_uid=uid + "-predel")
        post_delete.connect(_after_delete, sender=model, dispatch_uid=uid + "-postdel")


def _before_save(sender, instance, raw=False, **kw):
    instance._audit_old = None
    if raw or not instance.pk:
        return
    try:
        old = sender.objects.get(pk=instance.pk)
        instance._audit_old = snapshot(old)
    except sender.DoesNotExist:
        pass


def _after_save(sender, instance, created, raw=False, **kw):
    if raw:
        return
    label = model_ar(sender._meta.model_name, str(sender._meta.verbose_name))
    if created:
        snap = {k: v for k, v in snapshot(instance).items() if v != ""}
        write("create", model=label, object_id=instance.pk, object_repr=_repr(instance),
              changes=dict(list(snap.items())[:14]))
        _notify("create", sender, instance, label, _repr(instance), snap)
        return
    old = getattr(instance, "_audit_old", None)
    if old is None:
        return
    new = snapshot(instance)
    diff = {k: [old.get(k, ""), v] for k, v in new.items() if old.get(k, "") != v}
    if diff:
        write("update", model=label, object_id=instance.pk,
              object_repr=_repr(instance), changes=diff)
        _notify("update", sender, instance, label, _repr(instance), diff)


def _before_delete(sender, instance, **kw):
    instance._audit_repr = _repr(instance)
    instance._audit_snap = {k: v for k, v in snapshot(instance).items() if v != ""}


def _after_delete(sender, instance, **kw):
    write("delete", model=str(sender._meta.verbose_name), object_id=instance.pk,
          object_repr=getattr(instance, "_audit_repr", f"#{instance.pk}"),
          changes=dict(list(getattr(instance, "_audit_snap", {}).items())[:14]))
    _notify("delete", sender, instance, model_ar(sender._meta.model_name, str(sender._meta.verbose_name)),
            getattr(instance, "_audit_repr", f"#{instance.pk}"))


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kw):
    write("login", model="مستخدم", object_id=user.pk, object_repr=user.get_username(),
          user=user, ip=client_ip(request) if request else "")


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kw):
    if user is not None:
        write("logout", model="مستخدم", object_id=user.pk, object_repr=user.get_username(),
              user=user, ip=client_ip(request) if request else "")
        log_event(f"🚪 تسجيل خروج: {user.get_username()}", user=user)


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kw):
    name = credentials.get("username", "")
    write("login_failed", model="مستخدم", object_repr=name, username=name,
          user=None, ip=client_ip(request) if request else "")


_connect()
